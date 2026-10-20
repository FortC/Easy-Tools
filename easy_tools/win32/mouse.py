"""全局低级鼠标钩子：中键短按/拖动判定（方案 2.2）。

判定语义（对应方案决策 1「两类行为互斥」）：
- 短按（按下→释放，时长 <= tap_max_ms 且位移 < drag_threshold_px）→ 触发面板
- 按住拖动 → 保持"中键拖动滚动"的手感，不弹面板

关于中键消费的诚实说明：
Windows 低级钩子只能在事件到达时决定放行还是吞掉，**没有"撤回"能力**。
因此本实现提供两种模式，这是个真实的取舍，不存在两全：

- swallow_tap（默认）：按下即吞掉，避免浏览器中键粘贴/资源管理器中键开新窗口
  这类原生副作用。代价是拖动滚动由我们用 WHEEL 事件模拟（可关）。
- native_passthrough：全部放行，原生中键行为 100% 保留；代价是短按时
  目标程序仍会收到一次原生中键。

选择哪个是用户偏好，不是能力缺陷，所以做成配置项而不是硬编码。
"""

from __future__ import annotations

import ctypes
import math
import threading
import time
from ctypes import wintypes
from dataclasses import dataclass
from typing import Callable, List, Optional, Set

from ..core.log import get_logger
from . import api, input as input_mod
from .api import kernel32, user32

log = get_logger("win32.mouse")

HOOKPROC = ctypes.WINFUNCTYPE(api.LONG_PTR, ctypes.c_int, ctypes.c_size_t, ctypes.c_ssize_t)


@dataclass(frozen=True)
class TapEvent:
    x: int
    y: int
    duration_ms: float


@dataclass(frozen=True)
class ScrollEvent:
    ticks: int
    source: str = "middle-drag"


#: 触发键配置值 -> (按下消息, 释放消息, X 按钮编号；中键为 0)。
#: X1/X2 共用同一条 WM_XBUTTONDOWN/UP 消息，具体按钮在 mouseData 高 16 位。
_TRIGGER_BUTTONS = {
    "middle": (api.WM_MBUTTONDOWN, api.WM_MBUTTONUP, 0),
    "x1": (api.WM_XBUTTONDOWN, api.WM_XBUTTONUP, api.XBUTTON1),
    "x2": (api.WM_XBUTTONDOWN, api.WM_XBUTTONUP, api.XBUTTON2),
}


def is_trigger_event(msg: int, mouse_data: int, button: str) -> bool:
    """判定钩子消息是否属于配置的触发键。纯函数，无 Win32 依赖，可单测。

    未识别的 button 值按中键处理（与历史配置兼容）。
    """
    down, up, xnum = _TRIGGER_BUTTONS.get(str(button or "middle"),
                                          _TRIGGER_BUTTONS["middle"])
    if msg not in (down, up):
        return False
    if xnum == 0:
        return True
    return ((int(mouse_data) >> 16) & 0xFFFF) == xnum


class _TapClassifier:
    """中键手势状态机。纯逻辑，无 Win32 依赖，可被单测直接驱动。"""

    def __init__(self, tap_max_ms: int = 400, drag_threshold_px: int = 12,
                 scroll_ticks_per_pixel: float = 0.08) -> None:
        self.tap_max_ms = tap_max_ms
        self.drag_threshold_px = drag_threshold_px
        self.ticks_per_pixel = scroll_ticks_per_pixel
        self.reset()

    def reset(self) -> None:
        self.down = False
        self.dragging = False
        self.t0 = 0.0
        self.x0 = 0
        self.y0 = 0
        self.last_y = 0
        self._acc = 0.0
        # 供诊断日志读取的真实位移（release() 会顺手记下来）
        self.last_dist = 0.0

    def press(self, x: int, y: int) -> None:
        self.down = True
        self.dragging = False
        self.t0 = time.perf_counter()
        self.x0, self.y0 = x, y
        self.last_y = y
        self._acc = 0.0

    def move(self, x: int, y: int) -> int:
        """按住中键移动。返回需要补发的滚轮 tick 数（0 表示无）。"""
        if not self.down:
            return 0
        dist = math.hypot(x - self.x0, y - self.y0)
        if dist >= self.drag_threshold_px:
            self.dragging = True
        if not self.dragging:
            return 0
        delta_px = y - self.last_y
        self.last_y = y
        self._acc += delta_px * self.ticks_per_pixel
        whole = int(self._acc)
        self._acc -= whole
        return whole * api.WHEEL_DELTA

    def release(self, x: int, y: int) -> tuple:
        """返回 (is_tap, ticks_to_flush, duration_ms)。"""
        if not self.down:
            return False, 0, 0.0
        dur_ms = (time.perf_counter() - self.t0) * 1000.0
        dist = math.hypot(x - self.x0, y - self.y0)
        self.last_dist = dist
        is_tap = (not self.dragging) and dist < self.drag_threshold_px and dur_ms <= self.tap_max_ms
        flush = 0
        if self.dragging and self._acc:
            # 注意：move() 里每次已把整数 tick 减掉，此时 _acc 只剩小数余量。
            # 旧代码写 int(self._acc) —— 恒为 0，"释放补发剩余滚动"从未生效过。
            # 改为把小数余量按轮距换算后四舍五入发出（不足一格的部分滚动也发）。
            flush = round(self._acc * api.WHEEL_DELTA)
            self._acc = 0.0
        self.reset()
        return is_tap, flush, dur_ms


