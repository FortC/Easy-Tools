"""应用图标。

图案代码与 make_icon.py 是同一套，保证：
- 打包后即使拿不到 app.ico，运行时画出来的图标也和资源文件长得一样；
- 改 make_icon.py 重生成后两边自动同步，不会各画各的。

优先加载磁盘上的 app.ico（Windows 会按 DPI 选不同尺寸，多尺寸 ICO 文件
比运行时缩放清晰）；找不到才退回运行时绘制。
"""

from __future__ import annotations

import os

from PySide6.QtGui import QColor, QIcon, QImage, QPixmap

from ..core.paths import app_root, resource_root


def _candidate_paths():
    for base in (resource_root(), app_root()):
        yield os.path.join(str(base), "app.ico")


def _pixmap_from_pixels(px, size: int) -> QPixmap:
    """把 make_icon.render() 产出的 RGBA 矩阵转成 QPixmap。"""
    img = QImage(size, size, QImage.Format_RGBA8888)
    for y in range(size):
        row = px[y]
        for x in range(size):
            img.setPixelColor(x, y, QColor(*row[x]))
    return QPixmap.fromImage(img)


def app_icon() -> QIcon:
    for path in _candidate_paths():
        if os.path.isfile(path):
            icon = QIcon(path)
            if not icon.isNull():
                return icon
    try:
        from make_icon import render
    except Exception:
        return QIcon()
    icon = QIcon()
    for s in (16, 32, 48, 64, 128, 256):
        icon.addPixmap(_pixmap_from_pixels(render(s), s))
    return icon
