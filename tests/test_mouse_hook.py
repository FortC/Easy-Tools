"""中键手势分类器测试。

存在理由：tap_max_ms=200 / drag_threshold_px=8 曾把大量真人点击判成"非短按"，
而 swallow 模式下这些点击已被吞掉，用户看到的是"按了完全没反应"。
机器注入式点击（0ms/0px）永远测不出这个问题——所以这里的用例
全部按真人尺度构造：按住 150~300ms、带几像素手抖。
"""

from __future__ import annotations

import time
import unittest

from easy_tools.win32.mouse import _TapClassifier


def human_click(clf, x=1000, y=1000, hold_s=0.15, drift_px=0):
    """按真人节奏模拟一次按下 -> (漂移) -> 释放。"""
    clf.press(x, y)
    if drift_px:
        for i in range(1, drift_px + 1):
            clf.move(x + i, y)
    time.sleep(hold_s)
    return clf.release(x + drift_px, y)


class TestHumanScaleTaps(unittest.TestCase):
    def setUp(self):
        self.clf = _TapClassifier()          # 用类默认值 = 产物实际默认值

    def test_quick_clean_click_is_tap(self):
        is_tap, _, _ = human_click(self.clf, hold_s=0.05)
        self.assertTrue(is_tap)

    def test_typical_human_click_with_jitter_is_tap(self):
        """手抖 10px + 按住 150ms —— 旧默认(200ms/8px)会判成非短按。"""
        is_tap, _, dur = human_click(self.clf, hold_s=0.15, drift_px=10)
        self.assertTrue(is_tap, "带 10px 手抖的正常点击应判为短按（实测 %.0fms）" % dur)

    def test_slow_press_is_still_tap(self):
        """按住 300ms 才松开（真人慢按）不应被吞掉。"""
        is_tap, _, _ = human_click(self.clf, hold_s=0.30)
        self.assertTrue(is_tap)

    def test_real_drag_is_not_tap(self):
        """纵向拖动 80px 是明确的拖动手势，绝不能判成短按。"""
        self.clf.press(1000, 1000)
        for i in range(0, 81, 4):
            self.clf.move(1000, 1000 + i)
            time.sleep(0.005)
        is_tap, flush, _ = self.clf.release(1000, 1080)
        self.assertFalse(is_tap)
        self.assertGreater(flush, 0, "纵向拖动应产生滚动 tick")

    def test_horizontal_drag_is_not_tap(self):
        """横向拖动同样不算短按；滚动 tick 只跟纵向位移有关（设计如此）。"""
        self.clf.press(1000, 1000)
        for i in range(0, 81, 4):
            self.clf.move(1000 + i, 1000)
        is_tap, flush, _ = self.clf.release(1080, 1000)
        self.assertFalse(is_tap, "大幅横向移动是拖动，不是点击")
        self.assertEqual(flush, 0, "横向位移不应换算成纵向滚动 tick")

    def test_old_default_thresholds_were_too_tight(self):
        """证明历史默认值(200ms/8px)确实会吞掉真人点击。

        这是护栏：将来有人把默认值改回去时，这条测试会解释为什么不能改。
        """
        tight = _TapClassifier(tap_max_ms=200, drag_threshold_px=8)
        is_tap, _, _ = human_click(tight, hold_s=0.15, drift_px=10)
        self.assertFalse(is_tap, "旧阈值下该点击会被吞掉且无任何反应")


if __name__ == "__main__":
    unittest.main()
