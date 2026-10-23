"""Runtime regressions for bounded execution, preview isolation and reminders."""
from datetime import datetime, timedelta
import hashlib
import subprocess
import sys
import threading
import time
from unittest.mock import patch

from easy_tools.core.actions.base import ActionContext, run_action
from easy_tools.core.actions.office_macro import macro_hash
from easy_tools.core.actions.processors import get_processor, parse_table
from easy_tools.core.chain import ChainRunner
from easy_tools.core.config_store import ConfigStore
from easy_tools.core.reminders import ReminderScheduler, validate_reminders
from easy_tools.core.tasks import TaskManager
from easy_tools.core.processor_worker import run_processor
from easy_tools.core.process_job import ProcessJob
import os
import pytest
from easy_tools.win32.window import WindowInfo


def wait_for(predicate, timeout=3):
    deadline = time.monotonic() + timeout
    while not predicate():
        assert time.monotonic() < deadline
        time.sleep(0.005)


def test_nested_preview_never_calls_real_actions_or_clipboard():
    steps = [dict(type='condition', expr='always', then=[dict(type='loop', times=2, steps=[
        dict(type='action', id='clipboard.copy'),
        dict(type='action', id='word.clean_paragraphs'),
        dict(type='action', id='input.paste')])])]
    with patch('easy_tools.core.chain.action_base.run_action', side_effect=AssertionError('side effect')), \
         patch('easy_tools.core.chain.clip.get_text', side_effect=AssertionError('live data')):
        result = ChainRunner(preview=True, sample='  demo  ').run_chain(dict(steps=steps), ActionContext())
    assert result.ok
    assert result.output == 'demo'
    assert result.steps_run == 6


def test_all_effects_preview_does_not_fall_back_to_real_execution():
    with patch('easy_tools.core.chain.action_base.run_action', side_effect=AssertionError('side effect')):
        result = ChainRunner(preview=True, sample='test').run_chain(
            dict(steps=[dict(type='action', id='clipboard.clear')]), ActionContext())
    assert result.ok


def test_preview_blocks_scripts_unknown_actions_and_macros():
    for step in [dict(type='python', trusted=True, code="raise AssertionError('executed')"),
                 dict(type='action', id='office.macro'), dict(type='action', id='open.command')]:
        assert not ChainRunner(preview=True).run_chain(dict(steps=[step]), ActionContext()).ok


def test_wait_cancels_promptly_and_nested_loops_have_global_budget():
    runner = ChainRunner()
    result = []
    thread = threading.Thread(target=lambda: result.append(runner.run_chain(
        dict(steps=[dict(type='wait', seconds=60)]), ActionContext())))
    thread.start()
    time.sleep(0.03)
    runner.cancel()
    thread.join(0.5)
    assert not thread.is_alive()
    assert not result[0].ok
    loop = dict(type='loop', times=1000, steps=[dict(type='loop', times=1000, steps=[dict(type='setvar', name='x', value='1')])])
    result = ChainRunner(preview=True).run_chain(dict(steps=[loop]), ActionContext())
    assert not result.ok and '上限' in result.message


def test_recursive_chain_calls_stop(tmp_path):
    store = ConfigStore(tmp_path / 'config.json')
    cfg = store.get()
    cfg['chains'].append(dict(id='r', name='recursive', steps=[dict(type='action', id='chain.run', params=dict(chain_id='r'))]))
    assert not store.update(cfg, persist=False)
    result = ChainRunner(store).run_by_id('r', ActionContext(store=store))
    assert not result.ok and '递归' in result.message


def test_task_queue_is_bounded_deduplicated_and_cancellable():
    manager = TaskManager(workers=1, capacity=1)
    started = threading.Event()
    def work(cancel):
        started.set()
        cancel.wait(2)
    try:
        first = manager.submit('one', work, key='one')
        assert started.wait(1)
        assert manager.submit('duplicate', work, key='one') is None
        second = manager.submit('two', work)
        assert second
        assert manager.submit('overflow', work) is None
        manager.cancel(first)
        manager.cancel(second)
        wait_for(lambda: all(t['state'] == 'cancelled' for t in manager.snapshot()))
        assert all('output' not in task for task in manager.snapshot())
    finally:
        manager.shutdown(timeout=1)
    assert manager.submit('late', work) is None


