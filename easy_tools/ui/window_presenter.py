"""GUI-only restoration of persistent windows and background tray reload."""
from PySide6.QtCore import QObject, QTimer, Signal, Slot
from PySide6.QtWidgets import QApplication, QMessageBox
from shiboken6 import isValid
from ..core.log import get_logger
from .background import submit

log = get_logger("ui.windows")


def bring_forward(window):
    if not isValid(window):
        return
    if window.isMinimized():
        window.showNormal()
    else:
        window.show()
    window.raise_()
    window.activateWindow()
    from ..win32.window import set_foreground
    set_foreground(int(window.winId()))


class SettingsPresenter(QObject):
    reload_busy = Signal(bool)
    reload_done = Signal(str)

    def __init__(self, factory, store, before_show=None, parent=None):
        super().__init__(parent)
        self._factory, self._store = factory, store
        self._before_show = before_show
        self._window = None
        self._opening = False
        self._reloading = False

    @Slot()
    def show(self):
        if self._opening:
            return
        self._opening = True
        try:
            if self._before_show:
                self._before_show()
            modal = QApplication.activeModalWidget()
            if modal is not None:
                bring_forward(modal)
                return
            if self._window is None or not isValid(self._window):
                self._window = self._factory()
            window = self._window
            window.showNormal() if window.isMinimized() else window.show()
            # A monitor may have been disconnected since the last opening.
            if not any(screen.availableGeometry().intersects(window.frameGeometry())
                       for screen in QApplication.screens()):
                screen = QApplication.primaryScreen()
                if screen:
                    window.move(screen.availableGeometry().center() - window.rect().center())
            from ..core.focus import register_our_window
            register_our_window(int(window.winId()))
            bring_forward(window)
            QTimer.singleShot(0, self._retry_front)
        except Exception:
            log.exception("打开配置中心失败")
            QMessageBox.warning(None, "无法打开配置中心", "配置中心打开失败，请查看日志后重试。")
        finally:
            self._opening = False

    @Slot()
    def _retry_front(self):
        modal = QApplication.activeModalWidget()
        if modal is not None:
            bring_forward(modal)
        elif self._window is not None and isValid(self._window) and self._window.isVisible():
            bring_forward(self._window)

    @Slot()
    def reload_config(self):
        if self._reloading:
            return
        self._reloading = True
        self.reload_busy.emit(True)
        if not submit(self._store.reload, self._reload_finished):
            self._reload_finished(None, "后台任务较多，请稍后重试。")

    @Slot(object, str)
    def _reload_finished(self, changed, error):
        self._reloading = False
        self.reload_busy.emit(False)
        self.reload_done.emit("重载配置失败：" + error if error else "配置已重载。")
        if error:
            QMessageBox.warning(None, "配置重载失败", error)
