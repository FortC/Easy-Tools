"""键盘/鼠标输入注入（SendInput）。

粘贴模式使用剪贴板；拟人输入按字符节奏发送 Unicode 按键。
可打印字符不交给输入法拼音组词，保证中英数字混排原样输出。
Enter/Tab 保留真实按键行为，Esc 可随时中断。
"""

from __future__ import annotations

import ctypes
import random
import threading
import time
import unicodedata
from typing import List, Optional, Sequence

from . import api, clipboard
from .api import VK, user32

_send_lock = threading.Lock()


class _ImeMode:
    """按输入字符临时切换目标窗口的中/英文输入模式，并恢复用户原状态。"""

    NATIVE = 0x0001  # IME_CMODE_NATIVE

    def __init__(self) -> None:
        self.hwnd = user32.GetForegroundWindow()
        self.imm = None
        self.himc = None
        self.original = None
        self.sentence = 0
        self.active = None
        try:
            self.imm = ctypes.WinDLL("imm32", use_last_error=True)
            self.imm.ImmGetContext.argtypes = [ctypes.c_void_p]
            self.imm.ImmGetContext.restype = ctypes.c_void_p
            self.imm.ImmReleaseContext.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
            self.imm.ImmReleaseContext.restype = ctypes.c_int
            self.imm.ImmGetConversionStatus.argtypes = [
                ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong), ctypes.POINTER(ctypes.c_ulong)]
            self.imm.ImmGetConversionStatus.restype = ctypes.c_int
            self.imm.ImmSetConversionStatus.argtypes = [ctypes.c_void_p, ctypes.c_ulong,
                                                        ctypes.c_ulong]
            self.imm.ImmSetConversionStatus.restype = ctypes.c_int
            self.himc = self.imm.ImmGetContext(self.hwnd)
            if self.himc:
                conv, sentence = ctypes.c_ulong(), ctypes.c_ulong()
                if self.imm.ImmGetConversionStatus(self.himc, ctypes.byref(conv),
                                                   ctypes.byref(sentence)):
                    self.original, self.sentence = conv.value, sentence.value
        except Exception:
            self.himc = None

    @staticmethod
    def _native_for(ch: str):
        if not ch:
            return None
        name = unicodedata.name(ch, "")
        if any(script in name for script in
               ("CJK", "HIRAGANA", "KATAKANA", "HANGUL", "BOPOMOFO")):
            return True
        if ch.isascii():
            return False
        return None

    def for_character(self, ch: str) -> None:
        native = self._native_for(ch)
        if (native is None or native == self.active or self.himc is None
                or self.original is None):
            return
        mode = self.original | self.NATIVE if native else self.original & ~(self.NATIVE | 0x0008 | 0x0400)
        if self.imm.ImmSetConversionStatus(self.himc, mode, self.sentence):
            self.active = native

    def close(self) -> None:
        if self.himc is None:
            return
        try:
            if self.original is not None:
                self.imm.ImmSetConversionStatus(self.himc, self.original, self.sentence)
            self.imm.ImmReleaseContext(self.hwnd, self.himc)
        finally:
            self.himc = None


def _send(inputs: Sequence[api.INPUT]) -> int:
    """发送一批输入事件，返回系统实际接受的条数。"""
    if not inputs:
        return 0
    arr = (api.INPUT * len(inputs))(*inputs)
    sent = user32.SendInput(len(inputs), arr, ctypes.sizeof(api.INPUT))
    if sent != len(inputs):
        from .api import last_error
        raise OSError("SendInput 部分失败: %d/%d, err=%s"
                      % (sent, len(inputs), last_error()))
    return sent


def _key_input(vk: int, up: bool = False, scan: int = 0, flags: int = 0) -> api.INPUT:
    inp = api.INPUT(type=api.INPUT_KEYBOARD)
    inp.ki = api.KEYBDINPUT(wVk=vk, wScan=scan, dwFlags=flags,
                            time=0, dwExtraInfo=0)
    if up:
        inp.ki.dwFlags |= api.KEYEVENTF_KEYUP
    return inp


def key_down(vk: int) -> None:
    _send([_key_input(vk)])


def key_up(vk: int) -> None:
    _send([_key_input(vk, up=True)])


def tap_key(vk: int) -> None:
    _send([_key_input(vk), _key_input(vk, up=True)])


def parse_combo(combo: str) -> List[int]:
    """"ctrl+alt+q" -> [VK_CONTROL, VK_MENU, VK_Q]（保留原始顺序）。"""
    out: List[int] = []
    for part in (combo or "").split("+"):
        name = part.strip().lower()
        if not name:
            continue
        if name not in VK:
            raise KeyError("未知按键名：%s" % name)
        out.append(VK[name])
    if not out:
        raise KeyError("空快捷键")
    return out