class MiddleButtonHook:
    """全局中键钩子。start() 后在独立线程里跑消息循环。"""

    def __init__(self, on_tap: Callable[[TapEvent], None],
                 on_scroll: Optional[Callable[[ScrollEvent], None]] = None,
                 on_other_down: Optional[Callable[[int, int], None]] = None) -> None:
        self._on_tap = on_tap
        self._on_scroll = on_scroll
        # 左/右键按下上报（物理坐标），用于"点击面板外部关闭"。
        # QApplication 级 eventFilter 只看得到本进程的事件——点桌面/别的程序
        # 时事件归对方进程，永远到不了我们。必须走全局钩子。
        # 回调运行在钩子线程，必须快，禁止做重活。
        self._on_other_down = on_other_down
        self._hook_id: Optional[int] = None
        self._thread: Optional[threading.Thread] = None
        self._tid = 0
        self._ready = threading.Event()
        self._lock = threading.RLock()
        self._cfg = {
            "enabled": True,
            # 触发键可选 middle/x1/x2；x1/x2 即鼠标侧键（后退/前进键）。
            # 短按/拖动判定对三种键一视同仁。
            "mouse_button": "middle",
            # tap_max_ms=400 / drag_threshold_px=12 是被真实用户逼出来的值：
            # 200ms/8px 会把大量真人点击判成"非短按"，而 swallow 模式下这些
            # 点击已被吞掉，表现为"按了完全没反应"。改动前先看
            # tests/test_mouse_hook.py 里的类人点击用例。
            "tap_max_ms": 400,
            "drag_threshold_px": 12,
            "middle_click_mode": "swallow_tap",
            "drag_scroll_enabled": True,
            "scroll_ticks_per_pixel": 0.08,
        }
        self._clf = _TapClassifier()
        self._proc = HOOKPROC(self._hook_proc)   # 必须保持引用，否则被 GC 后钩子回调野指针

    # ------------------------------------------------------------ 配置热更新
    def update_config(self, trigger_cfg: dict) -> None:
        with self._lock:
            self._cfg.update({k: v for k, v in (trigger_cfg or {}).items() if k in self._cfg})
            self._clf.tap_max_ms = int(self._cfg["tap_max_ms"])
            self._clf.drag_threshold_px = int(self._cfg["drag_threshold_px"])
            self._clf.ticks_per_pixel = float(self._cfg["scroll_ticks_per_pixel"])
            self._clf.reset()

    @property
    def enabled(self) -> bool:
        with self._lock:
            return bool(self._cfg["enabled"])

    # ------------------------------------------------------------ 生命周期
    def start(self, timeout: float = 5.0) -> bool:
        if self._thread is not None:
            return True
        self._thread = threading.Thread(target=self._run, name="mb-hook", daemon=True)
        self._thread.start()
        ok = self._ready.wait(timeout)
        if not ok:
            log.error("中键钩子启动超时")
        return ok and self._hook_id is not None

    def stop(self) -> None:
        if self._tid:
            user32.PostThreadMessageW(self._tid, api.WM_QUIT, 0, 0)
        if self._thread is not None:
            self._thread.join(timeout=3.0)
        self._thread = None
        self._ready.clear()

    def _run(self) -> None:
        self._tid = kernel32.GetCurrentThreadId()
        hmod = api.get_module_handle()
        self._hook_id = user32.SetWindowsHookExW(api.WH_MOUSE_LL, self._proc, hmod, 0)
        if not self._hook_id:
            log.error("SetWindowsHookExW(WH_MOUSE_LL) 失败 err=%s", api.last_error())
            self._ready.set()
            return
        log.info("中键钩子已安装")
        self._ready.set()
        m = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(m), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(m))
            user32.DispatchMessageW(ctypes.byref(m))
        user32.UnhookWindowsHookEx(self._hook_id)
        self._hook_id = None
        log.info("中键钩子已卸载")

    # ------------------------------------------------------------ 钩子回调
    def _hook_proc(self, code: int, wparam: int, lparam: int) -> int:
        # 回调运行在安装钩子的线程上，必须尽快返回，绝不能碰 Qt
        try:
            if code != api.HC_ACTION:
                return user32.CallNextHookEx(None, code, wparam, lparam)
            msg = int(wparam)
            data = ctypes.cast(lparam, ctypes.POINTER(api.MSLLHOOKSTRUCT)).contents

            if msg in (api.WM_LBUTTONDOWN, api.WM_RBUTTONDOWN):
                if self._on_other_down is not None:
                    try:
                        self._on_other_down(int(data.pt.x), int(data.pt.y))
                    except Exception:
                        log.exception("外部点击回调异常")

            # 触发键按下/释放：只处理配置选定的那个键（中键或 X1/X2 侧键），
            # 其余按键原样放行给系统，保留原生行为。
            if msg in (api.WM_MBUTTONDOWN, api.WM_MBUTTONUP,
                       api.WM_XBUTTONDOWN, api.WM_XBUTTONUP):
                with self._lock:
                    button = self._cfg["mouse_button"]
                if not is_trigger_event(msg, int(data.mouseData), button):
                    return user32.CallNextHookEx(None, code, wparam, lparam)
                if msg in (api.WM_MBUTTONDOWN, api.WM_XBUTTONDOWN):
                    return self._handle_trigger_down(data)
                return self._handle_trigger_up(data)

            if msg == api.WM_MOUSEMOVE:
                with self._lock:
                    if not self._clf.down:
                        return 0
                    ticks = self._clf.move(int(data.pt.x), int(data.pt.y))
                    dragging = self._clf.dragging      # 必须在锁内快照，锁外读是竞态
                    do_scroll = self._cfg["drag_scroll_enabled"]
                    swallow = self._cfg["middle_click_mode"] == "swallow_tap"
                if ticks and do_scroll:
                    input_mod.wheel(-ticks)   # 手指下拖 => 内容向上滚
                return 1 if (swallow and dragging) else 0
        except Exception:
            log.exception("鼠标钩子回调异常")
        return user32.CallNextHookEx(None, code, wparam, lparam)

    def _handle_trigger_down(self, data) -> int:
        """触发键按下：进短按/拖动状态机。返回钩子吞放行码。"""
        if not self.enabled:
            return 0
        with self._lock:
            self._clf.press(int(data.pt.x), int(data.pt.y))
            swallow = self._cfg["middle_click_mode"] == "swallow_tap"
        return 1 if swallow else 0

    def _handle_trigger_up(self, data) -> int:
        """触发键释放：判短按/补滚动。返回钩子吞放行码。"""
        with self._lock:
            is_tap, flush, dur = self._clf.release(int(data.pt.x), int(data.pt.y))
            swallow = self._cfg["middle_click_mode"] == "swallow_tap"
            do_scroll = self._cfg["drag_scroll_enabled"]
        if is_tap:
            try:
                self._on_tap(TapEvent(x=int(data.pt.x), y=int(data.pt.y), duration_ms=dur))
            except Exception:
                log.exception("触发键短按回调异常")
        elif not self._clf.dragging and dur > 0:
            # 走到这里 = 这次按下被吞了但没触发任何行为。若不落日志，
            # 用户"按了没反应"就永远无法排查——这是真实发生过的故障。
            #
            # 之前这条日志把"阈值内位移未超"写死在文案里，无论真实原因是
            # 位移超标还是超时都照报，等于把唯一线索给抹掉了。现在打印
            # 真实位移/阈值与三个判定条件，让"按了没反应"可自诊断。
            dist = self._clf.last_dist
            why = []
            if dist >= self._clf.drag_threshold_px:
                why.append("位移 %.0fpx 超过阈值 %spx" % (dist, self._clf.drag_threshold_px))
            if dur > self._clf.tap_max_ms:
                why.append("时长 %.0fms 超过上限 %sms" % (dur, self._cfg["tap_max_ms"]))
            if not why:
                why.append("拖动标记残留（判定异常）")
            log.info("触发键按下未判为短按（%s，时长 %.0fms，位移 %.0fpx），"
                     "已吞掉且不弹面板；如需放宽请调 trigger.tap_max_ms / drag_threshold_px",
                     "、".join(why), dur, dist)
        elif flush and do_scroll and self._on_scroll is not None:
            try:
                self._on_scroll(ScrollEvent(ticks=flush))
            except Exception:
                log.exception("拖动滚动回调异常")
        return 1 if swallow else 0


