"""M10 六项轮：紧凑默认与迁移 / 通知开关 / 截图动作与工具函数。"""

import sys
import unittest

sys.path.insert(0, r"E:\clb-pjs\Easy-Tools")

from easy_tools.config.schema import load_defaults, normalize
from easy_tools.core.actions.base import ActionContext, get_action, run_action
from easy_tools.ui import labels
from easy_tools.win32.clipboard import build_dib
from easy_tools.win32.gdi import Capture, crop_capture


class TestPanelCompactDefaults(unittest.TestCase):
    def test_默认配置就是紧凑五列(self):
        p = load_defaults()["panel"]
        self.assertEqual((p["columns"], p["cell_width"], p["cell_height"]), (5, 88, 72))

    def test_通知开关默认开(self):
        self.assertTrue(load_defaults()["trigger"]["notify_enabled"])

    def test_旧默认面板参数自动升级(self):
        cfg = {"panel": {"columns": 6, "cell_width": 92, "cell_height": 78}}
        p = normalize(cfg)["panel"]
        self.assertEqual((p["columns"], p["cell_width"], p["cell_height"]), (5, 88, 72))

    def test_用户自定义列数保留(self):
        # 只有"三件全是旧默认值"才整体升级；用户改过任一项就原样保留
        cfg = {"panel": {"columns": 9, "cell_width": 92, "cell_height": 78}}
        p = normalize(cfg)["panel"]
        self.assertEqual(p["columns"], 9)
        self.assertEqual((p["cell_width"], p["cell_height"]), (92, 78))


class TestScreenshotAction(unittest.TestCase):
    def test_老配置自动补截图动作且不重复(self):
        cfg1 = normalize({"actions": []})
        caps = [a for a in cfg1["actions"] if a.get("kind") == "screen.capture"]
        self.assertEqual(len(caps), 1)
        cfg2 = normalize({"actions": [dict(a) for a in caps]})
        caps2 = [a for a in cfg2["actions"] if a.get("kind") == "screen.capture"]
        self.assertEqual(len(caps2), 1)

    def test_注册表里能查到截图kind(self):
        self.assertIsNotNone(get_action("screen.capture"))

    def test_截图提问动作会请求显示截图覆盖层(self):
        calls = []
        result = run_action(
            "screen.capture",
            ActionContext(start_capture=lambda *args: calls.append(args)),
            {"auto_ocr": True, "copy_image": True, "ask_question": True},
        )
        self.assertTrue(result.ok)
        self.assertEqual(calls, [(True, True, True)])

    def test_中文标签(self):
        self.assertEqual(labels.kind_label("screen.capture"), "截图（框选）")
        text = labels.describe_params(
            {"kind": "screen.capture",
             "params": {"auto_ocr": True, "copy_image": True}}, None)
        self.assertIn("OCR", text)


class TestCaptureHelpers(unittest.TestCase):
    def test_裁剪子矩形(self):
        w, h = 4, 3
        bgra = bytes(range(w * h * 4))
        cap = Capture(width=w, height=h, bgra=bgra)
        crop = crop_capture(cap, 1, 1, 2, 2)
        self.assertEqual((crop.width, crop.height), (2, 2))
        base = (1 * w + 1) * 4
        self.assertEqual(crop.bgra[0:8], bgra[base:base + 8])

    def test_dib头(self):
        data = build_dib(b"\x00" * 16, 2, 2)
        self.assertEqual(len(data), 40 + 16)
        self.assertEqual(data[:4], (40).to_bytes(4, "little"))
        self.assertEqual(data[4:8], (2).to_bytes(4, "little"))
        self.assertEqual(data[8:12], (-2).to_bytes(4, "little", signed=True))


if __name__ == "__main__":
    unittest.main()
