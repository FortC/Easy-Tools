"""动作基类与注册机制。

为什么用注册表而不是 if/elif 分发：
方案里动作可由用户在配置中心自由增删，如果分发逻辑写死在引擎里，
每加一种动作就要动引擎代码——那是典型的"改一个地方坏三个地方"。
注册表让新增能力变成纯增量：写一个 @register("xxx") 函数即可。
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from ..log import get_logger

log = get_logger("actions")


@dataclass
class ActionContext:
    """动作执行上下文。

    core 不允许 import Qt，所以"打开设置面板""弹提示"这类需要 UI 的事，
    一律由上层注入回调。这是 core/ui 分层的硬边界，别绕过它。
    """

    store: Any = None                                  # ConfigStore
    ocr: Any = None                                    # OcrService | None
    chain_runner: Any = None                           # ChainRunner | None
    open_settings: Optional[Callable[[], None]] = None
    show_panel: Optional[Callable[[], None]] = None
    notify: Optional[Callable[[str, str], None]] = None  # (level, message)
    start_capture: Optional[Callable[[bool, bool, bool], None]] = None  # (auto_ocr, copy_image, ask_question)
    ask_question: Optional[Callable[[str, bool], None]] = None
    cancel_event: Any = None
    target_hwnd: Optional[int] = None
    target_pid: Optional[int] = None
    deadline: Optional[float] = None

    def message(self, text: str, level: str = "info") -> None:
        log.info("%s", text)
        if self.notify:
            try:
                self.notify(level, text)
            except Exception:
                log.exception("notify 回调异常")


@dataclass
class ActionResult:
    ok: bool
    message: str = ""
    output: str = ""            # 供事件链后续步骤接续
    cancelled: bool = False
    trace: List[dict] = field(default_factory=list)

    @classmethod
    def success(cls, message: str = "", output: str = "") -> "ActionResult":
        return cls(True, message, output)

    @classmethod
    def failure(cls, message: str) -> "ActionResult":
        return cls(False, message, "")


Handler = Callable[[ActionContext, Dict[str, Any]], ActionResult]


@dataclass
class ActionSpec:
    kind: str
    title: str
    handler: Handler
    description: str = ""
    # 可被事件链调用的内置 id（非 None 时说明它也是链步骤）
    chain_id: Optional[str] = None
    params: List[Dict[str, Any]] = field(default_factory=list)  # 配置中心渲染用


ACTIONS: Dict[str, ActionSpec] = {}
_reg_lock = threading.RLock()


def register(kind: str, title: str, description: str = "",
             chain_id: Optional[str] = None,
             params: Optional[List[Dict[str, Any]]] = None):
    """动作注册装饰器。重复注册同 kind 视为配置错误，直接抛。"""

    def deco(fn: Handler) -> Handler:
        with _reg_lock:
            if kind in ACTIONS:
                raise ValueError("动作类型重复注册：%s" % kind)
            ACTIONS[kind] = ActionSpec(kind=kind, title=title, handler=fn,
                                       description=description, chain_id=chain_id,
                                       params=params or [])
        return fn

    return deco


def get_action(kind: str) -> Optional[ActionSpec]:
    return ACTIONS.get(kind)


def all_actions() -> Dict[str, ActionSpec]:
    with _reg_lock:
        return dict(ACTIONS)


def clear_registry() -> None:
    """仅测试用。"""
    with _reg_lock:
        ACTIONS.clear()


def run_action(kind: str, ctx: ActionContext, params: Optional[Dict[str, Any]] = None) -> ActionResult:
    """按 kind 执行动作。未知 kind 返回失败而不是抛异常——配置可能过期。"""
    spec = get_action(kind)
    if spec is None:
        return ActionResult.failure("未注册的动作类型：%s" % kind)
    try:
        return spec.handler(ctx, dict(params or {}))
    except Exception as e:
        log.exception("动作执行异常 kind=%s", kind)
        return ActionResult.failure("执行失败：%s" % e)