class _KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("vkCode", wintypes.DWORD), ("scanCode", wintypes.DWORD),
                ("flags", wintypes.DWORD), ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.c_size_t)]


class KeyboardHook:
    """全局热键钩子（方案 2.2「附加热键」）。

    用低级键盘钩子而不是 RegisterHotKey：注册热键数量有限（系统级配额），
    且拿不到"按键组合字符串"这类信息。低级钩子还能拿到修饰键按下状态，
    组合键判定更可控。
    """

    def __init__(self, on_hotkey: Callable[[str], None],
                 on_key_down: Optional[Callable[[int, int], None]] = None) -> None:
        self._on_hotkey = on_hotkey
        self._on_key_down = on_key_down
        self._hook_id: Optional[int] = None
        self._thread: Optional[threading.Thread] = None
        self._tid = 0
        self._ready = threading.Event()
        self._lock = threading.RLock()
        self._combos: dict = {}       # name -> frozenset(vk)
        self._down: Set[int] = set()
        self._fired: Set[str] = set()
        self._proc = HOOKPROC(self._hook_proc)

    def set_combos(self, specs: List[dict]) -> None:
        parsed = {}
        for spec in specs or []:
            if not spec.get("enabled"):
                continue
            try:
                parsed[spec.get("id") or spec.get("name")] = frozenset(input_mod.parse_combo(spec["combo"]))
            except Exception as e:
                log.warning("热键配置非法，已忽略: %s (%s)", spec, e)
        with self._lock:
            self._combos = parsed
            self._down.clear()
            self._fired.clear()

    def start(self, timeout: float = 5.0) -> bool:
        if self._thread is not None:
            return True
        self._thread = threading.Thread(target=self._run, name="kb-hook", daemon=True)
        self._thread.start()
        ok = self._ready.wait(timeout)
        return ok and self._hook_id is not None

    def stop(self) -> None:
        if self._tid:
            user32.PostThreadMessageW(self._tid, api.WM_QUIT, 0, 0)
        if self._thread is not None:
            self._thread.join(timeout=3.0)
        self._thread = None
        self._ready.clear()

    def _run(self) -> None:
        from ctypes import wintypes
        self._tid = kernel32.GetCurrentThreadId()
        self._hook_id = user32.SetWindowsHookExW(api.WH_KEYBOARD_LL, self._proc,
                                                 api.get_module_handle(), 0)
        if not self._hook_id:
            log.error("SetWindowsHookExW(WH_KEYBOARD_LL) 失败 err=%s", api.last_error())
        self._ready.set()
        m = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(m), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(m))
            user32.DispatchMessageW(ctypes.byref(m))
        if self._hook_id:
            user32.UnhookWindowsHookEx(self._hook_id)
            self._hook_id = None

    def _hook_proc(self, code: int, wparam: int, lparam: int) -> int:
        try:
            if code != api.HC_ACTION:
                return user32.CallNextHookEx(None, code, wparam, lparam)
            msg = int(wparam)
            kb = ctypes.cast(lparam, ctypes.POINTER(_KBDLLHOOKSTRUCT)).contents
            vk = int(kb.vkCode)
            up = bool(kb.flags & 0x80)
            fired = None
            if not up and self._on_key_down:
                self._on_key_down(vk, int(kb.flags))
            with self._lock:
                if up:
                    self._down.discard(vk)
                    for name in list(self._fired):
                        if vk in self._combos.get(name, ()):  # 松开组合键中任一键则解锁
                            self._fired.discard(name)
                else:
                    self._down.add(vk)
                    cur = frozenset(self._down)
                    fired = None
                    for name, want in self._combos.items():
                        if cur == want and name not in self._fired:
                            fired = name
                            self._fired.add(name)
                            break
            if fired:
                try:
                    self._on_hotkey(fired)
                except Exception:
                    log.exception("热键回调异常")
        except Exception:
            log.exception("键盘钩子回调异常")
        return user32.CallNextHookEx(None, code, wparam, lparam)
