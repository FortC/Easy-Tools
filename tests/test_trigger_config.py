"""触发键可配置（中键/X1/X2）与配置中心触发页/事件链选中修复的回归测试。

覆盖三块：
1. is_trigger_event 纯函数——X1/X2 与中键的消息路由判定；
2. schema 对 trigger.mouse_button 的枚举校验与默认值迁移；
3. 配置中心触发页的热键表单回合（含非法组合键拦截）与事件链卡片选中。
"""

from __future__ import annotations

import copy
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from easy_tools.win32 import api
from easy_tools.win32.mouse import MiddleButtonHook, is_trigger_event
from easy_tools.config.schema import load_defaults, validate


def _xbutton(xnum: int) -> int:
    """构造 WM_XBUTTON* 消息的 mouseData：按钮编号在高 16 位。"""
    return xnum << 16


class TestTriggerEventRouting(unittest.TestCase):
    """is_trigger_event：路由判定必须是纯函数，不碰 Win32 消息循环。"""

    def test_middle_selected_matches_only_middle(self):
        self.assertTrue(is_trigger_event(api.WM_MBUTTONDOWN, 0, "middle"))
        self.assertTrue(is_trigger_event(api.WM_MBUTTONUP, 0, "middle"))
        self.assertFalse(is_trigger_event(api.WM_XBUTTONDOWN, _xbutton(1), "middle"))

    def test_x1_selected_matches_only_x1(self):
        self.assertTrue(is_trigger_event(api.WM_XBUTTONDOWN, _xbutton(1), "x1"))
        self.assertTrue(is_trigger_event(api.WM_XBUTTONUP, _xbutton(1), "x1"))
        self.assertFalse(is_trigger_event(api.WM_XBUTTONDOWN, _xbutton(2), "x1"),
                         "X2 按下不属于 X1 触发键")
        self.assertFalse(is_trigger_event(api.WM_MBUTTONDOWN, 0, "x1"),
                         "触发键为 X1 时中键必须放行，保留原生行为")

    def test_x2_selected_matches_only_x2(self):
        self.assertTrue(is_trigger_event(api.WM_XBUTTONDOWN, _xbutton(2), "x2"))
        self.assertTrue(is_trigger_event(api.WM_XBUTTONUP, _xbutton(2), "x2"))
        self.assertFalse(is_trigger_event(api.WM_XBUTTONDOWN, _xbutton(1), "x2"))

    def test_unknown_value_falls_back_to_middle(self):
        """历史配置里没有该键或值异常时按中键处理，不能崩。"""
        self.assertTrue(is_trigger_event(api.WM_MBUTTONDOWN, 0, ""))
        self.assertTrue(is_trigger_event(api.WM_MBUTTONDOWN, 0, "garbage"))
        self.assertFalse(is_trigger_event(api.WM_XBUTTONDOWN, _xbutton(1), "garbage"))

    def test_move_and_other_buttons_are_not_trigger(self):
        self.assertFalse(is_trigger_event(api.WM_MOUSEMOVE, 0, "x1"))
        self.assertFalse(is_trigger_event(api.WM_LBUTTONDOWN, 0, "middle"))


class TestTriggerConfig(unittest.TestCase):
    def test_hook_update_config_accepts_mouse_button(self):
        hook = MiddleButtonHook(on_tap=lambda _ev: None)
        hook.update_config({"mouse_button": "x1"})
        self.assertEqual(hook._cfg["mouse_button"], "x1")

    def test_defaults_validate_clean_with_mouse_button(self):
        errs = validate(load_defaults())
        self.assertEqual(errs, [], "默认配置必须通过校验：%s" % errs)

    def test_invalid_mouse_button_rejected(self):
        cfg = load_defaults()
        cfg["trigger"]["mouse_button"] = "x9"
        errs = validate(cfg)
        self.assertTrue(any("mouse_button" in e for e in errs), errs)


