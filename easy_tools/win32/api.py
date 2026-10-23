"""Win32 API 的 ctypes 声明与常量。

只声明用得到的那部分——盲目全量抄 MSDN 会让一千行样板没人敢改。
所有 restype/argtypes 都显式声明：ctypes 默认按 c_int 处理返回值，
在 64 位 Windows 上会静默截断句柄，这是本文件存在的最大理由。
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)

LONG_PTR = ctypes.c_ssize_t
ULONG_PTR = ctypes.c_size_t
HHOOK = ctypes.c_void_p
HDC = wintypes.HDC
HBITMAP = ctypes.c_void_p
HGDIOBJ = ctypes.c_void_p
HANDLE = wintypes.HANDLE
HWND = wintypes.HWND

# ------------------------------------------------------------------ 钩子
WH_MOUSE_LL = 14
WH_KEYBOARD_LL = 13
HC_ACTION = 0

WM_MOUSEMOVE = 0x0200
WM_LBUTTONDOWN = 0x0201
WM_RBUTTONDOWN = 0x0204
WM_MBUTTONDOWN = 0x0207
WM_MBUTTONUP = 0x0208
WM_MOUSEWHEEL = 0x020A
WM_XBUTTONDOWN = 0x020B
WM_XBUTTONUP = 0x020C
XBUTTON1 = 0x0001          # WM_XBUTTONDOWN/UP 的 mouseData 高 16 位取值
XBUTTON2 = 0x0002
WM_SYSKEYDOWN = 0x0104
WM_SYSKEYUP = 0x0105
WM_KEYDOWN = 0x0101
WM_KEYUP = 0x0102
WM_QUIT = 0x0012

MK_SHIFT = 0x0004
MK_CONTROL = 0x0008
MK_MBUTTON = 0x0010

# ------------------------------------------------------------------ SendInput
INPUT_MOUSE = 0
INPUT_KEYBOARD = 1

KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
KEYEVENTF_SCANCODE = 0x0008

MOUSEEVENTF_MIDDLEDOWN = 0x0020
MOUSEEVENTF_MIDDLEUP = 0x0040
MOUSEEVENTF_WHEEL = 0x0800
MOUSEEVENTF_HWHEEL = 0x1000

WHEEL_DELTA = 120

#: 虚拟键码表：名称 -> VK。含修饰键、导航键、F1-F24、0-9、字母、常用符号。
VK = {
    "backspace": 0x08, "tab": 0x09, "enter": 0x0D, "return": 0x0D,
    "shift": 0x10, "ctrl": 0x11, "control": 0x11, "alt": 0x12, "menu": 0x12,
    "pause": 0x13, "capslock": 0x14, "esc": 0x1B, "escape": 0x1B,
    "space": 0x20, "pgup": 0x21, "pgdn": 0x22, "end": 0x23, "home": 0x24,
    "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28,
    "insert": 0x2D, "delete": 0x2E,
    "win": 0x5B, "lwin": 0x5B, "rwin": 0x5C, "apps": 0x5D,
    "numlock": 0x90, "scrolllock": 0x91,
    "lshift": 0xA0, "rshift": 0xA1, "lctrl": 0xA2, "rctrl": 0xA3,
    "lalt": 0xA4, "ralt": 0xA5,
    "semicolon": 0xBA, "equals": 0xBB, "comma": 0xBC, "minus": 0xBD,
    "period": 0xBE, "slash": 0xBF, "backtick": 0xC0,
    "lbracket": 0xDB, "backslash": 0xDC, "rbracket": 0xDD, "quote": 0xDE,
}
for _i in range(1, 25):
    VK["f%d" % _i] = 0x6F + _i
for _i in range(10):
    VK[str(_i)] = 0x30 + _i
for _ch in "abcdefghijklmnopqrstuvwxyz":
    VK[_ch] = ord(_ch.upper())
    VK[_ch.upper()] = ord(_ch)

MODIFIERS = ("ctrl", "alt", "shift", "win")

# ------------------------------------------------------------------ 窗口
GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_LAYERED = 0x00080000

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

SW_HIDE = 0
SW_SHOW = 5
SW_MINIMIZE = 6
SW_RESTORE = 9
SW_SHOWMAXIMIZED = 3

GA_ROOT = 2

# ------------------------------------------------------------------ 剪贴板 / GDI
CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002

SRCCOPY = 0x00CC0020
DIB_RGB_COLORS = 0
PW_RENDERFULLCONTENT = 0x00000002
HALFTONE = 4

SM_CXSCREEN = 0
SM_CYSCREEN = 1
#: 多显示器虚拟屏幕：用于把面板夹回可见区域
SM_XVIRTUALSCREEN = 76
SM_YVIRTUALSCREEN = 77
SM_CXVIRTUALSCREEN = 78
SM_CYVIRTUALSCREEN = 79


# ------------------------------------------------------------------ 结构体
class POINT(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


class RECT(ctypes.Structure):
    _fields_ = [("left", wintypes.LONG), ("top", wintypes.LONG),
                ("right", wintypes.LONG), ("bottom", wintypes.LONG)]


class MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", RECT),
                ("rcWork", RECT), ("dwFlags", wintypes.DWORD)]


class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("pt", POINT), ("mouseData", wintypes.DWORD),
                ("flags", wintypes.DWORD), ("time", wintypes.DWORD),
                ("dwExtraInfo", ULONG_PTR)]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                ("dwExtraInfo", ULONG_PTR)]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG),
                ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD),
                ("wParamH", wintypes.WORD)]


class _INPUTunion(ctypes.Union):
    _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUTunion)]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG),
                ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
                ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
                ("biClrImportant", wintypes.DWORD)]


class RGBQUAD(ctypes.Structure):
    _fields_ = [("rgbBlue", ctypes.c_ubyte), ("rgbGreen", ctypes.c_ubyte),
                ("rgbRed", ctypes.c_ubyte), ("rgbReserved", ctypes.c_ubyte)]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", RGBQUAD * 3)]


# ------------------------------------------------------------------ 签名声明
def _decl(lib, name, restype, *argtypes):
    fn = getattr(lib, name)
    fn.restype = restype
    fn.argtypes = list(argtypes)
    return fn


user32.SetWindowsHookExW = _decl(user32, "SetWindowsHookExW", HHOOK,
                                 ctypes.c_int, ctypes.c_void_p, wintypes.HINSTANCE, wintypes.DWORD)
user32.UnhookWindowsHookEx = _decl(user32, "UnhookWindowsHookEx", wintypes.BOOL, HHOOK)
user32.CallNextHookEx = _decl(user32, "CallNextHookEx", LONG_PTR, HHOOK,
                              ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
user32.PostThreadMessageW = _decl(user32, "PostThreadMessageW", wintypes.BOOL,
                                  wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
user32.PostQuitMessage = _decl(user32, "PostQuitMessage", None, ctypes.c_int)
user32.GetMessageW = _decl(user32, "GetMessageW", wintypes.BOOL,
                           ctypes.POINTER(wintypes.MSG), HWND, wintypes.UINT, wintypes.UINT)
user32.TranslateMessage = _decl(user32, "TranslateMessage", wintypes.BOOL, ctypes.POINTER(wintypes.MSG))
user32.DispatchMessageW = _decl(user32, "DispatchMessageW", LONG_PTR, ctypes.POINTER(wintypes.MSG))

user32.GetForegroundWindow = _decl(user32, "GetForegroundWindow", HWND)
user32.GetWindowTextW = _decl(user32, "GetWindowTextW", ctypes.c_int, HWND, wintypes.LPWSTR, ctypes.c_int)
user32.GetWindowTextLengthW = _decl(user32, "GetWindowTextLengthW", ctypes.c_int, HWND)
user32.GetClassNameW = _decl(user32, "GetClassNameW", ctypes.c_int, HWND, wintypes.LPWSTR, ctypes.c_int)
user32.GetWindowThreadProcessId = _decl(user32, "GetWindowThreadProcessId", wintypes.DWORD,
                                        HWND, ctypes.POINTER(wintypes.DWORD))
user32.IsWindowVisible = _decl(user32, "IsWindowVisible", wintypes.BOOL, HWND)
user32.IsIconic = _decl(user32, "IsIconic", wintypes.BOOL, HWND)
user32.GetWindowRect = _decl(user32, "GetWindowRect", wintypes.BOOL, HWND, ctypes.POINTER(RECT))
user32.GetClientRect = _decl(user32, "GetClientRect", wintypes.BOOL, HWND, ctypes.POINTER(RECT))
user32.ClientToScreen = _decl(user32, "ClientToScreen", wintypes.BOOL, HWND, ctypes.POINTER(POINT))
user32.ScreenToClient = _decl(user32, "ScreenToClient", wintypes.BOOL, HWND, ctypes.POINTER(POINT))
user32.SetForegroundWindow = _decl(user32, "SetForegroundWindow", wintypes.BOOL, HWND)
user32.ShowWindow = _decl(user32, "ShowWindow", wintypes.BOOL, HWND, ctypes.c_int)
user32.BringWindowToTop = _decl(user32, "BringWindowToTop", wintypes.BOOL, HWND)
user32.GetWindowLongW = _decl(user32, "GetWindowLongW", LONG_PTR, HWND, ctypes.c_int)
user32.SetWindowLongW = _decl(user32, "SetWindowLongW", LONG_PTR, HWND, ctypes.c_int, LONG_PTR)
user32.GetWindow = _decl(user32, "GetWindow", HWND, HWND, wintypes.UINT)
# GetWindow 的 uCmd 参数（WinUser.h GW_*）
GW_HWNDNEXT = 2
user32.GetAncestor = _decl(user32, "GetAncestor", HWND, HWND, wintypes.UINT)
user32.GetCursorPos = _decl(user32, "GetCursorPos", wintypes.BOOL, ctypes.POINTER(POINT))
user32.MonitorFromPoint = _decl(user32, "MonitorFromPoint", HANDLE, POINT, wintypes.DWORD)
user32.GetMonitorInfoW = _decl(user32, "GetMonitorInfoW", wintypes.BOOL,
                                HANDLE, ctypes.POINTER(MONITORINFO))
user32.GetAsyncKeyState = _decl(user32, "GetAsyncKeyState", ctypes.c_short, ctypes.c_int)
user32.SendInput = _decl(user32, "SendInput", wintypes.UINT,
                         wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int)
user32.GetSystemMetrics = _decl(user32, "GetSystemMetrics", ctypes.c_int, ctypes.c_int)
user32.MapVirtualKeyW = _decl(user32, "MapVirtualKeyW", wintypes.UINT, wintypes.UINT, wintypes.UINT)
user32.VkKeyScanW = _decl(user32, "VkKeyScanW", ctypes.c_short, wintypes.WCHAR)
user32.GetKeyState = _decl(user32, "GetKeyState", ctypes.c_short, ctypes.c_int)

user32.OpenClipboard = _decl(user32, "OpenClipboard", wintypes.BOOL, HWND)
user32.CloseClipboard = _decl(user32, "CloseClipboard", wintypes.BOOL)
user32.EmptyClipboard = _decl(user32, "EmptyClipboard", wintypes.BOOL)
user32.GetClipboardData = _decl(user32, "GetClipboardData", HANDLE, wintypes.UINT)
user32.SetClipboardData = _decl(user32, "SetClipboardData", HANDLE, wintypes.UINT, HANDLE)
user32.GetClipboardSequenceNumber = _decl(user32, 'GetClipboardSequenceNumber', wintypes.DWORD)
user32.IsClipboardFormatAvailable = _decl(user32, "IsClipboardFormatAvailable", wintypes.BOOL, wintypes.UINT)
user32.CountClipboardFormats = _decl(user32, "CountClipboardFormats", wintypes.UINT)

user32.GetDC = _decl(user32, "GetDC", HDC, HWND)
user32.GetWindowDC = _decl(user32, "GetWindowDC", HDC, HWND)
user32.ReleaseDC = _decl(user32, "ReleaseDC", ctypes.c_int, HWND, HDC)
user32.PrintWindow = _decl(user32, "PrintWindow", wintypes.BOOL, HWND, HDC, wintypes.UINT)

kernel32.OpenProcess = _decl(kernel32, "OpenProcess", HANDLE,
                                 wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
kernel32.QueryFullProcessImageNameW = _decl(kernel32, "QueryFullProcessImageNameW", wintypes.BOOL,
                                            HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                                            ctypes.POINTER(wintypes.DWORD))
kernel32.CloseHandle = _decl(kernel32, "CloseHandle", wintypes.BOOL, HANDLE)
kernel32.GlobalAlloc = _decl(kernel32, "GlobalAlloc", HANDLE, wintypes.UINT, ctypes.c_size_t)
kernel32.GlobalLock = _decl(kernel32, "GlobalLock", ctypes.c_void_p, HANDLE)
kernel32.GlobalUnlock = _decl(kernel32, "GlobalUnlock", wintypes.BOOL, HANDLE)
kernel32.GlobalSize = _decl(kernel32, "GlobalSize", ctypes.c_size_t, HANDLE)
kernel32.GetModuleHandleW = _decl(kernel32, "GetModuleHandleW", HANDLE, wintypes.LPCWSTR)
kernel32.GetCurrentThreadId = _decl(kernel32, "GetCurrentThreadId", wintypes.DWORD)
user32.GetSystemMetrics = _decl(user32, "GetSystemMetrics", ctypes.c_int, ctypes.c_int)

gdi32.CreateCompatibleDC = _decl(gdi32, "CreateCompatibleDC", HDC, HDC)
gdi32.CreateCompatibleBitmap = _decl(gdi32, "CreateCompatibleBitmap", HBITMAP, HDC,
                                     ctypes.c_int, ctypes.c_int)
gdi32.SelectObject = _decl(gdi32, "SelectObject", HGDIOBJ, HDC, HGDIOBJ)
gdi32.DeleteObject = _decl(gdi32, "DeleteObject", wintypes.BOOL, HGDIOBJ)
gdi32.DeleteDC = _decl(gdi32, "DeleteDC", wintypes.BOOL, HDC)
gdi32.BitBlt = _decl(gdi32, "BitBlt", wintypes.BOOL,
                      HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                      HDC, ctypes.c_int, ctypes.c_int, wintypes.DWORD)
gdi32.StretchBlt = _decl(gdi32, "StretchBlt", wintypes.BOOL,
                         HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                         HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                         wintypes.DWORD)
gdi32.SetStretchBltMode = _decl(gdi32, "SetStretchBltMode", ctypes.c_int, HDC, ctypes.c_int)


gdi32.GetDIBits = _decl(gdi32, "GetDIBits", ctypes.c_int, HDC, HBITMAP,
                              wintypes.UINT, wintypes.UINT,
                              ctypes.c_void_p, ctypes.c_void_p,
                              wintypes.UINT)   # 注意：GetDIBits 在 gdi32，不在 user32
def get_module_handle() -> HANDLE:
    """当前进程模块句柄：低级钩子的 hMod 需要它。"""
    return kernel32.GetModuleHandleW(None)


def last_error() -> int:
    return ctypes.get_last_error()
