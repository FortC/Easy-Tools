"""Question actions that delegate UI work through injected callbacks."""
from .base import ActionContext, ActionResult, register


@register("llm.ask", "AI 提问", "调用内置离线模型或 CC Switch 当前模型",
          params=[{"name": "use_ocr", "type": "bool"}])
def _ask(ctx: ActionContext, params) -> ActionResult:
    callback = getattr(ctx, "ask_question", None)
    if not callback:
        return ActionResult.failure("本机模型提问界面未连接")
    use_ocr = bool((params or {}).get("use_ocr", False))
    prompt = ctx.ocr.last_text() if use_ocr and ctx.ocr else ""
    if use_ocr and not prompt.strip():
        return ActionResult.failure("当前没有可用的 OCR 识别文字")
    callback(prompt, use_ocr)
    return ActionResult.success("已打开 AI 提问")
