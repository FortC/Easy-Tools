"""文本输入动作：粘贴模式 / 手打模式（方案 2.3）。

模式选择三级回退（动作级 > 全局默认 > paste）：
    params["mode"] 非空 -> 用它；否则读 text_input.default_mode。

两个必须做对、工程上最容易翻车的点：
1. **目标窗口**：面板自己抢了焦点，必须先 focus.restore() 再注入，
   否则文本全部打进面板自己；
2. **剪贴板恢复**：粘贴模式要能还原用户原本的剪贴板，而且当剪贴板
   里是图片/文件（无文本）时**不还原**——那会洗掉用户数据。
"""

from __future__ import annotations

import threading
import time
from typing import Optional

from ...win32 import clipboard as clip
from ...win32 import input as winput
from ...win32 import window as w32
from .. import focus
from ..log import get_logger
from ..variables import expand
from .base import ActionContext, ActionResult, register

log = get_logger("text_input")

_type_lock = threading.Lock()


def resolve_mode(params: dict, cfg: dict) -> str:
    mode = str((params or {}).get("mode") or "").strip().lower()
    if mode in ("paste", "type"):
        return mode
    return str((cfg or {}).get("default_mode", "paste")).lower()


def get_snippet(store, snippet_id: str) -> str:
    if not store:
        return ""
    for s in store.get().get("snippets", []):
        if s.get("id") == snippet_id:
            return s.get("text", "")
    return ""


def inject(text: str, mode: str, cfg: dict, ctx: Optional[ActionContext] = None) -> ActionResult:
    """把 text 注入到目标窗口的当前光标位置。"""
    if text == "":
        return ActionResult.failure("待输入文本为空")
    cfg = cfg or {}
    if not _type_lock.acquire(blocking=False):
        return ActionResult.failure("上一条文本仍在输入，请先按 Esc 中断或等待完成。")
    try:
        if cfg.get("ensure_foreground", True) and not focus.restore():
            log.warning("焦点未切回目标窗口，仍继续注入（部分应用可能收不到）")
        if mode == "type":
            cps = float(cfg.get("speed_cps", 20) or 20)
            jitter = int(cfg.get("jitter_ms", 40) or 0)
            cancel = threading.Event()
            n = winput.type_text(text, cps=cps, jitter_ms=jitter, cancel=cancel)
            normalized = text.replace("\r\n", "\n").replace("\r", "\n")
            if cancel.is_set() and n < len(normalized):
                return ActionResult(False, "已中断手打输入，已输入 %d 字符" % n,
                                    output=normalized[:n], cancelled=True)
            return ActionResult.success("手打输入 %d 字符" % n, output=normalized)
        # 粘贴模式
        snapshot = clip.snapshot()
        restore = bool(cfg.get("restore_clipboard", True)) and snapshot is not None
        delay = int(cfg.get("paste_restore_ms", 300) or 0)
        ok = winput.paste_text(text, restore_snapshot=snapshot,
                               restore_delay_ms=delay, restore=restore)
        if not ok:
            return ActionResult.failure("剪贴板写入失败")
        if not restore:
            log.info("原剪贴板无文本内容，跳过恢复")
        return ActionResult.success("已粘贴 %d 字符" % len(text), output=text)
    finally:
        _type_lock.release()


@register("text.input", "输入文本", "把固定文本注入当前光标位置", chain_id=None,
          params=[{"name": "text", "type": "text"}, {"name": "mode", "type": "enum",
                                                     "options": ["", "paste", "type"]}])
def _text_input(ctx: ActionContext, params) -> ActionResult:
    cfg = (ctx.store.section("text_input", {}) if ctx.store else {})
    raw = str((params or {}).get("text", ""))
    text = expand(raw, {"ocr": (lambda: ctx.ocr.last_text()) if ctx.ocr else (lambda: "")})
    mode = resolve_mode(params, cfg)
    res = inject(text, mode, cfg, ctx)
    if res.ok:
        ctx.message(res.message)
    return res


@register("input.paste", "粘贴文本", "事件链步骤：把 input 以粘贴模式注入", chain_id="input.paste")
def _chain_paste(ctx: ActionContext, params) -> ActionResult:
    cfg = (ctx.store.section("text_input", {}) if ctx.store else {})
    return inject(str((params or {}).get("input", "")), "paste", cfg, ctx)


@register("input.type", "键入文本", "事件链步骤：把 input 逐字符键入", chain_id="input.type")
def _chain_type(ctx: ActionContext, params) -> ActionResult:
    cfg = (ctx.store.section("text_input", {}) if ctx.store else {})
    return inject(str((params or {}).get("input", "")), "type", cfg, ctx)


@register("snippet.input", "输入文本片段", "从片段库取值后注入", chain_id=None,
          params=[{"name": "snippet_id", "type": "snippet"},
                  {"name": "mode", "type": "enum", "options": ["", "paste", "type"]}])
def _snippet_input(ctx: ActionContext, params) -> ActionResult:
    cfg = (ctx.store.section("text_input", {}) if ctx.store else {})
    sid = str((params or {}).get("snippet_id", ""))
    raw = get_snippet(ctx.store, sid)
    if not raw:
        return ActionResult.failure("片段不存在或内容为空：%s" % sid)
    text = expand(raw, {"ocr": (lambda: ctx.ocr.last_text()) if ctx.ocr else (lambda: "")})
    mode = resolve_mode(params, cfg)
    res = inject(text, mode, cfg, ctx)
    if res.ok:
        ctx.message("片段「%s」已输入 %d 字符" % (sid, len(text)))
    return res
