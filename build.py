# -*- coding: utf-8 -*-
"""一键打包：清理 → PyInstaller → **产物冒烟关卡**。

三个设计要点，都是被真实故障逼出来的：

1. **先杀掉残留进程**。Windows 上正在运行的 exe 会锁住文件，
   rmtree 静默失败、PyInstaller 覆盖失败——结果是"构建报错但磁盘上还留着
   上一版 exe"。曾经因为没检查退出码，把旧产物当成新产物验了一遍，
   报出一堆早已修好的 bug。构建脚本必须响亮地失败。
2. **rmtree 失败不吞**。ignore_errors=True 会把"删不掉"变成
   "以为删掉了"，然后让 PyInstaller 去覆盖一个还被占用的文件。
3. **冒烟关卡是构建的一部分**，不是可选步骤。构建没通过产物验证，
   就不能算构建成功——否则"通过"这个词就没有意义了。

用法：
    python build.py            # 构建 + 冒烟
    python build.py --no-smoke # 只构建（不推荐）
"""

from __future__ import annotations

import shutil
import os
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TAG = next((sys.argv[i + 1] for i, arg in enumerate(sys.argv[:-1]) if arg == "--tag"), "")
if TAG and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,40}", TAG):
    raise SystemExit("--tag 只接受字母、数字、点、下划线和连字符")
DIST_ROOT = ROOT / (("dist-" + TAG) if TAG else "dist")
BUILD = ROOT / (("build-" + TAG) if TAG else "build")
DIST = DIST_ROOT / "Easy-Tools"
EXE = DIST / "Easy-Tools.exe"
UPDATER_SRC = ROOT / "installer" / "updater_helper.cs"
UPDATER_EXE = BUILD / "EasyToolsUpdater.exe"


def kill_running() -> None:
    """Never kill a user's running app as a side effect of packaging."""
    if TAG:
        return
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
         "Get-CimInstance Win32_Process -Filter \"Name='Easy-Tools.exe'\" | "
         "ForEach-Object { $_.ExecutablePath }"],
        capture_output=True, text=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    current = str(EXE.resolve()).casefold()
    for line in result.stdout.splitlines():
        image = line.strip()
        if not image:
            raise RuntimeError("无法确认正在运行的 Easy-Tools 路径；为保护用户进程，停止默认目录构建。请使用 --tag 隔离构建。")
        if str(Path(image).resolve()).casefold() == current:
            raise RuntimeError("当前构建目录中的 Easy-Tools 正在运行。请关闭该程序，或使用 --tag 隔离构建。")


def clean() -> None:
    for p in (DIST, BUILD):
        if p.exists():
            shutil.rmtree(p)          # 不吞异常：删不掉就必须知道
            print("已清理", p)


def compile_update_helper() -> None:
    """Build the small independent .NET updater before collecting PyInstaller resources."""
    compiler = Path(os.environ.get("WINDIR", "C:/Windows")) / "Microsoft.NET" / "Framework64" / "v4.0.30319" / "csc.exe"
    if not compiler.is_file():
        compiler = Path(os.environ.get("WINDIR", "C:/Windows")) / "Microsoft.NET" / "Framework" / "v4.0.30319" / "csc.exe"
    if not compiler.is_file():
        raise RuntimeError("未找到 .NET Framework C# 编译器，无法构建更新助手。")
    BUILD.mkdir(parents=True, exist_ok=True)
    subprocess.run([str(compiler), "/nologo", "/target:winexe", "/platform:anycpu", "/optimize+",
                    "/reference:System.Web.Extensions.dll", "/reference:System.Windows.Forms.dll",
                    "/out:" + str(UPDATER_EXE), str(UPDATER_SRC)], check=True,
                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def main() -> int:
    args = sys.argv[1:]
    smoke = "--no-smoke" not in args

    kill_running()
    clean()

    compile_update_helper()

    cmd = [sys.executable, "-m", "PyInstaller", "--noconfirm",
           "--distpath", str(DIST_ROOT), "--workpath", str(BUILD), "Easy-Tools.spec"]
    print("执行：", " ".join(cmd))
    env = os.environ.copy()
    env["EASYTOOLS_BUILD_TAG"] = TAG
    r = subprocess.run(cmd, cwd=str(ROOT), env=env)
    if r.returncode != 0:
        print("!! PyInstaller 失败，退出码", r.returncode)
        print("!! 不要拿 dist 里的旧产物继续验证——它不是这次构建的结果。")
        return r.returncode
    if not EXE.is_file():
        print("!! 构建返回成功但没有产出", EXE)
        return 1
    print("构建完成：", EXE)

    if not smoke:
        print("已跳过冒烟关卡（--no-smoke）。未经产物验证的构建不算完成。")
        return 0

    print()
    print("=" * 72)
    print("产物冒烟关卡")
    print("=" * 72)
    gate = subprocess.run([sys.executable, "smoke_exe.py"], cwd=str(ROOT))
    if gate.returncode != 0:
        print()
        print("!! 冒烟关卡未通过，构建视为失败。")
        return gate.returncode
    print()
    print("构建 + 冒烟全部通过。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
