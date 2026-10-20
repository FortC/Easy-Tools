"""前台窗口记忆。

中键面板弹出时，面板自己成为前台窗口；如果此时才去问"用户想在哪儿输入"，
已经晚了。所以必须在**面板弹出之前**记下真正的目标窗口，
执行文本输入前再把它拉回前台。

这是方案 2.3「自动确保目标窗口为前台焦点」的实现落点。
"""

from __future__ import annotations

import threading
import time
from typing import Optional

from ..win32 import window as w32
from .log import get_logger

log = get_logger("focus")

_lock = threading.RLock()
_target: Optional[int] = None
_our_hwnds: set = set()


def register_our_window(hwnd: int) -> None:
    """面板/设置窗口创建后登记，避免把自己的窗口当成目标。"""
    with _lock:
        _our_hwnds.add(int(hwnd))


def unregister_our_window(hwnd: int) -> None:
    with _lock:
        _our_hwnds.discard(int(hwnd))


def remember() -> Optional[int]:
    """记录目标窗口。必须在面板抢焦点**之前**调用（面板弹出前的那一刻）。

    若调用时前台已经是我们自己的窗口（比如面板已弹出、或用户从配置中心
    呼出面板），不能就此放弃——沿 Z 序往回找真正的目标窗口，否则文本
    会打进面板自己然后随面板一起消失。
    """
    fg = w32.get_foreground()
    if not fg.valid:
        return None
    hwnd = int(fg.hwnd)
    if hwnd in _our_hwnds or w32.is_our_window(hwnd, list(_our_hwnds)):
        alt = w32.next_visible_window(hwnd)
        if not alt:
            return None
        hwnd = alt
    with _lock:
        global _target
        _target = hwnd
    return _target


def target_hwnd() -> Optional[int]:
    with _lock:
        return _target


def restore(max_wait_ms: int = 120) -> bool:
    """把焦点还给目标窗口。返回是否成功。

    Windows 前台锁会让 SetForegroundWindow 偶发失败，
    所以失败后做有限次重试——这正是"确保目标窗口为前台"的实际含义。
    """
    hwnd = target_hwnd()
    if not hwnd:
        return False
    deadline = time.perf_counter() + max_wait_ms / 1000.0
    ok = False
    while True:
        ok = w32.set_foreground(hwnd)
        if ok:
            return True
        if time.perf_counter() >= deadline:
            break
        time.sleep(0.02)
    if not ok:
        log.warning("无法将焦点切回目标窗口 hwnd=%s", hwnd)
    return ok


def clear() -> None:
    with _lock:
        global _target
        _target = None
