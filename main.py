#!/usr/bin/env python
"""Easy-Tools 入口：托盘常驻 + 中键面板 + 配置中心。

作为一个"正常的 exe"必须做到的四件事，本文件各自都有明确实现：

1. **单实例**。重复双击不会开出一堆托盘图标和一堆全局钩子
   （多个实例抢同一个鼠标中键，用户会以为程序抽风）。第二次启动会
   通过本地 socket 通知已有实例弹出面板，然后自己退出。
2. **无控制台窗口**。打包时 console=False，托盘图标用 app.ico。
3. **可关闭**。托盘菜单「退出」会停钩子、停 OCR 线程、释放本地 socket
   后退出；配置窗口关掉不等于退出程序（这是托盘程序的预期行为）。
4. **线程安全**。钩子线程要开面板时只能走 PanelBridge 的 Signal，
   由 Qt.QueuedConnection 搬回 GUI 线程——这是 Qt 跨线程唯一正确的姿势。

线程布局：
  GUI 线程   QApplication 事件循环，绝不做阻塞操作
  钩子线程   WH_MOUSE_LL / WH_KEYBOARD_LL
  后台线程   OCR 防抖刷新、前台窗口轮询、动作执行、事件链
"""

from __future__ import annotations

import sys
import time
from easy_tools.core.runtime import preload_msvc

preload_msvc()

# The PyInstaller executable can relaunch itself in a separate Python-node
# worker before loading Qt or starting the single-instance desktop process.
if "--run-python-node" in sys.argv:
    from easy_tools.core.python_node_worker import run_worker
    raise SystemExit(run_worker())
if '--run-processor' in sys.argv:
    from easy_tools.core.processor_worker import main as processor_main
    raise SystemExit(processor_main())
if '--selftest-upgrade' in sys.argv:
    sys.argv.remove('--selftest-upgrade')
    from easy_tools.diagnostics.upgrade_workflow import main as upgrade_main
    raise SystemExit(upgrade_main())

from PySide6.QtCore import QTimer, Qt, QEvent
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import (QApplication, QInputDialog, QMenu, QMessageBox,
                               QSystemTrayIcon)

from easy_tools import __version__
from easy_tools.core.app import AppService
from easy_tools.core.log import configure, get_logger
from easy_tools.core.privacy import notifications_allowed
from easy_tools.core.paths import log_path
from easy_tools.ui.capture_controller import CaptureController
from easy_tools.ui.capture_privacy import CapturePrivacyFilter
from easy_tools.ui.chain_editor import ChainEditorDialog          # noqa: F401  预热导入
from easy_tools.ui.icon import app_icon
from easy_tools.ui.panel import ActionPanel, OutsideClickFilter, PanelBridge
from easy_tools.ui.settings import SettingsWindow

log = get_logger("main")

SERVER_NAME = "Easy-Tools-single-instance-v1"


class SingleInstance:
    """基于本地命名管道的单实例守卫。

    第二次启动时不会静默失败——它会把「请把面板显示出来」这条消息
    发给第一个实例再退出。用户双击图标期望的是「面板出来」，
    而不是「什么也没发生」。
    """

    def __init__(self, name: str = SERVER_NAME) -> None:
        self.name = name
        self.server: QLocalServer | None = None

    def try_acquire(self) -> bool:
        probe = QLocalSocket()
        probe.connectToServer(self.name)
        if probe.waitForConnected(300):
            probe.write(b"show")
            probe.flush()
            probe.waitForBytesWritten(300)
            probe.disconnectFromServer()
            return False                      # 已有实例在跑
        # 上一次异常退出可能留下悬空管道，清掉再listen
        QLocalServer.removeServer(self.name)
        self.server = QLocalServer()
        self.server.setSocketOptions(QLocalServer.UserAccessOption)
        if not self.server.listen(self.name):
            log.warning("本地服务监听失败（%s），本实例将不启用单实例保护",
                        self.server.errorString())
            self.server = None
            return True
        return True

    def on_message(self, callback) -> None:
        if self.server is None:
            return

        def poll() -> None:
            while self.server.hasPendingConnections():
                conn = self.server.nextPendingConnection()
                conn.readyRead.connect(lambda c=conn: (c.readAll(), callback()))
                conn.disconnected.connect(c.deleteLater)

        self.timer = QTimer(self.server)
        self.timer.timeout.connect(poll)
        self.timer.start(400)

    def release(self) -> None:
        if self.server is not None:
            self.server.close()
            QLocalServer.removeServer(self.name)
            self.server = None


