"""剪贴板读写（CF_UNICODETEXT）。

方案 2.3 的粘贴模式依赖"备份→写入→粘贴→延迟恢复"，
这里只处理文本格式，并**如实标注**非文本剪贴板无法完整备份：
复制的是文件/图片时，我们无法在不实现 OLE IDataObject 的前提下
完整还原，这类情况下备份返回 None，调用方据此决定不恢复，
避免把用户剪贴板内容清成一段文本（那是数据损坏，不是自动化）。
"""

from __future__ import annotations

import ctypes
import time
from typing import Optional, Tuple

from . import api
from .api import kernel32, user32

_RETRIES = 8
_RETRY_DELAY = 0.01


class ClipboardBusy(RuntimeError):
    """剪贴板被其它进程长时间占用。"""


def _open(retries: int = _RETRIES) -> None:
    for i in range(retries):
        if user32.OpenClipboard(None):
            return
        time.sleep(_RETRY_DELAY)
    raise ClipboardBusy("打开剪贴板失败（可能被其它进程占用）")


def get_text() -> Optional[str]:
    """读取剪贴板文本；无文本或读失败返回 None。"""
    try:
        _open()
    except ClipboardBusy:
        return None
    try:
        if not user32.IsClipboardFormatAvailable(api.CF_UNICODETEXT):
            return None
        h = user32.GetClipboardData(api.CF_UNICODETEXT)
        if not h:
            return None
        addr = kernel32.GlobalLock(h)
        if not addr:
            return None
        try:
            return ctypes.c_wchar_p(addr).value or ""
        finally:
            kernel32.GlobalUnlock(h)
    finally:
        user32.CloseClipboard()


def set_text(text: str) -> bool:
    """写入剪贴板文本（系统接管内存）。"""
    try:
        _open()
    except ClipboardBusy:
        return False
    try:
        user32.EmptyClipboard()
        data = text.replace("\x00", "")
        size = (len(data) + 1) * ctypes.sizeof(ctypes.c_wchar)
        h = kernel32.GlobalAlloc(api.GMEM_MOVEABLE, size)
        if not h:
            return False
        addr = kernel32.GlobalLock(h)
        if not addr:
            kernel32.CloseHandle(h)
            return False
        try:
            ctypes.memmove(addr, ctypes.create_unicode_buffer(data), size)
        finally:
            kernel32.GlobalUnlock(h)
        if not user32.SetClipboardData(api.CF_UNICODETEXT, h):
            kernel32.CloseHandle(h)
            return False
        return True
    finally:
        user32.CloseClipboard()


_CF_DIB = 8


def build_dib(bgra: bytes, width: int, height: int) -> bytes:
    """BGRA 顶到底像素 → CF_DIB 内存块（负高度 = 自上而下）。"""
    import struct
    header = struct.pack("<IiiHHIIiiII", 40, width, -height, 1, 32, 0,
                         len(bgra), 0, 0, 0, 0)
    return header + bgra


def set_image_bgra(bgra: bytes, width: int, height: int) -> bool:
    """把 BGRA 位图放进剪贴板（CF_DIB，系统接管内存）。"""
    if not bgra or width <= 0 or height <= 0:
        return False
    try:
        _open()
    except ClipboardBusy:
        return False
    try:
        user32.EmptyClipboard()
        data = build_dib(bgra, int(width), int(height))
        h = kernel32.GlobalAlloc(api.GMEM_MOVEABLE, len(data))
        if not h:
            return False
        addr = kernel32.GlobalLock(h)
        if not addr:
            kernel32.CloseHandle(h)
            return False
        try:
            ctypes.memmove(addr, data, len(data))
        finally:
            kernel32.GlobalUnlock(h)
        if not user32.SetClipboardData(_CF_DIB, h):
            kernel32.CloseHandle(h)
            return False
        return True
    finally:
        user32.CloseClipboard()


def snapshot() -> Optional[str]:
    """备份文本剪贴板。

    返回 None 有两种含义，都需要调用方区分处理：
    1) 剪贴板本来就没有文本 —— 恢复时应当跳过，而不是写入空串；
    2) 剪贴板有非文本格式（文件/图片）—— 恢复会破坏数据，必须跳过。
    """
    if not user32.IsClipboardFormatAvailable(api.CF_UNICODETEXT):
        return None
    return get_text()


def has_non_text_formats() -> bool:
    """剪贴板是否还挂着除 CF_UNICODETEXT 之外的格式。"""
    try:
        _open()
    except ClipboardBusy:
        return False
    try:
        return user32.CountClipboardFormats() > 0
    finally:
        user32.CloseClipboard()
