"""Apply Windows capture exclusion to Easy-Tools top-level windows."""

from __future__ import annotations

import ctypes
import sys
import weakref
from ctypes import wintypes

from PySide6.QtCore import QEvent, QObject, QTimer, Signal, Slot
from PySide6.QtWidgets import QApplication, QWidget, QLabel
from PySide6.QtCore import Qt
from shiboken6 import isValid

from ..core.log import get_logger

log = get_logger("ui.capture_privacy")

WDA_NONE = 0x00000000
WDA_EXCLUDEFROMCAPTURE = 0x00000011


def set_window_capture_excluded(hwnd: int, enabled: bool) -> bool:
    """Set OS-level capture affinity on a top-level window owned by this process."""
    if sys.platform != "win32":
        return False
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    setter = user32.SetWindowDisplayAffinity
    setter.argtypes = (wintypes.HWND, wintypes.DWORD)
    setter.restype = wintypes.BOOL
    return bool(setter(wintypes.HWND(hwnd),
                       WDA_EXCLUDEFROMCAPTURE if enabled else WDA_NONE))


class CapturePrivacyFilter(QObject):
    """Protect existing windows and any top-level windows shown later."""

    refresh_requested = Signal()

    def __init__(self, app: QApplication, store, setter=None) -> None:
        super().__init__(app)
        self._app = app
        self._store = store
        self._setter = setter or set_window_capture_excluded
        self._enabled = bool(store.section("privacy", {}).get(
            "exclude_from_capture", True))
        self._scheduled = set()
        self._covers = weakref.WeakKeyDictionary()
        self.refresh_requested.connect(self.apply_all)
        self._unsubscribe = store.subscribe(self._on_config_changed)
        app.installEventFilter(self)
        self.apply_all()

    def _on_config_changed(self, _config: dict) -> None:
        # Config watcher callbacks may run off the GUI thread.
        self.refresh_requested.emit()

    def eventFilter(self, obj, event) -> bool:
        if event.type() == QEvent.Resize and isinstance(obj, QWidget) and obj in self._covers:
            cover = self._covers[obj]
            if isValid(cover):
                cover.setGeometry(obj.rect())
        if (event.type() in (QEvent.Show, QEvent.WinIdChange)
                and isinstance(obj, QWidget) and isValid(obj) and obj.isWindow()):
            # Apply before the first painted frame when the HWND is ready;
            # retain the deferred retry for windows created during Show.
            self._apply_widget(obj)
            self._schedule_widget(obj)
        return False

    @Slot()
    def apply_all(self) -> None:
        self._enabled = bool(self._store.section("privacy", {}).get(
            "exclude_from_capture", True))
        for widget in self._app.topLevelWidgets():
            if widget.isVisible():
                self._schedule_widget(widget)

    def _schedule_widget(self, widget: QWidget) -> None:
        key = id(widget)
        if key in self._scheduled:
            return
        self._scheduled.add(key)
        ref = weakref.ref(widget)

        def apply_later() -> None:
            self._scheduled.discard(key)
            target = ref()
            if target is not None and isValid(target):
                self._apply_widget(target)

        # Show/WinIdChange can arrive before Windows creates a usable HWND.
        QTimer.singleShot(0, apply_later)

    def _apply_widget(self, widget: QWidget) -> None:
        try:
            if (sys.platform != "win32" or not isValid(widget)
                    or not widget.isWindow() or not widget.isVisible()):
                return
            hwnd = int(widget.winId())
            if not hwnd:
                return
            protected = self._setter(hwnd, self._enabled)
            widget.setProperty('captureProtectionApplied', bool(protected and self._enabled))
            self._cover(widget, self._enabled and not protected)
            if not protected:
                log.warning("窗口捕获隐私属性应用失败（hwnd=%s enabled=%s，错误码=%s）",
                            hwnd, self._enabled, ctypes.get_last_error())
        except Exception:  # noqa: BLE001 - privacy failure must not crash the app
            if isValid(widget):
                self._cover(widget, self._enabled)
            log.exception("窗口捕获隐私属性应用异常")

    def _cover(self, widget, visible):
        cover = self._covers.get(widget)
        if not visible:
            if cover is not None and isValid(cover):
                cover.hide()
            return
        if cover is None or not isValid(cover):
            cover = QLabel('当前窗口隐私保护未成功\n已遮住内容；请关闭窗口后重试，或在支持的 Windows 环境运行。', widget)
            cover.setAlignment(Qt.AlignCenter)
            cover.setWordWrap(True)
            cover.setStyleSheet('background:#f7f9fc;color:#1a212c;padding:24px;')
            cover.setFocusPolicy(Qt.StrongFocus)
            self._covers[widget] = cover
        cover.setGeometry(widget.rect())
        cover.show()
        cover.raise_()
        cover.setFocus()

    def close(self) -> None:
        self._app.removeEventFilter(self)
        if self._unsubscribe:
            self._unsubscribe()
            self._unsubscribe = None
        for cover in list(self._covers.values()):
            if isValid(cover):
                cover.hide()
                cover.deleteLater()
        self._covers.clear()
