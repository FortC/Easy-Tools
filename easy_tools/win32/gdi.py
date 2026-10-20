"""窗口截图（GDI 直采，不依赖 PIL/numpy）。

方案 2.1 的 L2 OCR 需要拿到前台窗口客户区的像素。两条采集路径：
1. PrintWindow(PW_RENDERFULLCONTENT)：窗口被遮挡时仍可拿到内容，
   现代 Windows 上对 DWM 合成窗口有效；但对部分窗口会返回全黑；
2. BitBlt 从窗口 DC 拷贝：作为全黑时的兜底。

缩放同样交给 GDI（StretchBlt + HALFTONE），不引入 numpy 做插值——
把 OCR 输入分辨率压到 max_width 以内，这才是真正的性能抓手。

尺寸契约（这里曾经踩过一个坑，务必保持）：
无论走哪条采集路径，最终像素**一律**从 mem_dc/bmp 上按目标尺寸 (dw,dh) 取，
绝不能把原尺寸 (sw,sh) 的缓冲按 dw*dh 截断——那样拿到的是错位的花屏。
"""

from __future__ import annotations

import ctypes
from dataclasses import dataclass
from typing import Optional, Tuple

from . import api
from .api import gdi32, user32

_MAX_DIM = 4000     # 单边上限，防止超大窗口把内存打爆
_MIN_DIM = 8        # 小于此尺寸视为无效窗口


