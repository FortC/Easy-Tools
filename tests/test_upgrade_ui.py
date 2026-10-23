"""Exercise real Qt widgets with isolated config and synthetic text only."""
import copy
import os
import time
import pytest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtWidgets import QApplication, QWidget, QDialog
from PySide6.QtCore import QEvent
from easy_tools.core.app import AppService
from easy_tools.core.config_store import ConfigStore
from easy_tools.ui.chain_editor import ChainEditorDialog, StepEditor
from easy_tools.ui.capture_privacy import CapturePrivacyFilter
from easy_tools.ui.office_tools_page import ToolPreviewDialog
from easy_tools.core.actions.processors import get_processor
from easy_tools.ui.reminders_page import ReminderEditor, ReminderInbox

app = QApplication.instance() or QApplication([])
_services = []


@pytest.fixture(autouse=True)
def release_test_widgets():
    existing = {id(w) for w in app.topLevelWidgets()}
    yield
    for svc in _services:
        svc.tasks.shutdown(timeout=1)
    _services.clear()
    windows = [w for w in app.topLevelWidgets() if id(w) not in existing]
    for window in windows:
        window.close()
        window.deleteLater()
    app.processEvents()
    app.sendPostedEvents(None, QEvent.DeferredDelete)
    app.processEvents()


def service(tmp_path):
    store = ConfigStore(tmp_path / 'config.json')
    store.reload()
    svc = AppService(store)
    _services.append(svc)
    return svc


def pump_until(predicate, timeout=4):
    deadline = time.monotonic() + timeout
    while not predicate():
        assert time.monotonic() < deadline
        app.processEvents()
        time.sleep(0.005)


def test_edit_nested_steps_inline_and_cancel_preserves_original(tmp_path):
    svc = service(tmp_path)
    chain = dict(id='tree', name='tree', steps=[dict(type='condition', expr='always', then=[
        dict(type='action', id='text.strip', input=' hi ')], **{'else': []})])
    original = copy.deepcopy(chain)
    dialog = ChainEditorDialog(chain, svc)
    dialog.list.setCurrentRow(1)
    dialog.editor.input_edit.setText(' new ')
    dialog.list.setCurrentRow(0)
    assert dialog.result_chain()['steps'][0]['then'][0]['input'] == ' new '
    dialog.show_branch('else')
    dialog.add_action('case.upper')
    assert len(dialog.result_chain()['steps'][0]['else']) == 1
    dialog.reject()
    assert chain == original
    svc.tasks.shutdown()


def test_reorder_copy_delete_do_not_overwrite_other_steps(tmp_path):
    svc = service(tmp_path)
    chain = dict(id='order', steps=[dict(type='action', id='text.strip', input='A'),
                                    dict(type='action', id='case.upper', input='B')])
    dialog = ChainEditorDialog(chain, svc)
    dialog.move_step(1)
    assert [s['input'] for s in dialog.result_chain()['steps']] == ['B', 'A']
    dialog.duplicate_step()
    assert [s['input'] for s in dialog.result_chain()['steps']] == ['B', 'A', 'A']
    dialog.del_step()
    assert [s['input'] for s in dialog.result_chain()['steps']] == ['B', 'A']
    dialog.reject()


def test_invalid_arguments_cannot_silently_switch_or_save(tmp_path):
    svc = service(tmp_path)
    dialog = ChainEditorDialog(dict(steps=[dict(type='action', id='text.strip'), dict(type='wait', seconds=1)]), svc)
    dialog.editor.arg_edit.setPlainText('not JSON')
    dialog.list.setCurrentRow(1)
    assert dialog.list.currentRow() == 0
    dialog._on_save()
    assert dialog.result() != QDialog.Accepted
    dialog.reject()


def test_parameter_form_and_script_authorization_follow_edits():
    editor = StepEditor()
    editor.load(dict(type='action', id='table.fill_down', args=dict(col=2, header=False)))
    assert editor.dump()['args'] == dict(col=2, header=False)
    editor.load(dict(type='python', code='result = 1', trusted=True))
    assert editor.python_trusted.isChecked()
    editor.python_edit.setPlainText('result = 2')
    assert not editor.dump()['trusted']
    editor.load(dict(type='wait', seconds=1))
    assert editor.layout().labelForField(editor.python_edit).isHidden()


def test_preview_is_async_and_never_touches_real_clipboard(tmp_path):
    svc = service(tmp_path)
    dialog = ChainEditorDialog(dict(steps=[dict(type='action', id='clipboard.copy'),
        dict(type='action', id='word.clean_paragraphs'), dict(type='action', id='input.paste')]), svc)
    dialog.sample.setPlainText('  hello  ')
    with patch('easy_tools.core.chain.clip.get_text', side_effect=AssertionError('live clipboard')):
        started = time.monotonic()
        dialog.test_run()
        assert time.monotonic() - started < 0.2
        pump_until(lambda: dialog._task is None)
    assert 'hello' in dialog.preview_output.toPlainText()
    assert '完成' in dialog.status.text()
    dialog.reject()
    svc.tasks.shutdown(timeout=1)


def test_all_new_dialogs_inherit_privacy_and_fail_closed(tmp_path):
    svc = service(tmp_path)
    calls = []
    privacy = CapturePrivacyFilter(app, svc.store, setter=lambda h, enabled: calls.append((h, enabled)) or True)
    tool = ToolPreviewDialog(get_processor('word.clean_paragraphs'), svc)
    reminder = ReminderEditor()
    workflow = ChainEditorDialog(dict(steps=[]), svc)
    inbox = ReminderInbox()
    try:
        for dialog in (tool, reminder, workflow):
            dialog.show()
        inbox.show('synthetic reminder')
        app.processEvents()
        protected = {h for h, enabled in calls if enabled}
        assert all(int(w.winId()) in protected for w in (tool, reminder, workflow, inbox.dialog))
        privacy._setter = lambda *_: False
        privacy._apply_widget(tool)
        assert privacy._covers[tool].isVisible()
        privacy._setter = lambda *_: True
        privacy._apply_widget(tool)
        assert not privacy._covers[tool].isVisible()
    finally:
        for dialog in (tool, reminder, workflow, inbox.dialog):
            dialog.close()
        privacy.close()
        svc.tasks.shutdown()


def test_tool_runs_worker_and_clears_temporary_content(tmp_path):
    svc = service(tmp_path)
    dialog = ToolPreviewDialog(get_processor('word.clean_paragraphs'), svc)
    dialog.input.setPlainText('  synthetic  ')
    dialog.preview()
    pump_until(lambda: dialog._task is None)
    assert dialog._output == 'synthetic'
    dialog.reject()
    assert not dialog._output and not dialog.input.toPlainText()
    dialog._processed('late private result', '')
    assert not dialog._output and not dialog.output.toPlainText()
    svc.tasks.shutdown(timeout=1)


def test_workflow_template_creates_linked_panel_button_without_manual_id(tmp_path):
    from easy_tools.ui.settings import SettingsWindow
    from easy_tools.core.workflow_templates import instantiate
    svc = service(tmp_path)
    window = SettingsWindow(svc)
    flow = instantiate(0)
    window._chain_rows.append(flow)
    window._publish_chain_action(flow)
    cfg = window._collect()
    action = next(a for a in cfg['actions'] if a.get('params', {}).get('chain_id') == flow['id'])
    assert action['kind'] == 'chain.run' and action['zone'] == 'public'
    assert not svc.store.update(cfg, persist=False)
    window.close()
