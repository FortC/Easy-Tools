"""事件链编辑器（方案 2.5）。

交互形态刻意做成"列表式步骤编辑"而不是拖拽画布：步骤是有序流水线，
列表 + 上移/下移在键盘操作下比画布更快，也不会有"连线错位却看不出来"的
排障成本。每步支持改类型/改 id/改参数/试运行——试运行是必须的，
事件链是纯配置，不试运行就只能等真执行时才发现写错。
"""

from __future__ import annotations

import json
from typing import Any, Dict, List

from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QDialog,
                               QCheckBox, QDialogButtonBox, QFormLayout, QHBoxLayout,
                               QGridLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
                               QMessageBox, QPlainTextEdit, QPushButton, QSpinBox,
                               QVBoxLayout, QWidget)

from ..core.actions.processors import all_processors

STEP_TYPES = [
    ("action", "执行动作 / 文本处理"),
    ("python", "运行 Python 脚本（需信任）"),
    ("setvar", "创建或修改变量"),
    ("condition", "条件分支"),
    ("wait", "等待一段时间"),
    ("loop", "重复执行步骤"),
    ("stop", "结束并提示"),
]


def available_step_ids() -> List[str]:
    """链步骤可选 id：全部处理器 + 已注册动作。"""
    from ..core.actions import base as action_base
    return sorted(set(all_processors()) | set(action_base.all_actions()))


def describe_step(step: Dict[str, Any]) -> str:
    t = str(step.get("type", "action"))
    if t == "action":
        sid = str(step.get("id", "?"))
        proc = all_processors().get(sid)
        if proc:
            title = proc.name
        else:
            from ..core.actions import base as action_base
            spec = action_base.get_action(sid)
            title = spec.title if spec else sid
        return "执行：%s  ·  输入：%s" % (title, step.get("input", "上一步输出"))
    if t == "python":
        code = str(step.get("code", ""))
        return "Python 脚本 · %d 行%s" % (len(code.splitlines()),
            " · 已授权" if step.get("trusted") else " · 未授权")
    if t == "condition":
        return "条件：%s → then %d 步 / else %d 步" % (
            step.get("expr", "always"), len(step.get("then") or []),
            len(step.get("else") or []))
    if t == "wait":
        return "等待：%s 秒" % step.get("seconds", "0.3")
    if t == "loop":
        return "循环 %s 次，内含 %d 步" % (step.get("times", 1), len(step.get("steps") or []))
    if t == "setvar":
        return "设变量：%s = %s" % (step.get("name", "x"), step.get("value", ""))
    return "中止：%s" % step.get("message", "")


def _coerce(v: str):
    try:
        return int(v)
    except ValueError:
        pass
    try:
        return float(v)
    except ValueError:
        return v


