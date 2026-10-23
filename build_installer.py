"""打安装包 exe（自研 GUI 安装器），并做载荷自检。

用法：python build_installer.py
前置：dist/Easy-Tools 已由 build.py 产出（缺产物直接失败，不拿旧包糊弄）。
产出：dist-installer/Easy-Tools-Setup-<版本>.exe（单文件，双击即安装）
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from installer.installer_app import APP_NAME, VERSION  # noqa: E402

TAG = next((sys.argv[i + 1] for i, arg in enumerate(sys.argv[:-1]) if arg == "--tag"), "")
EDITION = next((sys.argv[i + 1] for i, arg in enumerate(sys.argv[:-1]) if arg == "--edition"), "full").lower()
if EDITION not in ("lite", "full"):
    raise SystemExit("--edition 只接受 lite 或 full")
if TAG and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,40}", TAG):
    raise SystemExit("--tag 只接受字母、数字、点、下划线和连字符")
SMOKE = "--no-smoke" not in sys.argv[1:]
PAYLOAD = ROOT / (("dist-" + TAG) if TAG else "dist") / APP_NAME
OUT_DIR = ROOT / (("dist-installer-" + TAG) if TAG else "dist-installer")
WORK = ROOT / (("build-installer-" + TAG) if TAG else "build-installer")

# 安装器只用到 QtWidgets/QtGui/QtCore，其余大件一律不带，
# 否则单个安装包会被 Qt 的 WebEngine/QML 拖到 300MB 以上
EXCLUDES = [
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebEngineQuick",
    "PySide6.QtQml", "PySide6.QtQuick", "PySide6.QtQuick3D", "PySide6.QtQuickWidgets",
    "PySide6.Qt3DCore", "PySide6.QtCharts", "PySide6.QtDataVisualization",
    "PySide6.QtMultimedia", "PySide6.QtMultimediaWidgets", "PySide6.QtBluetooth",
    "PySide6.QtNfc", "PySide6.QtPositioning", "PySide6.QtSerialPort",
    "PySide6.QtSql", "PySide6.QtTest", "PySide6.QtDesigner", "PySide6.QtHelp",
    "PySide6.QtOpenGL", "PySide6.QtPdf", "PySide6.QtPdfWidgets", "PySide6.QtSpatialAudio",
    "PySide6.QtSensors", "PySide6.QtWebChannel", "PySide6.QtWebSockets",
    "tkinter", "unittest", "pydoc", "doctest",
]


def main() -> int:
    if not (PAYLOAD / (APP_NAME + ".exe")).exists():
        print("缺少产物：%s，请先运行 python build.py" % PAYLOAD)
        return 1
    model_files = list(PAYLOAD.rglob("*.gguf"))
    if EDITION == "lite" and model_files:
        print("极速版载荷不得包含 GGUF 模型：%s" % model_files[0])
        return 1
    if EDITION == "full" and not any(path.name == "SmolLM2-360M-Instruct-Q4_K_M.gguf"
                                      for path in model_files):
        print("完整版载荷缺少内置模型权重")
        return 1

    for output in (OUT_DIR, WORK):
        resolved = output.resolve()
        if resolved.parent != ROOT.resolve() or not resolved.name.startswith(("dist-installer", "build-installer")):
            raise ValueError("构建输出路径必须位于当前工作区的安装器目录中。")
        if output.exists():
            shutil.rmtree(output)

    compiler = Path(os.environ.get("WINDIR", "C:/Windows")) / "Microsoft.NET" / "Framework64" / "v4.0.30319" / "csc.exe"
    if not compiler.is_file():
        compiler = compiler.parent.parent.parent / "Framework" / "v4.0.30319" / "csc.exe"
    if not compiler.is_file():
        raise RuntimeError("未找到 Windows .NET Framework 编译器，无法构建无控制台卸载器。")
    native = WORK / "Uninstall.exe"
    native.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([str(compiler), "/nologo", "/target:winexe", "/platform:anycpu", "/optimize+",
                    "/reference:System.Windows.Forms.dll", "/reference:System.Drawing.dll", "/reference:System.Management.dll",
                    "/out:" + str(native), str(ROOT / "installer" / "uninstaller.cs")],
                   check=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))

    cmd = [
        sys.executable, "-m", "PyInstaller", "--noconfirm", "--onefile",
        "--windowed",
        "--name", "Easy-Tools-Setup-%s-%s" % (VERSION, EDITION.title()),
        "--icon", str(ROOT / "app.ico"),
        "--distpath", str(OUT_DIR),
        "--workpath", str(WORK),
        "--specpath", str(WORK),
        "--add-data", "%s;payload" % PAYLOAD,
        "--add-data", "%s;uninstall" % native,
    ]
    for mod in EXCLUDES:
        cmd += ["--exclude-module", mod]
    cmd.append(str(ROOT / "installer" / "installer_app.py"))

    print("执行：", " ".join(cmd[:8]), "... 共 %d 个参数" % len(cmd))
    # 当前开发环境 PATH 可能带有其它应用捆绑的 API-set shim / MSVC DLL。
    # PyInstaller 会把它们误收进单文件安装器，运行时遮住 Windows 系统组件。
    env = os.environ.copy()
    env["EASYTOOLS_EDITION"] = EDITION
    native_runtime = os.path.normcase(os.path.normpath(
        os.path.join(str(Path.home()), ".cache", "codex-runtimes")))
    env["PATH"] = os.pathsep.join(
        entry for entry in env.get("PATH", "").split(os.pathsep)
        if not os.path.normcase(os.path.normpath(entry)).startswith(native_runtime)
    )
    rc = subprocess.run(cmd, cwd=str(ROOT), env=env).returncode
    if rc != 0:
        print("PyInstaller 失败（rc=%d），没有新安装包，不要用 dist-installer 里的旧文件" % rc)
        return rc

    setup = OUT_DIR / ("Easy-Tools-Setup-%s-%s.exe" % (VERSION, EDITION.title()))
    if not setup.exists():
        print("未产出安装包：%s" % setup)
        return 1

    # 先确认安装器模块自身没有"名字没导入"这类错误：这些只有在真装时才会炸
    if not SMOKE:
        print("已跳过安装器自检（--no-smoke）。")
        return 0

    imp = subprocess.run(
        [sys.executable, "-c",
         "import sys;sys.path.insert(0, %r);import installer.installer_app as m;"
         "m.uninstaller_source();"
         "print('installer-module-ok')" % str(ROOT)],
        capture_output=True, text=True, cwd=str(ROOT))
    print(imp.stdout.strip() or imp.stderr.strip()[-300:])
    if imp.returncode != 0:
        print("安装器模块自身有错，未交付")
        return 1

    print("安装包：%s（%.1f MB）" % (setup, setup.stat().st_size / 1024 / 1024))
    probe = subprocess.run([str(setup), "--selftest"], capture_output=True, text=True)
    print("载荷自检：%s（rc=%d）" % (probe.stdout.strip(), probe.returncode))
    if probe.returncode != 0:
        print("安装包载荷不完整，请勿交付")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
