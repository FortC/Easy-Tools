"""配置中心的中文可读化层。

配置中心原本把内部代码直接甩给用户看：
    类型  chain.run
    参数  {'snippet_id': 'snip_a', 'mode': ''}
这不是给人看的。本模块把 kind 翻译成中文名、把 params 渲染成人话摘要。

设计边界：
- 只做「翻译」，不改变任何存储的原始值。动作的 kind / params 仍是内部代码，
  存储格式不动，只是显示层翻译。用户看到的中文不能被写回配置。
- 找不到映射时如实显示原文，绝不假装认识——宁可漏翻，不能错翻。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from ..core.actions import base as action_base
from ..core.actions import processors as proc_mod

# 操作区代码 -> 中文
ZONE_LABELS = {"public": "公共区", "context": "特定操作区"}

# 注入模式代码 -> 中文
MODE_LABELS = {"": "粘贴", "paste": "粘贴", "type": "逐字键入"}


def kind_label(kind: Any) -> str:
    """动作类型 -> 中文名。查不到就原样返回，不编造。"""
    s = str(kind or "")
    spec = action_base.all_actions().get(s)
    return spec.title if spec else (s or "（空）")


def kind_hint(kind: Any) -> str:
    """动作类型的补充说明，给编辑框当提示用。"""
    s = str(kind or "")
    spec = action_base.all_actions().get(s)
    if not spec:
        return ""
    return ("%s（%s）" % (spec.title, s)) + ("｜" + spec.description if spec.description else "")


def zone_label(zone: Any) -> str:
    return ZONE_LABELS.get(str(zone or ""), str(zone or ""))


def mode_label(mode: Any) -> str:
    return MODE_LABELS.get(str(mode or ""), str(mode or "") or "粘贴")


def processor_label(pid: Any) -> str:
    """剪贴板/文本处理器 id -> 中文名。"""
    s = str(pid or "")
    p = proc_mod.all_processors().get(s)
    return p.name if p else s


def kind_choices() -> List[str]:
    """类型下拉框的候选值：内部代码，排序按中文名，显示走 kind_label。"""
    return sorted(action_base.all_actions().keys(), key=lambda k: kind_label(k))


def _snippet_name(store, sid: str) -> str:
    for s in (store.section("snippets", []) or []):
        if str(s.get("id")) == str(sid):
            return str(s.get("name") or sid)
    return ""


def _snippet_text(store, sid: str) -> str:
    if not store:
        return ""
    for s in (store.section("snippets", []) or []):
        if str(s.get("id")) == str(sid):
            return str(s.get("text") or "")
    return ""


def _chain_name(store, cid: str) -> str:
    for c in (store.section("chains", []) or []):
        if str(c.get("id")) == str(cid):
            return str(c.get("name") or cid)
    return ""


def _short(text: Any, n: int = 18) -> str:
    s = str(text if text is not None else "").replace("\r\n", " ↵ ").replace("\r", " ↵ ").replace("\n", " ↵ ")
    return s if len(s) <= n else s[:n] + "…"


def describe_params(row: Dict[str, Any], store=None) -> str:
    """把一条动作的 params 渲染成人话摘要。

    store 用于把 snippet_id / chain_id 反查成名字；没有 store 时退化为显示 id。
    """
    kind = str(row.get("kind") or "")
    p = row.get("params")
    if not isinstance(p, dict):
        return _short(p, 40) if p else ""

    if kind == "snippet.input":
        sid = str(p.get("snippet_id") or "")
        name = _snippet_name(store, sid) if store else ""
        who = name or sid or "（未选片段）"
        text = _snippet_text(store, sid)
        summary = " · 正文「%s」" % _short(text, 36) if text else ""
        return "片段「%s」· %s%s" % (who, mode_label(p.get("mode")), summary)
    if kind == "text.input":
        return "文本「%s」· %s" % (_short(p.get("text"), 36), mode_label(p.get("mode")))
    if kind == "input.paste":
        return "粘贴文本「%s」" % _short(p.get("input") or p.get("text"))
    if kind == "input.type":
        return "键入文本「%s」" % _short(p.get("input") or p.get("text"))
    if kind == "open.url":
        return "网址 %s" % _short(p.get("url"), 32)
    if kind == "open.app":
        return "程序 %s" % _short(p.get("app") or p.get("path"), 30)
    if kind == "open.file":
        return "文件 %s" % _short(p.get("path"), 30)
    if kind == "open.command":
        return "命令 %s" % _short(p.get("command"), 30)
    if kind == "open.explorer_select":
        return "定位 %s" % _short(p.get("path"), 30)
    if kind == "clipboard.processor":
        pid = str(p.get("processor") or "")
        name = processor_label(pid) or pid or "（未选处理器）"
        tail = "· 处理后自动粘贴" if p.get("auto_paste") else "· 只写回剪贴板"
        return "剪贴板处理「%s」%s" % (name, tail)
    if kind == "clipboard.set":
        return "写入剪贴板「%s」" % _short(p.get("input") or p.get("text"))
    if kind == "screen.capture":
        flags = []
        if p.get("copy_image", True):
            flags.append("复制图片")
        if p.get("auto_ocr", True):
            flags.append("自动 OCR")
        return " · ".join(flags) if flags else "仅截图"
    # 无参数的动作不重复播报含义——「类型」列已经写了中文名，
    # 参数列再写一遍纯属噪音。
    if kind in ("clipboard.copy", "clipboard.get", "clipboard.clear", "ocr.copy_text"):
        return ""
    if kind == "chain.run":
        cid = str(p.get("chain_id") or p.get("id") or "")
        name = _chain_name(store, cid) if store else ""
        return "事件链「%s」" % (name or cid or "（未选事件链）")
    if kind.startswith("system."):
        return ""

    # 未知 kind：如实显示原文，不假装认识
    if not p:
        return ""
    return " ".join("%s=%s" % (k, _short(v, 14)) for k, v in list(p.items())[:3])
