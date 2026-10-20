"""目标窗口记忆（focus.remember）的行为测试。

背景 bug：动作点击时才调用 focus.remember()，此刻面板已经抢到焦点，
记住的"目标窗口"就是面板自己，文本注入找不到目标窗口。
本测试锁住修复后的两个不变量：
1. 前台是本程序窗口时，remember() 沿 Z 序找回真正的目标，而不是放弃；
2. 前台是外部窗口时，原样记住它。
"""

from __future__ import annotations

import unittest
from unittest import mock

from easy_tools.core import focus
from easy_tools.win32.window import WindowInfo


def _info(hwnd: int, process: str = "app.exe") -> WindowInfo:
    return WindowInfo(hwnd=hwnd, title="W%d" % hwnd, process=process,
                      process_path="C:\\x\\" + process, class_name="Cls%d" % hwnd,
                      visible=True, minimized=False)


class FocusRememberTest(unittest.TestCase):
    def setUp(self) -> None:
        focus.clear()

    def tearDown(self) -> None:
        focus.clear()

    def test_外部窗口被原样记住(self) -> None:
        with mock.patch("easy_tools.core.focus.w32.get_foreground",
                        return_value=_info(1234)):
            self.assertEqual(focus.remember(), 1234)
            self.assertEqual(focus.target_hwnd(), 1234)

    def test_前台是自己窗口时沿Z序回找目标(self) -> None:
        """面板已经抢到焦点时，不能返回 None 放弃，必须找回下一层的真目标。"""
        with mock.patch("easy_tools.core.focus.w32.get_foreground",
                        return_value=_info(777, "Easy-Tools.exe"),
        ), mock.patch("easy_tools.core.focus.w32.is_our_window",
                       return_value=True,
        ), mock.patch("easy_tools.core.focus.w32.next_visible_window",
                       return_value=4321) as nxt:
            self.assertEqual(focus.remember(), 4321)
            nxt.assert_called_once_with(777)
            self.assertEqual(focus.target_hwnd(), 4321)

    def test_自己窗口且Z序后面没有别人时返回None(self) -> None:
        """无目标可找时如实返回 None，而不是把面板自己当目标。"""
        with mock.patch("easy_tools.core.focus.w32.get_foreground",
                        return_value=_info(777, "Easy-Tools.exe"),
        ), mock.patch("easy_tools.core.focus.w32.is_our_window",
                       return_value=True,
        ), mock.patch("easy_tools.core.focus.w32.next_visible_window",
                       return_value=None):
            self.assertIsNone(focus.remember())
            self.assertIsNone(focus.target_hwnd())

    def test_登记窗口后记住会跳过它(self) -> None:
        """register_our_window 登记的窗口也算自己的，不会被当成输入目标。"""
        focus.register_our_window(999)
        self.addCleanup(focus.unregister_our_window, 999)
        with mock.patch("easy_tools.core.focus.w32.get_foreground",
                        return_value=_info(999, "other.exe"),
        ), mock.patch("easy_tools.core.focus.w32.next_visible_window",
                       return_value=555):
            self.assertEqual(focus.remember(), 555)

    def test_无前台窗口时返回None(self) -> None:
        with mock.patch("easy_tools.core.focus.w32.get_foreground",
                        return_value=WindowInfo(hwnd=0, title="", process="",
                                                process_path="", class_name="",
                                                visible=False, minimized=False)):
            self.assertIsNone(focus.remember())


if __name__ == "__main__":
    unittest.main()
