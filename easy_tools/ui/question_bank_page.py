"""知识库导入与启用管理。"""
from __future__ import annotations

import uuid
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QFileDialog,
                               QHBoxLayout, QHeaderView, QLabel, QMenu,
                               QFormLayout, QMessageBox, QPushButton, QTableWidget,
                               QTableWidgetItem, QVBoxLayout, QWidget)

from ..core.question_bank import inspect_excel_bank, load_question_bank
from .bank_columns_dialog import BankColumnsDialog
from .background import submit


class QuestionBankPage(QWidget):
    MAX_ENABLED = 3

    def __init__(self, banks=None, parent=None, answer_question=None, answer_content_only=False):
        super().__init__(parent)
        self._answer_question = answer_question
        self._rows = []
        self._busy = False
        self._metadata_cache = {}
        self._metadata_pending = set()
        self._pending_bank_id = None
        layout = QVBoxLayout(self)
        intro = QLabel("知识库仅支持 Excel（.xlsx / .xlsm），最多启用 3 个。优先读取正确答案列。\n选中知识库可配置题目、题型、正确答案、答案内容及 A～H 选项列；配置后点击底部“保存并立即生效”。")
        intro.setWordWrap(True)
        intro.setObjectName("HintLabel")
        layout.addWidget(intro)
        self.answer_content_only = QCheckBox("仅显示答案内容，不显示 A/B/C/D（适合选项顺序打乱的组卷）")
        self.answer_content_only.setChecked(bool(answer_content_only))
        self.answer_content_only.setToolTip("按正确答案列映射到答案内容；缺少内容列或选项映射时会提示配置。多选逐条显示内容。")
        layout.addWidget(self.answer_content_only)
        bar = QHBoxLayout()
        add = self.add_button = QPushButton("＋ 添加知识库")
        add.clicked.connect(self.add_banks)
        remove = self.remove_button = QPushButton("移除选中")
        remove.clicked.connect(self.remove_selected)
        configure = self.configure_button = QPushButton("配置题型 / 答案列")
        configure.clicked.connect(self.configure_selected)
        bar.addWidget(add)
        bar.addWidget(configure)
        bar.addWidget(remove)
        bar.addStretch(1)
        self.count_label = QLabel("")
        bar.addWidget(self.count_label)
        layout.addLayout(bar)
        self.table = QTableWidget(0, 5, self)
        self.table.setHorizontalHeaderLabels(["启用", "知识库名称", "问答数", "答案列", "文件路径"])
        self.table.cellDoubleClicked.connect(lambda row, col: self.configure_selected(row) if col == 3 else None)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.ExtendedSelection)
        self.table.setAlternatingRowColors(True)
        self.table.setWordWrap(True)
        self.table.setTextElideMode(Qt.ElideNone)
        self.table.setSortingEnabled(False)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(48)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Fixed)
        header.setSectionResizeMode(1, QHeaderView.Interactive)
        header.setSectionResizeMode(2, QHeaderView.Fixed)
        header.setSectionResizeMode(3, QHeaderView.Interactive)
        header.setSectionResizeMode(4, QHeaderView.Stretch)
        self.table.setColumnWidth(0, 64)
        self.table.setColumnWidth(1, 130)
        self.table.setColumnWidth(2, 70)
        self.table.setColumnWidth(3, 125)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._path_context_menu)
        layout.addWidget(self.table, 1)
        self.set_rows(banks or [])

    def set_rows(self, rows):
        self._rows = [dict(row) for row in (rows or [])]
        for bank in self._rows:
            if Path(str(bank.get("path", ""))).suffix.lower() not in {".xlsx", ".xlsm"}:
                bank["enabled"] = False
        self._render()
        self._request_metadata()

    def _render(self):
        active = {str(bank.get("path")) for bank in self._rows}
        self._metadata_cache = {p: m for p, m in self._metadata_cache.items() if p in active}
        self.table.setRowCount(len(self._rows))
        for r, bank in enumerate(self._rows):
            cb = QCheckBox()
            cb.setObjectName("BankEnabledCheck")
            cb.setToolTip("启用此知识库用于截图、文本和 OCR 问答")
            cb.setChecked(bool(bank.get("enabled")))
            cb.setEnabled(Path(str(bank.get("path", ""))).suffix.lower() in {".xlsx", ".xlsm"})
            cb.stateChanged.connect(lambda state, row=r: self._toggle(row, state))
            holder = QWidget()
            h = QHBoxLayout(holder)
            h.setContentsMargins(0, 0, 0, 0)
            h.setAlignment(Qt.AlignCenter)
            h.addWidget(cb)
            self.table.setCellWidget(r, 0, holder)
            self.table.setItem(r, 1, QTableWidgetItem(str(bank.get("name") or "知识库")))
            self.table.setItem(r, 2, QTableWidgetItem(str(bank.get("entry_count", "—"))))
            mappings = bank.get("column_mapping") or {}
            meta = self._metadata_cache.get(str(bank.get("path")), {})
            names, details = [], []
            if mappings:
                for sheet, mapping in mappings.items():
                    if mapping.get("enabled", True) is False:
                        continue
                    index = mapping.get("answer")
                    headers = meta.get(sheet, {}).get("headers", [])
                    if type(index) is int and index >= 0:
                        from openpyxl.utils import get_column_letter
                        label = headers[index] if index < len(headers) else f"列 {get_column_letter(index + 1)}"
                    else:
                        label = "需配置"
                    if label not in names:
                        names.append(label)
                    details.append(f"{sheet}：{label}")
            else:
                for sheet, sheet_meta in meta.items():
                    index = sheet_meta.get("answer")
                    headers = sheet_meta.get("headers", [])
                    if type(index) is int and index >= 0:
                        label = headers[index] if index < len(headers) else f"列 {index + 1}"
                    else:
                        label = "需配置"
                    if label not in names:
                        names.append(label)
                    details.append(f"{sheet}：{label}")
            summary = "、".join(names) or "自动识别正确答案列"
            tooltip = "\n".join(details) + "\n双击配置列映射（后台读取文件）"
            answer_item = QTableWidgetItem(summary)
            answer_item.setToolTip(tooltip)
            answer_item.setFlags(answer_item.flags() & ~Qt.ItemIsEditable)
            self.table.setItem(r, 3, answer_item)
            path = str(bank.get("path") or "")
            path_item = QTableWidgetItem(path)
            path_item.setToolTip(path)
            path_item.setStatusTip("右键复制完整路径")
            path_item.setData(Qt.UserRole, path)
            path_item.setFlags(path_item.flags() & ~Qt.ItemIsEditable)
            self.table.setItem(r, 4, path_item)
        self.table.resizeRowsToContents()
        for r in range(self.table.rowCount()):
            self.table.setRowHeight(r, max(48, self.table.rowHeight(r)))
        self._update_count()

    def _request_metadata(self):
        paths = [str(Path(bank.get("path", "")).resolve()) for bank in self._rows
                 if bank.get("path") and Path(str(bank.get("path"))).is_file()
                 and Path(str(bank.get("path"))).suffix.lower() in {".xlsx", ".xlsm"}
                 and str(Path(bank.get("path")).resolve()) not in self._metadata_cache
                 and str(Path(bank.get("path")).resolve()) not in self._metadata_pending]
        if not paths:
            return
        self._metadata_pending.update(paths)

        def work():
            result = {}
            for path in paths:
                try:
                    result[path] = inspect_excel_bank(path)
                except Exception:
                    result[path] = {}
            return result

        if not submit(work, self._metadata_ready):
            self._metadata_pending.difference_update(paths)

    def _metadata_ready(self, result, error):
        self._metadata_pending.clear()
        if error or not result:
            return
        self._metadata_cache.update({path: value for path, value in result.items() if value})
        self._render()

    def _path_context_menu(self, pos) -> None:
        index = self.table.indexAt(pos)
        if not index.isValid() or index.column() != 4:
            return
        item = self.table.item(index.row(), 4)
        path = str(item.data(Qt.UserRole) or item.text()) if item else ""
        if not path:
            return
        menu = QMenu(self)
        copy = menu.addAction("复制完整文件路径")
        if menu.exec(self.table.viewport().mapToGlobal(pos)) is copy:
            QApplication.clipboard().setText(path)
            self.count_label.setText("完整路径已复制")

    def _toggle(self, row, state):
        enabled = state in (Qt.Checked, Qt.Checked.value)
        if enabled and sum(bool(x.get("enabled")) for x in self._rows) >= self.MAX_ENABLED:
            QMessageBox.information(self, "启用数量已达上限", "最多同时启用 3 个知识库。")
            self._rows[row]["enabled"] = False
            cb = self.table.cellWidget(row, 0).findChild(QCheckBox)
            cb.blockSignals(True)
            cb.setChecked(False)
            cb.blockSignals(False)
            return
        self._rows[row]["enabled"] = enabled
        self._update_count()

    def _update_count(self):
        n = sum(bool(x.get("enabled")) for x in self._rows)
        self.count_label.setText(f"已启用 {n}/{self.MAX_ENABLED}")

    def _set_busy(self, busy, message=""):
        self._busy = busy
        for button in (self.add_button, self.remove_button, self.configure_button):
            button.setEnabled(not busy)
        if message:
            self.count_label.setText(message)
        elif not busy:
            self._update_count()

    def add_banks(self):
        if self._busy:
            return
        files, _ = QFileDialog.getOpenFileNames(self, "添加知识库", "", "Excel 知识库 (*.xlsx *.xlsm)")
        existing = {str(Path(x.get("path", "")).absolute()).lower() for x in self._rows}
        files = list(dict.fromkeys(f for f in files if str(Path(f).absolute()).lower() not in existing))
        if not files:
            return
        self._set_busy(True, "后台读取知识库中…")

        def work():
            out = []
            for filename in files:
                path = Path(filename).resolve()
                bank = {"id": uuid.uuid4().hex, "name": path.stem, "path": str(path), "enabled": False}
                try:
                    metadata = inspect_excel_bank(path)
                    try:
                        bank["entry_count"] = len(load_question_bank(path))
                        needs_mapping = False
                    except ValueError:
                        needs_mapping = True
                    out.append((bank, metadata, needs_mapping, ""))
                except Exception as exc:
                    out.append((bank, {}, False, str(exc)))
            return out

        if not submit(work, self._import_finished):
            self._set_busy(False, "后台任务较多，请稍后重试。")

    def _import_finished(self, result, error):
        self._set_busy(False)
        if error:
            QMessageBox.warning(self, "读取知识库失败", error)
            return
        for bank, metadata, needs_mapping, failure in result:
            if failure:
                QMessageBox.warning(self, "读取知识库失败", f"{bank['name']}\n{failure}")
                continue
            self._metadata_cache[bank["path"]] = metadata
            if needs_mapping:
                dialog = BankColumnsDialog(bank, self._answer_question, self, metadata=metadata)
                if dialog.exec() != dialog.Accepted:
                    continue
                bank["column_mapping"] = dialog.result_mapping()
                bank["entry_count"] = dialog.entry_count
            self._rows.append(bank)
        self._render()

    def configure_selected(self, row=None):
        if self._busy:
            return
        if type(row) is not int:
            row = self.table.currentRow()
        if row < 0 or row >= len(self._rows):
            QMessageBox.information(self, "配置列", "请先选择一个 Excel 知识库。")
            return
        self.collect()
        bank = dict(self._rows[row])
        self._pending_bank_id = bank.get("id")
        self._set_busy(True, "后台读取表头中…")
        path = bank["path"]
        header_rows = {s: m.get("header_row", 1) for s, m in (bank.get("column_mapping") or {}).items()}
        if not submit(lambda: inspect_excel_bank(path, header_rows), self._configure_ready):
            self._set_busy(False, "后台任务较多，请稍后重试。")

    def _configure_ready(self, metadata, error):
        self._set_busy(False)
        if error:
            QMessageBox.warning(self, "无法读取表头", error)
            return
        row = next((i for i, b in enumerate(self._rows) if b.get("id") == self._pending_bank_id), -1)
        if row < 0:
            return
        bank = self._rows[row]
        self._metadata_cache[bank["path"]] = metadata
        dialog = BankColumnsDialog(bank, self._answer_question, self, metadata=metadata)
        if dialog.exec() == dialog.Accepted:
            bank["column_mapping"] = dialog.result_mapping()
            bank["entry_count"] = dialog.entry_count
            self._render()
            self.table.selectRow(row)

    def remove_selected(self):
        selected = {i.row() for i in self.table.selectionModel().selectedRows()}
        self._rows = [x for i, x in enumerate(self._rows) if i not in selected]
        self._render()

    def collect(self):
        if self._busy:
            raise ValueError("知识库正在后台读取，请等待读取完成后保存。")
        for row in range(self.table.rowCount()):
            self._rows[row]["name"] = self.table.item(row, 1).text().strip() or "知识库"
            cb = self.table.cellWidget(row, 0).findChild(QCheckBox)
            self._rows[row]["enabled"] = cb.isChecked()
        return [dict(row) for row in self._rows]