def hotkey(combo: str, delay: float = 0.01) -> None:
    """按下组合键再抬起。顺序按下、逆序抬起，避免 Ctrl+C 变成 Ctrl+V。"""
    vks = parse_combo(combo)
    with _send_lock:
        for vk in vks:
            key_down(vk)
            time.sleep(delay)
        for vk in reversed(vks):
            key_up(vk)
            time.sleep(delay)


def combo_name(combo: str) -> str:
    return combo


def wheel(delta: int) -> None:
    """注入滚轮。delta 以 1/120 度为单位，正数向上。"""
    if not delta:
        return
    inp = api.INPUT(type=api.INPUT_MOUSE)
    inp.mi = api.MOUSEINPUT(dx=0, dy=0, mouseData=ctypes.c_uint32(delta & 0xFFFFFFFF),
                            dwFlags=api.MOUSEEVENTF_WHEEL, time=0, dwExtraInfo=0)
    _send([inp])


def _unicode_unit(unit: str) -> List[api.INPUT]:
    """Unicode 注入一个 UTF-16 码元。"""
    code = ord(unit)
    down = _key_input(0, scan=code, flags=api.KEYEVENTF_UNICODE)
    up = _key_input(0, up=True, scan=code, flags=api.KEYEVENTF_UNICODE)
    return [down, up]


def _char_vk(ch: str) -> Optional[int]:
    """把一个字符映射成当前键盘布局下的 (VK, 需要Shift)。"""
    if len(ch) != 1:
        return None
    r = user32.VkKeyScanW(ch)
    if r == -1:
        return None
    return r & 0xFF


def _needs_shift(ch: str) -> bool:
    if len(ch) != 1:
        return False
    return bool((user32.VkKeyScanW(ch) >> 8) & 1)


_SPECIAL = {"\n": "enter", "\t": "tab"}


def type_text(text: str, cps: float = 20.0, jitter_ms: int = 40,
              cancel: Optional[threading.Event] = None) -> int:
    """逐字符输入并返回已输入数量；Esc 或 cancel 中断，字符等待也可打断。"""
    if not text:
        return 0
    from .mouse import KeyboardHook

    text = text.replace("\r\n", "\n").replace("\r", "\n")
    interval = (1.0 / cps) if cps and cps > 0 else 0.0
    jitter = max(0, int(jitter_ms)) / 1000.0
    cancel = cancel if cancel is not None else threading.Event()
    sent = 0

    def on_key(vk, flags):
        if vk == VK["esc"] and not flags & 0x10:
            cancel.set()

    def stopped():
        # Poll as a fallback if a low-level hook cannot be installed.
        if user32.GetAsyncKeyState(VK["esc"]) & 0x8000:
            cancel.set()
        return cancel.is_set()

    with _send_lock:
        hook = KeyboardHook(lambda _name: None, on_key_down=on_key)
        ime = None
        try:
            if stopped():
                return 0
            hook.start(timeout=0.5)
            ime = _ImeMode()
            for ch in text:
                if stopped():
                    break
                ime.for_character(ch)
                special = _SPECIAL.get(ch)
                if special:
                    tap_key(VK[special])
                else:
                    # Both halves of an emoji are sent together, never cancelled
                    # or delayed between UTF-16 surrogate units.
                    raw = ch.encode("utf-16-le")
                    inputs = []
                    for i in range(0, len(raw), 2):
                        inputs.extend(_unicode_unit(raw[i:i + 2].decode("utf-16-le", "surrogatepass")))
                    _send(inputs)
                sent += 1
                if interval and sent < len(text):
                    until = time.monotonic() + interval + (random.uniform(0, jitter) if jitter else 0)
                    while not stopped():
                        remaining = until - time.monotonic()
                        if remaining <= 0:
                            break
                        cancel.wait(min(0.01, remaining))
        finally:
            try:
                if ime is not None:
                    ime.close()
            finally:
                hook.stop()
    return sent


def paste_text(text: str, restore_snapshot: Optional[str] = None,
               restore_delay_ms: int = 300, restore: bool = True) -> bool:
    """粘贴模式：备份 → 写入 → Ctrl+V → 延迟恢复。

    restore_snapshot 为 None 表示"调用方拿不到备份"，此时**不恢复**
    （宁可剪贴板停在目标文本，也不能把用户的图片/文件剪贴板洗成字符串）。
    """
    if not clipboard.set_text(text):
        return False
    time.sleep(0.02)
    hotkey("ctrl+v")
    if not restore:
        return True
    if restore_snapshot is None:
        return True
    delay = max(0, int(restore_delay_ms)) / 1000.0
    if delay:
        time.sleep(delay)
    clipboard.set_text(restore_snapshot)
    return True


def copy_selection() -> None:
    hotkey("ctrl+c")


def undo() -> None:
    hotkey("ctrl+z")