def test_worker_receiver_failure_does_not_destroy_pool():
    manager = TaskManager(workers=1)
    try:
        manager.submit('bad receiver', lambda _: 'private', finished=lambda *_: 1 / 0)
        finished = threading.Event()
        manager.submit('next', lambda _: None, finished=lambda *_: finished.set())
        assert finished.wait(1)
    finally:
        manager.shutdown(timeout=1)


def test_word_and_excel_transformations_preserve_data():
    assert get_processor('word.join_wrapped').run('这是\n正文\n\nnext\nline') == '这是正文\n\nnext line'
    text = '客户\t编号\n 张三 \t00123\n\t00456\n'
    cleaned = get_processor('table.trim_cells').run(text)
    filled = get_processor('table.fill_down').run(cleaned)
    assert parse_table(filled) == [['客户', '编号'], ['张三', '00123'], ['张三', '00456']]
    assert parse_table(get_processor('table.trim_cells').run('a\tb\n"多\n行"\t001\n'))[1] == ['多\n行', '001']
    result = get_processor('table.column_summary').run('金额\n0.1\n0.2\n')
    assert ['合计', '0.3'] in parse_table(result)


def test_macro_permission_and_foreground_loss_stop_keys():
    params = dict(sequence='ctrl+b\nctrl+i', trusted=True)
    assert not run_action('office.macro', ActionContext(), params).ok
    params['trusted_hash'] = macro_hash(params['sequence'])
    ctx = ActionContext(target_hwnd=12)
    with patch('easy_tools.core.actions.office_macro.focus.restore_to', return_value=True), \
         patch('easy_tools.core.actions.office_macro.winput.hotkey') as hotkey, \
         patch('easy_tools.core.actions.office_macro.w32.get_foreground', side_effect=[
             WindowInfo(hwnd=12, process='winword.exe'), WindowInfo(hwnd=13, process='excel.exe')]):
        assert not run_action('office.macro', ctx, params).ok
        hotkey.assert_called_once_with('ctrl+b')


def test_modified_script_requires_new_authorization():
    step = dict(type='python', trusted=True, code="result = 'new'", trusted_hash=hashlib.sha256(b'old').hexdigest())
    result = ChainRunner().run_chain(dict(steps=[step]), ActionContext())
    assert not result.ok and '修改' in result.message


def test_reminders_persist_once_and_do_not_burst_after_resume(tmp_path):
    store = ConfigStore(tmp_path / 'config.json')
    now = datetime(2026, 10, 22, 9, 0)
    reminders = [dict(id='once', title='one', mode='once', at=now.isoformat()),
                 dict(id='daily', title='daily', mode='daily', at='09:00'),
                 dict(id='repeat', title='repeat', mode='interval', seconds=60)]
    assert not store.patch('reminders', reminders)
    seen = []
    scheduler = ReminderScheduler(store, seen.append)
    scheduler.tick(now)
    scheduler.tick(now)
    assert seen == ['one', 'daily']
    scheduler.tick(now + timedelta(hours=8))
    assert seen == ['one', 'daily', 'repeat']
    reloaded = ConfigStore(store.path)
    reloaded.reload()
    ReminderScheduler(reloaded, seen.append).tick(now)
    assert seen.count('one') == 1
    assert validate_reminders([dict(id='bad', title='x', mode='interval', seconds=1)])


def test_process_workers_release_and_handle_real_results():
    assert run_processor('word.clean_paragraphs', '  hi  ', {}) == 'hi'
    assert run_processor('word.clean_paragraphs', '  中文合同🙂  ', {}) == '中文合同🙂'
    cancel = threading.Event()
    cancel.set()
    try:
        run_processor('text.strip', 'hi', {}, cancel)
    except RuntimeError:
        pass
    else:
        raise AssertionError('cancel ignored')
    process = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])
    job = ProcessJob(process)
    try:
        job.close()
        process.wait(timeout=2)
    finally:
        if process.poll() is None:
            process.kill()


