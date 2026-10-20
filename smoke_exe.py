# -*- coding: utf-8 -*-
"""打包产物冒烟测试 —— **构建的最后一道关卡**。

为什么需要它
------------
历史上有两个 bug 只在打包产物里出现，开发态与 86 个单测全绿：

1. defaults.json 用了 app_root() 定位只读资源，打包后落到 exe 目录，
   首次启动直接 FileNotFoundError 崩溃；
2. get_logger() 在 import 阶段抢先 configure(None)，文件 handler 静默装不上，
   程序"运行正常"但一个日志字节都不写。

共同根因不是某个写错的路径，而是**验证阶梯缺了"产物能不能启动"这一级**。
把这个动作固化成脚本并接进 build.py，"靠记得跑一遍"就不再是保障。

它验什么（都是外部可观测的事实，不看内部状态）
------------------------------------------------
- 进程存活 N 秒、不弹异常框
- %APPDATA%\\Easy-Tools\\easy_tools.log 真的被创建且含启动与钩子状态
- %APPDATA%\\Easy-Tools\\config.json 真的被生成
- 第二个实例会主动退出（单实例生效），不会开出第二个托盘
- 杀掉实例后本地管道被释放，能再次启动（退出不留残留）

注意：运行期间会短暂安装全局鼠标钩子，这是被测程序的正常行为；
脚本无论成败都会在 finally 里清理进程。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
EXE = ROOT / "dist" / "Easy-Tools" / "Easy-Tools.exe"
APPDATA_DIR = Path(os.environ.get("APPDATA", "")) / "Easy-Tools"
LOG_FILE = APPDATA_DIR / "easy_tools.log"
CONFIG_FILE = APPDATA_DIR / "config.json"


def _kill_all() -> None:
    subprocess.run(["taskkill", "/F", "/IM", "Easy-Tools.exe"],
                   capture_output=True)
    time.sleep(0.5)


def _alive() -> bool:
    r = subprocess.run(["tasklist", "/FI", "IMAGENAME eq Easy-Tools.exe"],
                       capture_output=True, text=True, errors="replace")
    return "Easy-Tools.exe" in (r.stdout or "")


def _clean_slate() -> None:
    if APPDATA_DIR.exists():
        shutil.rmtree(APPDATA_DIR, ignore_errors=True)


def _launch() -> subprocess.Popen:
    return subprocess.Popen([str(EXE)], cwd=str(EXE.parent))


def _click_at(x, y) -> None:
    """把光标移到 (x,y)（物理像素）再注入中键，最后还原原位置。"""
    import ctypes
    sys.path.insert(0, str(ROOT))
    _make_probe_dpi_aware()
    from easy_tools.win32 import api
    from easy_tools.win32 import window as w32

    origin = w32.cursor_pos()
    u = api.user32
    u.SetCursorPos(int(x), int(y))
    time.sleep(0.45)
    _inject_middle_click()
    time.sleep(0.25)
    u.SetCursorPos(int(origin[0]), int(origin[1]))


def _measure_position_at(x, y, pid=None):
    """把光标移到指定物理坐标点击，返回 (面板矩形, 点到面板距离)。

    弹出后顺手再注入一次中键把面板关掉，保证每个测量点开始时面板
    处于关闭态，多个测量点之间互不串扰。
    """
    # 面板若已开着（比如上一个检查的注入把它打开了），先关掉，
    # 否则本点第一针注入会变成切换关闭，量不到弹出位置。
    if _panel_native_rect(pid):
        _inject_middle_click()
        time.sleep(0.6)
    _click_at(x, y)
    time.sleep(1.2)
    rect = _panel_native_rect(pid)
    if rect:
        _inject_middle_click()          # 再次中键 = 关闭
        time.sleep(0.5)
        return rect, _point_rect_distance((x, y), rect)
    return None, None

def _primary_screen_size():
    """主屏物理像素（探针 DPI-aware 后 GetSystemMetrics 不再被虚拟化）。"""
    import ctypes
    _make_probe_dpi_aware()
    u = ctypes.windll.user32
    return int(u.GetSystemMetrics(0)), int(u.GetSystemMetrics(1))

def _second_monitor_center():
    """第二块屏的中心物理坐标；只接了一块屏时返回 None。"""
    import ctypes
    sys.path.insert(0, str(ROOT))
    _make_probe_dpi_aware()
    from easy_tools.win32 import api

    user32 = api.user32
    mons = []
    MONITORENUMPROC = ctypes.WINFUNCTYPE(
        ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)

    class MONITORINFO(ctypes.Structure):
        _fields_ = [("cbSize", ctypes.c_ulong),
                    ("rcMonitor", api.RECT),
                    ("rcWork", api.RECT),
                    ("dwFlags", ctypes.c_ulong)]

    def cb(hmon, _hdc, _lparam):
        mi = MONITORINFO()
        mi.cbSize = ctypes.sizeof(MONITORINFO)
        if user32.GetMonitorInfoW(hmon, ctypes.byref(mi)):
            r = mi.rcMonitor
            mons.append((r.left, r.top, r.right, r.bottom))
        return True

    user32.EnumDisplayMonitors(None, None, MONITORENUMPROC(cb), None)
    others = [m for m in mons if not (m[0] == 0 and m[1] == 0)]
    if not others:
        return None
    l, t, r, b = others[0]
    return (l + r) // 2, (t + b) // 2


def _inject_middle_click() -> None:
    """向系统注入一次真实中键按下/释放（SendInput）。

    低级钩子能看到注入事件；swallow 模式会把它吞掉，不会惊扰前台程序。
    这是钩子链路在产物里端到端可用的硬证据：曾出现中键在产物里完全
    失灵而 86 个单测全绿，正是因为没人验过这条链。
    """
    import ctypes
    sys.path.insert(0, str(ROOT))
    from easy_tools.win32 import api
    arr = (api.INPUT * 2)()
    arr[0].type = api.INPUT_MOUSE
    arr[0].u.mi = api.MOUSEINPUT(0, 0, 0, 0x0020, 0, ctypes.c_ulonglong(0))
    arr[1].type = api.INPUT_MOUSE
    arr[1].u.mi = api.MOUSEINPUT(0, 0, 0, 0x0040, 0, ctypes.c_ulonglong(0))
    n = ctypes.sizeof(api.INPUT)
    api.user32.SendInput(1, ctypes.byref(arr[0]), n)
    time.sleep(0.06)
    api.user32.SendInput(1, ctypes.byref(arr[1]), n)


def _make_probe_dpi_aware() -> None:
    """让探针进程自己就是 DPI 感知的。

    必须做：探针若 DPI-unaware，GetWindowRect/GetCursorPos 返回的都是被
    系统虚拟化过的坐标，和被测程序（Qt 启动即 Per-Monitor 感知）说的
    不是一套坐标系——两边错得一样多，比值反而"对"，断言变成假绿灯。
    （位置 bug 曾因此躲过关卡。）
    """
    import ctypes
    try:
        # PER_MONITOR_AWARE_V2 = -4
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def _point_rect_distance(pt, rect) -> float:
    """点到矩形的最短距离（像素）。用于断言"面板贴着光标"。"""
    x, y = pt
    left, top, right, bottom = rect
    dx = max(left - x, 0, x - right)
    dy = max(top - y, 0, y - bottom)
    return (dx * dx + dy * dy) ** 0.5


def _panel_native_rect(pid=None):
    """找 Easy-Tools 面板窗口的物理矩形（GetWindowRect）。找不到返回 None。

    必须按进程过滤：桌面上微信/WeMail/Radeon 等同为 Qt 界面库，窗口类名
    同样形如 Qt*QWindowTool*（实测 Qt51514/Qt51517/Qt690 前缀都在），
    不限进程会捡到别人家的窗口，位置断言必然误报。
    """
    import ctypes
    sys.path.insert(0, str(ROOT))
    _make_probe_dpi_aware()
    from easy_tools.win32 import api

    # Qt 会把应用名追加到窗口标题后，所以按类名前缀匹配最稳：
    # 工具窗口类名形如 Qt6120QWindowToolSaveBits（6120 = PySide6 版本号）
    WNDENUMPROC = ctypes.WINFUNCTYPE(
        ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    result = []

    def cb(hwnd, _lparam):
        buf = ctypes.create_unicode_buffer(256)
        api.user32.GetClassNameW(hwnd, buf, 256)
        if not (buf.value.startswith("Qt") and "QWindowTool" in buf.value):
            return True
        if not api.user32.IsWindowVisible(hwnd):
            return True
        if pid is not None:
            owner = ctypes.c_ulong(0)
            api.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
            if owner.value != pid:
                return True
        r = api.RECT()
        if api.user32.GetWindowRect(hwnd, ctypes.byref(r)):
            result.append((r.left, r.top, r.right, r.bottom))
        return True

    api.user32.EnumWindows(WNDENUMPROC(cb), None)
    if not result:
        return None
    # 本进程可能残留多个工具窗口，面板是其中最大的一块
    return max(result, key=lambda rc: (rc[2] - rc[0]) * (rc[3] - rc[1]))


def _last_tap_xy(text: str):
    """从日志增量里取最后一条中键短按的坐标。"""
    import re
    ms = re.findall(r"中键短按 @ \((-?\d+),(-?\d+)\)", text)
    if not ms:
        return None
    a, b = ms[-1]
    return int(a), int(b)


def _log_text() -> str:
    if LOG_FILE.is_file():
        return LOG_FILE.read_text(encoding="utf-8", errors="replace")
    return ""


def check(results: list, name: str, ok: bool, detail: str = "") -> bool:
    results.append((name, ok, detail))
    return ok


def run() -> int:
    if not EXE.is_file():
        print("找不到 %s，请先运行 python build.py" % EXE)
        return 2

    results: list = []
    _kill_all()
    _clean_slate()

    try:
        proc = _launch()
        time.sleep(6.0)

        check(results, "启动后进程存活", proc.poll() is None,
              "pid=%s" % proc.pid)
        check(results, "未崩溃（无异常对话框）", proc.poll() is None,
              "returncode=%s" % proc.poll())

        check(results, "日志文件已创建", LOG_FILE.is_file(), str(LOG_FILE))
        log_text = ""
        if LOG_FILE.is_file():
            log_text = LOG_FILE.read_text(encoding="utf-8", errors="replace")
        check(results, "日志含启动记录", "启动" in log_text,
              "前 3 行: %s" % " / ".join(log_text.splitlines()[:3]))
        check(results, "日志含钩子状态（证明钩子真装了）",
              "钩子状态" in log_text, "")
        check(results, "配置文件已生成", CONFIG_FILE.is_file(), str(CONFIG_FILE))

        # --- 单实例 ---
        second = _launch()
        try:
            second.wait(timeout=8)
            check(results, "第二实例自行退出（单实例生效）",
                  second.returncode is not None and second.returncode == 0,
                  "returncode=%s" % second.returncode)
        except subprocess.TimeoutExpired:
            check(results, "第二实例自行退出（单实例生效）", False,
                  "8 秒后仍在运行，说明单实例保护失效")

        r = subprocess.run(["tasklist", "/FI", "IMAGENAME eq Easy-Tools.exe"],
                           capture_output=True, text=True, errors="replace")
        n = (r.stdout or "").count("Easy-Tools.exe")
        check(results, "进程数量 = 1", n == 1, "实际 %d 个" % n)

        # --- 退出后可再次启动（不残留管道） ---
        _kill_all()
        time.sleep(1.0)
        proc2 = _launch()
        time.sleep(4.0)
        check(results, "杀掉后能重新启动（管道已释放）", proc2.poll() is None,
              "")

        # 中键交互端到端：注入-弹出（位置在光标旁），再注入-切换关闭
        base = _log_text()
        _inject_middle_click()
        time.sleep(1.5)
        tail = _log_text()[len(base):]
        found_tap = "中键短按" in tail
        check(results, "中键注入 -> 短按被识别", found_tap,
              "日志出现『中键短按』" if found_tap else "日志未出现『中键短按』")

        # 弹出位置：在确定性坐标点逐一测量，不再依赖"注入时光标恰好在哪"。
        # 左上/右下各测一点，检测到第二块屏就加测其中心（多屏坐标可为负）。
        # 不变量：面板必须紧贴光标（最近边距小），不预设朝向——光标靠屏幕
        # 下沿时面板合法地翻到上方，固定象限断言会误报。
        pts = [(300, 300)]
        cx, cy = _primary_screen_size()
        if cx and cy:
            pts.append((cx - 120, cy - 120))
        second = _second_monitor_center()
        if second:
            pts.append(second)
        for (px, py) in pts:
            rect, dist = _measure_position_at(px, py, proc2.pid)
            ok = rect is not None and dist is not None and dist <= 60
            if rect is None:
                detail = "面板窗口未找到"
            elif dist is None:
                detail = "距离计算失败"
            else:
                detail = "最近边距 %dpx（阈值 60px）" % dist
            check(results, "弹出位置紧贴光标 @(%d,%d)" % (px, py), ok, detail)

        # 切换关闭：上面的循环结束时面板必为关闭态，先开再关，断言才成立。
        _inject_middle_click()
        time.sleep(1.2)
        base2 = _log_text()
        _inject_middle_click()
        time.sleep(1.2)
        closed = "面板已关闭" in _log_text()[len(base2):]
        check(results, "再次中键 -> 面板切换关闭", closed,
              "日志出现『面板已关闭』" if closed else "日志未出现『面板已关闭』")
        proc2.kill()
    finally:
        _kill_all()

    print("=" * 72)
    print("打包产物冒烟测试")
    print("=" * 72)
    failed = 0
    for name, ok, detail in results:
        mark = "PASS" if ok else "FAIL"
        print("  [%s] %-32s %s" % (mark, name, detail))
        if not ok:
            failed += 1
    print("-" * 72)
    print("%d 项，通过 %d，失败 %d" % (len(results), len(results) - failed, failed))
    if failed:
        print("构建视为失败：产物没有通过冒烟测试。")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(run())
