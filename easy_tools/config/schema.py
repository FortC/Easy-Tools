"""配置结构定义、默认配置加载、校验与版本迁移。

设计要点：
1. 用户配置永远"覆盖"默认值而不是替代默认值（deep_merge），
   这样升级版本新增字段时，老用户 config.json 不会缺 key 而崩溃；
2. validate() 返回**人类可读的错误串列表**，配置中心在保存前调用它，
   保存后再次调用作为落盘前的最后一道闸；
3. 本模块不依赖 Qt，也���依赖 easy_tools 其他模块，可在纯 Python 下测试。
"""

from __future__ import annotations

import copy
import json
from typing import Any, Dict, List

from ..core.paths import defaults_path

SCHEMA_VERSION = 1

#: 顶层 section，缺一即为非法配置
REQUIRED_SECTIONS = (
    "version", "general", "trigger", "panel", "ocr", "llm", "text_input",
    "actions", "snippets", "rules", "chains", "privacy", "question_banks",
)

_ENUMS = {
    ("trigger", "middle_click_mode"): {"swallow_tap", "native_passthrough"},
    ("trigger", "mouse_button"): {"middle", "x1", "x2"},
    ("panel", "theme"): {"light"},
    ("panel", "display_mode"): {"tabs", "scroll"},
    ("text_input", "default_mode"): {"paste", "type"},
    ("ocr", "region"): {"client", "window", "screen"},
}

_ACTION_KINDS = {
    "open.app", "open.file", "open.url", "open.command", "open.explorer_select",
    "text.input", "snippet.input", "clipboard.processor",
    "chain.run", "system.settings", "system.reload", "ocr.copy_text", "llm.ask",
    "system.window_min", "system.window_restore",
    "screen.capture",
}

_TRIGGER_TYPES = {"manual", "hotkey", "window_switch", "clipboard_change", "timer"}
_STEP_TYPES = {"action", "condition", "wait", "loop", "setvar", "stop", "python"}


def load_defaults() -> Dict[str, Any]:
    """读取内置 defaults.json（随包分发）。"""
    with open(defaults_path(), "r", encoding="utf-8") as f:
        return json.load(f)


def deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    """递归合并：override 覆盖 base，dict 递归，list/标量整体替换。

    list 整体替换是刻意的：动作列表、规则列表是"用户全权拥有"的集合，
    与其做三路合并让用户困惑，不如明确以用户文件为准。
    """
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def migrate(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """按 version 字段做前向迁移。当前只有 v1，保留钩子。"""
    ver = int(cfg.get("version", 0) or 0)
    if ver < 1:
        cfg["version"] = 1
    if ver > SCHEMA_VERSION:
        # 高版本配置不降级破坏，直接交给用户确认；此处仅告警不丢数据
        pass
    return cfg


def normalize(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """补齐缺省字段（以 defaults 为基准 deep_merge），并做类型规整。"""
    cfg = deep_merge(load_defaults(), cfg or {})
    # 仅升级识别出的历史默认值；用户自行设置的面板尺寸保持不变。
    p = cfg.get("panel") or {}
    if (p.get("columns"), p.get("cell_width"), p.get("cell_height")) in {
        (4, 100, 82), (6, 92, 78), (7, 84, 66), (9, 72, 58),
    }:
        p.update({"columns": 5, "cell_width": 88, "cell_height": 72})
        cfg["panel"] = p
    # Legacy dark settings are retained on disk only until normalization.
    cfg.setdefault("panel", {})["theme"] = "light"
    # 老配置补发截图动作；用户已自行建过 screen.capture 则不重复
    acts = cfg.get("actions") or []
    if not any(a.get("kind") == "screen.capture" for a in acts):
        for a in load_defaults().get("actions", []):
            if a.get("kind") == "screen.capture":
                acts.append(dict(a))
        cfg["actions"] = acts
    default_actions = load_defaults().get("actions", [])
    action_ids = {a.get("id") for a in acts}
    for default_action in default_actions:
        if default_action.get("kind") == "llm.ask" and default_action.get("id") not in action_ids:
            acts.append(copy.deepcopy(default_action))
            action_ids.add(default_action.get("id"))
    cfg["actions"] = acts
    for a in cfg.get("actions", []):
        a.setdefault("zone", "public")
        a.setdefault("icon", "star")
        a.setdefault("order", 100)
        a.setdefault("params", {})
        a.setdefault("group", "未分组")
        if a.get("kind") == "screen.capture":
            a["params"].setdefault("ask_question", True)
    for s in cfg.get("snippets", []):
        s.setdefault("group", "未分组")
        s.setdefault("order", 100)
    for r in cfg.get("rules", []):
        r.setdefault("match", "any")
        r.setdefault("processes", [])
        r.setdefault("title_regex", [])
        r.setdefault("ocr_keywords", [])
        r.setdefault("action_ids", [])
    for c in cfg.get("chains", []):
        c.setdefault("steps", [])
        c.setdefault("trigger", {"type": "manual"})
    return cfg


def validate(cfg: Dict[str, Any]) -> List[str]:
    """返回错误列表；空列表表示通过。绝不抛异常——配置中心要能展示错误。"""
    errs: List[str] = []

    for sec in REQUIRED_SECTIONS:
        if sec not in cfg:
            errs.append(f"缺少顶层配置节：{sec}")

    for (sec, key), allowed in _ENUMS.items():
        val = cfg.get(sec, {}).get(key)
        if val is not None and val not in allowed:
            errs.append(f"{sec}.{key} 取值非法：{val!r}，允许 {sorted(allowed)}")

    t = cfg.get("trigger", {})
    if not isinstance(t.get("tap_max_ms", 0), (int, float)) or t.get("tap_max_ms", 0) <= 0:
        errs.append("trigger.tap_max_ms 必须为正数（毫秒）")
    if not isinstance(t.get("drag_threshold_px", 0), (int, float)) or t.get("drag_threshold_px", 0) < 0:
        errs.append("trigger.drag_threshold_px 不能为负")

    p = cfg.get("panel", {})
    if not isinstance(p.get("columns"), int) or not 1 <= p.get("columns", 0) <= 20:
        errs.append("panel.columns 必须是 1~20 的整数")
    if not isinstance(p.get("max_rows"), int) or not 1 <= p.get("max_rows", 0) <= 30:
        errs.append("panel.max_rows 必须是 1~30 的整数")
    order = p.get("group_order", [])
    if not isinstance(order, list) or any(not isinstance(x, str) or not x.strip() for x in order):
        errs.append("大分类顺序必须为非空分类名称列表")
    elif len(order) != len(set(order)):
        errs.append("大分类顺序不能重复")
    op = p.get("opacity", 1.0)
    if not isinstance(op, (int, float)) or not 0.3 <= float(op) <= 1.0:
        errs.append("panel.opacity 必须在 0.3~1.0 之间")

    ti = cfg.get("text_input", {})
    if not isinstance(ti.get("speed_cps"), (int, float)) or ti.get("speed_cps", 1) <= 0:
        errs.append("text_input.speed_cps 必须为正数（字符/秒）")
    if not isinstance(ti.get("jitter_ms", 0), (int, float)) or ti.get("jitter_ms", 0) < 0:
        errs.append("text_input.jitter_ms 不能为负")

    privacy = cfg.get("privacy", {})
    if not isinstance(privacy.get("exclude_from_capture", True), bool):
        errs.append("privacy.exclude_from_capture 必须为布尔值")
    if not isinstance(cfg.get("llm", {}).get("enabled", False), bool):
        errs.append("llm.enabled 必须为布尔值")

    if not isinstance(cfg.get("llm", {}).get("answer_content_only", False), bool):
        errs.append("llm.answer_content_only 必须为布尔值")

    banks = cfg.get("question_banks", [])
    if not isinstance(banks, list):
        errs.append("question_banks 必须是列表")
    else:
        enabled_banks = 0
        seen_paths = set()
        for i, bank in enumerate(banks):
            if not isinstance(bank, dict):
                errs.append(f"question_banks 第 {i + 1} 项必须是对象")
                continue
            if not bank.get("id") or not bank.get("path"):
                errs.append(f"question_banks 第 {i + 1} 项缺少 id 或 path")
            if not isinstance(bank.get("enabled", False), bool):
                errs.append(f"question_banks 第 {i + 1} 项 enabled 必须为布尔值")
            enabled_banks += bool(bank.get("enabled"))
            path = str(bank.get("path") or "").casefold()
            if path and path in seen_paths:
                errs.append(f"知识库路径重复：{bank.get('path')}")
            seen_paths.add(path)
            mapping = bank.get("column_mapping", {})
            if not isinstance(mapping, dict):
                errs.append(f"知识库 {i + 1} 列配置必须是对象")
            else:
                from ..core.question_bank import validate_column_mapping
                for sheet, columns in mapping.items():
                    try:
                        validate_column_mapping(columns, 16384)
                    except (ValueError, TypeError) as exc:
                        errs.append(f"知识库 {i + 1} 工作表 {sheet} 列配置无效：{exc}")
        if enabled_banks > 3:
            errs.append("最多同时启用 3 个知识库")

    # 唯一 id
    for key in ("actions", "snippets", "rules", "chains"):
        seen = set()
        for item in cfg.get(key, []) or []:
            i = item.get("id")
            if not i:
                errs.append(f"{key} 中存在缺少 id 的条目")
            elif i in seen:
                errs.append(f"{key} 中 id 重复：{i}")
            else:
                seen.add(i)

    # 动作 kind 合法 + 引用完整
    snippet_ids = {s.get("id") for s in cfg.get("snippets", []) or []}
    chain_ids = {c.get("id") for c in cfg.get("chains", []) or []}
    action_ids = {a.get("id") for a in cfg.get("actions", []) or []}
    for a in cfg.get("actions", []) or []:
        if a.get("kind") not in _ACTION_KINDS:
            errs.append(f"动作 {a.get('id')} 的 kind={a.get('kind')!r} 未注册")
        if a.get("kind") == "snippet.input":
            sid = a.get("params", {}).get("snippet_id")
            if sid not in snippet_ids:
                errs.append(f"动作 {a.get('id')} 绑定的片段不存在：{sid}")
        if a.get("kind") == "chain.run":
            cid = a.get("params", {}).get("chain_id")
            if cid not in chain_ids:
                errs.append(f"动作 {a.get('id')} 绑定的事件链不存在：{cid}")
        if a.get("kind") == "clipboard.processor":
            if not a.get("params", {}).get("processor"):
                errs.append(f"动作 {a.get('id')} 是剪贴板处理器但未指定 processor")

    for r in cfg.get("rules", []) or []:
        for aid in r.get("action_ids", []) or []:
            if aid not in action_ids:
                errs.append(f"规则 {r.get('id')} 引用了不存在的动作：{aid}")
        for rx in r.get("title_regex", []) or []:
            import re as _re
            try:
                _re.compile(rx)
            except _re.error as e:
                errs.append(f"规则 {r.get('id')} 的标题正则非法：{rx}（{e}）")

    # 事件链结构
    def _walk_steps(steps, where):
        for i, s in enumerate(steps or []):
            t = s.get("type")
            if t not in _STEP_TYPES:
                errs.append(f"{where} 第 {i + 1} 步 type={t!r} 非法")
            if t == "action" and not s.get("id"):
                errs.append(f"{where} 第 {i + 1} 步 action 缺少 id")
            if t == "condition":
                _walk_steps(s.get("then"), f"{where} 第 {i + 1} 步 then")
                _walk_steps(s.get("else"), f"{where} 第 {i + 1} 步 else")
            if t == "loop":
                _walk_steps(s.get("steps"), f"{where} 第 {i + 1} 步 loop")
            if t == "python":
                if not isinstance(s.get("code", ""), str):
                    errs.append(f"{where} 第 {i + 1} 步 Python 脚本必须是文本")
                if not isinstance(s.get("trusted", False), bool):
                    errs.append(f"{where} 第 {i + 1} 步 Python 信任授权必须为布尔值")
                timeout = s.get("timeout_seconds", 5)
                if not isinstance(timeout, (int, float)) or not 1 <= timeout <= 30:
                    errs.append(f"{where} 第 {i + 1} 步 Python 超时必须在 1～30 秒之间")

    for c in cfg.get("chains", []) or []:
        if c.get("trigger", {}).get("type") not in _TRIGGER_TYPES:
            errs.append(f"事件链 {c.get('id')} 的 trigger.type 非法")
        _walk_steps(c.get("steps"), f"事件链 {c.get('id')}")

    return errs
