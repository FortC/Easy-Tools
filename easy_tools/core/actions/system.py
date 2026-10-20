"""系统类动作。

需要 Qt 的能力（打开设置面板、弹提示）一律通过 ActionContext 注入的回调实现，
core 层不 import Qt——这是分层纪律，不是洁癖：core 能在无 GUI 环境跑测试。
"""

from __future__ import annotations

from ...win32 import window as w32
from ..log import get_logger
from .base import ActionContext, ActionResult, register

log = get_logger("system")


@register("system.settings", "打开配置中心", "", chain_id=None)
def _settings(ctx: ActionContext, params) -> ActionResult:
    if not ctx.open_settings:
        return ActionResult.failure("未接入配置中心（无 UI 环境）")
    ctx.open_settings()
    return ActionResult.success()


@register("system.reload", "重载配置", "从磁盘重新读取并广播", chain_id=None)
def _reload(ctx: ActionContext, params) -> ActionResult:
    if not ctx.store:
        return ActionResult.failure("未接入配置存储")
    changed = ctx.store.reload()
    ctx.message("配置已重载%s" % ("（有变更）" if changed else "（无变更）"))
    return ActionResult.success()


@register("chain.run", "运行事件链", "把一条事件链挂成面板动作", chain_id=None,
          params=[{"name": "chain_id", "type": "chain"}])
def _chain_run(ctx: ActionContext, params) -> ActionResult:
    cid = str((params or {}).get("chain_id", ""))
    runner = ctx.chain_runner
    if runner is None:
        return ActionResult.failure("事件链引擎未接入")
    res = runner.run_by_id(cid, ctx)
    return ActionResult(res.ok, res.message, res.output)


@register("system.window_min", "最小化当前窗口", "", chain_id="system.window_min")
def _win_min(ctx: ActionContext, params) -> ActionResult:
    fg = w32.get_foreground()
    if not fg.valid:
        return ActionResult.failure("没有有效前台窗口")
    return ActionResult.success() if w32.minimize(fg.hwnd) else ActionResult.failure("最小化失败")


@register("system.window_restore", "恢复当前窗口", "", chain_id="system.window_restore")
def _win_restore(ctx: ActionContext, params) -> ActionResult:
    fg = w32.get_foreground()
    if not fg.valid:
        return ActionResult.failure("没有有效前台窗口")
    return ActionResult.success() if w32.restore(fg.hwnd) else ActionResult.failure("恢复失败")


@register("ocr.copy_text", "抓取界面文字到剪贴板", "依赖 OCR 后端，未启用时给出明确原因", chain_id="ocr.read")
def _ocr_copy(ctx: ActionContext, params) -> ActionResult:
    if not ctx.ocr:
        return ActionResult.failure("OCR 服务未初始化")
    status = ctx.ocr.status()
    if not status.get("ready"):
        return ActionResult.failure("OCR 不可用：%s" % status.get("reason", "未知原因"))
    text = ctx.ocr.refresh(force=True)
    if not text:
        return ActionResult.failure("未识别到文字")
    from ...win32 import clipboard as clip
    clip.set_text(text)
    ctx.message("已抓取 %d 字界面文字到剪贴板" % len(text))
    return ActionResult.success(output=text)
