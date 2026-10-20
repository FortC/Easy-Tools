"""配置中心中文可读化层测试。

用户反馈：配置中心里全是 chain.run / {'snippet_id': 'snip_a', ...} 这种内部代码，
可读性太差。本测试锁住翻译层的两个不变量：
1. 翻译只发生在显示层，写回配置的必须还是原始代码（不能把中文存进去）；
2. 查不到映射就如实显示原文，绝不编造中文。
"""

from __future__ import annotations

import unittest

from easy_tools.core.config_store import ConfigStore
from easy_tools.ui import labels
from easy_tools.ui.widgets import Column, Field


class KindLabelTest(unittest.TestCase):
    def test_已注册类型翻译成中文(self) -> None:
        self.assertEqual(labels.kind_label("snippet.input"), "输入文本片段")
        self.assertEqual(labels.kind_label("chain.run"), "运行事件链")
        self.assertEqual(labels.kind_label("clipboard.processor"), "剪贴板处理")
        self.assertEqual(labels.kind_label("system.settings"), "打开配置中心")

    def test_未知类型原样返回不编造(self) -> None:
        self.assertEqual(labels.kind_label("no.such.kind"), "no.such.kind")
        self.assertEqual(labels.kind_label(""), "（空）")

    def test_操作区翻译(self) -> None:
        self.assertEqual(labels.zone_label("public"), "公共区")
        self.assertEqual(labels.zone_label("context"), "特定操作区")
        self.assertEqual(labels.zone_label("weird"), "weird")


class DescribeParamsTest(unittest.TestCase):
    def setUp(self) -> None:
        ConfigStore.reset_instance()
        self.store = ConfigStore.instance()
        cfg = self.store.get()
        cfg["snippets"] = list(cfg.get("snippets", [])) + [
            {"id": "snip_a", "name": "问候语", "group": "常用语", "order": 1, "text": "hi"}]
        cfg["chains"] = list(cfg.get("chains", [])) + [
            {"id": "chain_1", "name": "清理链", "steps": []}]
        self.assertFalse(self.store.update(cfg))

    def test_文本片段参数带出片段名和注入方式(self) -> None:
        s = labels.describe_params({"kind": "snippet.input",
                                    "params": {"snippet_id": "snip_a", "mode": ""}}, self.store)
        self.assertIn("问候语", s)
        self.assertIn("粘贴", s)
        self.assertNotIn("snip_a", s)

    def test_手打模式显示逐字键入(self) -> None:
        s = labels.describe_params({"kind": "snippet.input",
                                    "params": {"snippet_id": "snip_a", "mode": "type"}}, self.store)
        self.assertIn("逐字键入", s)

    def test_片段被删时显示原始id而不是空白(self) -> None:
        s = labels.describe_params({"kind": "snippet.input",
                                    "params": {"snippet_id": "gone", "mode": ""}}, self.store)
        self.assertIn("gone", s)

    def test_剪贴板处理器参数翻译处理器名(self) -> None:
        s = labels.describe_params(
            {"kind": "clipboard.processor",
             "params": {"processor": "text.drop_blank_lines", "auto_paste": True}}, self.store)
        self.assertNotIn("text.drop_blank_lines", s)
        self.assertIn("自动粘贴", s)

    def test_事件链参数带出链名(self) -> None:
        s = labels.describe_params({"kind": "chain.run",
                                    "params": {"chain_id": "chain_1"}}, self.store)
        self.assertIn("清理链", s)

    def test_打开网址等常见类型(self) -> None:
        self.assertIn("bing", labels.describe_params(
            {"kind": "open.url", "params": {"url": "https://bing.com"}}, self.store))
        self.assertIn("notepad.exe", labels.describe_params(
            {"kind": "open.app", "params": {"app": "notepad.exe"}}, self.store))

    def test_未知类型如实显示键值不编造(self) -> None:
        s = labels.describe_params({"kind": "mystery.thing",
                                    "params": {"a": 1, "b": 2}}, self.store)
        self.assertIn("a=1", s)
        self.assertIn("b=2", s)

    def test_空参数返回空串(self) -> None:
        self.assertEqual(labels.describe_params({"kind": "clipboard.clear", "params": {}}, self.store), "")


class WidgetsContractTest(unittest.TestCase):
    def test_Column_fmt_默认为空(self) -> None:
        c = Column("kind", "类型")
        self.assertIsNone(c.fmt)

    def test_Field_labels_默认为空(self) -> None:
        f = Field("kind", "类型", "enum", ["a", "b"])
        self.assertEqual(f.labels, {})

    def test_翻译只在显示层原始kind不变(self) -> None:
        """关键不变量：显示用中文，存储仍是内部代码。"""
        row = {"kind": "snippet.input", "params": {"snippet_id": "x"}}
        self.assertEqual(labels.kind_label(row["kind"]), "输入文本片段")
        self.assertEqual(row["kind"], "snippet.input")


if __name__ == "__main__":
    unittest.main()
