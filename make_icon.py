# -*- coding: utf-8 -*-
"""生成 app.ico：不依赖任何图形库，用像素数学画一个"动作面板"图标。

为什么不用 QPixmap.save("app.ico")：Qt 的 ICO 写入插件并不保证存在，
在不同 PySide6 构建上行为不一致。这里直接按 ICO 文件格式手写字节，
产物确定、跨机器一致。

图案：深色圆角方块 + 3x2 白色圆点网格 + 一道强调色下划线 ——
直接对应面板本身的视觉形态，比画个 "E" 字母更能说明这是什么工具。

想改图案就改这里，然后重跑：python make_icon.py
"""

import struct
from pathlib import Path

ACCENT = (76, 141, 255)
BG = (30, 34, 41)
DOT = (255, 255, 255)


def render(size):
    """返回 size x size 的 BGRA 像素矩阵（每格为 (r,g,b,a)）。"""
    px = [[(0, 0, 0, 0)] * size for _ in range(size)]
    r = size * 0.22          # 圆角半径
    m = size * 0.06          # 外边距
    lo, hi = m, size - m

    def in_round_rect(x, y):
        if not (lo <= x < hi and lo <= y < hi):
            return False
        cx = min(max(x, lo + r), hi - r)
        cy = min(max(y, lo + r), hi - r)
        return (x - cx) ** 2 + (y - cy) ** 2 <= r * r

    for y in range(size):
        for x in range(size):
            if in_round_rect(x, y):
                px[y][x] = (*BG, 255)

    # 3 列 x 2 行圆点
    dot_r = size * 0.085
    pad = size * 0.235
    span = size - 2 * pad
    for row in range(2):
        for col in range(3):
            cx = pad + span * (col + 0.5) / 3.0
            cy = pad + span * (row + 0.5) / 2.0
            rr = dot_r * dot_r
            y0 = max(0, int(cy - dot_r) - 1)
            y1 = min(size, int(cy + dot_r) + 2)
            x0 = max(0, int(cx - dot_r) - 1)
            x1 = min(size, int(cx + dot_r) + 2)
            for y in range(y0, y1):
                for x in range(x0, x1):
                    if (x + 0.5 - cx) ** 2 + (y + 0.5 - cy) ** 2 <= rr:
                        px[y][x] = (*DOT, 255)

    # 底部强调色下划线
    bar_h = max(1, int(size * 0.055))
    top = size - int(size * 0.30)
    for y in range(top, min(size, top + bar_h)):
        for x in range(int(size * 0.34), int(size * 0.66)):
            if in_round_rect(x, y):
                px[y][x] = (*ACCENT, 255)
    return px


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
