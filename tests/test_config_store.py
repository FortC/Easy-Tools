"""ConfigStore 落盘行为测试。

重点覆盖"首次启动是否真的写出配置文件"——这条曾经是假的：
reload() 遇到文件不存在只在内存里用默认值，不落盘，
于是用户找不到配置文件，README 里"首次启动复制默认配置"的承诺也成了空话。
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from easy_tools.core.config_store import ConfigStore


class TestConfigStorePersistence(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "config.json"

    def tearDown(self):
        self.tmp.cleanup()

    def test_first_reload_writes_defaults_to_disk(self):
        store = ConfigStore(self.path)
        store.reload()
        self.assertTrue(self.path.is_file(),
                        "首次启动没有生成配置文件，用户将无处可查可改")
        data = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertGreaterEqual(len(data.get("actions", [])), 20)
        self.assertIn("trigger", data)
        self.assertIn("panel", data)

    def test_reload_is_idempotent_and_does_not_clobber(self):
        store = ConfigStore(self.path)
        store.reload()
        store.update({"panel": {"columns": 9}})
        self.assertEqual(store.section("panel")["columns"], 9)
        store.reload()
        self.assertEqual(store.section("panel")["columns"], 9,
                         "重载不应回退用户在界面上的修改")
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8"))
                         ["panel"]["columns"], 9)

    def test_broken_config_is_rejected_and_keeps_memory_state(self):
        store = ConfigStore(self.path)
        store.reload()
        good = store.get()
        self.path.write_text("{ 这不是合法 JSON", encoding="utf-8")
        self.assertFalse(store.reload())
        self.assertEqual(store.get(), good, "配置文件损坏时不应清空内存配置")

    def test_export_import_roundtrip(self):
        """导入语义是"用这份文件覆盖当前配置"，不是合并。

        所以导出时的值（6）导入回来必须是 6，哪怕中途改成了 4。
        这一点值得钉死：曾经把这里写成"合并"会导致用户导入一份旧配置
        却发现改不回去，行为与"导入"这个词的直觉相反。
        """
        store = ConfigStore(self.path)
        store.reload()
        exported_columns = store.section("panel")["columns"]
        text = store.export_json()
        store.update({"panel": {"columns": 4}})
        self.assertEqual(store.section("panel")["columns"], 4)
        errs = store.import_json(text)
        self.assertEqual(errs, [])
        self.assertEqual(store.section("panel")["columns"], exported_columns,
                         "导入应当覆盖而不是合并")


if __name__ == "__main__":
    unittest.main()
