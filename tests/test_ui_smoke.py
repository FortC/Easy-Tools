"""UI 冒烟测试（offscreen）+ 截图取证。

这是唯一能在无人值守环境里证明"面板/设置/链编辑器真的画得出来并响应"的测试，
因此它做三件事：
1. 真的构造控件、真的走一遍关键交互（搜索过滤、键盘选择、链编辑读改写）；
2. 断言行为，而不是只断言"没崩"；
3. 把每个界面 grab 成 PNG 存到 evidence/，供人工复核视觉结果。

注意：这里刻意不调用 AppService.start()——那会真的装全局鼠标钩子，
在测试机上劫持用户的真实鼠标。
"""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QApplication

from easy_tools.core.app import AppService
from easy_tools.core.config_store import ConfigStore

EVIDENCE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "evidence")

_app = QApplication.instance() or QApplication([])


def _shot(widget, name: str) -> str:
    os.makedirs(EVIDENCE, exist_ok=True)
    path = os.path.join(EVIDENCE, name)
    widget.grab().save(path, "PNG")
    return path


class TestPanel(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.service = AppService(ConfigStore.instance())
        from easy_tools.ui.panel import ActionPanel
        cls.panel = ActionPanel(cls.service)

    def test_panel_renders_cells(self):
        self.panel.open_at(300, 300)
        self.assertTrue(self.panel.isVisible())
        self.assertGreater(len(self.panel._cells), 0, "面板一格都没画出来")
        # Tab/分页模式下 _items 只是当前分组当前页，数量随分组大小波动；
        # 真正要保证的是全部公共动作都进了面板数据源
        total = sum(len(v) for v in self.panel._collect().values())
        self.assertGreater(total, 5, "面板一个可用动作都没有")

    def test_search_filters_and_restores(self):
        self.panel.open_at(300, 300)
        total = len(self.panel._cells)
        self.panel.search.setText("记事本")
        self.assertLessEqual(len(self.panel._cells), total, "搜索后格子数不应增加")
        self.panel.search.clear()
        self.assertEqual(len(self.panel._cells), total, "清空搜索应恢复全部")

    def test_search_reuses_panel_snapshot(self):
        with patch.object(self.panel, "_collect", wraps=self.panel._collect) as collect:
            self.panel.open_at(300, 300)
            initial_calls = collect.call_count
            self.panel.search.setText("记事本")
            self.panel.search.setText("浏览器")
            self.panel.search.clear()
            self.assertEqual(collect.call_count, initial_calls)
        self.panel.close_panel()

    def test_search_no_match_empties_panel(self):
        self.panel.open_at(300, 300)
        self.panel.search.setText("绝对不存在的动作xyzzy")
        self.assertEqual(len(self.panel._cells), 0)
        self.panel.search.clear()

    def test_keyboard_selection_moves(self):
        self.panel.open_at(300, 300)
        before = self.panel._selected
        nxt = (before + 1) % len(self.panel._cells)
        self.panel.keyPressEvent(
            QKeyEvent(QEvent.KeyPress, Qt.Key_Down, Qt.NoModifier))
        self.assertEqual(self.panel._selected, nxt)

    def test_escape_closes_panel(self):
        self.panel.open_at(300, 300)
        self.panel.keyPressEvent(
            QKeyEvent(QEvent.KeyPress, Qt.Key_Escape, Qt.NoModifier))
        self.assertFalse(self.panel.isVisible(), "Esc 必须能关闭面板")

    def test_activation_emits_and_closes(self):
        self.panel.open_at(300, 300)
        got = []
        self.panel.activated.connect(got.append)
        first = self.panel._items[0].action_id
        self.panel._activate(first)
        self.assertEqual(got, [first])
        self.assertFalse(self.panel.isVisible(), "激活动作后面板应关闭")

    def test_context_groups_assembly(self):
        """L1 规则命中后，特定操作区动作要能真正组装出来。"""
        store = self.service.store
        saved = [dict(r) for r in store.section("rules", [])]
        # action id 与 kind 是两回事：id=open.notepad，kind=open.app。
        # 硬编码一个"看起来对"的 id 会在默认集调整时静默变成 None，再炸在别处。
        act = next(a for a in self.service.store.section("actions", [])
                        if a.get("zone") == "context")
        try:
            store.patch("rules", [{"id": "rule.test", "name": "冒烟规则",
                                    "match": "any", "processes": ["*"],
                                    "action_ids": [act["id"]], "order": 1}])
            groups = self.service.context_groups()
            self.assertIn("冒烟规则", groups)
            self.assertEqual(groups["冒烟规则"][0].zone, "context")
            # 服务层返回裸规则名；"▸ " 前缀由面板渲染层添加。两层各锁各的契约。
            snap = self.service.panel_snapshot()
            self.assertIn("冒烟规则", snap["context"])
            self.panel.open_at(400, 400)
            rendered = [g for g in self.panel._collect()]
            self.assertIn("▸ 冒烟规则", rendered)
            self.assertTrue(any(i.zone == "context" for i in self.panel._items))
        finally:
            store.patch("rules", saved)

    def test_panel_stays_on_screen_near_corner(self):
        """越界翻转：面板贴屏幕右下角时也必须完整可见。"""
        from easy_tools.win32 import window as w32
        vl, vt, vw, vh = w32.virtual_screen()
        self.panel.open_at(vl + vw - 5, vt + vh - 5)
        g = self.panel.frameGeometry()
        self.assertGreaterEqual(g.left(), vl)
        self.assertGreaterEqual(g.top(), vt)
        self.assertLessEqual(g.right(), vl + vw)
        self.assertLessEqual(g.bottom(), vt + vh)

    def test_screenshot(self):
        self.panel.open_at(120, 120)
        path = _shot(self.panel, "panel.png")
        self.assertGreater(os.path.getsize(path), 3000)


class TestSettingsWindow(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.service = AppService(ConfigStore.instance())
        from easy_tools.ui.settings import SettingsWindow
        cls.win = SettingsWindow(cls.service)

    def test_tabs_present(self):
        titles = [self.win.tabs.tabText(i) for i in range(self.win.tabs.count())]
        for expect in ("动作库", "文本片段", "特定操作区规则", "事件链",
                       "触发设置", "面板样式", "OCR 设置", "文本输入",
                       "数据/导入导出", "综合配置"):
            self.assertIn(expect, titles)

    def test_tool_management_is_primary_and_configuration_pages_are_grouped(self):
        navigation = self.win.tabs._nav
        visible_titles = [navigation.item(i).text() for i in range(navigation.count())
                          if not navigation.item(i).isHidden()]
        self.assertEqual(visible_titles[0], "工具管理")
        self.assertIn("综合配置", visible_titles)
        self.assertFalse(hasattr(self.win.tabs, "more"))
        config_titles = [self.win.tabs._config_tabs.tabText(i)
                         for i in range(self.win.tabs._config_tabs.count())]
        self.assertIn("触发设置", config_titles)
        self.assertIn("本机模型", config_titles)
        self.assertIn("隐私保护", config_titles)
        local_model_index = next(i for i in range(self.win.tabs.count())
                                 if self.win.tabs.tabText(i) == "本机模型")
        self.win.tabs.setCurrentIndex(local_model_index)
        self.assertIs(self.win.tabs.currentWidget(), self.win.local_model_page)

    def test_save_roundtrip_is_valid(self):
        """保存 → 校验 → 立即生效：跑真实的 _collect + store.update。"""
        cfg = self.win._collect()
        errs = self.win.store.update(cfg)
        self.assertEqual(errs, [], "默认配置保存后校验不应报错：%s" % errs)
        self.assertGreaterEqual(len(self.win.store.section("actions", [])), 20)

    def test_bad_params_json_is_rejected(self):
        # 必须自恢复：setUpClass 的 SettingsWindow 是共享实例，脏数据会顺着
        # 字母序污染后面的用例，把"测试写错"伪装成"产品有 bug"。
        original = self.win.action_editor.rows()
        try:
            rows = self.win.action_editor.rows()
            rows[0]["params"] = "{不是合法 JSON"
            self.win.action_editor.set_rows(rows)
            with self.assertRaises(ValueError):
                self.win._collect()
        finally:
            self.win.action_editor.set_rows(original)

    def test_screenshot_actions_tab(self):
        self.win.resize(1100, 720)
        self.win.show()
        actions_index = next(i for i in range(self.win.tabs.count())
                             if self.win.tabs.tabText(i) == "动作库")
        self.win.tabs.setCurrentIndex(actions_index)
        path = _shot(self.win, "settings_actions.png")
        self.assertGreater(os.path.getsize(path), 5000)

    def test_screenshot_rules_tab(self):
        rules_index = next(i for i in range(self.win.tabs.count())
                           if self.win.tabs.tabText(i) == "特定操作区规则")
        self.win.tabs.setCurrentIndex(rules_index)
        path = _shot(self.win, "settings_rules.png")
        self.assertGreater(os.path.getsize(path), 5000)


class TestChainEditor(unittest.TestCase):
    def test_step_editor_roundtrip(self):
        from easy_tools.ui.chain_editor import ChainEditorDialog, StepEditor
        service = AppService(ConfigStore.instance())
        chain = {"id": "chain.test", "name": "冒烟链", "steps": [
            {"type": "action", "id": "text.strip", "input": "  a  "},
            {"type": "wait", "seconds": 0.2},
            {"type": "condition", "expr": "has_table",
             "then": [{"type": "action", "id": "table.transpose",
                       "input": "{last.output}"}], "else": []}]}
        dlg = ChainEditorDialog(chain, service)
        self.assertEqual(len(dlg._steps), 3)
        ed: StepEditor = dlg.editor
        ed.load(dlg._steps[1])
        dumped = ed.dump()
        self.assertEqual(dumped["type"], "wait")
        self.assertEqual(dumped["seconds"], 0.2)

    def test_condition_dump_preserves_branches(self):
        from easy_tools.ui.chain_editor import StepEditor
        ed = StepEditor()
        ed.load({"type": "condition", "expr": "is_empty",
                 "then": [{"type": "stop"}], "else": []})
        out = ed.dump()
        self.assertEqual(out["expr"], "is_empty")
        self.assertEqual(out["then"], [{"type": "stop"}])

    def test_default_chains_resolve(self):
        from easy_tools.core.chain import ChainRunner
        service = AppService(ConfigStore.instance())
        runner = ChainRunner(service.store)
        chains = service.store.section("chains", [])
        self.assertGreaterEqual(len(chains), 2)
        for chain in chains:
            self.assertTrue(chain.get("steps"), "默认链 %s 没有步骤" % chain["id"])
            self.assertIsNotNone(runner.find_chain(chain["id"]))

    def test_selecting_a_step_does_not_destroy_it(self):
        """回归：点选步骤曾经把"新选中的行"覆盖成"旧编辑器的内容"，
        表现为一点步骤那一步就被抹成空白。这里逐个切换并断言内容不变。"""
        from easy_tools.ui.chain_editor import ChainEditorDialog
        service = AppService(ConfigStore.instance())
        chain = {"id": "chain.sel", "name": "选择回归", "steps": [
            {"type": "action", "id": "clipboard.copy"},
            {"type": "action", "id": "text.strip", "input": "{clipboard}"},
            {"type": "wait", "seconds": 0.5},
            {"type": "condition", "expr": "has_table",
             "then": [{"type": "action", "id": "table.transpose",
                       "input": "{last.output}"}], "else": []}]}
        before = [dict(s) for s in chain["steps"]]
        dlg = ChainEditorDialog(chain, service)
        self.assertEqual(dlg._steps[0], before[0], "初始加载就破坏了第 1 步")
        for row in (1, 2, 3, 0, 2):
            dlg.list.setCurrentRow(row)
            self.assertEqual(dlg._steps, before, "切到第 %d 行后步骤内容被改动" % (row + 1))

    def test_selected_step_is_loaded_into_editor(self):
        from easy_tools.ui.chain_editor import ChainEditorDialog
        service = AppService(ConfigStore.instance())
        chain = {"id": "chain.ld", "name": "载入", "steps": [
            {"type": "action", "id": "clipboard.copy"},
            {"type": "action", "id": "input.paste", "input": "{last.output}"}]}
        dlg = ChainEditorDialog(chain, service)
        dlg.list.setCurrentRow(1)
        # 下拉框显示「中文名 · id」，原始 id 存在 itemData；断言数据而不是显示文本
        self.assertEqual(dlg.editor.id_box.currentData(), "input.paste")
        self.assertEqual(dlg.editor.input_edit.text(), "{last.output}")

    def test_screenshot_chain_editor(self):
        from easy_tools.ui.chain_editor import ChainEditorDialog
        service = AppService(ConfigStore.instance())
        chain = dict(service.store.section("chains", [])[0])
        dlg = ChainEditorDialog(chain, service)
        dlg.resize(760, 620)
        dlg.show()
        path = _shot(dlg, "chain_editor.png")
        self.assertGreater(os.path.getsize(path), 5000)


if __name__ == "__main__":
    unittest.main()
