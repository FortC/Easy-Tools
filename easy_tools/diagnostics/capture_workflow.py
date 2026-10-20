"""Exercise actual widget painting and queued screenshot delivery in isolation.

All selected pixels and question-bank data are synthetic. The diagnostic does
not install hooks, load the user's configuration, or change the clipboard.
"""
from __future__ import annotations

import ctypes
import json
import sys
import tempfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

from PySide6.QtCore import QEvent, QEventLoop, QPoint, QPointF, QRect, Qt, QThread
from PySide6.QtGui import QColor, QFont, QImage, QKeyEvent, QMouseEvent, QPainter
from PySide6.QtWidgets import QApplication, QInputDialog, QWidget
from shiboken6 import isValid

from ..core.config_store import ConfigStore
from ..core.ocr import OcrService
from ..core.question_bank import QuestionAnswerService
from ..ui import capture_overlay as overlay_module
from ..ui.capture_controller import CaptureController
from ..ui.capture_privacy import CapturePrivacyFilter, set_window_capture_excluded
from ..ui.question_answer_dialog import QuestionAnswerDialog
from ..ui.bank_columns_dialog import BankColumnsDialog
from ..ui.settings import SettingsWindow
from ..ui.panel import ActionPanel, PanelBridge
from ..core.app import PanelItem
from ..core.actions.base import ActionContext, run_action
from ..win32.gdi import Capture


@contextmanager
def _replace(obj, name, value):
    original = getattr(obj, name)
    setattr(obj, name, value)
    try:
        yield
    finally:
        setattr(obj, name, original)


