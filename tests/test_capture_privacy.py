"""Window capture privacy and toast policy tests."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QDialog, QWidget

from easy_tools.core.config_store import ConfigStore
from easy_tools.core.privacy import notifications_allowed
from easy_tools.ui.capture_privacy import CapturePrivacyFilter


_app = QApplication.instance() or QApplication([])


class TestPrivacySettings(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = ConfigStore(Path(self.tmp.name) / "config.json")
        self.store.reload()

    def tearDown(self):
        self.tmp.cleanup()

    def test_old_config_gets_capture_privacy_default(self):
        self.assertTrue(self.store.section("privacy")["exclude_from_capture"])

    def test_window_affinity_applies_on_show_and_setting_changes(self):
        calls = []
        privacy = CapturePrivacyFilter(
            _app, self.store, setter=lambda hwnd, enabled: calls.append((hwnd, enabled)) or True)
        window = QWidget()
        result_dialog = QDialog()
        try:
            window.show()
            result_dialog.show()
            _app.processEvents()
            self.assertGreaterEqual(sum(enabled for _hwnd, enabled in calls), 2)

            self.store.patch("privacy", {"exclude_from_capture": False})
            _app.processEvents()
            self.assertFalse(calls[-1][1])

            self.store.patch("privacy", {"exclude_from_capture": True})
            _app.processEvents()
            self.assertTrue(calls[-1][1])
        finally:
            window.close()
            result_dialog.close()
            privacy.close()

    def test_deferred_privacy_callback_ignores_destroyed_widget(self):
        import shiboken6

        calls = []
        privacy = CapturePrivacyFilter(
            _app, self.store, setter=lambda hwnd, enabled: calls.append((hwnd, enabled)) or True)
        window = QWidget()
        try:
            privacy._schedule_widget(window)
            shiboken6.delete(window)
            _app.processEvents()
            self.assertEqual(calls, [])
        finally:
            privacy.close()

    def test_notifications_are_suppressed_by_user_setting_and_privacy(self):
        cfg = self.store.get()
        cfg["privacy"]["exclude_from_capture"] = False
        self.assertTrue(notifications_allowed(cfg))
        cfg["trigger"]["notify_enabled"] = False
        self.assertFalse(notifications_allowed(cfg))
        cfg["trigger"]["notify_enabled"] = True
        cfg["privacy"]["exclude_from_capture"] = True
        self.assertFalse(notifications_allowed(cfg))


if __name__ == "__main__":
    unittest.main()
