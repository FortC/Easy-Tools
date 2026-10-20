"""变量/占位符展开（方案 2.3 + 2.5 共用）。

两类变量，同一套语法：
- 内置：{date} {time} {datetime} {clipboard} {selection} {ocr_text}
- 链变量：{step3.output} {last.output} {var:name}

刻意不引入模板引擎：配置里可能出现用户随手写的花括号，
正则只替换**已知变量名**，遇到未知的一律原样保留，绝不抛异常——
配置里的笔误不该让整个动作失效。
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Callable, Dict, Mapping, Optional

from ..win32 import clipboard as clip

BUILTIN_PATTERNS = {
    "date": lambda: datetime.now().strftime("%Y-%m-%d"),
    "time": lambda: datetime.now().strftime("%H:%M:%S"),
    "datetime": lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    "now": lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    "weekday": lambda: "星期" + "一二三四五六日"[datetime.now().weekday()],
}

_PLACEHOLDER = re.compile(r"\{([^{}]+)\}")


def expand(text: str, extra: Optional[Mapping[str, Callable[[], str]]] = None) -> str:
    """展开占位符。未知变量保持原样。"""
    if not text:
        return ""
    sources: Dict[str, Callable[[], str]] = dict(BUILTIN_PATTERNS)
    sources["clipboard"] = lambda: clip.get_text() or ""
    sources["ocr_text"] = lambda: (extra or {}).get("ocr", lambda: "")()
    if extra:
        for k, v in extra.items():
            if callable(v):
                sources[k] = v

    def repl(m: "re.Match") -> str:
        key = m.group(1).strip()
        fn = sources.get(key)
        if fn is None:
            return m.group(0)
        try:
            return fn() or ""
        except Exception:
            return m.group(0)

    return _PLACEHOLDER.sub(repl, text)


def known_placeholders() -> list:
    return sorted(BUILTIN_PATTERNS) + ["clipboard", "ocr_text"]