class TestSettingsTriggerAndChainUI(unittest.TestCase):
    """需要 Qt 的配置中心回归。"""

    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])
        from easy_tools.core.app import AppService
        from easy_tools.core.config_store import ConfigStore
        from easy_tools.ui.settings import SettingsWindow, _ChainBox
        cls.AppService = AppService
        cls.ConfigStore = ConfigStore
        cls.SettingsWindow = SettingsWindow
        cls._ChainBox = _ChainBox

    def _window(self):
        return self.SettingsWindow(self.AppService(self.ConfigStore.instance()))

    # ------------------------------------------------------------ 触发页
    def test_trigger_form_flat_and_collect_roundtrip(self):
        win = self._window()
        setters = {k: setter for k, _g, setter in win.trigger_page._getters}
        getters = {k: get for k, get, _s in win.trigger_page._getters}
        # 表单摊平：热键两项可见
        self.assertIn("hotkey_panel_combo", getters)
        self.assertIn("mouse_button", getters)
        # 改触发键 + 改热键，collect 后合回结构化配置
        setters["mouse_button"]("x2")
        setters["hotkey_panel_enabled"](True)
        setters["hotkey_panel_combo"]("ctrl+alt+p")
        cfg = win._collect()
        trigger = cfg["trigger"]
        self.assertEqual(trigger["mouse_button"], "x2")
        hk = next(h for h in trigger["hotkeys"] if h.get("id") == "panel")
        self.assertTrue(hk["enabled"])
        self.assertEqual(hk["combo"], "ctrl+alt+p")
        self.assertEqual(validate(cfg), [], "修改后的配置必须通过 schema 校验")

    def test_invalid_hotkey_combo_blocked_at_collect(self):
        win = self._window()
        setters = {k: setter for k, _g, setter in win.trigger_page._getters}
        setters["hotkey_panel_enabled"](True)
        setters["hotkey_panel_combo"]("not+a+key")
        with self.assertRaises(ValueError):
            win._collect()

    def test_disabled_hotkey_kept_in_config(self):
        win = self._window()
        setters = {k: setter for k, _g, setter in win.trigger_page._getters}
        setters["hotkey_panel_enabled"](False)
        cfg = win._collect()
        hk = next(h for h in cfg["trigger"]["hotkeys"] if h.get("id") == "panel")
        self.assertFalse(hk["enabled"])

    # ------------------------------------------------------------ 事件链选中
    def test_chain_box_click_selects_and_edit_finds_chain(self):
        win = self._window()
        self.assertGreaterEqual(len(win._chain_rows), 1)
        boxes = [win.chain_list.itemAt(i).widget()
                 for i in range(win.chain_list.count())]
        self.assertTrue(all(isinstance(b, self._ChainBox) for b in boxes))
        # 未选中时编辑入口应提示而不是返回任意链
        self.assertIsNone(win._selected_chain())
        # 点击信号 -> 选中
        boxes[0].clicked.emit()
        selected = win._selected_chain()
        self.assertIsNotNone(selected, "点击卡片后必须能选中事件链")
        self.assertEqual(selected.get("id"), win._chain_rows[0].get("id"))
        self.assertTrue(boxes[0]._et_selected)
        # 换选另一张卡时旧的取消高亮
        if len(boxes) > 1:
            boxes[1].clicked.emit()
            self.assertFalse(boxes[0]._et_selected)
            self.assertTrue(boxes[1]._et_selected)

    def test_trigger_form_cfg_flattens_hotkey(self):
        cfg = {"trigger": {"hotkeys": [
            {"id": "panel", "name": "呼出面板", "combo": "win+f2", "enabled": True}]}}
        flat = self.SettingsWindow._trigger_form_cfg(cfg)
        self.assertTrue(flat["hotkey_panel_enabled"])
        self.assertEqual(flat["hotkey_panel_combo"], "win+f2")


if __name__ == "__main__":
    unittest.main()