class StepEditor(QWidget):
    """单个步骤的属性编辑区。"""

    def __init__(self, service=None, parent=None) -> None:
        super().__init__(parent)
        self.service = service
        self._step: Dict[str, Any] = {}

        form = QFormLayout(self)
        self.type_box = QComboBox()
        for value, label in STEP_TYPES:
            self.type_box.addItem(label, value)
        self.type_box.currentIndexChanged.connect(lambda _index: self._sync_visibility())
        form.addRow("步骤类型", self.type_box)

        self.id_box = QComboBox()
        self.id_box.setEditable(True)
        self._step_labels = {}
        from ..core.actions import base as action_base
        processors = all_processors()
        actions = action_base.all_actions()
        choices = []
        for sid in sorted(set(processors) | set(actions)):
            if sid in processors:
                spec = processors[sid]
                label = f"{spec.name}（文本处理） · {sid}"
            else:
                spec = actions[sid]
                label = f"{spec.title} · {sid}"
            choices.append((label, sid))
        for label, sid in choices:
            self.id_box.addItem(label, sid)
        self.id_box.setEditable(True)
        self.id_box.setInsertPolicy(QComboBox.NoInsert)
        form.addRow("选择要执行的动作", self.id_box)

        self.input_edit = QLineEdit()
        self.input_edit.setPlaceholderText("{clipboard} / {last.output} / {step2.output}")
        form.addRow("传入内容", self.input_edit)

        self.python_edit = QPlainTextEdit()
        self.python_edit.setPlaceholderText(
            "# input_text 是上一步传入的文本，variables 是事件链变量字典\n"
            "# 把结果赋给 result，或使用 print() 输出\n"
            "result = input_text.strip().upper()")
        self.python_edit.setMinimumHeight(150)
        form.addRow("Python 脚本", self.python_edit)
        self.python_timeout = QSpinBox()
        self.python_timeout.setRange(1, 30)
        self.python_timeout.setSuffix(" 秒")
        form.addRow("脚本超时", self.python_timeout)
        self.python_trusted = QCheckBox(
            "我信任此脚本，并接受它使用当前 Windows 账户权限")
        self.python_trusted.clicked.connect(self._confirm_python_trust)
        form.addRow("运行授权", self.python_trusted)

        self.var_name_edit = QLineEdit()
        self.var_name_edit.setPlaceholderText("例如：selected_text")
        form.addRow("变量名", self.var_name_edit)
        self.var_value_edit = QLineEdit()
        self.var_value_edit.setPlaceholderText("可写固定值或 {last.output}、{clipboard}、{ocr_text}")
        form.addRow("变量内容", self.var_value_edit)

        self.seconds_edit = QLineEdit()
        self.seconds_edit.setPlaceholderText("0.5")
        form.addRow("等待秒数", self.seconds_edit)
        self.times_edit = QLineEdit()
        self.times_edit.setPlaceholderText("1")
        form.addRow("重复次数", self.times_edit)
        self.message_edit = QLineEdit()
        self.message_edit.setPlaceholderText("流程停止时显示的原因")
        form.addRow("结束提示", self.message_edit)

        self.expr_edit = QLineEdit()
        self.expr_edit.setPlaceholderText("has_table / is_empty / contains:关键词")
        form.addRow("条件表达式", self.expr_edit)
        self.then_button = QPushButton("编辑满足条件时的步骤（0）")
        self.then_button.clicked.connect(lambda: self._edit_branch("then"))
        form.addRow("满足条件", self.then_button)
        self.else_button = QPushButton("编辑不满足时的步骤（0）")
        self.else_button.clicked.connect(lambda: self._edit_branch("else"))
        form.addRow("否则", self.else_button)
        self.loop_button = QPushButton("编辑循环内部步骤（0）")
        self.loop_button.clicked.connect(lambda: self._edit_branch("steps"))
        form.addRow("循环内容", self.loop_button)

        self.arg_edit = QPlainTextEdit()
        self.arg_edit.setPlaceholderText('高级参数（可选），JSON 格式，如 {"col": 0, "desc": true}')
        self.arg_edit.setMaximumHeight(90)
        form.addRow("参数 args", self.arg_edit)

        self.note = QLabel("", self)
        self.note.setStyleSheet("color:#475569;font-size:11px")
        self.note.setWordWrap(True)
        form.addRow("", self.note)

        self._sync_visibility()

    def _sync_visibility(self) -> None:
        t = self.type_box.currentData()
        is_action = t == "action"
        self.id_box.setVisible(is_action)
        self.input_edit.setVisible(is_action or t == "python")
        self.python_edit.setVisible(t == "python")
        self.python_timeout.setVisible(t == "python")
        self.python_trusted.setVisible(t == "python")
        self.expr_edit.setVisible(t == "condition")
        self.then_button.setVisible(t == "condition")
        self.else_button.setVisible(t == "condition")
        self.loop_button.setVisible(t == "loop")
        self.arg_edit.setVisible(is_action)
        self.var_name_edit.setVisible(t == "setvar")
        self.var_value_edit.setVisible(t == "setvar")
        self.seconds_edit.setVisible(t == "wait")
        self.times_edit.setVisible(t == "loop")
        self.message_edit.setVisible(t == "stop")
        hints = {
            "action": "可先用“复制选区”“读取剪贴板”或“OCR 抓取文字”采集输入，再用后续步骤处理。变量示例：{last.output}、{step1.output}、{var:selected_text}、{ocr_text}。",
            "python": "脚本以独立进程运行并有超时，避免脚本卡死主界面；这不是安全沙箱。获信任的脚本可读取/修改文件、访问网络、自行截图或录屏（这可能绕过 Easy-Tools 自身窗口的捕获排除）。不要运行来源不明的脚本。输入通过 input_text 传入，variables 提供事件链变量，输出写入 result 或 print()。",
            "condition": "按当前文本判断后执行 then/else 分支。支持：always、never、has_table、is_empty、is_number、contains:关键词、regex:表达式、len_gt:数字、var:变量名。",
            "wait": "在流程中暂停指定秒数（最多 60 秒）。",
            "loop": "重复执行已配置的子步骤（最多 1000 次）。",
            "setvar": "变量可在后续步骤用 {var:变量名} 读取。",
            "stop": "用于校验失败或无法继续时，停止后续步骤并给出原因。",
        }
        self.note.setText(hints.get(t, ""))

    def load(self, step: Dict[str, Any]) -> None:
        self._step = dict(step or {})
        t = str(self._step.get("type", "action"))
        self.type_box.blockSignals(True)
        type_index = self.type_box.findData(t)
        self.type_box.setCurrentIndex(type_index if type_index >= 0 else self.type_box.findData("action"))
        self.type_box.blockSignals(False)
        sid = str(self._step.get("id", ""))
        action_index = self.id_box.findData(sid)
        if action_index >= 0:
            self.id_box.setCurrentIndex(action_index)
        else:
            self.id_box.setEditText(sid)
        self.input_edit.setText(str(self._step.get("input", "") or ""))
        self.python_edit.setPlainText(str(self._step.get("code", "")))
        try:
            timeout = int(float(self._step.get("timeout_seconds", 5) or 5))
        except (TypeError, ValueError):
            timeout = 5
        self.python_timeout.setValue(max(1, min(30, timeout)))
        self.python_trusted.blockSignals(True)
        self.python_trusted.setChecked(self._step.get("trusted") is True)
        self.python_trusted.blockSignals(False)
        self.var_name_edit.setText(str(self._step.get("name", "")))
        self.var_value_edit.setText(str(self._step.get("value", "")))
        self.seconds_edit.setText(str(self._step.get("seconds", "0.3")))
        self.times_edit.setText(str(self._step.get("times", "1")))
        self.message_edit.setText(str(self._step.get("message", "")))
        self.expr_edit.setText(str(self._step.get("expr", "")))
        self.then_button.setText("编辑满足条件时的步骤（%d）" % len(self._step.get("then") or []))
        self.else_button.setText("编辑不满足时的步骤（%d）" % len(self._step.get("else") or []))
        self.loop_button.setText("编辑循环内部步骤（%d）" % len(self._step.get("steps") or []))
        args = self._step.get("args")
        self.arg_edit.setPlainText(json.dumps(args, ensure_ascii=False) if args else "")
        self._sync_visibility()

    def dump(self) -> Dict[str, Any]:
        t = self.type_box.currentData()
        out: Dict[str, Any] = {"type": t}
        if t == "action":
            selected_id = self.id_box.currentData()
            selected_label = (self.id_box.itemText(self.id_box.currentIndex())
                              if self.id_box.currentIndex() >= 0 else "")
            out["id"] = (str(selected_id) if selected_id and
                         self.id_box.currentText() == selected_label else
                         self.id_box.currentText().strip())
            if self.input_edit.text().strip():
                out["input"] = self.input_edit.text()
            txt = self.arg_edit.toPlainText().strip()
            if txt:
                try:
                    out["args"] = json.loads(txt)
                except Exception as e:
                    raise ValueError("args 不是合法 JSON：%s" % e)
        elif t == "python":
            out["code"] = self.python_edit.toPlainText()
            out["input"] = self.input_edit.text()
            out["timeout_seconds"] = self.python_timeout.value()
            out["trusted"] = self.python_trusted.isChecked()
        elif t == "condition":
            out["expr"] = self.expr_edit.text().strip() or "always"
            out["then"] = list(self._step.get("then") or [])
            out["else"] = list(self._step.get("else") or [])
        elif t == "setvar":
            out["name"] = self.var_name_edit.text().strip() or "x"
            out["value"] = self.var_value_edit.text()
        elif t == "wait":
            out["seconds"] = _coerce(self.seconds_edit.text().strip() or "0.3")
        elif t == "stop":
            out["message"] = self.message_edit.text().strip() or "链已中止"
        elif t == "loop":
            out["steps"] = list(self._step.get("steps") or [])
            out["times"] = _coerce(self.times_edit.text().strip() or "1")
        return out

    def _confirm_python_trust(self, checked: bool) -> None:
        if not checked:
            return
        answer = QMessageBox.warning(
            self, "Python 脚本权限确认",
            "Python 脚本将在独立进程中运行，但不会被安全沙箱限制。\n\n"
            "脚本可使用当前 Windows 账户权限，读取或修改文件、访问网络、截屏/录屏（可能绕过 Easy-Tools 自身的窗口捕获排除），"
            "也可能执行其它系统操作。只有确认脚本来源可信并检查过代码后才授权。\n\n"
            "运行时会向脚本传入本步骤文本和事件链变量。",
            QMessageBox.Yes | QMessageBox.Cancel, QMessageBox.Cancel)
        if answer != QMessageBox.Yes:
            self.python_trusted.blockSignals(True)
            self.python_trusted.setChecked(False)
            self.python_trusted.blockSignals(False)

    def _edit_branch(self, key: str) -> None:
        current = [dict(item) for item in (self._step.get(key) or [])]
        child = {"id": "nested", "name": "子流程", "steps": current}
        dialog = ChainEditorDialog(child, self.service, self)
        dialog.setWindowTitle({"then": "满足条件时执行", "else": "不满足条件时执行",
                               "steps": "循环内部步骤"}.get(key, "编辑子流程"))
        if dialog.exec() == QDialog.Accepted:
            self._step[key] = dialog.result_chain().get("steps", [])
            if key == "then":
                self.then_button.setText("编辑满足条件时的步骤（%d）" % len(self._step[key]))
            elif key == "else":
                self.else_button.setText("编辑不满足时的步骤（%d）" % len(self._step[key]))
            else:
                self.loop_button.setText("编辑循环内部步骤（%d）" % len(self._step[key]))