class LocalModelPage(QWidget):
    def __init__(self, settings=None, model_client=None, parent=None):
        super().__init__(parent)
        self._client = model_client
        self._model_busy = False
        self._settings = dict(settings or {})
        layout = QVBoxLayout(self)
        title = QLabel("本地 / 云端模型")
        title.setStyleSheet("font-size:16px;font-weight:600")
        layout.addWidget(title)
        self.enabled = QCheckBox("启用截图、文本与 OCR 问答")
        self.enabled.setChecked(bool(self._settings.get("enabled", True)))
        layout.addWidget(self.enabled)
        self.prefer_bank_answer = QCheckBox("命中知识库时直接返回原答案")
        self.prefer_bank_answer.setChecked(bool(self._settings.get("prefer_bank_answer", True)))
        layout.addWidget(self.prefer_bank_answer)
        form = QFormLayout()
        self.provider = QComboBox()
        self.provider.addItem("内置 SmolLM2-360M（完全离线）", "builtin")
        self.provider.addItem("CC Switch 当前模型（本机或云端）", "ccswitch")
        self.provider.setCurrentIndex(max(0, self.provider.findData(
            self._settings.get("provider", "builtin"))))
        form.addRow("回答模型：", self.provider)
        layout.addLayout(form)
        note = QLabel("内置 SmolLM2-360M 是轻量检索模型，仅适合根据已启用知识库查找并返回原答案，不适合开放域通用问答。使用 CC Switch 模型可组合截图 OCR、框选和文本提问。CC Switch 配置只读，密钥不复制到 Easy-Tools。选择云端配置后，问题/OCR 文字和可能附带的知识库内容会经 HTTPS 发往云端；隐私捕获屏蔽不会阻止你主动发送给模型。")
        note.setWordWrap(True)
        note.setObjectName("HintLabel")
        layout.addWidget(note)
        self.status = QLabel("点击检查内置模型文件，或导入 CC Switch 当前配置。")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        buttons = QHBoxLayout()
        import_button = self.import_button = QPushButton("一键导入 CC Switch 当前配置")
        import_button.clicked.connect(self.import_ccswitch)
        buttons.addWidget(import_button)
        check = self.check_button = QPushButton("检查当前模型")
        check.clicked.connect(self.check_model)
        buttons.addWidget(check)
        buttons.addStretch(1)
        layout.addLayout(buttons)
        layout.addStretch(1)

    def set_settings(self, settings):
        self._settings = dict(settings or {})
        self.enabled.setChecked(bool(self._settings.get("enabled", True)))
        self.prefer_bank_answer.setChecked(bool(self._settings.get("prefer_bank_answer", True)))
        self.provider.setCurrentIndex(max(0, self.provider.findData(
            self._settings.get("provider", "builtin"))))

    def collect(self):
        return {"enabled": self.enabled.isChecked(),
                "prefer_bank_answer": self.prefer_bank_answer.isChecked(),
                "answer_content_only": bool(self._settings.get("answer_content_only", False)),
                "provider": self.provider.currentData() or "builtin",
                "ccswitch_app": str(self._settings.get("ccswitch_app", "")),
                "ccswitch_base_url": str(self._settings.get("ccswitch_base_url", "")),
                "allow_cloud": bool(self._settings.get("allow_cloud", False))}

    def _set_model_busy(self, busy):
        self._model_busy = busy
        self.import_button.setEnabled(not busy)
        self.check_button.setEnabled(not busy)
        self.provider.setEnabled(not busy)

    def import_ccswitch(self):
        if self._model_busy:
            return
        if not self._client:
            self.status.setText("CC Switch 配置读取组件不可用。")
            return
        self._set_model_busy(True)
        self.status.setText("后台读取 CC Switch 当前配置中…")
        client = self._client
        if not submit(client.candidates, self._candidates_ready):
            self._set_model_busy(False)
            self.status.setText("后台任务较多，请稍后重试。")

    def _candidates_ready(self, candidates, error):
        self._set_model_busy(False)
        if error:
            self.status.setText("读取 CC Switch 配置失败：" + error)
            return
        if not candidates:
            QMessageBox.warning(self, "没有可导入的配置",
                                "未找到 CC Switch 当前启用的 Claude / Codex 模型配置。请先在 CC Switch 中切换到一个模型。")
            return
        chosen = candidates[0]
        if len(candidates) > 1:
            from PySide6.QtWidgets import QInputDialog
            labels = [f"{x['app'].title()} · {x['name']} · {x['model']} · "
                      f"{'本机' if x['local'] else '云端'}" for x in candidates]
            label, ok = QInputDialog.getItem(self, "选择当前模型配置",
                                             "导入以下已启用配置：", labels, 0, False)
            if not ok:
                return
            chosen = candidates[labels.index(label)]
        allow_cloud = False
        if not chosen["local"]:
            answer = QMessageBox.warning(
                self, "启用云端模型",
                f"即将导入 {chosen['name']}（{chosen['model']}）。\n\n"
                f"地址：{chosen['base_url']}\n\n"
                "截图识别出的文字、手动输入的问题和可能附带的知识库匹配内容，将发送到该云端服务。\n"
                "Easy-Tools 不保存 API 密钥，但服务提供方会按其政策处理请求。",
                QMessageBox.Yes | QMessageBox.Cancel, QMessageBox.Cancel)
            if answer != QMessageBox.Yes:
                return
            allow_cloud = True
        self._settings.update({"provider": "ccswitch", "ccswitch_app": chosen["app"],
                               "ccswitch_base_url": chosen["base_url"],
                               "allow_cloud": allow_cloud})
        self.provider.setCurrentIndex(self.provider.findData("ccswitch"))
        self.status.setText(
            f"已导入：{chosen['app'].title()} · {chosen['name']} · {chosen['model']} · "
            f"{'云端 HTTPS' if allow_cloud else '本机'}。保存设置后生效；密钥仍只读 CC Switch。")
        self.status.setStyleSheet("color:#27834b")

    def check_model(self):
        if self._model_busy:
            return
        self._set_model_busy(True)
        self.status.setText("后台检查模型中…")
        builtin = self.provider.currentData() == "builtin"
        client, settings = self._client, dict(self._settings)

        def work():
            if builtin:
                from ..core.builtin_model import BuiltinModel
                assets = BuiltinModel().assets()
                return {"available": assets["available"], "builtin": True,
                    "reason": "内置模型权重与运行时已就绪；首次回答会后台加载模型。" if assets["available"] else "缺少离线模型文件或运行时，请重新安装完整安装包。"}
            return client.discover(str(settings.get("ccswitch_app") or ""),
                bool(settings.get("allow_cloud", False)),
                expected_url=str(settings.get("ccswitch_base_url") or "")) if client else {
                    "available": False, "reason": "CC Switch 模型服务未连接。"}

        if not submit(work, self._model_checked):
            self._set_model_busy(False)
            self.status.setText("后台任务较多，请稍后重试。")

    def _model_checked(self, result, error):
        self._set_model_busy(False)
        if error:
            self.status.setText("模型检查失败：" + error)
            return
        if result.get("builtin"):
            self.status.setText(result["reason"])
            return
        if result.get("available"):
            kind = "本机" if result.get("local") else "云端 HTTPS"
            self.status.setText(f"CC Switch {kind}模型可用：{result.get('name')} / {result.get('model')}")
            self.status.setStyleSheet("color:#27834b")
        else:
            self.status.setText(result.get("reason", "本机模型不可用。"))
            self.status.setStyleSheet("color:#b34242")