@dataclass
class Capture:
    width: int
    height: int
    bgra: bytes          # 自上而下 BGRA，每像素 4 字节

    @property
    def pixels(self) -> int:
        return self.width * self.height

    def blank_ratio(self) -> float:
        """黑色像素占比。>0.98 基本可判定采集失败（DWM 返回黑图）。"""
        if not self.bgra:
            return 1.0
        step = 4
        n = len(self.bgra) // step
        if n == 0:
            return 1.0
        dark = sum(1 for i in range(0, n, max(1, n // 2000))
                   if self.bgra[i * step] == 0 and self.bgra[i * step + 1] == 0
                   and self.bgra[i * step + 2] == 0)
        sampled = len(range(0, n, max(1, n // 2000)))
        return dark / sampled if sampled else 1.0


def _source_rect(hwnd: int, region: str) -> Tuple[int, int, int, int]:
    """返回 (left, top, width, height)，均为屏幕坐标。"""
    from . import window as w32
    if region == "screen":
        sw, sh = w32.screen_size()
        return 0, 0, min(sw, 1920), min(sh, 1080)
    if region == "window":
        r = api.RECT()
        if not user32.GetWindowRect(hwnd, ctypes.byref(r)):
            return 0, 0, 0, 0
        return r.left, r.top, r.right - r.left, r.bottom - r.top
    l, t, r_, b = w32.client_rect_on_screen(hwnd)
    return l, t, r_ - l, b - t


def _read_dib(mem_dc, bmp, w: int, h: int) -> bytes:
    bi = api.BITMAPINFO()
    bi.bmiHeader.biSize = ctypes.sizeof(api.BITMAPINFOHEADER)
    bi.bmiHeader.biWidth = w
    bi.bmiHeader.biHeight = -h            # 负数 = 自上而下，省一次翻转
    bi.bmiHeader.biPlanes = 1
    bi.bmiHeader.biBitCount = 32
    bi.bmiHeader.biCompression = 0        # BI_RGB
    buf = ctypes.create_string_buffer(w * h * 4)
    if not gdi32.GetDIBits(mem_dc, bmp, 0, h, buf, ctypes.byref(bi), api.DIB_RGB_COLORS):
        return b""
    return buf.raw


def capture_rect(left: int, top: int, width: int, height: int) -> Optional[Capture]:
    """抓取屏幕任意物理矩形（物理像素、不缩放）。失败返回 None。

    截图覆盖层的"底片"用它：触发瞬间先抓屏，再全屏显示底片，
    覆盖层自身永远不会出现在截图里（微信截图同款思路）。
    """
    w, h = int(width), int(height)
    if w < _MIN_DIM or h < _MIN_DIM or w > 8192 or h > 8192:
        return None
    src_dc = user32.GetDC(None)
    if not src_dc:
        return None
    mem_dc = bmp = old = None
    try:
        mem_dc = gdi32.CreateCompatibleDC(src_dc)
        bmp = gdi32.CreateCompatibleBitmap(src_dc, w, h)
        if not mem_dc or not bmp:
            return None
        old = gdi32.SelectObject(mem_dc, bmp)
        if not gdi32.BitBlt(mem_dc, 0, 0, w, h, src_dc, left, top, 0x00CC0020):
            return None
        data = _read_dib(mem_dc, bmp, w, h)
        if not data:
            return None
        return Capture(width=w, height=h, bgra=data)
    finally:
        if mem_dc and old:
            gdi32.SelectObject(mem_dc, old)
        if bmp:
            gdi32.DeleteObject(bmp)
        if mem_dc:
            gdi32.DeleteDC(mem_dc)
        user32.ReleaseDC(None, src_dc)


def crop_capture(cap: Capture, left: int, top: int, width: int, height: int) -> Capture:
    """从 Capture 裁出子矩形（坐标相对其左上角，物理像素）。"""
    left = max(0, min(int(left), cap.width - 1))
    top = max(0, min(int(top), cap.height - 1))
    w = max(1, min(int(width), cap.width - left))
    h = max(1, min(int(height), cap.height - top))
    row = cap.width * 4
    start = top * row + left * 4
    out = bytearray()
    for y in range(h):
        out += cap.bgra[start + y * row: start + y * row + w * 4]
    return Capture(width=w, height=h, bgra=bytes(out))


def capture_window(hwnd: int, region: str = "client", max_width: int = 1280) -> Optional[Capture]:
    """抓取窗口像素。region ∈ {client, window, screen}；失败返回 None。"""
    if not hwnd:
        return None
    from . import window as w32

    _, _, sw, sh = _source_rect(hwnd, region)
    sw, sh = min(sw, _MAX_DIM), min(sh, _MAX_DIM)
    if sw < _MIN_DIM or sh < _MIN_DIM:
        return None

    scale = min(1.0, float(max_width) / float(sw)) if max_width and sw > max_width else 1.0
    dw = max(1, int(sw * scale))
    dh = max(1, int(sh * scale))

    owner = None if region == "screen" else hwnd
    src_dc = user32.GetDC(None) if region == "screen" else (
        user32.GetWindowDC(hwnd) if region == "window" else user32.GetDC(hwnd))
    if not src_dc:
        return None

    mem_dc = bmp = old = None
    try:
        mem_dc = gdi32.CreateCompatibleDC(src_dc)
        if not mem_dc:
            return None
        bmp = gdi32.CreateCompatibleBitmap(mem_dc, dw, dh)
        if not bmp:
            return None
        old = gdi32.SelectObject(mem_dc, bmp)
        gdi32.SetStretchBltMode(mem_dc, api.HALFTONE)

        used_printwindow = False
        if region != "screen":
            tmp_dc = tmp_bmp = None
            try:
                tmp_dc = gdi32.CreateCompatibleDC(src_dc)
                if tmp_dc:
                    tmp_bmp = gdi32.CreateCompatibleBitmap(tmp_dc, sw, sh)
                if tmp_dc and tmp_bmp:
                    old_t = gdi32.SelectObject(tmp_dc, tmp_bmp)
                    try:
                        if user32.PrintWindow(hwnd, tmp_dc, api.PW_RENDERFULLCONTENT):
                            # 统一从 mem_dc 取像素，保证尺寸契约
                            gdi32.StretchBlt(mem_dc, 0, 0, dw, dh,
                                             tmp_dc, 0, 0, sw, sh, api.SRCCOPY)
                            used_printwindow = True
                    finally:
                        gdi32.SelectObject(tmp_dc, old_t)
            finally:
                if tmp_bmp:
                    gdi32.DeleteObject(tmp_bmp)
                if tmp_dc:
                    gdi32.DeleteDC(tmp_dc)

        if not used_printwindow:
            gdi32.StretchBlt(mem_dc, 0, 0, dw, dh, src_dc, 0, 0, sw, sh, api.SRCCOPY)

        data = _read_dib(mem_dc, bmp, dw, dh)
        if not data:
            return None
        cap = Capture(width=dw, height=dh, bgra=data)
        # PrintWindow 全黑 -> 回退 BitBlt 再试一次
        if used_printwindow and cap.blank_ratio() > 0.98:
            gdi32.StretchBlt(mem_dc, 0, 0, dw, dh, src_dc, 0, 0, sw, sh, api.SRCCOPY)
            data2 = _read_dib(mem_dc, bmp, dw, dh)
            if data2:
                return Capture(width=dw, height=dh, bgra=data2)
        return cap
    finally:
        if old:
            gdi32.SelectObject(mem_dc, old)
        if bmp:
            gdi32.DeleteObject(bmp)
        if mem_dc:
            gdi32.DeleteDC(mem_dc)
        user32.ReleaseDC(owner, src_dc)
