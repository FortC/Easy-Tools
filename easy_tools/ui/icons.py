"""Lucide SVG 图标库加载器。

图标来自 lucide-static v1.60.0（ISC 许可，免费可商用，见 icons/README.md）。
原始 SVG 是 24x24 viewBox 的描边图标，颜色用 stroke="currentColor" 表达；
QSvgRenderer 不解析 currentColor，因此渲染前把占位符替换为目标颜色。

兼容策略：配置里 action.icon 存的是图标名（如 "notepad-pen"）。
找不到同名 SVG 时调用方应退回旧的 emoji/文本显示——用户手工写的
配置不会因为升级而丢图标。允许 "lucide:xxx" 前缀写法，纯为可读性。

渲染统一按 2 倍像素出图并设置 devicePixelRatio，高 DPI 屏上不糊。
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QImage, QPainter, QPixmap

_ICON_DIR = Path(__file__).parent / "icons"

#: 进程内缓存：(名字, 物理像素, 颜色) -> QPixmap。图标是静态资源，
#: 缓存只增不减，数量以百计、每个几 KB，没有淘汰的必要。
_cache: dict = {}

_PREFIX = "lucide:"


def _normalize(name: str) -> str:
    name = (name or "").strip()
    if name.lower().startswith(_PREFIX):
        name = name[len(_PREFIX):].strip()
    return name


def icon_names() -> list:
    """全部内置图标名（按字母序）。供图标选择器枚举。"""
    return sorted(p.stem for p in _ICON_DIR.glob("*.svg"))


def has_icon(name: str) -> bool:
    return (_ICON_DIR / (_normalize(name) + ".svg")).is_file()


def _svg_text(name: str) -> Optional[str]:
    path = _ICON_DIR / (name + ".svg")
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def pixmap(name: str, px: int, color: str = "#2458c5") -> Optional[QPixmap]:
    """按逻辑尺寸 px 渲染图标；名字不存在返回 None（调用方退回 emoji）。

    内部按 2 倍物理像素渲染并设置 devicePixelRatio，保证高 DPI 清晰。
    """
    name = _normalize(name)
    if not name or not _ICON_DIR.exists():
        return None
    physical = max(8, int(px)) * 2
    key = (name, physical, color)
    if key in _cache:
        return _cache[key]
    svg = _svg_text(name)
    if svg is None:
        return None
    from PySide6.QtSvg import QSvgRenderer
    # QSvgRenderer 构造解析失败时不抛异常，只能靠 isValid() 判断
    renderer = QSvgRenderer(QByteArray(svg.replace("currentColor", color).encode("utf-8")))
    if not renderer.isValid():
        return None
    img = QImage(physical, physical, QImage.Format_RGBA8888)
    img.fill(Qt.transparent)
    painter = QPainter(img)
    painter.setRenderHint(QPainter.Antialiasing, True)
    vb = renderer.viewBoxF()
    if vb.width() > 0 and vb.height() > 0:
        # 等比缩放居中：Lucide 图形本身留有约 1/12 的内边距，直接铺满即可
        scale = min(physical / vb.width(), physical / vb.height())
        w, h = vb.width() * scale, vb.height() * scale
        target = QRectF((physical - w) / 2, (physical - h) / 2, w, h)
        renderer.render(painter, target)
    painter.end()
    pm = QPixmap.fromImage(img)
    pm.setDevicePixelRatio(2.0)
    _cache[key] = pm
    return pm


def icon(name: str, color: str = "#2458c5", px: int = 18) -> Optional[QIcon]:
    """给按钮/列表项用的 QIcon；名字不存在返回 None。"""
    pm = pixmap(name, px, color)
    return QIcon(pm) if pm is not None else None


def tint(name: str, px: int, color: str) -> Optional[QPixmap]:
    """pixmap() 的别名，语义上强调"换个颜色再渲染"。"""
    return pixmap(name, px, color)


def icon_label(name: str, px: int, color: str = "#2458c5", parent=None):
    """造一个显示图标的 QLabel；名字无效时返回 None。

    面板/表单里大量使用"有图标用图标、没有退回 emoji"的模式，
    收敛到这里避免每个调用点重复写判断。
    """
    pm = pixmap(name, px, color)
    if pm is None:
        return None
    from PySide6.QtWidgets import QLabel
    lab = QLabel(parent)
    lab.setAttribute(Qt.WA_TranslucentBackground, True)
    lab.setAlignment(Qt.AlignCenter)
    lab.setPixmap(pm)
    return lab
