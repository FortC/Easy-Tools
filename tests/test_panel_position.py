"""面板定位的 DPI 不变量锁：move() 必须收到逻辑像素（物理 ÷ dpr）。

这条结论被错误判断破坏过两次：一次是 DPI-unaware 探针拿到被虚拟化的
GetWindowRect 坐标，误判「move 直通原生像素」把除法删掉；一次是凭直觉
想改回去。_position 的注释引用了本文件——它必须真实存在并锁死行为，
否则下一个维护者还会踩同一个坑。

注意：面板宽高取自实例实测（布局有最小尺寸，resize 不一定生效），
预期值全部由同一实例的属性推导，不硬编码尺寸。
"""
import os
import sys
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtWidgets import QApplication

from easy_tools.core.app import AppService
from easy_tools.core.config_store import ConfigStore
from easy_tools.ui.panel import ActionPanel

_QAPP = None


def _fresh_panel():
    global _QAPP
    if _QAPP is None:
        _QAPP = QApplication.instance() or QApplication(sys.argv)
    ConfigStore.reset_instance()
    panel = ActionPanel(AppService(ConfigStore.instance()))
    # 与具体配置解耦：offset/flip 固定，避免环境差异造成漂移
    panel._offset = 16
    panel._flip = True
    return panel


class PanelPositionDpiTest(unittest.TestCase):
    WORK_AREA = (0, 0, 3840, 2160)  # 当前显示器可用工作区（物理像素）

    def _run(self, x, y, dpr, work_area=None):
        p = _fresh_panel()
        with (mock.patch.object(p, "_dpr_at", return_value=dpr),
              mock.patch.object(p, "adjustSize"),
              mock.patch.object(p, "_work_area_at",
                                return_value=work_area or self.WORK_AREA),
              mock.patch.object(p, "move") as mv):
            p._position(x, y)
        # move 被 mock，几何在运行前后不变，宽高可直接实测
        return mv.call_args[0], p

    def test_高分屏_move_收到逻辑像素(self):
        dpr = 1.25
        args, p = self._run(300, 300, dpr)
        off = int(p._offset * dpr)
        self.assertEqual(args, (round((300 + off) / dpr),
                                round((300 + off) / dpr)))
        # 反向保证：断言真的在验「除过 dpr」，不是恒等式
        self.assertLess(args[0], 300 + off)

    def test_贴边翻转与夹取后仍是逻辑像素(self):
        dpr = 1.25
        args, p = self._run(3800, 2100, dpr)
        off = int(p._offset * dpr)
        w = int(p.width() * dpr)
        h = int(p.height() * dpr)
        px = 3800 - off - w         # 右侧放不下 → 翻到左侧
        py = 2100 - off - h         # 下方放不下 → 翻到上方
        self.assertEqual(args, (round(px / dpr), round(py / dpr)))

    def test_dpr为1时恒等(self):
        args, _ = self._run(300, 300, 1.0)
        self.assertEqual(args, (316, 316))  # 16×1=16

    def test_底部工作区边界触发时避开任务栏(self):
        dpr = 1.25
        work_area = (0, 0, 1920, 1040)  # 显示器下方任务栏占用 40 个物理像素
        x, y = 960, 1032
        args, p = self._run(x, y, dpr, work_area)
        h = int(p.height() * dpr)
        top = round(args[1] * dpr)
        self.assertLessEqual(top + h, work_area[1] + work_area[3] - 2)
        self.assertLess(top, y)  # 底部触发应向上展开

    def test_左上角触发时向右下展开(self):
        dpr = 1.0
        args, _ = self._run(2, 2, dpr)
        self.assertGreaterEqual(args[0], 2)
        self.assertGreaterEqual(args[1], 2)


if __name__ == "__main__":
    unittest.main()