def main() -> int:
    if "--selftest-capture" in sys.argv:
        import argparse
        from easy_tools.diagnostics.capture_workflow import run
        parser = argparse.ArgumentParser()
        parser.add_argument("--selftest-capture", action="store_true")
        parser.add_argument("--report", required=True)
        parser.add_argument("--skip-real-ocr", action="store_true")
        options = parser.parse_args()
        return run(options.report, real_ocr=not options.skip_real_ocr)

    configure()
    log.info("Easy-Tools %s 启动，日志：%s", __version__, log_path())

    # 启动各步耗时打点：偶发"卡死在后台"这类问题没有时间线就无从定位
    _t0 = time.monotonic()

    def _mark(name: str) -> None:
        nonlocal _t0
        now = time.monotonic()
        log.info("启动[%s] %dms", name, int((now - _t0) * 1000))
        _t0 = now

    _mark("日志初始化")
    guard = SingleInstance()
    if not guard.try_acquire():
        log.info("检测到已有实例在运行，已通知其显示面板，本次启动退出")
        return 0

    app = QApplication(sys.argv)
    from easy_tools.ui.theme import apply_application_theme
    apply_application_theme(app)
    app.setApplicationName("Easy-Tools")
    app.setApplicationDisplayName("Easy-Tools")
    app.setApplicationVersion(__version__)
    app.setQuitOnLastWindowClosed(False)   # 关掉设置窗口 ≠ 退出程序
    app.setWindowIcon(app_icon())

    service = AppService.instance()
    service.store.reload()
    capture_privacy = CapturePrivacyFilter(app, service.store)

    panel = ActionPanel(service)
    bridge = PanelBridge(panel)
    app.installEventFilter(OutsideClickFilter(panel))

    state = {"qa_dialogs": []}
    from easy_tools.ui.reminders_page import ReminderInbox
    reminder_inbox = ReminderInbox(app)
    from easy_tools.ui.notifications import ProtectedNotice
    protected_notice = ProtectedNotice(app)
    from easy_tools.ui.window_presenter import SettingsPresenter
    settings_presenter = SettingsPresenter(lambda: SettingsWindow(service), service.store,
                                          before_show=panel.close_panel, parent=app)
    bridge.request_settings.connect(settings_presenter.show, Qt.QueuedConnection)

    def show_settings() -> None:
        # Actions run on workers; only the QObject receiver touches widgets.
        bridge.request_settings.emit()

    panel.settings_requested.connect(show_settings)

    def show_panel(x: int = -1, y: int = -1) -> None:
        if x < 0:
            from easy_tools.win32 import window as w32
            x, y = w32.cursor_pos()
        bridge.open_at(x, y)

    def toggle_panel(x: int = -1, y: int = -1) -> None:
        # 中键切换。严禁在这里直接摸 panel（本函数运行在钩子线程，
        # isVisible/hide 都是 GUI 线程专属；违反的症状是时灵时不灵）。
        if x < 0:
            from easy_tools.win32 import window as w32
            x, y = w32.cursor_pos()
        bridge.toggle_at(x, y)

    def outside_click(x: int, y: int) -> None:
        # 钩子线程 → 队列信号 → GUI 线程判断关闭
        bridge.request_close.emit(int(x), int(y))

    def notify(level: str, message: str) -> None:
        # 任意线程可调（动作在后台线程执行）；GUI 部分一律经桥搬回 GUI 线程。
        # 直接从工作线程调 tray.showMessage 是跨线程违规——"卡死在后台"
        # 这类偶发问题的嫌疑来源之一，就在这类细节里。
        log.info("[%s] 应用操作提示", level)
        notification_config = service.store.get()
        if not notification_config.get('trigger', {}).get('notify_enabled', True):
            return
        if not notifications_allowed(notification_config):
            if level in ('warning', 'error'):
                protected_notice.requested.emit(level, message)
            return
        bridge.notify(level, message)

    _mark("服务初始化")

    # 截图与通知：动作线程只发信号，一切 UI 都在 GUI 线程创建
    def start_capture(auto_ocr: bool, copy_image: bool, ask_question: bool = True) -> None:
        bridge.request_capture.emit(bool(auto_ocr), bool(copy_image), bool(ask_question))

    def show_question_answer(ocr_text: str, answer: str, sources: str, anchor=None) -> None:
        from easy_tools.ui.question_answer_dialog import QuestionAnswerDialog
        dialog = QuestionAnswerDialog(ocr_text, answer, sources)
        dialog.setAttribute(Qt.WA_DeleteOnClose)
        state["qa_dialogs"].append(dialog)
        dialog.destroyed.connect(lambda *_: state["qa_dialogs"].remove(dialog)
                                 if dialog in state["qa_dialogs"] else None)
        # Keep the answer card non-activating and below the always-on-top
        # action panel, so a middle-button press can reopen and use the panel.
        dialog.setAttribute(Qt.WA_ShowWithoutActivating, True)
        dialog.position_near_selection(anchor)
        dialog.show()
        return dialog

    def start_model_question(prompt: str, is_ocr: bool) -> None:
        if state.get("model_busy"):
            from shiboken6 import isValid
            pending = state.get("model_pending")
            if pending is not None and isValid(pending):
                pending.show()
                pending.raise_()
            else:
                pending = show_question_answer("", "上一条提问仍在处理中，请稍后再试。", "处理中，动作面板仍可使用。")
                pending.copy_button.setEnabled(False)
                state["model_pending"] = pending
            return
        if not service.store.section("llm", {}).get("enabled", True):
            notify("warning", "请先在配置中心启用本机模型回答")
            return
        if not prompt.strip():
            prompt, accepted = QInputDialog.getMultiLineText(
                panel, "AI 提问", "输入问题：")
            if not accepted or not prompt.strip():
                return

        state["model_busy"] = True
        pending = show_question_answer(prompt, "正在检索题库 / 等待模型回答…", "处理中，动作面板仍可使用。")
        pending.copy_button.setEnabled(False)
        state["model_pending"] = pending

        def worker() -> None:
            try:
                matches = service.question_answer.retrieve(prompt)
                details = "\n".join(
                    f"{m['source']} · {m['row']} · 答案列：{m.get('answer_column') or '答案'}\n题目：{m['question']}"
                    for m in matches)
                llm_cfg = service.store.section("llm", {})
                if matches and llm_cfg.get("prefer_bank_answer", True):
                    bridge.model_answer_ready.emit(
                        prompt, matches[0]["answer"], "直接返回题库原答案。\n" + details)
                    return
                try:
                    answer = service.answer_question(prompt, matches)
                except Exception as exc:
                    if matches:
                        answer = matches[0]["answer"]
                        details = ("模型不可用，已回退题库原答案。\n" + details).strip()
                    else:
                        answer = str(exc)
                bridge.model_answer_ready.emit(prompt, answer, details)
            except Exception as exc:
                bridge.model_answer_ready.emit(prompt, "提问失败：" + str(exc), "")

        import threading
        threading.Thread(target=worker, name="model-question", daemon=True).start()

    def finish_model_question(text, answer, sources):
        from shiboken6 import isValid
        pending = state.pop("model_pending", None)
        if pending is not None and isValid(pending):
            pending.close()
        state["model_busy"] = False
        show_question_answer(text, answer, sources)

    capture_controller = CaptureController(service, before_capture=panel.close_panel, parent=app)
    bridge.request_capture.connect(capture_controller.start, Qt.QueuedConnection)
    capture_controller.answer_at.connect(show_question_answer)
    capture_controller.done.connect(lambda message: notify("info", message))
    bridge.request_model_question.connect(start_model_question, Qt.QueuedConnection)
    bridge.model_answer_ready.connect(finish_model_question, Qt.QueuedConnection)

    service.bind_ui(open_settings=show_settings, show_panel=show_panel,
                    toggle_panel=toggle_panel, outside_click=outside_click, notify=notify,
                    start_capture=start_capture,
                    remind=reminder_inbox.requested.emit,
                    ask_question=lambda text, is_ocr:
                        bridge.request_model_question.emit(str(text), bool(is_ocr)))

    def on_action(action_id: str) -> None:
        # 注意：这里**不能**再 focus.remember()——点击动作时面板已经抢到焦点，
        # 此刻记住的目标就是面板自己。真正要记住目标窗口的时机是面板弹出前，
        # 见 ActionPanel.open_at()。
        result = service.run_action(action_id)
        if not result.ok:
            notify("warning", result.message)

    panel.activated.connect(on_action)

    status = service.start()
    log.info("服务启动状态：%s", status)
    _mark("钩子/服务启动")

    tray = QSystemTrayIcon(app_icon(), app)
    tray.setToolTip("Easy-Tools %s" % __version__)

    def _toast(level: str, message: str) -> None:
        # Preferences can change after a worker queues this message.
        if not notifications_allowed(service.store.get()):
            return
        icon = {"warning": QSystemTrayIcon.Warning,
                "error": QSystemTrayIcon.Critical}.get(level, QSystemTrayIcon.Information)
        tray.showMessage("Easy-Tools", str(message), icon, 3000)

    bridge.request_notify.connect(_toast, Qt.QueuedConnection)

    menu = QMenu()
    a_panel = menu.addAction("显示动作面板")
    a_panel.triggered.connect(lambda: show_panel())
    a_settings = menu.addAction("配置中心")
    a_settings.triggered.connect(show_settings)
    a_update = menu.addAction("检查灰度更新")
    def open_update_page() -> None:
        settings_presenter.show()
        def select_update_page() -> None:
            window = settings_presenter._window
            if window is not None:
                window.tabs.setCurrentIndex(0)
                window.update_page.check()
        QTimer.singleShot(0, select_update_page)
    a_update.triggered.connect(open_update_page)
    a_reload = menu.addAction("重载配置")
    a_reload.triggered.connect(settings_presenter.reload_config)
    settings_presenter.reload_busy.connect(a_reload.setDisabled)
    settings_presenter.reload_done.connect(lambda message: notify("info", message))
    menu.addSeparator()
    a_quit = menu.addAction("退出 Easy-Tools")
    a_quit.triggered.connect(app.quit)
    menu.addSeparator()
    a_about = menu.addAction("关于")
    a_about.triggered.connect(
        lambda: QMessageBox.about(None, "关于 Easy-Tools",
                                  "Easy-Tools %s\n\n全局动作面板 · 托盘常驻\n"
                                  "作者：clb <lamthebest@foxmail.com>\n"
                                  "问题反馈：https://github.com/FortC/Easy-Tools/issues\n"
                                  "日志：%s" % (__version__, log_path())))
    tray.setContextMenu(menu)
    tray.activated.connect(
        lambda reason: show_panel() if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick)
        else None)
    tray.show()
    _mark("托盘就绪")

    if not QSystemTrayIcon.isSystemTrayAvailable():
        log.warning("系统托盘不可用：面板仍可由热键唤起，但没有常驻入口")

    def on_second_instance() -> None:
        log.info("收到第二次启动请求，弹出面板")
        show_panel()

    guard.on_message(on_second_instance)

    def shutdown() -> None:
        log.info("正在退出：停止钩子与后台线程")
        service.stop()
        tray.hide()
        capture_privacy.close()
        # Dispose Python-overridden widgets before QApplication / interpreter teardown.
        for window in app.topLevelWidgets():
            window.close()
            window.deleteLater()
        app.sendPostedEvents(None, QEvent.DeferredDelete)
        guard.release()

    app.aboutToQuit.connect(shutdown)
    QTimer.singleShot(1200, lambda: tray.setToolTip(
        "Easy-Tools %s · %s" % (__version__, service.status_text())))
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
