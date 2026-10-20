"""快速新增「文本按钮」向导（公用技能自助配置）。

背景：数据模型里一个"公用文本按钮"由**两段**组成——snippets 里的文本
片段，加上一条 kind=snippet.input 的动作。原流程要求用户先在「文本
片段」页建片段，再去「动作库」页手工新建动作并把 snippet_id 填进 JSON
参数里：两步、跨页、极易填错。实测下来等于"没有这个功能"。

本向导把两步合成一次表单填写，产出「片段 + 动作」两条记录并立即落盘，
面板下一次呼出即可见。

纯逻辑（build_text_button_records）与 Qt 对话框分离，前者可无 GUI 单测。
"""

from __future__ import annotations

import re
import time
from typing import Any, Dict, List, Tuple


def _slug(name: str) -> str:
    """从名称生成 id 片段；中文等非 ASCII 会退化成时间戳。"""
    ascii_part = re.sub(r"[^a-zA-Z0-9]+", "_", str(name)).strip("_").lower()
    return ascii_part[:24] or ("u%d" % int(time.time() * 1000))


def _unique_id(base: str, taken: List[str]) -> str:
    if base not in taken:
        return base
    i = 2
    while "%s_%d" % (base, i) in taken:
        i += 1
    return "%s_%d" % (base, i)


def build_text_button_records(cfg: Dict[str, Any], name: str, icon: str,
                              group: str, text: str, mode: str
                              ) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """生成 (片段, 动作) 两条记录。纯函数，不碰 store，方便单测。

    - 片段内容支持变量 {date} {time} {datetime} {clipboard} {ocr_text}
    - mode=type 时走逐字手打，否则走剪贴板粘贴（快且不怕中文输入法）
    - order 取同分组内最大值 +10，新按钮排在已有文本按钮之后
    """
    name = str(name).strip()
    text = str(text)
    group = str(group).strip() or "文本输入"
    if not name:
        raise ValueError("按钮名称不能为空")
    if not text.strip():
        raise ValueError("文本内容不能为空")

    actions: List[Dict[str, Any]] = list(cfg.get("actions") or [])
    snippets: List[Dict[str, Any]] = list(cfg.get("snippets") or [])
    taken_actions = [str(a.get("id")) for a in actions]
    taken_snips = [str(s.get("id")) for s in snippets]

    slug = _slug(name)
    snip_id = _unique_id("snip_" + slug, taken_snips)
    act_id = _unique_id("snipbtn_" + slug, taken_actions)

    same_group = [s for s in snippets if str(s.get("group")) == group]
    order = 10
    if same_group:
        try:
            order = max(int(s.get("order", 0)) for s in same_group) + 10
        except Exception:
            order = 10

    snippet = {
        "id": snip_id,
        "name": name,
        "group": group,
        "order": order,
        "text": text,
    }
    action = {
        "id": act_id,
        "name": name,
        "group": group,
        "icon": str(icon).strip() or "keyboard",
        "zone": "public",
        "order": order,
        "kind": "snippet.input",
        "params": {"snippet_id": snip_id, "mode": "type" if mode == "type" else ""},
    }
    return snippet, action


def quick_add_text_button(cfg: Dict[str, Any], name: str, icon: str, group: str,
                          text: str, mode: str) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """就地写入 cfg 的 actions/snippets，返回创建的两条记录。"""
    snippet, action = build_text_button_records(cfg, name, icon, group, text, mode)
    cfg.setdefault("snippets", []).append(snippet)
    cfg.setdefault("actions", []).append(action)
    return snippet, action


def _qt_available() -> bool:
    """纯逻辑测试环境常常没有 Qt 绑定，对话框部分要能被安全跳过。"""
    try:
        import PySide6.QtWidgets  # noqa: F401
        return True
    except Exception:
        return False


if _qt_available():
    from PySide6.QtWidgets import QMessageBox
    from .widgets import Field, JsonFormDialog

    class TextButtonDialog(JsonFormDialog):
        """快速新增复用动作库编辑器的字段表单和视觉样式。"""

        def __init__(self, parent=None) -> None:
            fields = [
                Field("name", "按钮名称"),
                Field("icon", "图标", "icon", default="keyboard"),
                Field("group", "所在分组", default="文本输入"),
                Field("mode", "输入方式", "enum", ["", "type"],
                      labels={"": "粘贴（快，中文友好）", "type": "逐字手打（拟真）"}),
                Field("text", "文本内容", "textarea",
                      hint="支持变量：{date} {time} {datetime} {clipboard} {ocr_text}"),
            ]
            super().__init__("新增文本按钮", fields,
                             {"name": "", "icon": "keyboard", "group": "文本输入",
                              "mode": "", "text": ""}, parent=parent)
            self.setMinimumWidth(480)

        def values(self) -> Dict[str, str]:
            return {key: str(self.result.get(key, ""))
                    for key in ("name", "icon", "group", "text", "mode")}

        def accept(self) -> None:
            v = self.values()
            if not v["name"].strip():
                QMessageBox.warning(self, "还差一点", "按钮名称不能为空")
                return
            if not v["text"].strip():
                QMessageBox.warning(self, "还差一点", "文本内容不能为空")
                return
            super().accept()
