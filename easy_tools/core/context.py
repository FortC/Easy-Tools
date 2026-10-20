"""上下文识别（方案 2.1）。

两级组合，各自负责不同问题：
- L1 应用识别：进程名 + 窗口标题正则。零成本、100% 准，回答"是哪个软件"。
- L2 内容识别：OCR 关键词，回答"界面里是什么内容"。

匹配语义（写清楚免得日后扯皮）：
- 一条规则里，**没填的条件类型不参与判断**；
- match=any：填了的条件里任意一条命中即可；
- match=all：填了的条件必须全部命中；
- 一条条件都没填的规则**永不命中**（否则它会变成"全局规则"，
  而用户以为它只在某个软件生效——这是最坑的一种配置歧义）。
"""

from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from ..win32 import window as w32
from .log import get_logger

log = get_logger("context")


@dataclass
class ContextState:
    window: w32.WindowInfo
    ocr_text: str = ""
    ocr_at: float = 0.0
    ocr_backend: str = "none"

    def ocr_fresh(self, ttl_ms: int) -> bool:
        if not self.ocr_text:
            return False
        return (time.time() - self.ocr_at) * 1000.0 <= max(0, ttl_ms)


@dataclass
class MatchedRule:
    rule: dict
    reasons: List[str] = field(default_factory=list)

    @property
    def name(self) -> str:
        return self.rule.get("name") or self.rule.get("id", "规则")

    @property
    def action_ids(self) -> List[str]:
        return list(self.rule.get("action_ids") or [])


class ContextResolver:
    """持有当前上下文并对规则求值。"""

    def __init__(self, store=None) -> None:
        self.store = store
        self._lock = threading.RLock()
        self._state: Optional[ContextState] = None
        self._regex_cache: Dict[str, "re.Pattern"] = {}

    # ------------------------------------------------------------ 状态
    def current(self, refresh: bool = True) -> ContextState:
        with self._lock:
            if refresh or self._state is None:
                self._state = ContextState(window=w32.get_foreground())
            return self._state

    def update_ocr(self, text: str, backend: str = "none") -> None:
        with self._lock:
            st = self._state or ContextState(window=w32.get_foreground())
            st.ocr_text = text or ""
            st.ocr_at = time.time()
            st.ocr_backend = backend
            self._state = st

    def invalidate(self) -> None:
        with self._lock:
            self._state = None

    # ------------------------------------------------------------ 规则
    def rules(self) -> List[dict]:
        if not self.store:
            return []
        return list(self.store.section("rules", []) or [])

    def needs_ocr(self, state: Optional[ContextState] = None) -> bool:
        """Return whether OCR could change a rule result for this foreground window."""
        st = state or self.current()
        if not st.window.valid:
            return False
        proc = (st.window.process or "").lower()
        title = st.window.title or ""
        for rule in self.rules():
            if not any(str(k).strip() for k in (rule.get("ocr_keywords") or [])):
                continue
            base: List[bool] = []
            procs = [str(p).lower() for p in (rule.get("processes") or []) if str(p).strip()]
            if procs:
                base.append(any(p == "*" or p == proc or
                                (p.endswith("*") and proc.startswith(p[:-1])) for p in procs))
            regexes = [r for r in (rule.get("title_regex") or []) if str(r).strip()]
            if regexes:
                base.append(any(self._compiled(str(r)).search(title) for r in regexes))
            if not base:
                return True
            mode = str(rule.get("match", "any")).lower()
            # In "all", OCR matters after all L1 checks pass. In "any", it
            # matters only if L1 did not already satisfy the rule.
            if all(base) if mode == "all" else not any(base):
                return True
        return False

    def _compiled(self, pattern: str):
        with self._lock:
            rx = self._regex_cache.get(pattern)
            if rx is None:
                try:
                    rx = re.compile(pattern, re.IGNORECASE)
                except re.error as e:
                    log.warning("标题正则非法已忽略：%s (%s)", pattern, e)
                    rx = re.compile(r"(?!)")
                self._regex_cache[pattern] = rx
            return rx

    def match_rule(self, rule: dict, state: ContextState) -> Optional[MatchedRule]:
        proc = (state.window.process or "").lower()
        title = state.window.title or ""
        ocr = (state.ocr_text or "").lower()

        checks: List[bool] = []
        reasons: List[str] = []

        procs = [str(p).lower() for p in (rule.get("processes") or []) if str(p).strip()]
        if procs:
            hit = any(p == "*" or p == proc or (p.endswith("*") and proc.startswith(p[:-1]))
                      for p in procs)
            checks.append(hit)
            if hit:
                reasons.append("进程 %s" % proc)

        regexes = [r for r in (rule.get("title_regex") or []) if str(r).strip()]
        if regexes:
            hit = any(self._compiled(str(r)).search(title) for r in regexes)
            checks.append(hit)
            if hit:
                reasons.append("标题匹配")

        kws = [str(k).lower() for k in (rule.get("ocr_keywords") or []) if str(k).strip()]
        if kws:
            hit = bool(ocr) and any(k in ocr for k in kws)
            checks.append(hit)
            if hit:
                reasons.append("OCR 关键词")

        if not checks:
            log.debug("规则 %s 没有配置任何条件，永不命中", rule.get("id"))
            return None
        mode = str(rule.get("match", "any")).lower()
        ok = all(checks) if mode == "all" else any(checks)
        return MatchedRule(rule=rule, reasons=reasons) if ok else None

    def match_all(self, state: Optional[ContextState] = None) -> List[MatchedRule]:
        st = state or self.current()
        if not st.window.valid:
            return []
        out = []
        for r in self.rules():
            m = self.match_rule(r, st)
            if m:
                out.append(m)
        out.sort(key=lambda m: int(m.rule.get("order", 100)))
        if out:
            # 没有这行日志，"当前识别成了什么上下文"完全不可观测——
            # 用户报"桌面被识别成浏览器"时曾无从查起。
            log.info("上下文命中：%s ← %s", st.window.process or "?",
                     "、".join("%s（%s）" % (m.name, "，".join(m.reasons) or "默认")
                               for m in out))
        else:
            log.debug("上下文无命中：%s", st.window.process or "?")
        return out

    def actions_for(self, state: Optional[ContextState] = None) -> Dict[str, List[dict]]:
        """返回 {规则名: [动作...]}，供面板渲染特定操作区。"""
        actions = (self.store.section("actions", []) if self.store else [])
        by_id = {a.get("id"): a for a in actions}
        groups: Dict[str, List[dict]] = {}
        for m in self.match_all(state):
            picked = [by_id[i] for i in m.action_ids if i in by_id]
            if picked:
                picked.sort(key=lambda a: int(a.get("order", 100)))
                groups[m.name] = picked
        return groups
