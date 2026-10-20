"""快速新增文本按钮：纯逻辑单测（无需 Qt）。"""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from easy_tools.ui.text_button_wizard import (build_text_button_records,
                                              quick_add_text_button)


class TestTextButton(unittest.TestCase):
    def setUp(self):
        self.cfg = {
            "actions": [],
            "snippets": [{"id": "snip_greeting", "group": "常用语", "order": 10}],
        }

    def test_creates_snippet_and_action(self):
        snip, act = build_text_button_records(
            self.cfg, "项目周报开头", "📌", "文本输入", "周报 {date}", "type")
        self.assertEqual(snip["text"], "周报 {date}")
        self.assertEqual(act["kind"], "snippet.input")
        self.assertEqual(act["zone"], "public")
        # 动作必须指向刚建的片段，否则点了没反应
        self.assertEqual(act["params"]["snippet_id"], snip["id"])
        self.assertEqual(act["params"]["mode"], "type")
        self.assertEqual(act["icon"], "📌")

    def test_paste_mode_is_default(self):
        _snip, act = build_text_button_records(
            self.cfg, "招呼", "💬", "文本输入", "您好", "")
        self.assertEqual(act["params"]["mode"], "")

    def test_ids_are_unique_on_repeat(self):
        s1, a1 = build_text_button_records(self.cfg, "hello", "x", "g", "t", "")
        quick_add_text_button(self.cfg, "hello", "x", "g", "t", "")
        s2, a2 = build_text_button_records(self.cfg, "hello", "x", "g", "t", "")
        self.assertNotEqual(s1["id"], s2["id"])
        self.assertNotEqual(a1["id"], a2["id"])

    def test_chinese_name_gets_timestamp_id(self):
        snip, _act = build_text_button_records(self.cfg, "周报", "x", "g", "t", "")
        self.assertTrue(snip["id"].startswith("snip_"))
        self.assertTrue(snip["id"])

    def test_order_appends_after_same_group(self):
        snip, _act = build_text_button_records(self.cfg, "x", "x", "常用语", "t", "")
        self.assertEqual(snip["order"], 20)

    def test_rejects_blank_name_and_text(self):
        with self.assertRaises(ValueError):
            build_text_button_records(self.cfg, "  ", "x", "g", "t", "")
        with self.assertRaises(ValueError):
            build_text_button_records(self.cfg, "n", "x", "g", "   ", "")

    def test_quick_add_appends_to_cfg(self):
        quick_add_text_button(self.cfg, "按钮A", "⌨️", "文本输入", "内容A", "")
        self.assertEqual(len(self.cfg["actions"]), 1)
        self.assertEqual(len(self.cfg["snippets"]), 2)
        self.assertEqual(self.cfg["actions"][0]["group"], "文本输入")
        # 生成的记录必须能被 schema 之外的普通 JSON 往返，不含不可序列化对象
        json.dumps(self.cfg, ensure_ascii=False)


if __name__ == "__main__":
    unittest.main()