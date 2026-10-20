"""剪贴板动作（方案 2.6）：处理器管线 + 链内置步骤。

面板动作形态：clipboard.processor
    读剪贴板 -> 跑处理器 -> 写回剪贴板 -> (可选) 自动粘贴回原位置

事件链步骤形态（同一批处理器，方案 2.5）：
    clipboard.copy / clipboard.get / clipboard.set /
    所有 PROCESSORS（text.* table.* number.* case.* date.* data.*）

面板动作与链步骤共用同一份处理器实现，是方案里"每个剪贴板处理器
就是可复用的链步骤"的直接落地，不存在两套实现漂移。
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from ...win32 import clipboard as clip
from ...win32 import input as winput
from .. import focus
from ..log import get_logger
from .base import ActionContext, ActionResult, register
from .processors import get_processor

log = get_logger("clipboard")


def process_text(text: str, pid: str, args: Optional[Dict[str, Any]] = None) -> str:
    """纯函数入口：给面板动作与事件链共用。"""
    p = get_processor(pid)
    if p is None:
        raise KeyError("未注册的处理器：%s" % pid)
    return p.run(text, args or {})


@register("clipboard.processor", "剪贴板处理", "读剪贴板→处理→写回→可选自动粘贴", chain_id=None,
          params=[{"name": "processor", "type": "processor"},
                  {"name": "auto_paste", "type": "bool"}])
def _clipboard_processor(ctx: ActionContext, params) -> ActionResult:
    pid = str((params or {}).get("processor", ""))
    if not pid:
        return ActionResult.failure("未指定处理器")
    source = clip.get_text()
    if source is None:
        return ActionResult.failure("剪贴板中没有文本内容")
    try:
        out = process_text(source, pid, params.get("args"))
    except KeyError as e:
        return ActionResult.failure(str(e))
    except Exception as e:
        return ActionResult.failure("处理失败：%s" % e)

    auto_paste = bool((params or {}).get("auto_paste", False))
    snapshot = clip.snapshot()
    if not clip.set_text(out):
        return ActionResult.failure("结果写回剪贴板失败")
    if auto_paste:
        cfg = (ctx.store.section("text_input", {}) if ctx.store else {})
        focus.restore()
        time_ok = winput.paste_text(out, restore_snapshot=None, restore=False)
        if not time_ok:
            return ActionResult.failure("自动粘贴失败")
    ctx.message("已处理剪贴板（%s）" % pid)
    return ActionResult.success(output=out)


@register("clipboard.get", "读取剪贴板", "取剪贴板文本作为链步骤输出", chain_id="clipboard.get")
def _clip_get(ctx: ActionContext, params) -> ActionResult:
    t = clip.get_text()
    return ActionResult.success(output=t if t is not None else "")


@register("clipboard.set", "写入剪贴板", "把 input 写入剪贴板", chain_id="clipboard.set")
def _clip_set(ctx: ActionContext, params) -> ActionResult:
    ok = clip.set_text(str((params or {}).get("input", "")))
    return ActionResult.success() if ok else ActionResult.failure("写入剪贴板失败")


@register("clipboard.clear", "清空剪贴板", "", chain_id="clipboard.clear")
def _clip_clear(ctx: ActionContext, params) -> ActionResult:
    return ActionResult.success() if clip.set_text("") else ActionResult.failure("清空失败")


@register("clipboard.copy", "复制选区", "向目标窗口发 Ctrl+C 并回读结果", chain_id="clipboard.copy")
def _clip_copy(ctx: ActionContext, params) -> ActionResult:
    """事件链第一步的常用动作。

    复制后要等目标程序真的写完剪贴板，所以做短暂轮询而不是固定 sleep——
    固定 sleep 在慢盘/远程桌面上会漏读。
    """
    import time as _t
    from ...win32 import input as _inp
    before = clip.get_text()
    focus.restore()
    _inp.hotkey("ctrl+c")
    for _ in range(12):
        _t.sleep(0.02)
        now = clip.get_text()
        if now is not None and now != before:
            return ActionResult.success(output=now)
    return ActionResult.success("未检测到剪贴板变化（目标程序可能未选中内容）",
                                output=before or "")
