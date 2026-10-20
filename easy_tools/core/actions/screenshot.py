"""截图类动作：微信式框选 + 可选 OCR。

选区覆盖层是 UI 能力；core 只通过 ActionContext.start_capture 回调请求
上层发起截图（结果经 notify 回报）——与 system.settings 同一分层纪律。
"""

from __future__ import annotations

from ..log import get_logger
from .base import ActionContext, ActionResult, register

log = get_logger("screenshot")


@register("screen.capture", "截图（框选）",
          "微信式框选截图：自动复制图片，可选 OCR 出文字", chain_id=None,
          params=[{"name": "auto_ocr", "type": "bool"},
                  {"name": "copy_image", "type": "bool"},
                  {"name": "ask_question", "type": "bool"}])
def _capture(ctx: ActionContext, params) -> ActionResult:
    p = params or {}
    if not getattr(ctx, "start_capture", None):
        return ActionResult.failure("未接入截图界面（无 UI 环境）")
    ctx.start_capture(bool(p.get("auto_ocr", True)), bool(p.get("copy_image", True)),
                      bool(p.get("ask_question", True)))
    return ActionResult.success("已进入截图模式")