def test_python_pipe_preserves_unicode_and_import_revokes_nested_trust(tmp_path):
    step = dict(type='python', trusted=True, code='result = input_text + "处理完成🙂"', input='中文')
    result = ChainRunner().run_chain(dict(steps=[step]), ActionContext())
    assert result.ok and result.output == '中文处理完成🙂'
    import json
    store = ConfigStore(tmp_path / 'config.json')
    cfg = store.get()
    cfg['chains'].append(dict(id='imported', name='imported', trigger=dict(type='manual'), steps=[
        dict(type='loop', times=1, steps=[step, dict(type='action', id='office.macro', args=dict(
            sequence='ctrl+b', trusted=True, trusted_hash=macro_hash('ctrl+b')))])]))
    assert not store.import_json(json.dumps(cfg))
    imported = store.section('chains')[-1]['steps'][0]['steps']
    assert not imported[0]['trusted'] and not imported[1]['args']['trusted']


def test_model_idle_release_keeps_future_start_enabled():
    from easy_tools.core.builtin_model import BuiltinModel
    from unittest.mock import Mock
    model = BuiltinModel()
    model._last_used = time.monotonic() - 301
    process, job = Mock(), Mock()
    process.poll.return_value = None
    model._process, model._job = process, job
    model.release_if_idle()
    assert model._process is None
    job.close.assert_called_once()
    assert not model._stop_requested.is_set()


@pytest.mark.skipif(os.name != 'nt', reason='Windows Job Object lifecycle')
def test_job_reaps_descendants_and_abrupt_owner_exit(tmp_path):
    import ctypes
    from ctypes import wintypes
    from easy_tools.core.process_job import spawn_worker
    marker = tmp_path / 'owned-child.pid'
    code = ('import subprocess,sys,time; from pathlib import Path; '
            'p=subprocess.Popen([sys.executable,"-c","import time; time.sleep(60)"],'
            'stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL); '
            f'Path({str(marker)!r}).write_text(str(p.pid)); time.sleep(60)')
    process, job = spawn_worker([sys.executable, '-c', code], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    kernel = ctypes.WinDLL('kernel32')
    kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    handle = None
    try:
        wait_for(marker.is_file)
        handle = kernel.OpenProcess(0x100000, False, int(marker.read_text()))
        assert handle
        job.close()
        assert kernel.WaitForSingleObject(handle, 2000) == 0
        process.wait(timeout=2)
    finally:
        job.close()
        if handle:
            kernel.CloseHandle(handle)
    owner = ('import os,sys,subprocess; from easy_tools.core.process_job import spawn_worker; '
             'p,j=spawn_worker([sys.executable,"-c","import time; time.sleep(60)"],'
             'stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL); print(p.pid,flush=True); os._exit(0)')
    ended = subprocess.run([sys.executable, '-c', owner], capture_output=True, text=True, timeout=5)
    assert ended.returncode == 0
    handle = kernel.OpenProcess(0x100000, False, int(ended.stdout.strip()))
    if handle:
        try:
            assert kernel.WaitForSingleObject(handle, 2000) == 0
        finally:
            kernel.CloseHandle(handle)


def test_scripts_cannot_exhaust_parent_with_unbounded_pipe_output():
    result = ChainRunner().run_chain(dict(steps=[dict(type='python', trusted=True,
        code="import os\nwhile True: os.write(1, b'x' * 65536)", timeout_seconds=3)]), ActionContext())
    assert not result.ok and '输出超过预算' in result.message


def test_release_versions_are_consistent():
    import tomllib
    from pathlib import Path
    from easy_tools import __version__
    from installer.installer_app import VERSION
    assert VERSION == __version__
    with (Path(__file__).resolve().parents[1] / 'pyproject.toml').open('rb') as stream:
        assert tomllib.load(stream)['project']['version'] == __version__
