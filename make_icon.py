# -*- coding: utf-8 -*-
"""生成 app.ico：Lucide「zap」闪电图标 + 品牌渐变圆角底。

图案元素来自现成资源（lucide-static 的 zap.svg，ISC 许可，可商用），
本脚本只做栅格化组合：渐变圆角底板 + 白色闪电，输出多尺寸 ICO。

ICO 容器仍按文件格式手写字节（不用 QPixmap.save("app.ico")）：Qt 的
ICO 写入插件并不保证存在，在不同 PySide6 构建上行为不一致；直接写
字节产物确定、跨机器一致。像素来源换成 Qt 栅格化，运行本脚本需要
PySide6（开发机都有；打包产物里自带 app.ico，不会走到这里）。

想换图案就换 easy_tools/ui/icons/ 里的 GLYPH，然后重跑：python make_icon.py
"""

import struct
from pathlib import Path

ACCENT_TOP = (76, 141, 255)     # #4C8DFF
ACCENT_BOTTOM = (36, 88, 197)   # #2458C5
GLYPH = "zap"


def _svg_renderer(bold: bool = False):
    from PySide6.QtCore import QByteArray
    from PySide6.QtSvg import QSvgRenderer
    svg_path = (Path(__file__).parent / "easy_tools" / "ui" / "icons"
                / (GLYPH + ".svg"))
    svg = svg_path.read_text(encoding="utf-8").replace("currentColor", "#FFFFFF")
    if bold:
        # 16/32px 下 2px 描边缩成亚像素会消失，小尺寸加粗到 3px 保可辨识
        svg = svg.replace('stroke-width="2"', 'stroke-width="3"')
    renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
    if not renderer.isValid():
        raise RuntimeError("%s.svg 解析失败，无法生成应用图标" % GLYPH)
    return renderer


def render(size):
    """返回 size x size 的 RGBA 像素矩阵（每格为 (r,g,b,a)）。

    保持旧接口不变：ui/icon.py 的兜底路径（磁盘上没有 app.ico 时）
    直接拿这个矩阵拼 QIcon。
    """
    from PySide6.QtCore import QRectF, Qt
    from PySide6.QtGui import QColor, QImage, QLinearGradient, QPainter

    renderer = _svg_renderer(bold=size <= 32)
    img = QImage(size, size, QImage.Format_RGBA8888)
    img.fill(Qt.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing, True)

    m = size * 0.05                    # 底板外边距：16px 下约 1px，避免切边
    r = size * 0.225                   # 圆角半径
    board = QRectF(m, m, size - 2 * m, size - 2 * m)
    grad = QLinearGradient(board.topLeft(), board.bottomRight())
    grad.setColorAt(0.0, QColor(*ACCENT_TOP))
    grad.setColorAt(1.0, QColor(*ACCENT_BOTTOM))
    p.setPen(Qt.NoPen)
    p.setBrush(grad)
    p.drawRoundedRect(board, r, r)

    # 白色闪电居中：大尺寸占底板约 62%，小尺寸放大到 78% 防止糊没
    vb = renderer.viewBoxF()
    target_w = board.width() * (0.78 if size <= 32 else 0.62)
    scale = target_w / max(vb.width(), vb.height())
    w, h = vb.width() * scale, vb.height() * scale
    target = QRectF(board.center().x() - w / 2, board.center().y() - h / 2, w, h)
    renderer.render(p, target)
    p.end()

    # QImage(RGBA8888 内存序 R,G,B,A) -> 像素矩阵。
    # PySide6 的 constBits() 返回覆盖整个图像数据的 memoryview
    raw = bytes(img.constBits())
    bpl = img.bytesPerLine()
    out = []
    for y in range(size):
        base = y * bpl
        out.append([tuple(raw[base + x * 4: base + x * 4 + 4])
                    for x in range(size)])
    return out


def to_bgra(px):
    """ICO 的像素数据自下而上存放。"""
    out = bytearray()
    for y in range(len(px) - 1, -1, -1):
        for x in range(len(px)):
            r, g, b, a = px[y][x]
            out += bytes((b, g, r, a))
    return bytes(out)


def and_mask(px, size):
    """1bpp AND 掩码：alpha 为 0 的像素置 1。行按 4 字节对齐。"""
    row_bytes = ((size + 31) // 32) * 4
    out = bytearray()
    for y in range(len(px) - 1, -1, -1):
        row = bytearray(row_bytes)
        for x in range(size):
            if px[y][x][3] == 0:
                row[x // 8] |= 0x80 >> (x % 8)
        out += row
    return bytes(out)


def build(sizes=(16, 32, 48, 64, 128, 256)):
    images = []
    for s in sizes:
        px = render(s)
        body = to_bgra(px) + and_mask(px, s)
        header = struct.pack("<IiiHHIIiiII", 40, s, s * 2, 1, 32, 0,
                             len(body), 0, 0, 0, 0)
        images.append(header + body)

    offset = 6 + 16 * len(images)
    entries, blob = b"", b""
    for img in images:
        s = img[4]                                   # BMP 头宽 == 尺寸
        entries += struct.pack("<BBBBHHII", s % 256, s % 256, 0, 0, 1, 32,
                               len(img), offset)
        blob += img
        offset += len(img)
    return struct.pack("<HHH", 0, 1, len(images)) + entries + blob


if __name__ == "__main__":
    data = build()
    path = Path(__file__).parent / "app.ico"
    path.write_bytes(data)
    print("wrote", path, len(data), "bytes")
