"""文本按钮投递链路测试：snippet.input 必须把片段文本送进注入器。

覆盖本轮修的"点了没反应"故障链：
- 片段丢失 -> 明确失败（而不是无声无息）
- 片段存在 -> 文本被送到注入层（mode 决定粘贴/手打）
- 动作失败 -> run_action 后台线程必须上报，不能吞掉结果
"""

from __future__ import annotations

import time
import unittest
from unittest import mock

from easy_tools.core.actions import text_input
from easy_tools.core.config_store import ConfigStore
from easy_tools.core.app import AppService


SNIPPET = {"id": "snip_a", "name": "A", "group": "文本输入",
           "order": 10, "text": "你好世界"}
ACTION = {"id": "act_ok", "name": "好按钮", "group": "文本输入", "icon": "⌨",
          "zone": "public", "order": 10, "kind": "snippet.input",
          "params": {"snippet_id": "snip_a", "mode": ""}}


class SnippetDeliveryTest(unittest.TestCase):
    def setUp(self) -> None:
        ConfigStore.reset_instance()
        self.store = ConfigStore.instance()
        cfg = self.store.get()
        # 只追加，不覆盖默认动作/规则，否则规则会引用不到动作而校验失败
        cfg["snippets"] = list(cfg.get("snippets", [])) + [SNIPPET]
        cfg["actions"] = list(cfg.get("actions", [])) + [ACTION]
        self.assertFalse(self.store.update(cfg))
        self.svc = AppService(self.store)

    def test_片段文本被送进注入层(self) -> None:
        seen = {}

        def fake_paste(text, **kw):
            seen["text"] = text
            return True

        with mock.patch.object(text_input.focus, "restore", return_value=True), \
             mock.patch.object(text_input.winput, "paste_text", side_effect=fake_paste):
            res = self.svc.run_action("act_ok", background=False)
        self.assertTrue(res.ok, res.message)
        self.assertEqual(seen.get("text"), "你好世界")

    def test_手打模式走逐字键入(self) -> None:
        self.store.patch("text_input", {"default_mode": "type"})
        with mock.patch.object(text_input.focus, "restore", return_value=True), \
             mock.patch.object(text_input.winput, "type_text", return_value=4) as tt:
            res = self.svc.run_action("act_ok", background=False)
        self.assertTrue(res.ok, res.message)
        self.assertEqual(tt.call_args[0][0], "你好世界")

    def test_片段丢失时明确失败(self) -> None:
        """片段被删后按钮必须报错，而不是静默什么也不做。"""
        with mock.patch.object(text_input, "get_snippet", return_value=None):
            res = self.svc.run_action("act_ok", background=False)
        self.assertFalse(res.ok)
        self.assertIn("片段", res.message)

    def test_后台执行失败必须上报不能吞掉(self) -> None:
        """修复前 run_action 恒返回成功，失败被吞成"点了没反应"。"""
        notes = []
        self.svc.bind_ui(notify=lambda level, msg: notes.append((level, msg)))
        with mock.patch.object(text_input.focus, "restore", return_value=True), \
             mock.patch.object(text_input.winput, "paste_text", return_value=False):
            self.svc.run_action("act_ok", background=True)
            deadline = time.time() + 5
            while time.time() < deadline and not notes:
                time.sleep(0.05)
        self.assertTrue(notes, "失败必须通过 notify 上报")
        level, msg = notes[0]
        self.assertEqual(level, "warning")
        self.assertIn("失败", msg)


if __name__ == "__main__":
    unittest.main()
