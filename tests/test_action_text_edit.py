"""Actions backed by snippets must expose and save their full text."""
import os
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path
from PySide6.QtWidgets import QApplication, QPlainTextEdit
from easy_tools.ui.widgets import _widget_for
from easy_tools.ui.widgets import JsonFormDialog
from easy_tools.core.app import AppService
from easy_tools.core.config_store import ConfigStore
from easy_tools.ui.settings import SettingsWindow
from easy_tools.ui import labels

os.environ.setdefault("QT_QPA_PLATFORM", "windows")
_APP = QApplication.instance() or QApplication([])

class ActionTextEditTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="et-text-edit-")
        self.store = ConfigStore(Path(self.tmp.name) / "config.json")
        cfg = self.store.get()
        cfg["snippets"].append({"id": "snippet.edit.test", "name": "正文测试", "group": "文本输入",
                                "order": 999, "text": "第一行\n第二行 {date} - 原文完整"})
        cfg["actions"].append({"id": "action.edit.test", "name": "编辑正文", "group": "文本输入",
                               "icon": "⌨️", "zone": "public", "order": 999,
                               "kind": "snippet.input", "params": {"snippet_id": "snippet.edit.test", "mode": "type"}})
        cfg["actions"].append({"id": "action.direct.test", "name": "直接输入", "group": "文本输入",
                               "icon": "⌨️", "zone": "public", "order": 1000,
                               "kind": "text.input", "params": {"text": "直接文本正文", "mode": "paste"}})
        self.assertEqual(self.store.update(cfg), [])
        self.window = SettingsWindow(AppService(self.store))

    def tearDown(self):
        self.window.close()
        self.window.deleteLater()
        _APP.processEvents()
        self.tmp.cleanup()

    def test_snippet_edit_form_shows_body_and_saves_it_back(self):
        row = next(a for a in self.window.action_editor.rows() if a["id"] == "action.edit.test")
        fields, prepared = self.window._prepare_action_edit(row)
        labels_by_key = {f.key: f.label for f in fields}
        self.assertEqual(prepared["text_content"], "第一行\n第二行 {date} - 原文完整")
        self.assertEqual(labels_by_key["text_content"], "文本内容")
        content_field = next(f for f in fields if f.key == "text_content")
        editor, _read = _widget_for(content_field, prepared["text_content"], {})
        self.assertIsInstance(editor, QPlainTextEdit)
        self.assertEqual(editor.toPlainText(), prepared["text_content"])
        prepared["text_content"] = "修改后的完整正文\n支持多行"
        updated = self.window._apply_action_edit(row, prepared)
        linked = next(s for s in self.window.snippet_editor.rows() if s["id"] == "snippet.edit.test")
        self.assertEqual(linked["text"], "修改后的完整正文\n支持多行")
        self.assertEqual(updated["params"], {"mode": "type", "snippet_id": "snippet.edit.test"})
        cfg = self.store.get()
        cfg["snippets"] = self.window.snippet_editor.rows()
        cfg["actions"] = [updated if a["id"] == row["id"] else a for a in cfg["actions"]]
        self.assertEqual(self.store.update(cfg), [])
        self.assertIn("修改后的完整正文", labels.describe_params(updated, self.store))

    def test_direct_text_edit_form_exposes_actual_action_text(self):
        row = next(a for a in self.window.action_editor.rows() if a["id"] == "action.direct.test")
        fields, prepared = self.window._prepare_action_edit(row)
        self.assertEqual(prepared["text_content"], "直接文本正文")
        prepared["text_content"] = "新直接正文"
        updated = self.window._apply_action_edit(row, prepared)
        self.assertEqual(updated["params"]["text"], "新直接正文")
        self.assertEqual(updated["params"]["mode"], "paste")


    def test_edit_button_uses_content_form_and_updates_linked_snippet(self):
        row = next(a for a in self.window.action_editor.rows() if a["id"] == "action.edit.test")
        table_row = next(i for i, a in enumerate(self.window.action_editor.rows()) if a["id"] == row["id"])
        self.window.action_editor.table.setCurrentCell(table_row, 0)
        seen = {}
        class AcceptedDialog:
            Accepted = 1
            def __init__(inner, title, fields, item, choices, parent):
                seen["body"] = item["text_content"]
                inner.result = dict(item)
                inner.result["text_content"] = "保存按钮修改正文"
            def exec(inner):
                return AcceptedDialog.Accepted
        with patch("easy_tools.ui.widgets.JsonFormDialog", AcceptedDialog):
            self.window.action_editor.edit_current()
        self.assertEqual(seen["body"], "第一行\n第二行 {date} - 原文完整")
        action = next(a for a in self.window.action_editor.rows() if a["id"] == row["id"])
        snippet = next(s for s in self.window.snippet_editor.rows() if s["id"] == "snippet.edit.test")
        self.assertEqual(snippet["text"], "保存按钮修改正文")
        self.assertEqual(action["params"]["snippet_id"], "snippet.edit.test")

    def test_action_list_summary_includes_snippet_body_preview(self):
        summary = labels.describe_params({"kind": "snippet.input",
            "params": {"snippet_id": "snippet.edit.test", "mode": "type"}}, self.store)
        self.assertIn("第一行", summary)
        self.assertIn("正文「", summary)
        self.assertIn("逐字键入", summary)

if __name__ == "__main__":
    unittest.main()
