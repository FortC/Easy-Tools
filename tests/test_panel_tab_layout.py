"""Regression: small/large Tabs must not move the search box or stretch headers."""
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from easy_tools.core.app import PanelItem
from easy_tools.core.config_store import ConfigStore
from easy_tools.ui.panel import ActionPanel
from easy_tools.ui import theme

_APP = None

class PanelTabLayoutTest(unittest.TestCase):
    def setUp(self):
        global _APP
        _APP = QApplication.instance() or QApplication([])
        theme.apply_application_theme(_APP)
        self.temp = tempfile.TemporaryDirectory(prefix="et-tab-layout-")
        self.store = ConfigStore(Path(self.temp.name) / "config.json")
        self.groups = {name: [PanelItem(name + str(i), "Action " + str(i), "*", name, "public", "")
                             for i in range(n)]
                       for name, n in (("Text", 4), ("Many", 73), ("Single", 1), ("Empty", 0), ("Other", 12))}
        service = SimpleNamespace(store=self.store,
            panel_snapshot=lambda: {"public": self.groups, "context": {}, "window": {}},
            refresh_context=lambda: None, ocr=SimpleNamespace(status=lambda: {"enabled": False}))
        self.panel = ActionPanel(service)
        self.panel.show()
        QTest.qWait(30)

    def tearDown(self):
        self.panel.hide()
        self.panel.deleteLater()
        _APP.processEvents()
        self.temp.cleanup()

    def geometry(self):
        p = self.panel
        return tuple(w.geometry().getRect() for w in (p.search, p.tab_host, p.scroll, p.pager)), (p.width(), p.height())

    def assert_stable(self, expected):
        p = self.panel
        self.assertEqual(self.geometry(), expected)
        QTest.qWait(10)
        self.assertEqual(self.geometry(), expected)
        self.assertLess(p.search.geometry().bottom(), p.tab_host.geometry().top())
        self.assertLess(p.tab_host.geometry().bottom(), p.scroll.geometry().top())
        self.assertLess(p.scroll.geometry().bottom(), p.pager.geometry().top())
        self.assertEqual(p.scroll.verticalScrollBarPolicy(), Qt.ScrollBarAlwaysOff)
        for cell in p._cells:
            self.assertGreaterEqual(cell.mapTo(p._root, cell.rect().topLeft()).y(), p.scroll.y())

    def test_click_small_large_empty_tabs_repeatedly(self):
        expected = self.geometry()
        for _ in range(8):
            for name in self.groups:
                QTest.mouseClick(self.panel._tab_buttons[name], Qt.LeftButton)
                self.assertEqual(self.panel._active_group, name)
                self.assert_stable(expected)

    def test_search_and_partial_pages_keep_headers_and_size(self):
        p = self.panel
        expected = self.geometry()
        p._choose_group("Many")
        for _ in range(6):
            p._change_page(1)
            self.assert_stable(expected)
        p._change_page(999)
        self.assert_stable(expected)
        self.assertEqual(p._items[-1].action_id, "Many72")
        for query in ("Action", "Action 7", "no such action", "", "Other", ""):
            p.search.setText(query)
            self.assert_stable(expected)

    def test_long_category_label_cannot_expand_panel(self):
        p = self.panel
        width = p.width()
        name = "A very long category name " * 8
        self.groups[name] = self.groups["Text"]
        p.apply_config()
        QTest.qWait(20)
        self.assertEqual(p.width(), width)
        self.assertLessEqual(p._tab_buttons[name].width(), p.tab_host.width())

if __name__ == "__main__":
    unittest.main()