def run(report_path: str | Path, real_ocr: bool = True) -> int:
    app = QApplication.instance() or QApplication([])
    from easy_tools.ui.theme import apply_application_theme
    apply_application_theme(app)
    app.setQuitOnLastWindowClosed(False)
    report = {"platform": app.platformName(), "scenarios": [], "ok": False}
    errors = []
    previous_hook = sys.excepthook
    sys.excepthook = lambda kind, value, tb: errors.append(f"{kind.__name__}: {value}")

    def pump(seconds=0.03):
        until = time.monotonic() + seconds
        while time.monotonic() < until:
            app.processEvents(QEventLoop.AllEvents, 10)
            app.sendPostedEvents(None, QEvent.DeferredDelete)
            time.sleep(0.003)
        if errors:
            raise AssertionError(errors[-1])

    def wait_for(predicate, timeout=8):
        until = time.monotonic() + timeout
        while not predicate():
            if time.monotonic() > until:
                raise AssertionError("截图流程等待超时")
            pump(0.01)
        pump(0.01)

    def mouse(widget, kind, point):
        button = Qt.NoButton if kind == QEvent.MouseMove else Qt.LeftButton
        buttons = Qt.NoButton if kind == QEvent.MouseButtonRelease else Qt.LeftButton
        global_point = widget.mapToGlobal(point)
        app.sendEvent(widget, QMouseEvent(kind, QPointF(point), QPointF(global_point),
                                         button, buttons, Qt.NoModifier))
        pump()

    native = app.platformName() == "windows"
    calls = []

    def setter(hwnd, enabled):
        calls.append((hwnd, enabled))
        return set_window_capture_excluded(hwnd, enabled) if native else True

    def check_affinity(widget, expected):
        if native:
            from ctypes import wintypes
            getter = ctypes.WinDLL("user32", use_last_error=True).GetWindowDisplayAffinity
            getter.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
            getter.restype = wintypes.BOOL
            value = wintypes.DWORD()
            assert getter(int(widget.winId()), ctypes.byref(value)), "无法读取窗口捕获属性"
            assert value.value == (0x11 if expected else 0), f"隐私属性错误：{value.value}"

    privacy = None
    dialogs = []
    gate = threading.Event()
    try:
        with tempfile.TemporaryDirectory(prefix="easy-tools-capture-check-") as temporary:
            from openpyxl import Workbook
            root = Path(temporary)
            wb = Workbook()
            wb.active.append(["题目名称", "题目类型", "题目难度", "答案1", "答案2", "正确答案", "答案"])
            wb.active.append(["What is 2 plus 2?", "单选", "容易", "3", "4", "B", "A"])
            wb.save(root / "synthetic-bank.xlsx")
            wb.close()
            store = ConfigStore(root / "config.json")
            store.reload()
            store.patch("question_banks", [{"id": "test", "name": "Synthetic",
                        "path": str(root / "synthetic-bank.xlsx"), "enabled": True}])
            store.patch("llm", {"enabled": False, "prefer_bank_answer": True})
            privacy = CapturePrivacyFilter(app, store, setter=setter)

            class DelayedOcr:
                def recognize_capture(self, cap):
                    assert gate.wait(6), "后台 OCR 测试未被释放"
                    return "What is 2 plus 2?"

                def status(self):
                    return {"ready": True, "enabled": False, "backend": "synthetic"}

            service = SimpleNamespace(store=store, ocr=DelayedOcr(),
                                      question_answer=QuestionAnswerService(store))
            service.ccswitch_model = None
            service.status_text = lambda: "Isolated capture diagnostic"
            service.refresh_context = lambda: None
            service.panel_snapshot = lambda: {"public": {"Synthetic": [
                PanelItem("test", "Test action", "", "Synthetic", "public", "")]},
                "context": {}, "window": {"process": "synthetic.exe"}}
            panel = ActionPanel(service)
            settings = SettingsWindow(service)
            prompt = QInputDialog()
            prompt.setWindowTitle("Synthetic prompt")
            columns_dialog = BankColumnsDialog({"path": str(root / "synthetic-bank.xlsx")})
            assert columns_dialog.answer.currentData() == 5
            assert columns_dialog.options["B"].currentData() == 4

            class Marker(QWidget):
                def paintEvent(self, event):
                    painter = QPainter(self)
                    try:
                        painter.fillRect(0, 0, self.width() // 2, self.height(), QColor("#ff00ff"))
                        painter.fillRect(self.width() // 2, 0, self.width(), self.height(), QColor("#00ff00"))
                    finally:
                        painter.end()

            marker = Marker(None, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
            marker.setGeometry(app.primaryScreen().availableGeometry().x() + 24,
                               app.primaryScreen().availableGeometry().y() + 24, 200, 100)
            held = [False]
            controller = CaptureController(service, parent=app, mouse_pressed=lambda: held[0])
            bridge = PanelBridge(panel)
            bridge.request_capture.connect(controller.start, Qt.QueuedConnection)
            context = ActionContext(start_capture=bridge.request_capture.emit)

            def launch_action():
                result = run_action("screen.capture", context,
                    {"auto_ocr": True, "copy_image": False, "ask_question": True})
                if not result.ok:
                    errors.append(result.message)

            answers = []

            def on_answer(text, answer, sources, anchor):
                assert QThread.currentThread() == app.thread(), "答案未返回 GUI 线程"
                answers.append((text, answer, sources))
                dialog = QuestionAnswerDialog(text, answer, sources)
                dialog.setAttribute(Qt.WA_ShowWithoutActivating)
                assert not anchor.isEmpty(), "截图答案丢失选区定位"
                dialog.position_near_selection(anchor)
                available = (app.screenAt(anchor.center()) or app.primaryScreen()).availableGeometry()
                if anchor.right() + 12 + dialog.width() <= available.right():
                    assert dialog.x() > anchor.right(), "答案未显示在选框右侧"
                report["answer_near_selection"] = True
                dialogs.append(dialog)
                dialog.show()

            controller.answer_at.connect(on_answer)

            class Screen:
                def geometry(self):
                    screen = app.primaryScreen().availableGeometry()
                    return QRect(screen.x() + 24, screen.y() + 24, 400, 280)

                def devicePixelRatio(self):
                    return 2.0

            for enabled in (False, True):
                store.patch("privacy", {"exclude_from_capture": enabled})
                pump()
                for widget in (settings, panel, prompt, columns_dialog):
                    widget.show()
                    pump()
                    check_affinity(widget, enabled)
                    widget.hide()
                if native:
                    # Pixel-level Windows GDI check against only a synthetic
                    # two-colour marker. No screenshots are saved to disk.
                    from ..win32 import api
                    marker.show()
                    marker.raise_()
                    pump(0.10)
                    check_affinity(marker, enabled)
                    bounds = api.RECT()
                    assert api.user32.GetWindowRect(int(marker.winId()), ctypes.byref(bounds))
                    samples = []
                    for fraction in (0.25, 0.75):
                        capture = overlay_module.gdi.capture_rect(
                            int(bounds.left + (bounds.right - bounds.left) * fraction),
                            int((bounds.top + bounds.bottom) / 2), 8, 8)
                        assert capture is not None
                        b, g, r = capture.bgra[:3]
                        samples.append((r, g, b))
                    marker_present = samples == [(255, 0, 255), (0, 255, 0)]
                    assert marker_present != enabled, "Windows GDI 窗口捕获排除验证失败"
                    report["gdi_hidden" if enabled else "gdi_visible_when_disabled"] = True
                    marker.hide()
                for scale, reverse in ((1, False), (1.25, True), (1.5, False), (2, True)):
                    width, height = int(400 * scale), int(280 * scale)
                    image = QImage(width, height, QImage.Format_RGB32)
                    image.fill(QColor("white"))
                    painter = QPainter(image)
                    painter.scale(scale, scale)
                    painter.setPen(QColor("black"))
                    painter.setFont(QFont("Segoe UI", 18))
                    painter.drawText(40, 82, "What is 2 plus 2?")
                    painter.drawText(40, 128, "A. 3       B. 4")
                    painter.end()
                    raw = bytearray(image.constBits())
                    raw[3::4] = bytes(width * height)  # GDI's undefined alpha bytes
                    cap = Capture(width, height, bytes(raw))
                    requested = []

                    def capture(*rect):
                        requested.append(rect)
                        return cap

                    gate.clear()
                    before = len(answers)
                    first_call = len(calls)
                    with _replace(overlay_module, "_screen_at_cursor", lambda: Screen()), \
                            _replace(overlay_module, "monitor_rect_at_cursor", lambda: (-1733, 200, width, height)), \
                            _replace(overlay_module.gdi, "capture_rect", capture), \
                            _replace(overlay_module.focus, "remember", lambda: None), \
                            _replace(overlay_module.focus, "restore", lambda: None), \
                            _replace(overlay_module.clipboard, "set_text", lambda text: True):
                        held[0] = True
                        threading.Thread(target=launch_action, daemon=True).start()
                        pump(0.06)
                        assert not controller._sessions, "启动鼠标未松开就显示了框选层"
                        held[0] = False
                        wait_for(lambda: bool(controller._sessions))
                        overlay = next(iter(controller._sessions.values()))
                        controller.start(True, False, True)
                        assert len(controller._sessions) == 1, "重复启动创建了重叠框选层"
                        check_affinity(overlay, enabled)
                        assert requested == [(-1733, 200, width, height)]
                        start, end = QPoint(20, 20), QPoint(360, 210)
                        if reverse:
                            start, end = end, start
                        mouse(overlay, QEvent.MouseButtonPress, start)
                        mouse(overlay, QEvent.MouseMove, end)
                        overlay.repaint()
                        painted = overlay.grab().toImage()
                        ratio = painted.devicePixelRatio()
                        assert painted.pixelColor(int(30 * ratio), int(30 * ratio)).red() > 230
                        assert painted.pixelColor(int(390 * ratio), int(260 * ratio)).red() < 210
                        mouse(overlay, QEvent.MouseButtonRelease, end)
                        assert isValid(overlay) and overlay._phase == "processing"
                        assert not overlay.isVisible()
                        gate.set()
                        wait_for(lambda: len(answers) > before)
                        wait_for(lambda: not controller._sessions)
                    assert answers[-1][1] == "B. 4", answers[-1]
                    check_affinity(dialogs[-1], enabled)
                    assert dialogs[-1].windowModality() == Qt.NonModal
                    panel.show()
                    pump()
                    check_affinity(panel, enabled)
                    pressed = []
                    panel.activated.connect(pressed.append)
                    cell = panel._cells[0]
                    mouse(cell, QEvent.MouseButtonPress, QPoint(10, 10))
                    mouse(cell, QEvent.MouseButtonRelease, QPoint(10, 10))
                    panel.activated.disconnect(pressed.append)
                    assert pressed == ["test"], "答案弹窗阻碍动作面板点击"
                    if enabled:
                        assert all(flag for _, flag in calls[first_call:]), "主动截图临时解除了隐私保护"
                    report["scenarios"].append({"privacy": enabled, "scale": scale,
                                                "reverse": reverse, "answer": "B. 4"})
                    dialogs[-1].close()
                    dialogs[-1].deleteLater()
                    pump()

                # Cancellation follows real key/mouse dispatch and must not run OCR.
                for cancel in ("escape", "right_click"):
                    with _replace(overlay_module, "_screen_at_cursor", lambda: Screen()), \
                            _replace(overlay_module, "monitor_rect_at_cursor", lambda: (0, 0, width, height)), \
                            _replace(overlay_module.gdi, "capture_rect", lambda *rect: cap), \
                            _replace(overlay_module.focus, "remember", lambda: None), \
                            _replace(overlay_module.focus, "restore", lambda: None):
                        controller.start(True, False, True)
                        wait_for(lambda: bool(controller._sessions))
                        overlay = next(iter(controller._sessions.values()))
                        if cancel == "escape":
                            app.sendEvent(overlay, QKeyEvent(QEvent.KeyPress, Qt.Key_Escape, Qt.NoModifier))
                        else:
                            app.sendEvent(overlay, QMouseEvent(QEvent.MouseButtonPress,
                                QPointF(50, 50), QPointF(50, 50), Qt.RightButton, Qt.RightButton, Qt.NoModifier))
                        wait_for(lambda: not controller._sessions)
                        report["scenarios"].append({"privacy": enabled, "cancel": cancel})

            if real_ocr:
                # Run the shipped backend through the same full action/drag/
                # worker/result route, rather than only importing the OCR DLL.
                service.ocr = OcrService(store)
                before = len(answers)
                with _replace(overlay_module, "_screen_at_cursor", lambda: Screen()), \
                        _replace(overlay_module, "monitor_rect_at_cursor", lambda: (0, 0, width, height)), \
                        _replace(overlay_module.gdi, "capture_rect", lambda *rect: cap), \
                        _replace(overlay_module.focus, "remember", lambda: None), \
                        _replace(overlay_module.focus, "restore", lambda: None), \
                        _replace(overlay_module.clipboard, "set_text", lambda text: True):
                    threading.Thread(target=launch_action, daemon=True).start()
                    wait_for(lambda: bool(controller._sessions))
                    overlay = next(iter(controller._sessions.values()))
                    check_affinity(overlay, True)
                    mouse(overlay, QEvent.MouseButtonPress, QPoint(20, 20))
                    mouse(overlay, QEvent.MouseMove, QPoint(360, 210))
                    mouse(overlay, QEvent.MouseButtonRelease, QPoint(360, 210))
                    wait_for(lambda: len(answers) > before, timeout=30)
                    wait_for(lambda: not controller._sessions)
                assert "plus" in answers[-1][0].lower(), "真实 OCR 没有识别合成题目"
                assert answers[-1][1] == "B. 4", answers[-1]
                check_affinity(dialogs[-1], True)
                report["real_ocr"] = {"ready": True, "matched_answer": "B. 4"}

                if native:
                    # Final end-to-end check uses the actual desktop/GDI path,
                    # rather than the synthetic screenshot buffer above. This
                    # disposable text-only window stands in for a foreign app.
                    class SourceWindow(QWidget):
                        def paintEvent(self, event):
                            painter = QPainter(self)
                            try:
                                painter.fillRect(self.rect(), QColor("white"))
                                painter.setPen(QColor("black"))
                                painter.setFont(QFont("Segoe UI", 20))
                                painter.drawText(40, 80, "What is 2 plus 2?")
                                painter.drawText(40, 140, "A. 3       B. 4")
                            finally:
                                painter.end()

                    screen = overlay_module._screen_at_cursor().availableGeometry()
                    source = SourceWindow(None, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
                    source.setGeometry(screen.center().x() - 250, screen.center().y() - 130, 500, 260)
                    source.show()
                    source.raise_()
                    pump(0.10)
                    # Only synthetic source data is made capturable inside this
                    # isolated diagnostic process. All product UI stays excluded.
                    assert set_window_capture_excluded(int(source.winId()), False)
                    pump(0.10)
                    before = len(answers)
                    with _replace(overlay_module.focus, "remember", lambda: None), \
                            _replace(overlay_module.focus, "restore", lambda: None), \
                            _replace(overlay_module.clipboard, "set_text", lambda text: True):
                        threading.Thread(target=launch_action, daemon=True).start()
                        wait_for(lambda: bool(controller._sessions))
                        overlay = next(iter(controller._sessions.values()))
                        check_affinity(overlay, True)
                        start = overlay.mapFromGlobal(source.mapToGlobal(QPoint(15, 15)))
                        end = overlay.mapFromGlobal(source.mapToGlobal(QPoint(480, 240)))
                        assert overlay.rect().contains(start) and overlay.rect().contains(end)
                        mouse(overlay, QEvent.MouseButtonPress, start)
                        mouse(overlay, QEvent.MouseMove, end)
                        mouse(overlay, QEvent.MouseButtonRelease, end)
                        wait_for(lambda: len(answers) > before, timeout=30)
                        wait_for(lambda: not controller._sessions)
                    source.hide()
                    source.deleteLater()
                    assert answers[-1][1] == "B. 4", answers[-1]
                    check_affinity(dialogs[-1], True)
                    report["native_desktop_capture_ocr"] = True

            report["native_affinity_verified"] = native
            report["column_mapping_dialog_verified"] = True
            report["ok"] = True
    except Exception as exc:
        import traceback
        report["error"] = f"{type(exc).__name__}: {exc}"
        report["traceback"] = traceback.format_exc()
    finally:
        gate.set()
        sys.excepthook = previous_hook
        if privacy is not None:
            privacy.close()
        for widget in app.topLevelWidgets():
            if isValid(widget):
                widget.hide()
        report["qt_errors"] = errors
        target = Path(report_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if report["ok"] and not errors else 1
