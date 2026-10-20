"""钩子编排：把全局鼠标/键盘钩子接到配置与业务回调上。

为什么放在 core 而不是 ui：
钩子有自己的线程和生命周期，必须能在没有 Qt 的情况下启动/停止/改配置，
UI 只是它的一个消费者。放 core 才能单测，也才符合方案的分层。
"""

from __future__ import annotations

import threading
from typing import Callable, List, Optional

from ..win32.mouse import KeyboardHook, MiddleButtonHook, ScrollEvent, TapEvent
from .log import get_logger

log = get_logger("hooks")


class HookManager:
    """全局钩子的门面。对外只暴露 start/stop/apply_config。"""

    def __init__(self, store=None,
                 on_tap: Optional[Callable[[TapEvent], None]] = None,
                 on_scroll: Optional[Callable[[ScrollEvent], None]] = None,
                 on_hotkey: Optional[Callable[[str], None]] = None,
                 on_other_down: Optional[Callable[[int, int], None]] = None) -> None:
        self.store = store
        self._on_tap = on_tap
        self._on_scroll = on_scroll
        self._on_hotkey = on_hotkey
        self._on_other_down = on_other_down
        self._mb: Optional[MiddleButtonHook] = None
        self._kb: Optional[KeyboardHook] = None
        self._unsub = None
        self._lock = threading.RLock()

    # ------------------------------------------------------------ 生命周期
    def start(self) -> dict:
        """启动钩子。返回每个钩子的实际状态，UI 要如实展示而不是假装成功。"""
        result = {"mouse": False, "keyboard": False}
        with self._lock:
            cfg = self.store.section("trigger", {}) if self.store else {}
            if cfg.get("middle_button_enabled", True):
                self._mb = MiddleButtonHook(on_tap=self._on_tap, on_scroll=self._on_scroll,
                                 on_other_down=self._on_other_down)
                self._mb.update_config(cfg)
                result["mouse"] = self._mb.start()
            else:
                log.info("中键触发已在配置中关闭")

            self._kb = KeyboardHook(on_hotkey=self._on_hotkey or (lambda _n: None))
            self._kb.set_combos(cfg.get("hotkeys") or [])
            if any(h.get("enabled") for h in (cfg.get("hotkeys") or [])):
                result["keyboard"] = self._kb.start()

        if self.store:
            # 配置热更新：保存即生效，无需重启
            self._unsub = self.store.subscribe(self._on_config_changed)
        return result

    def stop(self) -> None:
        with self._lock:
            if self._unsub:
                self._unsub()
                self._unsub = None
            if self._mb:
                self._mb.stop()
                self._mb = None
            if self._kb:
                self._kb.stop()
                self._kb = None

    def _on_config_changed(self, cfg: dict) -> None:
        trigger = (cfg or {}).get("trigger", {})
        with self._lock:
            want_mouse = bool(trigger.get("middle_button_enabled", True))
            if want_mouse and self._mb is None:
                self._mb = MiddleButtonHook(on_tap=self._on_tap, on_scroll=self._on_scroll,
                                 on_other_down=self._on_other_down)
                self._mb.update_config(trigger)
                self._mb.start()
            elif not want_mouse and self._mb is not None:
                self._mb.stop()
                self._mb = None
            elif self._mb is not None:
                self._mb.update_config(trigger)

            if self._kb is not None:
                self._kb.set_combos(trigger.get("hotkeys") or [])

    # ------------------------------------------------------------ 状态
    def status(self) -> dict:
        with self._lock:
            return {
                "mouse": bool(self._mb and self._mb._hook_id),
                "keyboard": bool(self._kb and self._kb._hook_id),
            }

    def is_mouse_active(self) -> bool:
        with self._lock:
            return self._mb is not None and self._mb.enabled
