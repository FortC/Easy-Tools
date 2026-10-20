"""动作注册表。

设计约束（来自方案 2.3/2.5/2.6）：
- 动作 = kind + params，kind 决定处理器；新增能力只加 kind，不改面板。
- 每个剪贴板处理器**同时**是面板动作和事件链步骤（方案 2.5 原话），
  因此处理器注册表与动作注册表分离，由 core.chain_builtins 桥接。
"""
from .base import (ActionContext, ActionResult, ActionSpec, ACTIONS,
                   register, get_action, all_actions, clear_registry)
from . import processors  # noqa: F401  触发处理器自注册
from . import openers      # noqa: F401
from . import text_input   # noqa: F401
from . import clipboard    # noqa: F401
from . import system       # noqa: F401
from . import screenshot   # noqa: F401
from . import llm          # noqa: F401
