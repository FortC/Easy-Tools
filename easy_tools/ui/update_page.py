"""Settings page for privacy-preserving, incremental gray-rollout updates."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import QTimer, Signal, Slot, Qt
from PySide6.QtWidgets import (QApplication, QFrame, QHBoxLayout, QLabel,
                               QMessageBox, QProgressBar, QPushButton, QVBoxLayout, QWidget)

from easy_tools import __version__
from easy_tools.core.paths import app_root, data_dir, is_frozen, resource_root
from easy_tools.core.update_client import check_update, stage_update


class SoftwareUpdatePage(QWidget):
    progress_changed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._task = None
        self._manager = parent.service.tasks
        self._candidate = None
        self._staged = None
        self._pending_result = None
        self._action = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(26, 22, 26, 22)
        layout.setSpacing(12)
        title = QLabel("软件更新", self)
        title.setStyleSheet("font-size:17px;font-weight:600")
        layout.addWidget(title)
        self.current = QLabel("当前版本：Easy-Tools " + __version__, self)
        layout.addWidget(self.current)
        self.status = QLabel("灰度更新只面向分批放量中、且本机被分配到的安装实例。", self)
        self.status.setWordWrap(True)
        self.status.setStyleSheet("color:#475569")
        self.progress_changed.connect(self.status.setText, Qt.QueuedConnection)
        layout.addWidget(self.status)
        self.progress = QProgressBar(self)
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.hide()
        layout.addWidget(self.progress)
        buttons = QHBoxLayout()
        self.check_button = QPushButton("检查灰度更新", self)
        self.check_button.clicked.connect(self.check)
        self.install_button = QPushButton("下载并安装", self)
        self.install_button.setObjectName("PrimaryButton")
        self.install_button.setEnabled(False)
        self.install_button.clicked.connect(self.install)
        buttons.addWidget(self.check_button)
        buttons.addWidget(self.install_button)
        buttons.addStretch(1)
        layout.addLayout(buttons)
        info = QFrame(self)
        info.setFrameShape(QFrame.StyledPanel)
        info_layout = QVBoxLayout(info)
        info_layout.addWidget(QLabel(
            "灰度分组使用本机生成的随机编号，不上传电脑名或 Windows 账户。"
            "程序文件将在 Easy-Tools 退出后更新；配置和用户数据保留。", info))
        info.layout().itemAt(0).widget().setWordWrap(True)
        layout.addWidget(info)
        layout.addStretch(1)
        if not is_frozen():
            self.check_button.setEnabled(False)
            self.status.setText("开发运行模式不执行安装目录更新，请在已安装版本中使用此功能。")
        else:
            QTimer.singleShot(350, self.check)

    def _start(self, action, manifest=None):
        if self._task:
            return
        self.check_button.setEnabled(False)
        self.install_button.setEnabled(False)
        self.progress.setRange(0, 0 if action == "stage" else 100)
        self.progress.setValue(0)
        self.progress.show()
        self._action = action
        from .task_bridge import submit_task
        progress = self.progress_changed.emit
        self._task = submit_task(self._manager, '检查更新' if action == 'check' else '下载更新',
                                 lambda cancel: check_update(__version__) if action == 'check' else stage_update(manifest, progress, cancel),
                                 self._completed, key='software-update')
        if not self._task:
            self._completed(None, '任务队列已满，请稍后重试')

    def check(self):
        self._candidate = None
        self._staged = None
        self.install_button.setText("下载并安装")
        self.status.setText("正在安全检查更新…")
        self._start("check")

    def install(self):
        if self._staged:
            self._launch_helper()
            return
        if not self._candidate:
            return
        answer = QMessageBox.question(
            self, "安装灰度更新",
            "将下载 Easy-Tools %s 的增量文件。下载完成后，当前程序会退出，由独立更新助手完成替换并重启。\n\n"
            "是否继续？" % self._candidate["manifest"]["version"],
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if answer != QMessageBox.Yes:
            return
        self.status.setText("正在准备灰度版本…")
        self._start("stage", self._candidate["manifest"])

    @Slot(object, str)
    def _completed(self, result, error):
        self._task = None
        self._finished(self._action, result, error)

    def _finished(self, action, result, error):
        self.progress.hide()
        self.check_button.setEnabled(is_frozen())
        if error:
            self.status.setText("更新检查/下载失败，当前版本仍可使用：" + error)
            return
        if action == "check":
            if result["status"] == "current":
                self.status.setText("当前已是最新版本（%s）。" % __version__)
            elif result["status"] == "not_in_cohort":
                self.status.setText("已发现 %s（灰度 %s%%），本机分组为 %.2f%%，暂未进入本轮。" % (
                    result["version"], result["rollout_percent"], result["bucket_percent"]))
            else:
                self._candidate = result
                manifest = result["manifest"]
                total = sum(f["size"] for f in manifest["files"])
                notes = manifest.get("notes") or "暂无更新说明"
                self.status.setText("发现版本 %s · 灰度 %s%% · 本机分组 %.2f%%\n%s\n完整版本 %s MB（将优先复用已有内容块）。" % (
                    manifest["version"], manifest.get("rollout_percent", 100), result["bucket_percent"],
                    notes, total // (1024 * 1024)))
                self.install_button.setEnabled(True)
        else:
            self._staged = result
            self.status.setText("下载和校验完成：本次接收 %s MB / 完整版本 %s MB。正在启动更新助手…" % (
                result["downloaded_bytes"] // (1024 * 1024), result["full_bytes"] // (1024 * 1024)))
            self._launch_helper()

    def _launch_helper(self):
        packaged = resource_root() / "update" / "EasyToolsUpdater.exe"
        if not packaged.is_file():
            self.status.setText("安装包缺少更新助手，请使用正式安装器升级到启用更新功能的版本。")
            self.install_button.setEnabled(False)
            return
        helper = data_dir() / "EasyToolsUpdater.exe"
        temp = helper.with_suffix(".tmp.exe")
        try:
            shutil.copy2(packaged, temp)
            os.replace(str(temp), str(helper))
            creationflags = getattr(subprocess, "DETACHED_PROCESS", 0x00000008) | getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
            subprocess.Popen([str(helper), "--apply", str(os.getpid()), str(app_root()), self._staged["stage"]],
                             cwd=str(app_root()), close_fds=True, creationflags=creationflags)
            app = QApplication.instance()
            if app is not None:
                QTimer.singleShot(0, app.quit)
        except Exception as exc:
            temp.unlink(missing_ok=True)
            self.status.setText("无法启动更新助手：%s。当前程序仍可使用。" % exc)
            self.install_button.setEnabled(True)
