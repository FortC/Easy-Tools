import os
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QRect
from PySide6.QtWidgets import QApplication

from easy_tools.ui.capture_overlay import CaptureOverlay
from easy_tools.win32.gdi import Capture


_app = QApplication.instance() or QApplication([])


class _Screen:
    def devicePixelRatio(self):
        return 1.0

    def geometry(self):
        return QRect(0, 0, 100, 100)


def test_overlay_is_not_deleted_before_background_ocr_finishes(monkeypatch):
    cap = Capture(100, 100, bytes(100 * 100 * 4))
    monkeypatch.setattr("easy_tools.ui.capture_overlay._screen_at_cursor", lambda: _Screen())
    monkeypatch.setattr("easy_tools.ui.capture_overlay.monitor_rect_at_cursor", lambda: (0, 0, 100, 100))
    monkeypatch.setattr("easy_tools.ui.capture_overlay.gdi.capture_rect", lambda *args: cap)
    monkeypatch.setattr("easy_tools.ui.capture_overlay.gdi.crop_capture", lambda *args: cap)
    overlay = CaptureOverlay(object(), auto_ocr=True, copy_image=False)

    with patch("easy_tools.ui.capture_overlay.focus.restore"), \
            patch("easy_tools.ui.capture_overlay.threading.Thread") as worker, \
            patch.object(overlay, "deleteLater") as delete_later:
        overlay._finish(QRect(1, 1, 20, 20))

    delete_later.assert_not_called()
    worker.return_value.start.assert_called_once()
    overlay.close()


def test_capture_delivery_reports_answer_then_signals_completion(monkeypatch):
    class Service:
        class Ocr:
            def recognize_capture(self, _cap):
                return "题目文本"

        class QuestionAnswer:
            def answer(self, _text):
                return {"matched": True, "answer": "正确答案", "matches": []}

        class Store:
            def section(self, _name, default=None):
                return {"enabled": False}

        ocr = Ocr()
        question_answer = QuestionAnswer()
        store = Store()

    cap = Capture(100, 100, bytes(100 * 100 * 4))
    monkeypatch.setattr("easy_tools.ui.capture_overlay._screen_at_cursor", lambda: _Screen())
    monkeypatch.setattr("easy_tools.ui.capture_overlay.monitor_rect_at_cursor", lambda: (0, 0, 100, 100))
    monkeypatch.setattr("easy_tools.ui.capture_overlay.gdi.capture_rect", lambda *args: cap)
    monkeypatch.setattr("easy_tools.ui.capture_overlay.clipboard.set_text", lambda _text: True)
    overlay = CaptureOverlay(Service(), auto_ocr=True, copy_image=False)
    answers, done, completed = [], [], []
    overlay.answer_ready.connect(lambda *args: answers.append(args))
    overlay.done.connect(done.append)
    overlay.delivery_finished.connect(lambda: completed.append(True))

    overlay._deliver(cap)

    assert answers == [("题目文本", "正确答案", "")]
    assert done == ["文字 4 字已复制"]
    assert completed == [True]
    overlay.close()


def test_screenshot_ocr_workers_do_not_enter_shared_backend_concurrently():
    from concurrent.futures import ThreadPoolExecutor
    import threading
    import time
    from easy_tools.core.ocr import OcrService

    class Backend:
        active = 0
        maximum = 0
        lock = threading.Lock()

        def available(self):
            return True

        def recognize(self, cap):
            with self.lock:
                self.active += 1
                self.maximum = max(self.maximum, self.active)
            time.sleep(0.01)
            with self.lock:
                self.active -= 1
            return "text"

    service = OcrService()
    backend = service._backend = Backend()
    cap = Capture(8, 8, bytes(8 * 8 * 4))
    with ThreadPoolExecutor(max_workers=4) as pool:
        assert list(pool.map(service.recognize_capture, [cap] * 8)) == ["text"] * 8
    assert backend.maximum == 1


def test_parallel_capture_answers_keep_their_own_selection_anchor(monkeypatch):
    from PySide6.QtCore import Qt
    from easy_tools.ui.capture_controller import CaptureController
    cap = Capture(100, 100, bytes(100 * 100 * 4))
    monkeypatch.setattr("easy_tools.ui.capture_overlay._screen_at_cursor", lambda: _Screen())
    monkeypatch.setattr("easy_tools.ui.capture_overlay.monitor_rect_at_cursor", lambda: (0, 0, 100, 100))
    monkeypatch.setattr("easy_tools.ui.capture_overlay.gdi.capture_rect", lambda *args: cap)
    controller = CaptureController(object(), mouse_pressed=lambda: False)
    first = CaptureOverlay(object(), False, False)
    second = CaptureOverlay(object(), False, False)
    first._selection_global = QRect(10, 20, 30, 40)
    second._selection_global = QRect(500, 600, 70, 80)
    received = []
    controller.answer_at.connect(lambda text, answer, source, anchor: received.append((answer, anchor)))
    for overlay in (first, second):
        overlay.answer_ready.connect(controller._answer, Qt.QueuedConnection)
    second.answer_ready.emit("q", "second", "")
    first.answer_ready.emit("q", "first", "")
    _app.processEvents()
    assert received == [("second", QRect(500, 600, 70, 80)), ("first", QRect(10, 20, 30, 40))]
    first.close()
    second.close()
