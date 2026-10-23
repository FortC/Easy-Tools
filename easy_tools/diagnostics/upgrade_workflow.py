"""Synthetic-only Qt/native privacy and office-upgrade smoke evidence.

Does not install global hooks, edit user config, send keys or read the clipboard.
Run: python -m easy_tools.diagnostics.upgrade_workflow [--native]
"""
import argparse
import ctypes
from ctypes import wintypes
import gc
import json
import os
from pathlib import Path
import tempfile
import time
import sys


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--native', action='store_true')
    parser.add_argument('--output', default='evidence/upgrade')
    options = parser.parse_args()
    os.environ['QT_QPA_PLATFORM'] = 'windows' if options.native else 'offscreen'
    from PySide6.QtCore import QEvent, QTimer
    from PySide6.QtWidgets import QApplication
    from ..core.app import AppService
    from ..core.config_store import ConfigStore
    from ..core.workflow_templates import instantiate
    from ..core.actions.processors import get_processor
    from ..core.processor_worker import run_processor
    from ..ui.theme import apply_application_theme
    from ..ui.settings import SettingsWindow
    from ..ui.chain_editor import ChainEditorDialog
    from ..ui.office_tools_page import ToolPreviewDialog
    from ..ui.reminders_page import ReminderEditor, ReminderInbox
    from ..ui.capture_privacy import CapturePrivacyFilter

    app = QApplication([])
    app.setQuitOnLastWindowClosed(False)
    apply_application_theme(app)
    output = Path(options.output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    from easy_tools import __version__
    report = dict(version=__version__, platform=app.platformName(), fixture='synthetic-only', windows=[], benchmarks={})

    def pump(duration=0.1):
        until = time.monotonic() + duration
        while time.monotonic() < until:
            app.processEvents()
            app.sendPostedEvents(None, QEvent.DeferredDelete)
            time.sleep(0.003)

    def wait(predicate, timeout=10):
        deadline = time.monotonic() + timeout
        while not predicate():
            assert time.monotonic() < deadline, 'background task did not finish'
            pump(0.01)

    def capture(widget, name):
        widget.show()
        pump()
        protected = None
        if options.native:
            affinity = wintypes.DWORD()
            getter = ctypes.WinDLL('user32').GetWindowDisplayAffinity
            getter.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
            getter.restype = wintypes.BOOL
            assert getter(int(widget.winId()), ctypes.byref(affinity))
            protected = affinity.value == 0x11
            assert protected, f'privacy not applied to {name}'
        # Internal Qt rendering of synthetic data only; affinity is never disabled.
        assert widget.grab().save(str(output / (name + '.png')))
        report['windows'].append(dict(name=name, protected=protected, width=widget.width(), height=widget.height()))

    with tempfile.TemporaryDirectory(prefix='easy-tools-upgrade-') as temp:
        store = ConfigStore(Path(temp) / 'config.json')
        store.reload()
        service = AppService(store)
        service.status_text = lambda: 'Synthetic verification · no global hooks'
        privacy = CapturePrivacyFilter(app, store, setter=None if options.native else lambda *_: True)
        windows = []
        try:
            from unittest.mock import patch
            with patch('easy_tools.ui.update_page.is_frozen', return_value=False):
                settings = SettingsWindow(service)
            settings.resize(1100, 720)
            windows.append(settings)
            capture(settings, 'office-tools')
            for title, name in [('事件链', 'workflows'), ('定时提醒', 'reminders'), ('运行任务', 'tasks')]:
                index = next(i for i in range(settings.tabs.count()) if settings.tabs.tabText(i) == title)
                settings.tabs.setCurrentIndex(index)
                capture(settings, name)
            settings.hide()

            workflow = ChainEditorDialog(instantiate(8), service)
            windows.append(workflow)
            workflow.sample.setPlainText('姓名\t部门\n张三\t销售\n李四\t采购\n')
            workflow.list.setCurrentRow(1)
            workflow.test_run()
            wait(lambda: workflow._task is None)
            assert '预览完成' in workflow.status.text()
            capture(workflow, 'workflow-editor')
            workflow.hide()

            macro = ChainEditorDialog(instantiate(12), service)
            windows.append(macro)
            capture(macro, 'macro-editor')
            macro.hide()

            tool = ToolPreviewDialog(get_processor('word.clean_paragraphs'), service)
            windows.append(tool)
            tool.input.setPlainText('  示例合同说明  \n\n\n  请检查附件和日期。 ')
            tool.preview()
            wait(lambda: tool._task is None)
            assert tool._output == '示例合同说明\n\n请检查附件和日期。', (repr(tool._output), tool.status.text())
            capture(tool, 'word-preview')
            tool.hide()

            reminder = ReminderEditor()
            reminder.title.setText('检查合同附件（示例）')
            windows.append(reminder)
            capture(reminder, 'reminder-editor')
            reminder.hide()
            inbox = ReminderInbox(app)
            inbox.show('检查合同附件（示例）')
            windows.append(inbox.dialog)
            capture(inbox.dialog, 'reminder-inbox')
            inbox.dialog.hide()

            # Responsiveness while a pathological regular expression is isolated.
            heartbeats = []
            timer = QTimer()
            timer.setInterval(20)
            timer.timeout.connect(lambda: heartbeats.append(time.monotonic()))
            timer.start()
            task = service.tasks.submit('synthetic-regex', lambda cancel: run_processor(
                'text.replace', 'a' * 100000 + '!', dict(find='(a+)+$', replace='', regex=True), cancel, timeout=0.6))
            wait(lambda: next(t for t in service.tasks.snapshot() if t['id'] == task)['state'] not in ('queued', 'running'))
            timer.stop()
            assert len(heartbeats) > 5
            report['ui_max_heartbeat_gap_ms'] = round(max((b-a for a, b in zip(heartbeats, heartbeats[1:])), default=0) * 1000, 2)

            table = '名称\t编号\t数量\n' + ''.join(f'客户{i}\t00{i}\t1\n' for i in range(10000))
            for pid, value in [('word.clean_paragraphs', '合同说明\n\n' * 100000), ('table.trim_cells', table)]:
                started = time.monotonic()
                result = run_processor(pid, value, {})
                report['benchmarks'][pid] = dict(input_characters=len(value), output_characters=len(result),
                                                  seconds=round(time.monotonic() - started, 3))
            report['scope'] = 'Native HWND affinity and synthetic Qt workflow; not real recording, low-end hardware, Office delivery, installer or 24-hour acceptance.'
            report['ok'] = True
        finally:
            for window in windows:
                window.close()
            service.tasks.shutdown(timeout=2)
            privacy.close()
            for window in app.topLevelWidgets():
                window.deleteLater()
            pump()
            gc.collect()
    (output / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    if sys.stdout is not None:
        print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
