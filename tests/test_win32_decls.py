"""Win32 声明审计。

为什么单独写这个测试：ctypes 只在**真正调用**时校验 argtypes 元数，
所以元数写错会潜伏到运行时才炸，且一次只暴露一个。手工声明几十个
API 必然出错，必须把"声明正确性"变成可静态校验的东西。

本测试把每个 API 的真实参数个数固化成表，任何人改动 _decl 漏参数，
CI/pytest 立刻红，而不是等到用户点了一下面板才崩。
"""

from __future__ import annotations

import unittest

from easy_tools.win32 import api


#: 名称 -> 真实参数个数（依据 MSDN 签名逐个核对）
EXPECTED_ARITY = {
    # user32 —— 钩子与消息
    "SetWindowsHookExW": 4, "UnhookWindowsHookEx": 1, "CallNextHookEx": 4,
    "PostThreadMessageW": 4, "PostQuitMessage": 1, "GetMessageW": 4,
    "TranslateMessage": 1, "DispatchMessageW": 1,
    # user32 —— 窗口
    "GetForegroundWindow": 0, "GetWindowTextW": 3, "GetWindowTextLengthW": 1,
    "GetClassNameW": 3, "GetWindowThreadProcessId": 2, "IsWindowVisible": 1,
    "IsIconic": 1, "GetWindowRect": 2, "GetClientRect": 2, "ClientToScreen": 2,
    "ScreenToClient": 2, "SetForegroundWindow": 1, "ShowWindow": 2,
    "BringWindowToTop": 1, "GetWindowLongW": 2, "SetWindowLongW": 3,
    "GetWindow": 2, "GetAncestor": 2, "GetCursorPos": 1,
    # user32 —— 输入与屏幕
    "SendInput": 3, "GetSystemMetrics": 1, "MapVirtualKeyW": 2,
    "VkKeyScanW": 1, "GetKeyState": 1,
    # user32 —— 剪贴板
    "OpenClipboard": 1, "CloseClipboard": 0, "EmptyClipboard": 0,
    "GetClipboardData": 1, "SetClipboardData": 2,
    "IsClipboardFormatAvailable": 1, "CountClipboardFormats": 0,
    # user32 —— DC 与截图
    "GetDC": 1, "GetWindowDC": 1, "ReleaseDC": 2, "PrintWindow": 3,
    # kernel32
    "OpenProcess": 3, "QueryFullProcessImageNameW": 4, "CloseHandle": 1,
    "GlobalAlloc": 2, "GlobalLock": 1, "GlobalUnlock": 1, "GlobalSize": 1,
    "GetModuleHandleW": 1, "GetCurrentThreadId": 0,
    # gdi32
    "CreateCompatibleDC": 1, "CreateCompatibleBitmap": 3, "SelectObject": 2,
    "DeleteObject": 1, "DeleteDC": 1, "BitBlt": 9, "StretchBlt": 11,
    "SetStretchBltMode": 2,
}

#: 会真正破坏业务的常量错误（这些当年都踩过）
EXPECTED_CONSTS = {
    "SM_CXSCREEN": 0,
    "SM_CYSCREEN": 1,        # 不是 76，那是 SM_REMOTESESSION
    "CF_UNICODETEXT": 13,
    "GMEM_MOVEABLE": 0x0002,
    "WHEEL_DELTA": 120,
    "WM_MBUTTONDOWN": 0x0207,
    "WM_MBUTTONUP": 0x0208,
    "WH_MOUSE_LL": 14,
    "PW_RENDERFULLCONTENT": 2,
    "KEYEVENTF_UNICODE": 0x0004,
    "KEYEVENTF_KEYUP": 0x0002,
    "MOUSEEVENTF_WHEEL": 0x0800,
}


class TestWin32Declarations(unittest.TestCase):

    def test_every_declared_api_is_audited(self) -> None:
        """新增 API 必须同步补进 EXPECTED_ARITY，否则审计失效。"""
        declared = set()
        for lib in (api.user32, api.kernel32, api.gdi32):
            for name in EXPECTED_ARITY:
                if hasattr(lib, name):
                    declared.add(name)
        missing = set(EXPECTED_ARITY) - declared
        self.assertFalse(missing, "审计表里的 API 在 DLL 中不存在: %s" % sorted(missing))

    def test_argtypes_arity_matches(self) -> None:
        bad = []
        for name, want in EXPECTED_ARITY.items():
            for lib in (api.user32, api.kernel32, api.gdi32):
                fn = getattr(lib, name, None)
                if fn is None:
                    continue
                got = len(fn.argtypes)
                if got != want:
                    bad.append("%s 声明 %d 个参数，实际应为 %d 个" % (name, got, want))
        self.assertFalse(bad, "ctypes 声明元数错误:\n" + "\n".join(bad))

    def test_critical_constants(self) -> None:
        for name, want in EXPECTED_CONSTS.items():
            self.assertEqual(getattr(api, name), want, "常量 %s 取值错误" % name)

    def test_struct_sizes_are_64bit_correct(self) -> None:
        import ctypes
        import platform
        if platform.machine().lower() not in ("amd64", "x86_64"):
            self.skipTest("仅在 64 位下有意义")
        # INPUT = DWORD type + 4 字节对齐填充 + 32 字节 union
        self.assertEqual(ctypes.sizeof(api.INPUT), 40)
        # MSLLHOOKSTRUCT = POINT(8) + 3*DWORD(12) + ULONG_PTR(8, 对齐到 8) => 32
        self.assertEqual(ctypes.sizeof(api.MSLLHOOKSTRUCT), 32)
        self.assertEqual(ctypes.sizeof(api.KEYBDINPUT), 24)
        self.assertEqual(ctypes.sizeof(api.MOUSEINPUT), 32)

    def test_vk_table_covers_expected_keys(self) -> None:
        for k in ("ctrl", "alt", "shift", "enter", "tab", "esc", "space",
                  "f1", "f12", "0", "9", "a", "z", "left", "down"):
            self.assertIn(k, api.VK, "VK 表缺少 %s" % k)
        self.assertEqual(api.VK["ctrl"], 0x11)
        self.assertEqual(api.VK["enter"], 0x0D)
        self.assertEqual(api.VK["a"], 0x41)

    def test_module_handle_is_valid(self) -> None:
        """低级钩子的 hMod 必须非空，否则 SetWindowsHookEx 直接失败。"""
        self.assertTrue(api.get_module_handle())


if __name__ == "__main__":
    unittest.main()
