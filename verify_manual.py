"""人工验收脚本（方案 §6 里必须用真实 GUI 才能验证的几条）。

自动化能证明"控件画得出来、引擎算得对"，但证明不了
"文本真的落在记事本光标处""切到 Word 面板真的换了分组"——
这些必须在真实桌面、真实前台窗口下点一次。

用法：
    python main.py                 # 先把程序跑起来
    另开一个命令行：
    python verify_manual.py check  # 打印当前状态，帮你逐条核对
    python verify_manual.py open-notepad

本脚本**不**替你点鼠标，它只读状态并把结论打印出来。
为什么不做全自动点击：自动点击会抢你的鼠标和焦点，
在你自己用电脑时跑这种脚本是有害的。
"""

from __future__ import annotations

import sys

from easy_tools.core.app import AppService
from easy_tools.win32 import clipboard as clip
from easy_tools.win32 import window as w32


def show_state() -> None:
    svc = AppService.instance()
    svc.refresh_context()
    st = svc.resolver.current()
    print("=" * 68)
    print("前台窗口：%s | %s" % (st.window.process or "-", st.window.title or "-"))
    print("钩子状态：%s" % svc.status_text())
    print("OCR 后端：%s" % svc.ocr.status())
    print("-" * 68)
    print("命中的特定操作区规则：")
    groups = svc.context_groups()
    if not groups:
        print("  （无）—— 若当前窗口本应有专属动作，去配置中心检查规则里的进程名")
    for name, items in groups.items():
        print("  ▸ %s → %s" % (name, ", ".join(i.name for i in items)))
    print("-" * 68)
    print("验收对照：")
    print("  [1] 中键面板 + 搜索/分组      → 在任意位置按中键，面板出现且可搜索")
    print("  [2] 两种文本输入模式          → 见下方 paste / type 两项")
    print("  [3] 切到 Word/Excel 自动换组  → 上方「命中的特定操作区规则」应非空")
    print("  [4] Excel 框选 → 去空行+转置  → 面板点「整理框选表格」，表格应被回填")
    print("  [5] 事件链 复制→清理→粘贴      → 面板点「复制→清理→粘贴」，内容应被替换")
    print("  [6] 设置即时生效              → 配置中心改列数，保存后面板立刻变")
    print("=" * 68)


def check_text() -> None:
    print("当前剪贴板文本：%r" % (clip.get_text(),))


def main(argv) -> int:
    cmd = argv[1] if len(argv) > 1 else "check"
    if cmd == "check":
        show_state()
    elif cmd == "clip":
        check_text()
    elif cmd == "open-notepad":
        import subprocess
        subprocess.Popen(["notepad.exe"])
        print("已打开记事本，请在 1 秒内把光标点到编辑区，然后回本窗口按回车结束")
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