class ChainEditorDialog(QDialog):
    """编辑一条事件链：元信息 + 步骤列表。"""

    def __init__(self, chain: Dict[str, Any], service=None, parent=None) -> None:
        super().__init__(parent)
        self.service = service
        self.setWindowTitle("事件链编排 · 变量化步骤")
        self.resize(900, 680)
        self._chain = dict(chain)
        self._steps: List[Dict[str, Any]] = [dict(s) for s in chain.get("steps") or []]
        # 正在编辑的步骤下标。必须自己记，不能问 list.currentRow()：
        # 选中变化早于提交发生，问 list 会把"旧编辑内容"写进"新选中的行"，
        # 表现为一点步骤就把那一步抹成空白——静默毁数据。
        self._cur = -1

        root = QVBoxLayout(self)
        meta = QFormLayout()
        self.name_edit = QLineEdit(chain.get("name", ""))
        self.id_edit = QLineEdit(chain.get("id", ""))
        self.desc_edit = QLineEdit(chain.get("description", ""))
        self.name_edit.setPlaceholderText("例如：抓取选区并清理文本")
        self.id_edit.setPlaceholderText("例如：chain.clean_selection")
        self.desc_edit.setPlaceholderText("描述流程用途，便于之后识别和维护")
        meta.addRow("名称", self.name_edit)
        meta.addRow("id", self.id_edit)
        meta.addRow("说明", self.desc_edit)
        root.addLayout(meta)
        guide = QLabel("把步骤按顺序串起来：复制选区 / OCR → 处理文本 → 写入变量 → 粘贴或复制结果。\n"
                       "支持变量 {clipboard}、{selection}、{ocr_text}、{last.output}、{step1.output}、{var:name}。"
                       "云端或外部工具节点应显式配置并遵循隐私授权。")
        guide.setObjectName("HintLabel")
        guide.setWordWrap(True)
        root.addWidget(guide)

        body = QHBoxLayout()
        self.list = QListWidget()
        self.list.setSelectionMode(QAbstractItemView.SingleSelection)
        self.list.currentRowChanged.connect(self._on_select)
        body.addWidget(self.list, 1)

        right = QVBoxLayout()
        self.editor = StepEditor(service, self)
        right.addWidget(self.editor)
        bar = QGridLayout()
        controls = (("＋ 采集选区", lambda: self.add_action("clipboard.copy")),
                    ("＋ OCR 文字", lambda: self.add_action("ocr.copy_text")),
                    ("＋ 变量", lambda: self.add_type("setvar")),
                    ("＋ Python", lambda: self.add_type("python")),
                    ("＋ 动作", self.add_step), ("删除步骤", self.del_step),
                    ("上移", lambda: self.move_step(-1)),
                    ("下移", lambda: self.move_step(1)),
                    ("试运行", self.test_run))
        for i, (text, slot) in enumerate(controls):
            b = QPushButton(text, self)
            b.clicked.connect(slot)
            if text == "试运行":
                bar.addWidget(b, 2, 0, 1, 4)
            else:
                bar.addWidget(b, i // 4, i % 4)
        right.addLayout(bar)
        body.addLayout(right, 2)
        root.addLayout(body, 1)

        self.status = QLabel("", self)
        self.status.setWordWrap(True)
        root.addWidget(self.status)

        box = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        box.accepted.connect(self._on_save)
        box.rejected.connect(self.reject)
        root.addWidget(box)

        self._reload_list()

    def _commit_current(self) -> None:
        if 0 <= self._cur < len(self._steps):
            try:
                self._steps[self._cur] = self.editor.dump()
            except ValueError as e:
                self.status.setText("参数错误：%s" % e)

    def _reload_list(self) -> None:
        self.list.blockSignals(True)
        self.list.clear()
        for i, s in enumerate(self._steps):
            self.list.addItem(QListWidgetItem("%d. %s" % (i + 1, describe_step(s))))
        self.list.blockSignals(False)
        if self._steps:
            self.list.setCurrentRow(0)

    def _on_select(self, row: int) -> None:
        self._commit_current()          # 提交的是 self._cur（上一行），不是 row
        self._cur = row
        if 0 <= row < len(self._steps):
            self.editor.load(self._steps[row])

    def add_step(self) -> None:
        self.add_action("text.strip")

    def add_action(self, action_id: str) -> None:
        self._commit_current()
        self._steps.append({"type": "action", "id": action_id})
        self._reload_list()
        self.list.setCurrentRow(len(self._steps) - 1)   # _cur 由 _on_select 推进

    def add_type(self, step_type: str) -> None:
        self._commit_current()
        defaults = {"setvar": {"type": "setvar", "name": "result", "value": "{last.output}"},
                    "python": {"type": "python", "code": "result = input_text",
                               "input": "{last.output}", "timeout_seconds": 5,
                               "trusted": False},
                    "condition": {"type": "condition", "expr": "is_empty", "then": [], "else": []},
                    "wait": {"type": "wait", "seconds": 0.5},
                    "loop": {"type": "loop", "times": 2, "steps": []},
                    "stop": {"type": "stop", "message": "流程已停止"}}
        self._steps.append(defaults.get(step_type, {"type": step_type}))
        self._reload_list()
        self.list.setCurrentRow(len(self._steps) - 1)

    def del_step(self) -> None:
        self._commit_current()
        r = self.list.currentRow()
        if 0 <= r < len(self._steps):
            self._steps.pop(r)
            self._reload_list()
            self._cur = -1

    def move_step(self, delta: int) -> None:
        self._commit_current()
        r = self.list.currentRow()
        nr = r + delta
        if r < 0 or not 0 <= nr < len(self._steps):
            return
        self._steps[r], self._steps[nr] = self._steps[nr], self._steps[r]
        self._reload_list()
        self.list.setCurrentRow(nr)
        self._cur = nr

    def test_run(self) -> None:
        """试运行：用当前步骤构造临时链跑一遍，只看结果不动真实剪贴板。"""
        self._commit_current()
        if self.service is None:
            self.status.setText("无服务上下文，无法试运行")
            return
        python_steps = [s for s in self._steps if s.get("type") == "python"]
        if python_steps:
            if not all(s.get("trusted") is True for s in python_steps):
                self.status.setText("试运行 Python 脚本前，请先逐个检查脚本并勾选信任授权。")
                return
            answer = QMessageBox.warning(
                self, "试运行 Python 脚本",
                "试运行将执行已授权的 Python 脚本。脚本具有当前账户权限，可访问文件、网络及屏幕，可能绕过 Easy-Tools 自身的窗口捕获排除。",
                QMessageBox.Yes | QMessageBox.Cancel, QMessageBox.Cancel)
            if answer != QMessageBox.Yes:
                return
        from ..core.chain import ChainRunner
        chain = {"id": "test", "name": "试运行", "steps": self._steps}
        runner = ChainRunner(self.service.store)
        safe = [s for s in self._steps if s.get("type") != "action"
                or str(s.get("id", "")) not in _SIDE_EFFECT_IDS]
        chain["steps"] = safe or self._steps
        res = runner.run_chain(chain, self.service.action_context())
        skipped = len(self._steps) - len(safe)
        note = "（已跳过 %d 个会产生真实输入/剪贴板副作用的步骤）" % skipped if skipped else ""
        self.status.setText("%s%s\n输出：%s" % (
            "成功" if res.ok else "失败：" + res.message, note,
            (res.output[:500] if res.output else "（空）")))

    def result_chain(self) -> Dict[str, Any]:
        self._commit_current()
        return {
            "id": self.id_edit.text().strip() or self._chain.get("id"),
            "name": self.name_edit.text().strip() or "未命名链",
            "description": self.desc_edit.text().strip(),
            "trigger": self._chain.get("trigger") or {"type": "manual"},
            "steps": self._steps,
        }

    def _on_save(self) -> None:
        self.accept()


#: 试运行时会跳过这些 id——它们会真的敲键盘/改剪贴板。
_SIDE_EFFECT_IDS = {
    "clipboard.copy", "clipboard.set", "clipboard.clear",
    "input.paste", "input.type", "text.input", "snippet.input",
    "clipboard.processor", "ocr.copy_text", "chain.run",
}


if __name__ == "__main__":
    print("chain_editor 是 UI 模块，需在 Qt 环境中使用")
