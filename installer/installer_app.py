r"""Easy-Tools 安装器（自研 GUI 安装包）。

为什么不用 Inno Setup / NSIS：本机两者都没装，且离线环境无法获取；
IExpress 自带但界面与静默安装体验都很差、也无法定制卸载流程。所以用
PyInstaller 再打一个带界面的小程序，把 dist/Easy-Tools 作为载荷一起
封进单个 exe，运行时释放到用户目录并完成注册与快捷方式。

安装位置刻意选 %LOCALAPPDATA%\Programs（每用户），避免要管理员权限——
全局钩子程序装到 Program Files 会被 UAC 虚拟化，钩子与注入会失效。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
import winreg
import threading
import tempfile
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QThread, QTimer, Signal, Slot
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QFileDialog,
                               QFrame, QHBoxLayout, QLabel, QLineEdit,
                               QMessageBox, QProgressBar, QPushButton,
                               QVBoxLayout, QWidget)
from PySide6.QtGui import QIcon

# 源码里到处拼 Windows 路径，统一用 chr(92) 表示反斜杠：
# 直接写 "\" 会被多层字符串/模板转义吃掉，改成拼接反而更难读也更易错。
BS = chr(92)

APP_NAME = "Easy-Tools"
VERSION = "0.2.1"
PUBLISHER = "Easy-Tools"
UNINSTALL_KEY = "EasyTools"
REG_ROOT = ("HKCU" + chr(92) + "Software" + chr(92) + "Microsoft" + chr(92)
           + "Windows" + chr(92) + "CurrentVersion" + chr(92) + "Uninstall"
           + chr(92) + UNINSTALL_KEY)


def resource_dir() -> Path:
    """载荷目录：打包后 _MEIPASS，开发态 dist/Easy-Tools。"""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent)) / "payload"
    return Path(__file__).resolve().parents[1] / "dist" / APP_NAME


def app_icon_path() -> Path:
    bundled = resource_dir() / "app.ico"
    return bundled if bundled.is_file() else Path(__file__).resolve().parents[1] / "app.ico"


def install_dir() -> Path:
    return Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "Programs" / APP_NAME


def start_menu_dir() -> Path:
    base = os.environ.get("APPDATA", str(Path.home()))
    return Path(base) / "Microsoft" / "Windows" / "Start Menu" / "Programs"


def desktop_dir() -> Path:
    import ctypes
    folder = ctypes.create_unicode_buffer(32768)
    if ctypes.windll.shell32.SHGetFolderPathW(None, 0x10, None, 0, folder) == 0:
        return Path(folder.value)
    return Path(os.environ.get("USERPROFILE", str(Path.home()))) / "Desktop"


MANIFEST = ".easy-tools-uninstall.txt"
MANIFEST_HEADER = "Easy-Tools uninstall manifest v1"
UNINSTALL_SUBKEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\EasyTools"
NO_CONSOLE = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def uninstaller_source() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS) / "uninstall" / "Uninstall.exe"
    return Path(__file__).parent / "bin" / "Uninstall.exe"


def existing_install() -> dict | None:
    """Recognize both registered and legacy default directories, without scanning disks."""
    candidates = []
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, UNINSTALL_SUBKEY, 0,
                            winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as key:
            path = Path(winreg.QueryValueEx(key, "InstallLocation")[0])
            try:
                version = str(winreg.QueryValueEx(key, "DisplayVersion")[0])
            except OSError:
                version = "旧版本"
            if (path / (APP_NAME + ".exe")).is_file() or (path / MANIFEST).is_file():
                candidates.append({"path": path, "version": version})
    except (OSError, ValueError, TypeError):
        pass
    default = install_dir()
    if (default / (APP_NAME + ".exe")).is_file() and not any(
            item["path"].absolute() == default.absolute() for item in candidates):
        candidates.append({"path": default, "version": "旧版本（常规安装目录）"})
    if not candidates:
        return None
    preferred = candidates[0]
    temporary = Path(tempfile.gettempdir()).resolve()
    if preferred["path"].resolve().is_relative_to(temporary):
        preferred = next((item for item in candidates if not item["path"].resolve().is_relative_to(temporary)), preferred)
    return {**preferred, "candidates": candidates}


def validate_target(target: Path) -> Path:
    target = target.expanduser().absolute()
    resolved = target.resolve()
    forbidden = [Path(target.anchor), Path.home(), Path(os.environ.get("WINDIR", "C:/Windows")),
                 desktop_dir(), Path(os.environ.get("APPDATA", str(Path.home()))),
                 Path(os.environ.get("LOCALAPPDATA", str(Path.home())))]
    for env_name in ("ProgramFiles", "ProgramFiles(x86)", "ProgramData"):
        if os.environ.get(env_name):
            forbidden.append(Path(os.environ[env_name]))
    if any(resolved == path.resolve() for path in forbidden):
        raise ValueError("请选择 Easy-Tools 专用子目录，不能安装到磁盘根目录或系统/用户文件夹根目录。")
    if target != resolved:
        raise ValueError("安装目录包含目录链接，请选择实际安装目录。")
    if resolved.exists() and any(resolved.iterdir()) and not (
            (resolved / (APP_NAME + ".exe")).is_file() or (resolved / MANIFEST).is_file()):
        raise ValueError("该目录已有其他文件。请选择空目录或已有 Easy-Tools 的安装目录。")
    return resolved


def owned_file(target: Path, relative: str) -> Path:
    relative_path = Path(relative)
    if relative_path.is_absolute() or ":" in relative or ".." in relative_path.parts:
        raise ValueError("安装清单包含越界路径。")
    path = target / relative_path
    if not path.resolve().is_relative_to(target) or path.resolve() == target:
        raise ValueError("安装清单路径超出安装目录。")
    return path


def make_shortcut(target_exe: str, link: Path, desc: str) -> bool:
    """用 WScript.Shell 建快捷方式（不依赖 pywin32）。"""
    link.parent.mkdir(parents=True, exist_ok=True)
    ps = (
        "$s = New-Object -ComObject WScript.Shell; "
        "$l = $s.CreateShortcut('" + str(link).replace("'", "''") + "'); "
        "$l.TargetPath = '" + target_exe.replace("'", "''") + "'; "
        "$l.WorkingDirectory = '" + str(Path(target_exe).parent).replace("'", "''") + "'; "
        "$l.Description = '" + desc + "'; "
        "$l.Save()"
    )
    try:
        subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden", "-Command", ps],
                       check=True, capture_output=True, timeout=30, creationflags=NO_CONSOLE)
        return link.exists()
    except Exception:
        return False


def register_uninstall(target: Path) -> None:
    """Register the GUI uninstaller directly, without spawning a shell."""
    values = {"DisplayName": APP_NAME, "DisplayVersion": VERSION, "Publisher": PUBLISHER,
              "InstallLocation": str(target), "DisplayIcon": str(target / (APP_NAME + ".exe")),
              "UninstallString": '"' + str(target / "Uninstall.exe") + '"',
              "QuietUninstallString": '"' + str(target / "Uninstall.exe") + '" --quiet'}
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, UNINSTALL_SUBKEY, 0,
                           winreg.KEY_WRITE | winreg.KEY_WOW64_64KEY) as key:
        for name, value in values.items():
            winreg.SetValueEx(key, name, 0, winreg.REG_SZ, value)
        for name in ("NoModify", "NoRepair"):
            winreg.SetValueEx(key, name, 0, winreg.REG_DWORD, 1)


def kill_running(target: Path) -> None:
    result = subprocess.run([str(uninstaller_source()), "--stop-app", str(target)],
                            check=False, capture_output=True, timeout=15,
                            creationflags=NO_CONSOLE)
    if result.returncode:
        raise RuntimeError("无法关闭旧版本进程，请退出该目录中的 Easy-Tools 后重新安装。")


def perform_install(src: Path, dst: Path, shortcuts: bool, cb=None):
    """把载荷装到 dst。cb(百分比, 文案) 可为 None（静默模式）。

    GUI 线程与 --silent 静默模式共用这一份，避免"测的和发的不是一个逻辑"。
    """
    def say(v, t):
        if cb:
            cb(v, t)

    dst = validate_target(dst)
    src = src.resolve()
    if src == dst or src.is_relative_to(dst) or dst.is_relative_to(src):
        raise ValueError("安装目录不能与安装载荷目录重叠。")
    if not uninstaller_source().is_file():
        raise RuntimeError("安装包缺少无控制台卸载组件，请重新下载安装包。")
    upgrading = (dst / (APP_NAME + ".exe")).exists()
    say(0, "检测到旧版本，正在准备覆盖升级…" if upgrading else "正在准备安装…")
    kill_running(dst)
    time.sleep(0.2)
    files = [p for p in src.rglob("*") if p.is_file()]
    # Updates must remove DLLs left by an older payload. Overlay-copying alone
    # leaves those files in _internal; Windows may then load an obsolete Qt/CRT
    # dependency before the current PySide6 copy and fail importing QtCore.
    expected_internal = {
        p.relative_to(src).as_posix()
        for p in files
        if p.is_relative_to(src / "_internal")
    }
    manifest_path = dst / MANIFEST
    if manifest_path.is_file():
        lines = manifest_path.read_text(encoding="utf-8-sig").splitlines()
        if len(lines) < 3 or lines[0] != MANIFEST_HEADER or Path(lines[1]).resolve() != dst:
            raise ValueError("旧版本安装清单无效，已停止覆盖以保护文件。")
        expected = {p.relative_to(src).as_posix().casefold() for p in files}
        expected.add("uninstall.exe")
        for relative in lines[2:]:
            old_file = owned_file(dst, relative)
            if old_file.relative_to(dst).as_posix().casefold() not in expected and old_file.is_file():
                old_file.unlink()
    old_internal = dst / "_internal"
    if old_internal.is_dir():
        for old_file in old_internal.rglob("*"):
            if old_file.is_file() and old_file.relative_to(dst).as_posix() not in expected_internal:
                owned_file(dst, str(old_file.relative_to(dst))).unlink()
        for old_dir in sorted(
                (p for p in old_internal.rglob("*") if p.is_dir()),
                key=lambda p: len(p.parts), reverse=True):
            try:
                owned_file(dst, str(old_dir.relative_to(dst))).rmdir()
            except OSError:
                pass
    total = max(1, len(files))
    say(0, "正在复制文件…")
    dst.mkdir(parents=True, exist_ok=True)
    done = 0
    last_percent = -1
    for f in files:
        rel = f.relative_to(src)
        out = owned_file(dst, str(rel))
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(f, out)
        done += 1
        percent = int(done * 95 / total)
        if percent != last_percent:
            say(percent, "正在复制 " + str(rel))
            last_percent = percent
    exe = dst / (APP_NAME + ".exe")
    shutil.copy2(uninstaller_source(), dst / "Uninstall.exe")
    # Remove the old recursive CMD entry points during an upgrade.
    for filename in ("卸载 " + APP_NAME + ".cmd", "uninstall_inner.cmd"):
        legacy = owned_file(dst, filename)
        if legacy.is_file():
            legacy.unlink()
    manifest_files = [str(p.relative_to(src)) for p in files] + ["Uninstall.exe"]
    manifest_temporary = dst / (MANIFEST + ".tmp")
    manifest_temporary.write_text("\n".join([MANIFEST_HEADER, str(dst), *manifest_files]) + "\n", encoding="utf-8")
    os.replace(manifest_temporary, manifest_path)
    say(96, "正在创建快捷方式…")
    if shortcuts:
        make_shortcut(str(exe), start_menu_dir() / (APP_NAME + ".lnk"),
                      APP_NAME + " 动作面板")
        make_shortcut(str(exe), desktop_dir() / (APP_NAME + ".lnk"),
                      APP_NAME + " 动作面板")
    register_uninstall(dst)
    say(100, "安装完成")
    return exe, exe.exists()


class Installer(QThread):
    """后台复制载荷，进度回主线程。"""

    progress = Signal(int, str)
    finished_ok = Signal(str, bool)

    def __init__(self, src: Path, dst: Path, shortcuts: bool) -> None:
        super().__init__()
        self.src = src
        self.dst = dst
        self.shortcuts = shortcuts

    def run(self) -> None:                      # noqa: D401
        try:
            exe, ok = perform_install(
                self.src, self.dst, self.shortcuts,
                lambda v, t: self.progress.emit(v, t))
            self.finished_ok.emit(str(exe), ok)
        except Exception as e:                  # noqa: BLE001
            self.finished_ok.emit("安装失败：" + str(e), False)


class ExistingInstallProbe(QObject):
    ready = Signal(object)


class Window(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(APP_NAME + " 安装程序")
        icon_path = app_icon_path()
        if icon_path.is_file():
            self.setWindowIcon(QIcon(str(icon_path)))
        self.setStyleSheet("""
            QWidget { background: #f3f6fb; color: #182230; font-size: 13px; }
            QFrame#Card { background: #ffffff; border: 1px solid #dce4ef; border-radius: 14px; }
            QLabel#Title { color: #142239; font-size: 22px; font-weight: 700; }
            QLabel#Subtitle { color: #5d6c80; font-size: 13px; }
            QLabel#SectionTitle { color: #24344a; font-size: 13px; font-weight: 700; }
            QLabel#Status { color: #43536a; }
            QLabel#Version { background: #eaf2ff; color: #1458b5; border-radius: 9px; padding: 5px 10px; font-weight: 700; }
            QLineEdit { background: #ffffff; color: #172033; border: 1px solid #aebbd0; border-radius: 7px; padding: 9px 10px; selection-background-color: #286bd6; }
            QLineEdit:focus { border: 2px solid #286bd6; padding: 8px 9px; }
            QComboBox { background: #ffffff; color: #172033; border: 1px solid #aebbd0; border-radius: 7px; padding: 8px 10px; }
            QPushButton { background: #ffffff; color: #203047; border: 1px solid #aebbd0; border-radius: 7px; padding: 9px 15px; font-weight: 600; }
            QPushButton:hover { background: #edf4ff; border-color: #286bd6; }
            QPushButton:focus { border: 2px solid #1458b5; }
            QPushButton#Primary { background: #2167d5; color: #ffffff; border-color: #2167d5; padding: 10px 22px; }
            QPushButton#Primary:hover { background: #1557bd; border-color: #1557bd; }
            QPushButton#Primary:disabled { background: #a9bddb; color: #ffffff; border-color: #a9bddb; }
            QPushButton:disabled { background: #edf0f5; color: #718096; border-color: #d5dce6; }
            QCheckBox { color: #26364c; spacing: 9px; }
            QCheckBox::indicator { width: 17px; height: 17px; }
            QProgressBar { border: 0; border-radius: 5px; color: #ffffff; text-align: center; background: #e6ebf3; min-height: 10px; max-height: 10px; }
            QProgressBar::chunk { background: #286bd6; border-radius: 5px; }
        """)
        self.setFixedSize(700, 500)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 20, 22, 20)
        lay.setSpacing(14)

        card = QFrame()
        card.setObjectName("Card")
        content = QVBoxLayout(card)
        content.setContentsMargins(22, 20, 22, 18)
        content.setSpacing(15)
        lay.addWidget(card, 1)

        head_row = QHBoxLayout()
        head_row.setSpacing(14)
        logo = QLabel()
        logo.setFixedSize(58, 58)
        logo.setAlignment(Qt.AlignCenter)
        if icon_path.is_file():
            logo.setPixmap(QIcon(str(icon_path)).pixmap(56, 56))
        else:
            logo.setPixmap(QApplication.windowIcon().pixmap(56, 56))
        head_row.addWidget(logo)
        title_box = QVBoxLayout()
        title = QLabel(APP_NAME)
        title.setObjectName("Title")
        subtitle = QLabel("轻松呼出常用动作 · 安全保留本机配置")
        subtitle.setObjectName("Subtitle")
        title_box.addWidget(title)
        title_box.addWidget(subtitle)
        head_row.addLayout(title_box, 1)
        version = QLabel("版本 " + VERSION)
        version.setObjectName("Version")
        version.setAlignment(Qt.AlignCenter)
        head_row.addWidget(version, 0, Qt.AlignTop)
        content.addLayout(head_row)

        divider = QFrame()
        divider.setFrameShape(QFrame.HLine)
        divider.setStyleSheet("color: #e7ecf3; background: #e7ecf3; max-height: 1px;")
        content.addWidget(divider)

        location_title = QLabel("安装位置")
        location_title.setObjectName("SectionTitle")
        content.addWidget(location_title)
        self.path_edit = QLineEdit(str(install_dir()))
        self.path_edit.setPlaceholderText("选择 Easy-Tools 专用安装目录")
        self.path_edit.setToolTip("已有版本会自动选择原安装目录；配置和知识库保存在用户数据目录，不受覆盖升级影响。")
        path_row = QHBoxLayout()
        path_row.setSpacing(8)
        path_row.addWidget(self.path_edit, 1)
        self.browse_btn = QPushButton("浏览…")
        self.browse_btn.clicked.connect(self.browse_install_dir)
        path_row.addWidget(self.browse_btn)
        content.addLayout(path_row)

        info = self.install_info = QLabel("正在后台检查旧版本安装目录…")
        info.setObjectName("Status")
        info.setWordWrap(True)
        info.setMinimumHeight(38)
        content.addWidget(info)
        self.old_locations = QComboBox()
        self.old_locations.setVisible(False)
        self.old_locations.currentIndexChanged.connect(self._select_old_location)
        content.addWidget(self.old_locations)

        self.shortcut_box = QCheckBox("创建桌面与开始菜单快捷方式")
        self.shortcut_box.setChecked(True)
        content.addWidget(self.shortcut_box)

        self.status = QLabel("准备就绪")
        self.status.setObjectName("Status")
        content.addWidget(self.status)

        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        self.bar.setValue(0)
        self.bar.setTextVisible(False)
        content.addWidget(self.bar)

        row = QHBoxLayout()
        row.addStretch(1)
        self.run_btn = QPushButton("立即安装")
        self.run_btn.setObjectName("Primary")
        self.run_btn.setMinimumHeight(42)
        self.run_btn.clicked.connect(self.start_install)
        self.run_btn.setEnabled(False)
        self.exit_btn = QPushButton("退出")
        self.exit_btn.setMinimumHeight(42)
        self.exit_btn.clicked.connect(self.close)
        row.addWidget(self.exit_btn)
        row.addWidget(self.run_btn)
        lay.addLayout(row)

        src = resource_dir()
        if not (src / (APP_NAME + ".exe")).exists():
            self.status.setText("未找到安装载荷：%s" % src)
            self.run_btn.setEnabled(False)

        self._install_started = False
        self._detecting = True
        probe = ExistingInstallProbe(QApplication.instance())
        probe.ready.connect(self._existing_ready, Qt.QueuedConnection)
        probe.ready.connect(probe.deleteLater, Qt.QueuedConnection)

        def detect():
            try:
                old = existing_install()
            except Exception:
                old = None
            try:
                probe.ready.emit(old)
            except RuntimeError:
                pass
        threading.Thread(target=detect, name="existing-install-check", daemon=True).start()
        QTimer.singleShot(5000, self._allow_manual_install)

    @Slot(object)
    def _existing_ready(self, old):
        self._detecting = False
        if self._install_started:
            return
        if old:
            locations = old.get("candidates") or [old]
            self.old_locations.blockSignals(True)
            for item in locations:
                self.old_locations.addItem(str(item["version"]) + " · " + str(item["path"]), item)
            selected = next((i for i, item in enumerate(locations) if item["path"] == old["path"]), 0)
            self.old_locations.setCurrentIndex(selected)
            self.old_locations.blockSignals(False)
            self.old_locations.setVisible(len(locations) > 1)
            if not self.path_edit.isModified():
                self.path_edit.setText(str(old["path"]))
            self.install_info.setText("检测到 " + str(old["version"]) + "，默认原目录覆盖升级，保留用户配置。")
        else:
            self.install_info.setText("未发现旧版本，请选择 Easy-Tools 专用安装目录。")
        self.run_btn.setEnabled((resource_dir() / (APP_NAME + ".exe")).is_file())

    @Slot(int)
    def _select_old_location(self, index):
        item = self.old_locations.itemData(index)
        if item:
            self.path_edit.setText(str(item["path"]))
            self.install_info.setText("将覆盖所选目录中的 " + str(item["version"]) + "，用户配置保留。")

    def _allow_manual_install(self):
        if self._detecting and not self._install_started:
            self.install_info.setText("旧版本目录检查仍在后台进行，也可手动填写原安装目录。")
            self.run_btn.setEnabled((resource_dir() / (APP_NAME + ".exe")).is_file())

    def browse_install_dir(self) -> None:
        current = self.path_edit.text().strip() or str(install_dir())
        selected = QFileDialog.getExistingDirectory(self, "选择 Easy-Tools 安装目录", current)
        if selected:
            self.path_edit.setText(selected)

    def start_install(self) -> None:
        if self._install_started:
            return
        self._install_started = True
        src = resource_dir()
        dst = Path(self.path_edit.text().strip() or install_dir())
        self.run_btn.setEnabled(False)
        self.exit_btn.setEnabled(False)
        self.path_edit.setEnabled(False)
        self.old_locations.setEnabled(False)
        self.worker = Installer(src, dst, self.shortcut_box.isChecked())
        self.worker.progress.connect(
            lambda v, t: (self.bar.setValue(v), self.status.setText(t)))
        self.worker.finished_ok.connect(self.on_done)
        self.worker.start()

    def on_done(self, exe: str, ok: bool) -> None:
        self.bar.setValue(100 if ok else 0)
        if not ok:
            self._install_started = False
            self.status.setText(exe)
            self.run_btn.setEnabled(True)
            self.exit_btn.setEnabled(True)
            self.path_edit.setEnabled(True)
            self.old_locations.setEnabled(True)
            QMessageBox.warning(self, "安装失败", exe)
            return
        self.status.setText("安装完成，可以启动了")
        box = QMessageBox(self)
        box.setWindowTitle("安装完成")
        box.setText("已安装到：\n%s\n\n现在启动 %s 吗？" % (Path(exe).parent, APP_NAME))
        launch = box.addButton("立即启动", QMessageBox.AcceptRole)
        box.addButton("稍后", QMessageBox.RejectRole)
        box.exec_()
        if box.clickedButton() is launch:
            subprocess.Popen([exe])
        self.close()


def selftest() -> int:
    """无界面自检：确认载荷真的封进了 exe（构建关卡用，避免只验"文件存在"）。"""
    src = resource_dir()
    exe = src / (APP_NAME + ".exe")
    files = [p for p in src.rglob("*") if p.is_file()] if src.exists() else []
    total = sum(p.stat().st_size for p in files)
    ok = exe.exists() and uninstaller_source().is_file() and len(files) > 5 and total > 20 * 1024 * 1024
    print("payload=%s files=%d bytes=%d ok=%s" % (src, len(files), total, ok))
    return 0 if ok else 1


def silent_install(dst: Path) -> int:
    """无人值守安装，供构建后验证与批量部署使用。"""
    src = resource_dir()
    try:
        exe, ok = perform_install(src, dst, shortcuts=True)
    except Exception as e:                      # noqa: BLE001
        print("silent install failed: %s" % e)
        return 1
    print("silent install -> %s (ok=%s)" % (exe, ok))
    return 0 if ok else 1


def main() -> int:
    if "--selftest" in sys.argv:
        return selftest()
    if "--silent" in sys.argv:
        i = sys.argv.index("--silent")
        dst = Path(sys.argv[i + 1]) if len(sys.argv) > i + 1 else install_dir()
        return silent_install(dst)
    app = QApplication(sys.argv)
    icon_path = app_icon_path()
    if icon_path.is_file():
        app.setWindowIcon(QIcon(str(icon_path)))
    w = Window()
    w.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
