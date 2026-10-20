"""GUI-thread ownership and input sequencing for screenshot sessions."""
from __future__ import annotations

import time

from PySide6.QtCore import QObject, QRect, Qt, QTimer, Signal, Slot
from shiboken6 import isValid

from ..core.log import get_logger
from ..win32.api import user32
from .capture_overlay import CaptureOverlay

log = get_logger("capture.controller")


class CaptureController(QObject):
    answer_ready = Signal(str, str, str)
    answer_at = Signal(str, str, str, QRect)
    done = Signal(str)

    def __init__(self, service, before_capture=None, parent=None, mouse_pressed=None):
        super().__init__(parent)
        self._service = service
        self._before_capture = before_capture
        self._mouse_pressed = mouse_pressed or (
            lambda: bool(user32.GetAsyncKeyState(0x01) & 0x8000))
        self._sessions: dict[int, CaptureOverlay] = {}
        self._pending = None
        self._timer = QTimer(self)
        self._timer.setInterval(30)
        self._timer.timeout.connect(self._launch_when_released)

    @Slot(bool, bool, bool)
    def start(self, auto_ocr: bool, copy_image: bool, ask_question: bool) -> None:
        if self._pending is not None:
            return
        for overlay in self._sessions.values():
            if isValid(overlay) and overlay._phase == "selecting":
                overlay.raise_()
                overlay.activateWindow()
                return
        if any(isValid(overlay) and overlay._phase == "processing" for overlay in self._sessions.values()):
            self._failure("上一张截图仍在识别/回答中，请稍后再试。")
            return
        if self._before_capture:
            self._before_capture()
        self._pending = (auto_ocr, copy_image, ask_question)
        self._requested_at = time.monotonic()
        # Tiles run on mouse press. Wait for release before presenting another
        # mouse target, otherwise the opening click becomes selection input.
        self._timer.start()

    @Slot()
    def _launch_when_released(self) -> None:
        if self._pending is None:
            self._timer.stop()
            return
        if self._mouse_pressed():
            if time.monotonic() - self._requested_at > 5:
                self._timer.stop()
                self._pending = None
                self._failure("请松开鼠标左键后重新启动截图。")
            return
        self._timer.stop()
        options, self._pending = self._pending, None
        try:
            overlay = CaptureOverlay(self._service, *options)
            key = id(overlay)
            self._sessions[key] = overlay
            overlay.destroyed.connect(lambda *_args, key=key: self._sessions.pop(key, None))
            # QObject slots give Qt an explicit GUI receiver, including when
            # the originating signal is emitted by the OCR worker thread.
            overlay.answer_ready.connect(self._answer, Qt.QueuedConnection)
            overlay.done.connect(self._done, Qt.QueuedConnection)
            overlay.delivery_finished.connect(self._completed, Qt.QueuedConnection)
            overlay.start()
        except Exception as exc:
            log.exception("截图覆盖层启动失败")
            if "overlay" in locals() and isValid(overlay):
                overlay.hide()
                overlay.deleteLater()
            self._failure(f"截图启动失败：{exc}")

    def _failure(self, message):
        self.answer_at.emit("", message, "", QRect())
        self.answer_ready.emit("", message, "")

    @Slot(str, str, str)
    def _answer(self, text: str, answer: str, source: str) -> None:
        overlay = self.sender()
        anchor = QRect(getattr(overlay, "_selection_global", QRect()))
        self.answer_at.emit(text, answer, source, anchor)
        self.answer_ready.emit(text, answer, source)

    @Slot(str)
    def _done(self, message: str) -> None:
        self.done.emit(message)

    @Slot()
    def _completed(self) -> None:
        overlay = self.sender()
        if overlay is not None and isValid(overlay):
            overlay.deleteLater()
