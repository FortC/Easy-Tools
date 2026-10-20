"""运行时路径解析。

开发态：<项目根>/data/
打包态（PyInstaller）：%APPDATA%/Easy-Tools/

配置文件因此在开发与打包之间自动切换，用户无需手工搬运。

**只读资源（defaults.json / app.ico）必须用 resource_root() 或 package_dir()
定位，不能用 app_root()。** 开发态三者指向同一片地方，看不出区别；
打包态它们分叉：可写数据在 %APPDATA%，PyInstaller 的 --add-data 内容在
sys._MEIPASS 下。早期版本 defaults_path() 用了 app_root()，开发态一路绿灯，
打包后首次启动直接 FileNotFoundError 崩溃——这类 bug 只有真跑打包产物才会暴露。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def is_frozen() -> bool:
    """是否运行在 PyInstaller 打包产物中。"""
    return bool(getattr(sys, "frozen", False))


def app_root() -> Path:
    """应用根目录：开发态为项目根，打包态为 exe 所在目录。"""
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def resource_root() -> Path:
    """只读资源根：打包态是 sys._MEIPASS（--add-data 内容的落地处），
    开发态是项目根。放图标这类"与包结构无关"的资源用它。"""
    if is_frozen():
        return Path(getattr(sys, "_MEIPASS", app_root()))
    return Path(__file__).resolve().parents[2]


def package_dir() -> Path:
    """easy_tools 包目录，其中含 config/defaults.json。

    打包态按 spec 的 --add-data 目标 "easy_tools/config" 还原；
    开发态就是包自己。两个分支指向同一份文件。
    """
    if is_frozen():
        return resource_root() / "easy_tools"
    return Path(__file__).resolve().parents[1]


def data_dir() -> Path:
    """可写数据目录，首次调用时创建。"""
    if is_frozen():
        base = os.environ.get("APPDATA") or str(Path.home())
        d = Path(base) / "Easy-Tools"
    else:
        d = app_root() / "data"
    d.mkdir(parents=True, exist_ok=True)
    return d


def config_path() -> Path:
    return data_dir() / "config.json"


def log_path() -> Path:
    return data_dir() / "easy_tools.log"


def defaults_path() -> Path:
    return package_dir() / "config" / "defaults.json"
