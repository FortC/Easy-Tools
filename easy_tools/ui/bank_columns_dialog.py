"""Visible, editable Excel question/answer column mappings."""
from __future__ import annotations

import copy
import threading

from PySide6.QtCore import QObject, Qt, Signal, Slot
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDialog,
    QDialogButtonBox, QFormLayout, QGridLayout, QGroupBox, QHBoxLayout, QLabel,
    QMessageBox, QPushButton, QSpinBox, QVBoxLayout)
from openpyxl.utils import get_column_letter

from ..core.question_bank import (detect_columns, inspect_excel_bank,
    load_question_bank, mapping_ai_prompt, mapping_from_ai, validate_column_mapping)
from .theme import settings_qss
from .background import submit


class _AiResult(QObject):
    ready = Signal(object, str)


class BankColumnsDialog(QDialog):
    def __init__(self, bank: dict, answer_question=None, parent=None, metadata=None):
        super().__init__(parent)
        self.setWindowTitle("知识库列配置")
        self.resize(650, 560)
        self.setStyleSheet(settings_qss())
        self._path = str(bank["path"])
        self._answer_question = answer_question
        self._meta = copy.deepcopy(metadata) if metadata is not None else inspect_excel_bank(self._path)
        self._closed = False
        self._mappings = {name: copy.deepcopy(mapping) for name, mapping in
                          (bank.get("column_mapping") or {}).items() if name in self._meta}
        self._sheet = None
        self._busy = False
        self._result = None
        self.entry_count = 0
        for name, meta in self._meta.items():
            if name not in self._mappings:
                self._mappings[name] = {key: value for key, value in meta.items() if key != "headers"}
                self._mappings[name]["enabled"] = bool(meta["headers"])
                self._mappings[name]["has_header"] = meta["question"] is not None and meta["answer"] is not None
            configured = self._mappings[name]
            if metadata is None and configured.get("header_row", 1) != meta["header_row"]:
                self._meta[name] = inspect_excel_bank(self._path, {name: configured["header_row"]})[name]

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 18)
        intro = QLabel("先确认题目、题型和正确答案列。答案为 A/B/C/D 时，可选择独立的答案内容列，或配置下方各选项内容列。")
        intro.setWordWrap(True)
        root.addWidget(intro)
        form = QFormLayout()
        self.sheets = QComboBox()
        self.sheets.addItems(self._meta)
        form.addRow("工作表", self.sheets)
        self.enabled = QCheckBox("读取此工作表")
        form.addRow("启用", self.enabled)
        row_bar = QHBoxLayout()
        self.header_row = QSpinBox()
        self.header_row.setRange(1, 1000)
        row_bar.addWidget(self.header_row)
        self.read = QPushButton("读取本行表头")
        self.read.clicked.connect(self.reload_headers)
        row_bar.addWidget(self.read)
        form.addRow("表头行号", row_bar)
        self.question = QComboBox()
        self.answer = QComboBox()
        self.question_type = QComboBox()
        self.content = QComboBox()
        form.addRow("题目列", self.question)
        form.addRow("题型列（可选）", self.question_type)
        form.addRow("正确答案列", self.answer)
        form.addRow("答案内容列（可选）", self.content)
        root.addLayout(form)
        self.confirm_header = QCheckBox("当前行是表头（无表头请取消；AI 仅发送确认后的表头）")
        root.addWidget(self.confirm_header)
        option_box = QGroupBox("选项内容列（正确答案为字母时使用，可留空）")
        grid = QGridLayout(option_box)
        self.options = {}
        for index, key in enumerate("ABCDEFGH"):
            combo = self.options[key] = QComboBox()
            grid.addWidget(QLabel(key), index // 2, index % 2 * 2)
            grid.addWidget(combo, index // 2, index % 2 * 2 + 1)
        root.addWidget(option_box)
        actions = QHBoxLayout()
        self.auto = QPushButton("自动识别（本地）")
        self.auto.clicked.connect(self.auto_detect)
        self.ai = QPushButton("AI 辅助识别")
        self.ai.setEnabled(callable(answer_question))
        self.ai.clicked.connect(self.ai_detect)
        self.confirm_header.toggled.connect(lambda checked: self.ai.setEnabled(
            checked and callable(self._answer_question) and not self._busy))
        actions.addWidget(self.auto)
        actions.addWidget(self.ai)
        actions.addStretch(1)
        root.addLayout(actions)
        self.status = QLabel("AI 仅使用当前表头名称，调用配置中的模型；不会发送题目、答案内容或文件。识别后请检查，再确认保存。")
        self.status.setWordWrap(True)
        root.addWidget(self.status)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Save).setText("确认配置")
        buttons.button(QDialogButtonBox.Cancel).setText("取消")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)
        self._buttons = buttons
        self.sheets.currentTextChanged.connect(self._change_sheet)
        self._change_sheet(self.sheets.currentText())

    def _remember(self):
        if self._sheet is None:
            return
        self._mappings[self._sheet] = {"enabled": self.enabled.isChecked(),
            "header_row": self._meta[self._sheet]["header_row"],
            "has_header": self.confirm_header.isChecked(),
            "question": self.question.currentData(), "answer": self.answer.currentData(),
            "answer_content": self.content.currentData(),
            "question_type": self.question_type.currentData(),
            "options": {key: combo.currentData() for key, combo in self.options.items()
                        if combo.currentData() is not None}}

    def _populate(self, mapping):
        headers = self._meta[self._sheet]["headers"]
        for combo in [self.question, self.answer, self.content, self.question_type, *self.options.values()]:
            combo.clear()
            combo.addItem("未选择", None)
            for index, label in enumerate(headers):
                combo.addItem(f"{get_column_letter(index + 1)} · {label or '（空表头）'}", index)
        for combo, index in [(self.question, mapping.get("question")),
                (self.answer, mapping.get("answer")), (self.content, mapping.get("answer_content")),
                (self.question_type, mapping.get("question_type", self._meta[self._sheet].get("question_type"))),
                *((combo, (mapping.get("options") or {}).get(key)) for key, combo in self.options.items())]:
            combo.setCurrentIndex(max(0, combo.findData(index)))
        self.header_row.setValue(self._meta[self._sheet]["header_row"])
        self.enabled.setChecked(mapping.get("enabled", True))
        self.confirm_header.setChecked(mapping.get("has_header",
            self._meta[self._sheet].get("question") is not None and self._meta[self._sheet].get("answer") is not None))
        self.ai.setEnabled(self.confirm_header.isChecked() and callable(self._answer_question) and not self._busy)

    @Slot(str)
    def _change_sheet(self, name):
        self._remember()
        self._sheet = name
        self._populate(self._mappings[name])

    def _io_busy(self, busy):
        self._busy = busy
        for cls in (QComboBox, QCheckBox, QSpinBox, QPushButton):
            for widget in self.findChildren(cls):
                if widget is not self._buttons.button(QDialogButtonBox.Cancel):
                    widget.setEnabled(not busy)
        if not busy:
            self.ai.setEnabled(self.confirm_header.isChecked() and callable(self._answer_question))

    def reject(self):
        self._closed = True
        super().reject()

    def reload_headers(self):
        if self._busy:
            return
        path, sheet, row = self._path, self._sheet, self.header_row.value()
        self._io_busy(True)
        self.status.setText("后台读取表头中，可取消关闭…")
        if not submit(lambda: inspect_excel_bank(path, {sheet: row})[sheet], self._headers_ready):
            self._io_busy(False)
            self.status.setText("后台任务较多，请稍后重试。")

    def _headers_ready(self, metadata, error):
        if self._closed:
            return
        self._io_busy(False)
        if error:
            self.status.setText("读取表头失败：" + error)
            return
        self._meta[self._sheet] = metadata
        self.auto_detect()

    def auto_detect(self):
        meta = self._meta[self._sheet]
        mapping = {"enabled": True, "header_row": meta["header_row"], **detect_columns(meta["headers"])}
        self._populate(mapping)
        self.status.setText("已在本地识别列，请检查题目列、正确答案列和选项内容列；未识别的字段可手动选择。")

    def ai_detect(self):
        if self._busy or not callable(self._answer_question) or not self.confirm_header.isChecked():
            return
        headers = list(self._meta[self._sheet]["headers"])
        row = self._meta[self._sheet]["header_row"]
        model = self._answer_question
        emitter = _AiResult(QApplication.instance())
        emitter.ready.connect(self._ai_finished, Qt.QueuedConnection)
        emitter.ready.connect(emitter.deleteLater, Qt.QueuedConnection)
        self._busy = True
        self.sheets.setEnabled(False)
        self.header_row.setEnabled(False)
        self.read.setEnabled(False)
        self.confirm_header.setEnabled(False)
        self._buttons.button(QDialogButtonBox.Save).setEnabled(False)
        self.auto.setEnabled(False)
        self.ai.setEnabled(False)
        self.status.setText("AI 正在识别当前表头；不会发送题目或答案数据…")

        def worker():
            try:
                mapping = mapping_from_ai(model(mapping_ai_prompt(headers), []), headers, row)
                emitter.ready.emit(mapping, "")
            except Exception as exc:
                emitter.ready.emit(None, str(exc))

        threading.Thread(target=worker, name="bank-column-ai", daemon=True).start()

    @Slot(object, str)
    def _ai_finished(self, mapping, error):
        if self._closed:
            return
        self._busy = False
        for widget in (self.sheets, self.header_row, self.auto, self.read, self.confirm_header):
            widget.setEnabled(True)
        self._buttons.button(QDialogButtonBox.Save).setEnabled(True)
        self.ai.setEnabled(self.confirm_header.isChecked() and callable(self._answer_question))
        if error:
            self.status.setText("AI 识别未成功，请使用本地自动识别或手动选择：" + error)
            return
        self._populate(mapping)
        self.status.setText("AI 已填写当前工作表的列映射，请检查后确认保存。")

    def accept(self):
        if self._busy:
            return
        self._remember()
        try:
            for name, mapping in self._mappings.items():
                validate_column_mapping(mapping, len(self._meta[name]["headers"]))
        except Exception as exc:
            QMessageBox.warning(self, "列配置未保存", str(exc))
            return
        mappings, path = copy.deepcopy(self._mappings), self._path
        self._pending_mapping = mappings
        self._io_busy(True)
        self.status.setText("后台校验知识库中，窗口可正常移动或取消…")
        if not submit(lambda: len(load_question_bank(path, mappings)), self._validated):
            self._io_busy(False)
            self.status.setText("后台任务较多，请稍后重试。")

    def _validated(self, count, error):
        if self._closed:
            return
        self._io_busy(False)
        if error:
            self.status.setText("列配置未保存：" + error)
            return
        self.entry_count = count
        self._result = self._pending_mapping
        super().accept()

    def result_mapping(self):
        return copy.deepcopy(self._result)
