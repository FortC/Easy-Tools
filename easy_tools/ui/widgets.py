"""配置中心通用控件：字段描述 + 表单对话框 + JSON 表格编辑器。

为什么做通用件：配置中心要编辑的实体只有四类——动作、片段、规则、事件链，
差别仅仅是"字段不一样"。为每类各写一套 CRUD 就是四份重复代码，
以后加字段要在四个地方同步改。用字段描述驱动，字段变了 UI 自动跟上。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDialog,
                               QDialogButtonBox, QDoubleSpinBox, QFormLayout,
                               QHBoxLayout, QHeaderView, QLabel, QLineEdit,
                               QListWidget, QListWidgetItem, QPlainTextEdit,
                               QPushButton, QSpinBox, QTableWidget,
                               QTableWidgetItem, QVBoxLayout, QWidget)

from . import icons


class IconPickerDialog(QDialog):
    """内置图标选择器：搜索过滤 + 网格预览，返回 Lucide 图标名。

    也允许直接输入 emoji/文字（下面那行输入框），照顾不想用内置图标的用户。
    """

    def __init__(self, current: str = "", parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("选择图标")
        self.resize(560, 420)
        self._selected = ""

        lay = QVBoxLayout(self)
        self._search = QLineEdit(self)
        self._search.setPlaceholderText("搜索图标（按名称过滤）…")
        self._search.textChanged.connect(self._filter)
        lay.addWidget(self._search)

        self._grid = QListWidget(self)
        self._grid.setViewMode(QListWidget.IconMode)
        self._grid.setIconSize(QSize(22, 22))
        self._grid.setGridSize(QSize(76, 60))
        self._grid.setResizeMode(QListWidget.Adjust)
        self._grid.setMovement(QListWidget.Static)
        self._grid.setSpacing(6)
        self._grid.itemDoubleClicked.connect(lambda _i: self._accept())
        self._grid.itemClicked.connect(self._pick)
        lay.addWidget(self._grid, 1)

        custom = QHBoxLayout()
        custom.addWidget(QLabel("或直接使用文字/emoji：", self))
        self._custom = QLineEdit(current if not icons.has_icon(current) else "", self)
        self._custom.setFixedWidth(160)
        self._custom.textChanged.connect(lambda _t: self._grid.clearSelection())
        custom.addWidget(self._custom)
        custom.addStretch(1)
        lay.addLayout(custom)

        box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, self)
        box.accepted.connect(self._accept)
        box.rejected.connect(self.reject)
        lay.addWidget(box)

        self._all = icons.icon_names()
        self._populate("")
        if icons.has_icon(current):
            for i in range(self._grid.count()):
                if self._grid.item(i).text() == current:
                    self._grid.setCurrentItem(self._grid.item(i))
                    break

    def _populate(self, keyword: str) -> None:
        self._grid.clear()
        for name in self._all:
            if keyword and keyword not in name:
                continue
            it = QListWidgetItem(name)
            ic = icons.icon(name, "#2458c5", 20)
            if ic is not None:
                it.setIcon(ic)
            it.setToolTip(name)
            self._grid.addItem(it)

    def _filter(self, text: str) -> None:
        self._populate((text or "").strip())

    def _pick(self, item: QListWidgetItem) -> None:
        self._selected = item.text()
        self._custom.clear()

    def _accept(self) -> None:
        cur = self._grid.currentItem()
        if cur is not None:
            self._selected = cur.text()
        elif not self._selected:
            self._selected = self._custom.text().strip()
        self.accept()

    def selected(self) -> str:
        return self._selected


@dataclass
class Field:
    key: str
    label: str
    type: str = "text"          # text|bool|int|float|enum|list|multi|textarea|select_one
    options: List[str] = field(default_factory=list)
    hint: str = ""
    default: Any = None
    # 枚举项的显示名映射：存的仍是 options 里的原始值，只是下拉框显示中文。
    # 留空则原样显示——不影响任何既有字段。
    labels: Dict[str, str] = field(default_factory=dict)


@dataclass
class Column:
    key: str
    label: str
    width: int = 160
    # 整行显示格式化：接收整条记录返回展示文本。留空按原值渲染。
    fmt: Optional[Callable[[Dict[str, Any]], str]] = None


def _widget_for(f: Field, value: Any, choices: Dict[str, List[str]]):
    """按字段类型造输入控件，并返回取值闭包。"""
    if f.type == "icon":
        box = QWidget()
        h = QHBoxLayout(box)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(6)
        edit = QLineEdit()
        edit.setText(str(value if value is not None else ""))
        edit.setPlaceholderText("图标名或 emoji")
        btn = QPushButton("选择…")

        def _pick():
            dlg = IconPickerDialog(edit.text(), box.window())
            if dlg.exec() == QDialog.Accepted and dlg.selected():
                edit.setText(dlg.selected())

        btn.clicked.connect(_pick)
        h.addWidget(edit, 1)
        h.addWidget(btn)
        return box, lambda: edit.text().strip()
    if f.type == "bool":
        w = QCheckBox()
        w.setChecked(bool(value))
        return w, lambda: w.isChecked()
    if f.type == "int":
        w = QSpinBox()
        w.setRange(-1000000, 1000000)
        w.setValue(int(value or 0))
        return w, lambda: int(w.value())
    if f.type == "float":
        w = QDoubleSpinBox()
        w.setRange(0.0, 100.0)
        w.setDecimals(2)
        w.setSingleStep(0.05)
        w.setValue(float(value or 0))
        return w, lambda: round(float(w.value()), 3)
    if f.type == "enum":
        w = QComboBox()
        for opt in f.options:
            w.addItem(f.labels.get(opt, str(opt)), opt)
        target = None
        for i, opt in enumerate(f.options):
            if str(opt) == str(value):
                target = i
                break
        if target is not None:
            w.setCurrentIndex(target)
        # 取值必须返回原始值而非显示名，否则会把中文写进配置
        return w, lambda: (w.itemData(w.currentIndex())
                           if w.currentIndex() >= 0 else "")
    if f.type == "multi":
        w = QListWidget()
        for c in choices.get(f.key, []):
            it = QListWidgetItem(c)
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Checked if c in (value or []) else Qt.Unchecked)
            w.addItem(it)
        return w, (lambda: [w.item(i).text() for i in range(w.count())
                             if w.item(i).checkState() == Qt.Checked])
    if f.type == "list":
        w = QPlainTextEdit()
        w.setPlainText("\n".join(str(x) for x in (value or [])))
        w.setMaximumHeight(130)
        return w, (lambda: [ln.strip() for ln in w.toPlainText().splitlines()
                             if ln.strip()])
    if f.type == "textarea":
        w = QPlainTextEdit()
        w.setPlainText(str(value or ""))
        return w, lambda: w.toPlainText()
    if f.type == "select_one":
        w = QComboBox()
        w.addItem("")
        w.addItems(choices.get(f.key, []))
        if value in (choices.get(f.key) or []):
            w.setCurrentText(str(value))
        return w, lambda: w.currentText()
    w = QLineEdit()
    w.setText(str(value if value is not None else ""))
    return w, lambda: w.text()


class JsonFormDialog(QDialog):
    """按字段描述渲染的编辑对话框。动作/片段/规则共用同一个。"""

    def __init__(self, title: str, fields: List[Field], item: Dict[str, Any],
                 choices: Optional[Dict[str, List[str]]] = None, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(480)
        self._fields = fields
        self.result: Dict[str, Any] = dict(item)
        self._getters = []

        form = QFormLayout(self)
        for f in fields:
            w, get = _widget_for(f, item.get(f.key, f.default), choices or {})
            form.addRow(f.label, w)
            if f.hint:
                hint = QLabel(f.hint)
                hint.setStyleSheet("color:#475569;font-size:11px")
                hint.setWordWrap(True)
                form.addRow("", hint)
            self._getters.append((f, get))

        box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        box.accepted.connect(self._accept)
        box.rejected.connect(self.reject)
        form.addRow(box)

    def _accept(self) -> None:
        for f, get in self._getters:
            self.result[f.key] = get()
        self.accept()


class JsonTableEditor(QWidget):
    """通用 JSON 列表编辑器：新增 / 复制 / 编辑 / 删除 / 上移 / 下移。"""

    rows_changed = Signal()

    def __init__(self, columns: List[Column], fields: List[Field], id_prefix: str,
                 choices_fn: Optional[Callable[[], Dict[str, List[str]]]] = None,
                 title: str = "条目", parent=None) -> None:
        super().__init__(parent)
        self.columns = columns
        self.fields = fields
        self.id_prefix = id_prefix
        self.choices_fn = choices_fn or (lambda: {})
        self.title = title
        self.prepare_edit = None
        self.apply_edit = None
        self._rows: List[Dict[str, Any]] = []
        self._counter = 0

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.table = QTableWidget(0, len(columns), self)
        self.table.setHorizontalHeaderLabels([c.label for c in columns])
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(34)
        self.table.setAlternatingRowColors(True)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.itemDoubleClicked.connect(lambda _i: self.edit_current())
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.table.horizontalHeader().setStretchLastSection(True)
        for i, c in enumerate(self.columns):
            self.table.setColumnWidth(i, c.width)
        lay.addWidget(self.table)

        bar = QHBoxLayout()
        for text, slot in (("新增", self.add_item), ("复制", self.copy_item),
                           ("编辑", self.edit_current), ("删除", self.remove_current),
                           ("上移", lambda: self.move_current(-1)),
                           ("下移", lambda: self.move_current(1))):
            b = QPushButton(text, self)
            b.clicked.connect(slot)
            bar.addWidget(b)
        bar.addStretch(1)
        lay.addLayout(bar)

    # ------------------------------------------------------------ 数据
    def set_rows(self, rows: List[Dict[str, Any]]) -> None:
        self._rows = [dict(r) for r in rows or []]
        for r in self._rows:
            i = str(r.get("id", ""))
            if i.startswith(self.id_prefix + "."):
                try:
                    self._counter = max(self._counter, int(i.rsplit(".", 1)[-1]))
                except ValueError:
                    pass
        self._reload()

    def rows(self) -> List[Dict[str, Any]]:
        return [dict(r) for r in self._rows]

    def _reload(self) -> None:
        self.table.blockSignals(True)
        self.table.setRowCount(len(self._rows))
        for r, item in enumerate(self._rows):
            for c, col in enumerate(self.columns):
                if col.fmt is not None:
                    v = col.fmt(item)
                else:
                    v = item.get(col.key, "")
                    if isinstance(v, (list, tuple)):
                        v = ", ".join(str(x) for x in v)
                    elif isinstance(v, bool):
                        v = "是" if v else "否"
                q = QTableWidgetItem(str(v if v is not None else ""))
                if col.key == "icon" and icons.has_icon(str(v)):
                    q.setIcon(icons.icon(str(v), "#2458c5", 16))
                q.setToolTip(str(v))
                self.table.setItem(r, c, q)
        self.table.blockSignals(False)
        self.rows_changed.emit()
        self._select_visible(self.table.currentRow())

    def _select_visible(self, preferred: int) -> None:
        visible = [i for i in range(len(self._rows)) if not self.table.isRowHidden(i)]
        if visible:
            self.table.setCurrentCell(min(visible, key=lambda i: abs(i - preferred)), 0)
        else:
            self.table.clearSelection()
            self.table.setCurrentCell(-1, -1)

    def _current_row(self) -> int:
        r = self.table.currentRow()
        return r if 0 <= r < len(self._rows) and not self.table.isRowHidden(r) else -1

    def _next_id(self) -> str:
        self._counter += 1
        return "%s.%d" % (self.id_prefix, self._counter)

    # ------------------------------------------------------------ 操作
    def add_item(self) -> None:
        base: Dict[str, Any] = {"id": self._next_id()}
        for f in self.fields:
            if f.key in base:
                continue
            if f.default is not None:
                base[f.key] = f.default
            elif f.type in ("list", "multi"):
                base[f.key] = []
            elif f.type == "bool":
                base[f.key] = False
            elif f.type in ("int", "float"):
                base[f.key] = 0
            else:
                base[f.key] = ""
        base.update(getattr(self, "new_item_defaults", {}))
        dlg = JsonFormDialog("新增%s" % self.title, self.fields, base, self.choices_fn(), self)
        if dlg.exec() == QDialog.Accepted:
            self._rows.append(dlg.result)
            self._reload()
            self._select_visible(len(self._rows) - 1)

    def copy_item(self) -> None:
        r = self._current_row()
        if r < 0:
            return
        new = dict(self._rows[r])
        new["id"] = self._next_id()
        new["name"] = str(new.get("name", "")) + " 副本"
        self._rows.insert(r + 1, new)
        self._reload()

    def edit_current(self) -> None:
        r = self._current_row()
        if r < 0:
            return
        original = dict(self._rows[r])
        fields, item = self.fields, original
        if callable(self.prepare_edit):
            fields, item = self.prepare_edit(dict(original))
        dlg = JsonFormDialog("编辑%s" % self.title, fields, item,
                             self.choices_fn(), self)
        if dlg.exec() == QDialog.Accepted:
            updated = dlg.result
            if callable(self.apply_edit):
                updated = self.apply_edit(original, updated)
            self._rows[r] = updated
            self._reload()
            self._select_visible(r)

    def remove_current(self) -> None:
        r = self._current_row()
        if r >= 0:
            self._rows.pop(r)
            self._reload()

    def move_current(self, delta: int) -> None:
        r = self._current_row()
        visible = [i for i in range(len(self._rows)) if not self.table.isRowHidden(i)]
        if r not in visible or not 0 <= visible.index(r) + delta < len(visible):
            return
        nr = visible[visible.index(r) + delta]
        self._rows[r], self._rows[nr] = self._rows[nr], self._rows[r]
        for i, row in enumerate(self._rows):
            if "order" in row:
                row["order"] = (i + 1) * 10
        self._reload()
        self._select_visible(nr)
