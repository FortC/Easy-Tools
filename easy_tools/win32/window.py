"""前台窗口 / 进程 / 标题识别（方案 2.1 的 L1 层）。

为什么自己封装而不装 pywin32：本机 Python 3.14 无 cp314 wheel 风险，
且这里只需要 6 个 API，自己封装反而更短更可控。
"""

from __future__ import annotations

import ctypes
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from . import api
from .api import kernel32, user32



@dataclass(frozen=True)
class WindowInfo:
    """一次前台窗口快照。进程名一律小写，便于规则里忽略大小写。"""

    hwnd: int = 0
    title: str = ""
    process: str = ""          # 如 "winword.exe"
    process_path: str = ""     # 完整路径
    class_name: str = ""
    visible: bool = False
    minimized: bool = False
    rect: Tuple[int, int, int, int] = (0, 0, 0, 0)   # 屏幕坐标 l,t,r,b

    @property
    def valid(self) -> bool:
        return bool(self.hwnd and self.process)

    def to_dict(self) -> dict:
        return {
            "hwnd": self.hwnd, "title": self.title, "process": self.process,
            "process_path": self.process_path, "class_name": self.class_name,
            "visible": self.visible, "minimized": self.minimized,
        }


def _window_text(hwnd: int) -> str:
    n = user32.GetWindowTextLengthW(hwnd)
    if n <= 0:
        return ""
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value


def _class_name(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def _process_info(pid: int) -> Tuple[str, str]:
    """返回 (exe 文件名小写, 完整路径)。失败返回 ("", "")。"""
    if not pid:
        return "", ""
    h = kernel32.OpenProcess(api.PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        # 老系统 / 权限不足时退回 QueryFullProcessImageName 不可用的情况
        return "", ""
    try:
        size = ctypes.c_ulong(1024)
        buf = ctypes.create_unicode_buffer(size.value)
        if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            path = buf.value
            return path.rsplit("\\", 1)[-1].lower(), path
        return "", ""
    finally:
        kernel32.CloseHandle(h)


def get_foreground() -> WindowInfo:
    """取当前前台窗口快照。这是 L1 识别的唯一数据源，务必不抛异常。"""
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return WindowInfo()
    pid = ctypes.c_ulong(0)
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    exe, path = _process_info(pid.value)
    r = api.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    return WindowInfo(
        hwnd=int(hwnd),
        title=_window_text(hwnd),
        process=exe,
        process_path=path,
        class_name=_class_name(hwnd),
        visible=bool(user32.IsWindowVisible(hwnd)),
        minimized=bool(user32.IsIconic(hwnd)),
        rect=(r.left, r.top, r.right, r.bottom),
    )


def cursor_pos() -> Tuple[int, int]:
    p = api.POINT()
    if not user32.GetCursorPos(ctypes.byref(p)):
        return 0, 0
    return int(p.x), int(p.y)


def monitor_rect_at_cursor() -> Tuple[int, int, int, int]:
    """Physical bounds of the cursor's monitor, without scaling its origin."""
    x, y = cursor_pos()
    monitor = user32.MonitorFromPoint(api.POINT(x, y), 2)
    info = api.MONITORINFO()
    info.cbSize = ctypes.sizeof(info)
    if monitor and user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
        r = info.rcMonitor
        return r.left, r.top, r.right - r.left, r.bottom - r.top
    raise RuntimeError("无法读取当前显示器尺寸")


def client_rect_on_screen(hwnd: int) -> Tuple[int, int, int, int]:
    """客户区在屏幕坐标系下的 (left, top, right, bottom)。"""
    r = api.RECT()
    if not user32.GetClientRect(hwnd, ctypes.byref(r)):
        return (0, 0, 0, 0)
    p = api.POINT(0, 0)
    user32.ClientToScreen(hwnd, ctypes.byref(p))
    w, h = r.right - r.left, r.bottom - r.top
    return (p.x, p.y, p.x + w, p.y + h)


def screen_size() -> Tuple[int, int]:
    """主显示器尺寸。负值说明指标取错，静默修正为 0 由调用方判定无效。"""
    w = int(user32.GetSystemMetrics(api.SM_CXSCREEN))
    h = int(user32.GetSystemMetrics(api.SM_CYSCREEN))
    return (max(0, w), max(0, h))


def virtual_screen() -> Tuple[int, int, int, int]:
    """多显示器虚拟桌面 (left, top, width, height)，面板越界翻转时用。"""
    l = int(user32.GetSystemMetrics(api.SM_XVIRTUALSCREEN))
    t = int(user32.GetSystemMetrics(api.SM_YVIRTUALSCREEN))
    w = int(user32.GetSystemMetrics(api.SM_CXVIRTUALSCREEN))
    h = int(user32.GetSystemMetrics(api.SM_CYVIRTUALSCREEN))
    if w <= 0 or h <= 0:
        sw, sh = screen_size()
        return 0, 0, sw, sh
    return l, t, w, h


def set_foreground(hwnd: int) -> bool:
    """尽力置前。Windows 有前台锁，失败是常态，调用方必须容忍 False。"""
    if not hwnd:
        return False
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, api.SW_RESTORE)
    user32.BringWindowToTop(hwnd)
    return bool(user32.SetForegroundWindow(hwnd))


def minimize(hwnd: int) -> bool:
    return bool(user32.ShowWindow(hwnd, api.SW_MINIMIZE))


def restore(hwnd: int) -> bool:
    return bool(user32.ShowWindow(hwnd, api.SW_RESTORE))


def next_visible_window(hwnd: int) -> Optional[int]:
    """沿 Z 序向后找第一个可见且不属于本程序的顶层窗口。

    用途：面板已经抢到焦点时，真正的目标窗口就藏在面板下面一层——
    直接拿"当前前台"会把文本打进面板自己，必须沿 Z 序往回找。
    """
    if not hwnd:
        return None
    h = user32.GetWindow(hwnd, api.GW_HWNDNEXT)
    seen = 0
    while h and seen < 200:
        seen += 1
        if user32.IsWindowVisible(h) and not is_our_window(int(h)):
            return int(h)
        h = user32.GetWindow(h, api.GW_HWNDNEXT)
    return None


def is_our_window(hwnd: int, extra_owners: Optional[List[int]] = None) -> bool:
    """判断 hwnd 是否属于本程序（避免把自己的面板当成"上下文窗口"）。

    判定依据：窗口类名带 ET_PANEL/ET_SETTINGS 前缀，或进程路径 == 本进程。
    """
    if not hwnd:
        return False
    if extra_owners and int(hwnd) in set(int(x) for x in extra_owners):
        return True
    cls = _class_name(hwnd)
    if cls.startswith("ET_"):
        return True
    pid = ctypes.c_ulong(0)
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    import os
    return pid.value == os.getpid()
