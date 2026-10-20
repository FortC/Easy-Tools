"""配置中心（方案 2.7）。

硬要求：**保存即生效**。每次点保存都直接走 ConfigStore.update()，
由它广播变更，钩子/面板/规则即时重载，无需重启。

动作的 params 用 JSON 文本编辑并在保存前校验解析：不为每个 kind 维护
一份字段描述（那是十二份要同步改的样板），而校验保证写错立刻报错、
不会静默生效。
"""

from __future__ import annotations

import json
from typing import Any, Dict, List

from PySide6.QtCore import QSize, Qt, Signal, Slot
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox,
                               QDoubleSpinBox,
                               QFileDialog, QFormLayout, QFrame, QGroupBox, QHBoxLayout, QLabel,
                               QListWidget, QListWidgetItem,
                               QLineEdit, QMessageBox, QPlainTextEdit, QPushButton,
                               QSpinBox, QStackedWidget, QTextBrowser, QVBoxLayout, QWidget)

from ..core.actions import base as action_base
from ..core.actions.processors import all_processors
from ..core.log import get_logger
from ..core.paths import config_path, data_dir
from . import icons, labels, theme
from .background import submit
from .chain_editor import ChainEditorDialog
from .widgets import Column, Field, JsonTableEditor

log = get_logger("settings")


def _make(f: Field, value: Any):
    """按字段类型造控件，返回 (控件, 取值闭包, 赋值闭包)。"""
    if f.type == "bool":
        w = QCheckBox()
        w.setChecked(bool(value))
        return w, (lambda: w.isChecked()), (lambda v: w.setChecked(bool(v)))
    if f.type == "int":
        w = QSpinBox()
        w.setRange(-1000000, 1000000)
        w.setValue(int(value or 0))
        return w, (lambda: int(w.value())), (lambda v: w.setValue(int(v)))
    if f.type == "float":
        w = QDoubleSpinBox()
        w.setRange(0.0, 100.0)
        w.setDecimals(2)
        w.setSingleStep(0.02)
        w.setValue(float(value or 0))
        return w, (lambda: round(float(w.value()), 3)), (lambda v: w.setValue(float(v)))
    if f.type == "enum":
        w = QComboBox()
        for opt in f.options:
            w.addItem(f.labels.get(opt, opt), opt)
        w.setCurrentIndex(max(0, w.findData(value)))
        return w, (lambda: w.currentData()), (lambda v: w.setCurrentIndex(max(0, w.findData(v))))
    if f.type == "group_order":
        from PySide6.QtWidgets import QAbstractItemView
        host = QWidget()
        lay = QVBoxLayout(host)
        lay.setContentsMargins(0, 0, 0, 0)
        listing = QListWidget()
        listing.setDragDropMode(QAbstractItemView.InternalMove)
        host.setMaximumHeight(390)
        lay.setAlignment(Qt.AlignTop)
        listing.setFixedHeight(320)
        listing.setStyleSheet("QListWidget { background: white; color: #172033; border: 1px solid #8493a8; border-radius: 6px; } QListWidget::item { padding: 6px 10px; min-height: 22px; } QListWidget::item:selected { background: #2458c5; color: white; } QListWidget::item:hover:!selected { background: #edf3fc; }")
        lay.addWidget(listing)
        def setter(values):
            listing.clear()
            listing.addItems(list(dict.fromkeys(values or [])))
            listing.setCurrentRow(0)
        def move(delta):
            row = listing.currentRow()
            if row >= 0 and 0 <= row + delta < listing.count():
                item = listing.takeItem(row)
                listing.insertItem(row + delta, item)
                listing.setCurrentRow(row + delta)
        buttons = QHBoxLayout()
        for title, delta in (("上移", -1), ("下移", 1)):
            button = QPushButton(title)
            button.clicked.connect(lambda checked=False, d=delta: move(d))
            buttons.addWidget(button)
        buttons.addStretch()
        lay.addLayout(buttons)
        setter(value)
        return host, (lambda: [listing.item(i).text() for i in range(listing.count())]), setter
    if f.type == "textarea":
        w = QPlainTextEdit()
        w.setPlainText(str(value or ""))
        w.setMinimumHeight(80)
        return w, (lambda: w.toPlainText()), (lambda v: w.setPlainText(str(v)))
    w = QLineEdit()
    w.setText(str(value if value is not None else ""))
    return w, (lambda: w.text()), (lambda v: w.setText(str(v)))


class FormPage(QWidget):
    """由字段描述渲染的一页设置，读写落到同一个 dict。"""

    def __init__(self, fields: List[Field], data: Dict[str, Any], parent=None) -> None:
        super().__init__(parent)
        self._data = dict(data)
        self._getters = []
        form = QFormLayout(self)
        form.setContentsMargins(22, 18, 22, 22)
        form.setHorizontalSpacing(18)
        form.setVerticalSpacing(11)
        for f in fields:
            w, get, setter = _make(f, self._data.get(f.key, f.default))
            form.addRow(f.label, w)
            if f.hint:
                hint = QLabel(f.hint)
                hint.setStyleSheet("color:#475569;font-size:11px")
                hint.setWordWrap(True)
                form.addRow("", hint)
            self._getters.append((f.key, get, setter))

    def collect(self) -> Dict[str, Any]:
        out = dict(self._data)
        for key, get, _s in self._getters:
            out[key] = get()
        return out


class SidebarTabs(QWidget):
    """左侧导航 + 内容堆栈，保持现有 addTab/currentWidget 调用方式。"""

    currentChanged = Signal(int)

    # 每个导航页对应的 Lucide 图标名（easy_tools/ui/icons/，ISC 许可）。
    # 图标缺失时条目仍创建，只是没有图标——不阻塞配置中心启动。
    _MARKS = {
        "软件更新": "rocket", "动作库": "layout-grid", "文本片段": "text",
        "特定操作区规则": "crosshair", "事件链": "workflow",
        "触发设置": "mouse-pointer-click", "面板样式": "palette",
        "分类排序": "list-ordered",
        "OCR 设置": "scan-text", "文本输入": "keyboard",
        "隐私保护": "shield", "知识库": "database",
        "本机模型": "cpu", "数据/导入导出": "arrow-down-up",
    }

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._titles: List[str] = []
        self._pages = QStackedWidget(self)
        self._pages.setObjectName("SettingsPages")
        self._nav = QListWidget(self)
        self._nav.setObjectName("SettingsNavigation")
        self._nav.setFixedWidth(196)
        self._nav.setIconSize(QSize(18, 18))
        self._nav.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        sidebar = QFrame(self)
        sidebar.setObjectName("SettingsSidebar")
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(8, 16, 8, 12)
        side.setSpacing(8)
        brand = QLabel("Easy-Tools\n设置中心", sidebar)
        brand.setObjectName("SettingsBrand")
        side.addWidget(brand)
        side.addWidget(self._nav, 1)
        content = QWidget(self)
        content_lay = QVBoxLayout(content)
        content_lay.setContentsMargins(0, 0, 0, 0)
        content_lay.setSpacing(0)
        page_head = QFrame(content)
        page_head.setObjectName("SettingsPageHeader")
        head_lay = QVBoxLayout(page_head)
        head_lay.setContentsMargins(24, 14, 20, 12)
        self._page_title = QLabel("", page_head)
        self._page_title.setObjectName("SettingsPageTitle")
        head_lay.addWidget(self._page_title)
        content_lay.addWidget(page_head)
        content_lay.addWidget(self._pages, 1)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(sidebar)
        layout.addWidget(content, 1)
        self._nav.currentRowChanged.connect(self._select_page)

    def addTab(self, page: QWidget, title: str) -> int:
        self._titles.append(title)
        item = QListWidgetItem(title)
        item.setToolTip(title)
        mark = self._MARKS.get(title)
        if mark and icons.has_icon(mark):
            item.setIcon(icons.icon(mark, "#475569", 18))
        self._nav.addItem(item)
        index = self._pages.addWidget(page)
        if index == 0:
            self._nav.setCurrentRow(0)
        return index

    def _select_page(self, index: int) -> None:
        if index < 0:
            return
        self._pages.setCurrentIndex(index)
        if index < len(self._titles):
            self._page_title.setText(self._titles[index])
        self.currentChanged.emit(index)

    def currentWidget(self) -> QWidget | None:
        return self._pages.currentWidget()

    def count(self) -> int:
        return len(self._titles)

    def tabText(self, index: int) -> str:
        return self._titles[index]

    def setCurrentIndex(self, index: int) -> None:
        self._nav.setCurrentRow(index)


class _ChainBox(QGroupBox):
    """事件链列表里的可选中卡片。

    原实现只给卡片挂了 _et_chain 数据，没有任何点击/选中处理，
    _selected_chain() 永远找不到 _et_selected 标记——「编辑选中/删除
    选中/立即运行」全部卡在"请先选中"，事件链等于不可编辑。这里补上：
    单击选中（高亮），双击直接进编辑。
    """

    clicked = Signal()
    double_clicked = Signal()

    def __init__(self, chain: Dict[str, Any], parent=None) -> None:
        super().__init__("%s（%s）" % (chain.get("name", "?"), chain.get("id", "?")), parent)
        self._et_chain = chain
        self._et_selected = False
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("单击选中；双击直接编辑")
        self.set_selected(False)

    def set_selected(self, on: bool) -> None:
        self._et_selected = bool(on)
        # 选中态用样式表达；颜色与主题 accent（#2458c5 / #e8f1ff）一致
        self.setStyleSheet(
            "QGroupBox { background: #e8f1ff; border: 1px solid #2458c5;"
            " border-radius: 8px; margin-top: 10px; }"
            if on else
            "QGroupBox { border-radius: 8px; margin-top: 10px; }")

    def mousePressEvent(self, ev) -> None:
        if ev.button() == Qt.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(ev)

    def mouseDoubleClickEvent(self, ev) -> None:
        if ev.button() == Qt.LeftButton:
            self.double_clicked.emit()
        super().mouseDoubleClickEvent(ev)


class SettingsWindow(QWidget):
    """配置中心主窗口。"""

    def __init__(self, service, parent=None) -> None:
        super().__init__(parent)
        self.service = service
        self.store = service.store
        self.setWindowTitle("Easy-Tools 配置中心")
        self.resize(1100, 720)
        self.setMinimumSize(920, 580)
        self.setStyleSheet(theme.settings_qss())

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self.tabs = SidebarTabs(self)
        root.addWidget(self.tabs, 1)

        footer = QFrame(self)
        footer.setObjectName("SettingsFooter")
        footer_lay = QHBoxLayout(footer)
        footer_lay.setContentsMargins(18, 8, 18, 8)
        footer_lay.setSpacing(8)
        self.status = QLabel("", footer)
        self.status.setObjectName("SettingsStatus")
        self.status.setWordWrap(True)
        footer_lay.addWidget(self.status, 1)

        reload_btn = self.reload_btn = QPushButton("放弃修改", footer)
        reload_btn.setObjectName("QuietButton")
        reload_btn.clicked.connect(self.reload_from_store)
        footer_lay.addWidget(reload_btn)
        self.save_btn = QPushButton("保存并立即生效", footer)
        self.save_btn.setObjectName("PrimaryButton")
        self.save_btn.clicked.connect(self.save)
        footer_lay.addWidget(self.save_btn)
        root.addWidget(footer)

        self._build_tabs()
        self.reload_from_store()

    # ------------------------------------------------------------ 选项数据
    def _action_choices(self) -> Dict[str, List[str]]:
        return {}

    def _prepare_action_edit(self, row: Dict[str, Any]):
        """把文本正文从内部 JSON/片段编号提取成清晰的编辑字段。"""
        kind = str(row.get("kind") or "")
        if kind not in ("text.input", "snippet.input"):
            return self.action_editor.fields, row
        params = row.get("params") or {}
        if isinstance(params, str):
            try:
                params = json.loads(params) if params.strip() else {}
            except (TypeError, ValueError):
                params = {}
        params = dict(params) if isinstance(params, dict) else {}
        fields = [f for f in self.action_editor.fields if f.key not in ("params", "kind")]
        fields += [
            Field("mode", "输入方式", "enum", ["", "paste", "type"],
                  labels={"": "跟随全局设置", "paste": "粘贴", "type": "逐字手打"}),
            Field("text_content", "文本内容", "textarea",
                  hint="此处显示完整文本，换行和变量会原样保留。"),
            Field("other_params", "其他参数（JSON）", "textarea",
                  hint="高级参数；通常留空即可。")]
        prepared = dict(row)
        prepared["mode"] = str(params.get("mode") or "")
        if kind == "text.input":
            prepared["text_content"] = str(params.get("text") or "")
            extras = {k: v for k, v in params.items() if k not in ("text", "mode")}
        else:
            sid = str(params.get("snippet_id") or "")
            prepared["_linked_snippet_id"] = sid
            prepared["text_content"] = next(
                (str(s.get("text") or "") for s in self.snippet_editor._rows
                 if str(s.get("id")) == sid), "")
            extras = {k: v for k, v in params.items() if k not in ("snippet_id", "mode")}
        prepared["other_params"] = json.dumps(extras, ensure_ascii=False, indent=2)
        return fields, prepared

    def _apply_action_edit(self, original: Dict[str, Any], edited: Dict[str, Any]):
        """保存文本动作正文；片段动作同步写回动作库中的关联片段。"""
        kind = str(original.get("kind") or "")
        if kind not in ("text.input", "snippet.input") or edited.get("kind") != kind:
            raw = edited.pop("text_content", None)
            edited.pop("mode", None)
            edited.pop("other_params", None)
            return edited
        try:
            extras = json.loads(str(edited.get("other_params") or "{}"))
        except (TypeError, ValueError):
            extras = None
        if not isinstance(extras, dict):
            # 留下用户输入的无效 JSON，让保存时的校验给出可读提示。
            edited["params"] = str(edited.get("other_params") or "")
            edited.pop("text_content", None)
            edited.pop("other_params", None)
            edited.pop("mode", None)
            return edited
        params = dict(extras)
        params["mode"] = str(edited.get("mode") or "")
        if kind == "text.input":
            params["text"] = str(edited.get("text_content") or "")
        else:
            original_params = original.get("params") or {}
            if isinstance(original_params, str):
                try:
                    original_params = json.loads(original_params) if original_params.strip() else {}
                except (TypeError, ValueError):
                    original_params = {}
            sid = str(original_params.get("snippet_id") or "") if isinstance(original_params, dict) else ""
            params["snippet_id"] = sid
            for snippet in self.snippet_editor._rows:
                if str(snippet.get("id")) == sid:
                    snippet["text"] = str(edited.get("text_content") or "")
                    break
            self.snippet_editor._reload()
        edited["params"] = params
        for key in ("_linked_snippet_id", "text_content", "other_params", "mode"):
            edited.pop(key, None)
        return edited

    def _rule_choices(self) -> Dict[str, List[str]]:
        names = ["%s（%s）" % (a.get("name"), a.get("id"))
                 for a in self.store.section("actions", [])]
        # multi 字段存的是显示文本，保存时再剥出 id，见 _rule_rows
        return {"action_ids": names}

    # ------------------------------------------------------------ 页面
    def _build_tabs(self) -> None:
        cfg = self.store.get()
        from .update_page import SoftwareUpdatePage
        self.update_page = SoftwareUpdatePage(self)
        self.tabs.addTab(self.update_page, "软件更新")

        # 列表里的「类型」「参数」「操作区」都用人话显示（见 ui/labels.py）；
        # 编辑框里存的仍是原始代码，只是下拉框显示中文。
        self.action_editor = JsonTableEditor(
            [Column("name", "名称", 130), Column("group", "分组", 100),
             Column("kind", "类型", 96, fmt=lambda r: labels.kind_label(r.get("kind"))),
             Column("zone", "操作区", 82, fmt=lambda r: labels.zone_label(r.get("zone"))),
             Column("icon", "图标", 110),
             Column("params", "参数", 260, fmt=lambda r: labels.describe_params(r, self.store))],
            [Field("name", "名称"),
             Field("group", "分组", default="未分组"),
             Field("icon", "图标", "icon", default="star"),
             Field("zone", "操作区", "enum", ["public", "context"],
                   "公共区=始终显示；特定操作区=仅被规则命中时显示",
                   labels={"public": "公共区", "context": "特定操作区"}),
             Field("kind", "类型", "enum", labels.kind_choices(),
                   "决定执行逻辑；下拉框里显示的是中文名",
                   labels={k: labels.kind_label(k) for k in labels.kind_choices()}),
             Field("order", "排序", "int", default=100),
             Field("params", "参数", "textarea",
                   hint='高级编辑，直接写 JSON，如 {"text":"你好","mode":"type"}；'
                        '不熟悉 JSON 的话，用下方「新增文本按钮」一步完成更省心')],
            "act", choices_fn=self._action_choices, title="动作")
        self.action_editor.prepare_edit = self._prepare_action_edit
        self.action_editor.apply_edit = self._apply_action_edit
        tab = QWidget()
        lay = QVBoxLayout(tab)
        tip = QLabel("公共操作区 = zone 为 public；特定操作区动作由「特定操作区规则」页决定何时显示。")
        tip.setWordWrap(True)
        lay.addWidget(tip)
        # 分类筛选：动作多的时候按分组看
        frow = QWidget()
        flay = QHBoxLayout(frow)
        flay.setContentsMargins(0, 2, 0, 2)
        flay.addWidget(QLabel("分类筛选："))
        self._group_filter = QComboBox()
        self._group_filter.setMinimumWidth(160)
        flay.addWidget(self._group_filter, 1)
        flay.addStretch(1)
        lay.addWidget(frow)
        self._group_filter.currentTextChanged.connect(self._apply_group_filter)
        # 自助配置入口：想加一个"输入某段文本"的按钮，从这里一步完成，
        # 不必先去片段页建片段再回本页手填 snippet_id
        quick = QWidget()
        qlay = QHBoxLayout(quick)
        qlay.setContentsMargins(0, 2, 0, 2)
        qbtn = QPushButton("＋ 新增文本按钮（一步完成）")
        qbtn.clicked.connect(self.quick_add_text_button)
        qlay.addWidget(qbtn)
        qtip = QLabel("按钮名 / 图标 / 分组 / 文本内容 一次填完，自动生成片段+动作，面板立即可见")
        qtip.setWordWrap(True)
        qtip.setStyleSheet("color:#475569;font-size:11px")
        qlay.addWidget(qtip, 1)
        lay.addWidget(quick)
        lay.addWidget(self.action_editor, 1)
        self.tabs.addTab(tab, "动作库")
        # 切到动作库页时重建分组下拉（表格数据可能被增删改过）
        self.tabs.currentChanged.connect(
            lambda _i: self._rebuild_group_filter()
            if self.tabs.currentWidget() is tab else None)
        self.action_editor.rows_changed.connect(self._rebuild_group_filter)
        self._rebuild_group_filter()

        self.snippet_editor = JsonTableEditor(
            [Column("name", "名称", 140), Column("group", "分组", 110),
             Column("text", "内容", 340)],
            [Field("name", "名称"),
             Field("group", "分组", default="常用语"),
             Field("order", "排序", "int", default=100),
             Field("text", "内容", "textarea",
                   hint="支持变量 {date} {time} {datetime} {clipboard} {ocr_text}")],
            "snip", title="片段")
        self.tabs.addTab(self.snippet_editor, "文本片段")

        self.rule_editor = JsonTableEditor(
            [Column("name", "规则名", 120), Column("processes", "进程名", 180),
             Column("title_regex", "标题正则", 145), Column("ocr_keywords", "OCR 关键词", 135),
             Column("action_ids", "动作", 220)],
            [Field("name", "规则名"),
             Field("match", "匹配方式", "enum", ["any", "all"],
                   "any=填写的条件任意命中即可；all=全部命中"),
             Field("order", "排序", "int", default=100),
             Field("processes", "进程名（每行一个，* 通配）", "list",
                   hint="如 winword.exe / chrome.exe；留空表示不限"),
             Field("title_regex", "窗口标题正则（每行一个）", "list"),
             Field("ocr_keywords", "OCR 关键词（每行一个）", "list",
                   hint="需要 OCR 后端可用，否则永不命中"),
             Field("action_ids", "命中后显示的动作", "multi")],
            "rule", choices_fn=self._rule_choices, title="规则")
        self.tabs.addTab(self.rule_editor, "特定操作区规则")

        self._build_chain_tab(cfg)
        self._build_simple_tabs(cfg)

    # ------------------------------------------------------------ 自助：文本按钮
    def _rebuild_group_filter(self) -> None:
        groups = sorted({str(a.get("group") or "未分组")
                         for a in self.action_editor.rows()})
        cur = self._group_filter.currentText()
        if cur and cur != "全部分类" and cur not in groups:
            groups.append(cur)
        self._group_filter.blockSignals(True)
        self._group_filter.clear()
        self._group_filter.addItem("全部分类")
        for g in groups:
            self._group_filter.addItem(g)
        if cur:
            self._group_filter.setCurrentText(cur)
        self._group_filter.blockSignals(False)
        self._apply_group_filter()

    def _apply_group_filter(self, *_a) -> None:
        sel = self._group_filter.currentText()
        self.action_editor.new_item_defaults = {"group": sel} if sel and sel != "全部分类" else {}
        for i in range(self.action_editor.table.rowCount()):
            item = self.action_editor.table.item(i, 1)   # 第 1 列 = 分组
            text = (item.text() if item else "") or "未分组"
            self.action_editor.table.setRowHidden(
                i, sel != "全部分类" and text != sel)
        self.action_editor._select_visible(self.action_editor.table.currentRow())

    def quick_add_text_button(self) -> None:
        """一次表单生成「文本片段 + 面板按钮」，保存即生效。"""
        from .text_button_wizard import TextButtonDialog, quick_add_text_button
        dlg = TextButtonDialog(self)
        if dlg.exec() != TextButtonDialog.Accepted:
            return
        cfg = self.store.get()
        try:
            _snippet, action = quick_add_text_button(cfg, **dlg.values())
        except ValueError as e:
            QMessageBox.warning(self, "无法新增", str(e))
            return
        errs = self.store.update(cfg)
        if errs:
            QMessageBox.warning(self, "新增未生效", "\n".join(errs))
            return
        self.reload_from_store()
        self.status.setText("已新增文本按钮「%s」，面板下次呼出即可见 ｜ %s"
                            % (action["name"], self.service.status_text()))

    def _build_chain_tab(self, cfg: Dict[str, Any]) -> None:
        tab = QWidget()
        lay = QVBoxLayout(tab)
        self.chain_list = QVBoxLayout()
        holder = QWidget()
        holder.setLayout(self.chain_list)
        scroll = QWidget()
        sl = QVBoxLayout(scroll)
        sl.addWidget(holder)
        sl.addStretch(1)

        from PySide6.QtWidgets import QScrollArea
        area = QScrollArea(tab)
        area.setWidgetResizable(True)
        area.setWidget(scroll)
        lay.addWidget(area, 1)

        bar = QHBoxLayout()
        for text, slot in (("新增事件链", self.add_chain),
                           ("编辑选中", self.edit_chain),
                           ("删除选中", self.del_chain),
                           ("立即运行选中", self.run_chain)):
            b = QPushButton(text, tab)
            b.clicked.connect(slot)
            bar.addWidget(b)
        bar.addStretch(1)
        lay.addLayout(bar)

        self._chain_rows: List[Dict[str, Any]] = [dict(c) for c in cfg.get("chains", [])]
        self._rebuild_chain_list()
        self.tabs.addTab(tab, "事件链")

    def _rebuild_chain_list(self) -> None:
        while self.chain_list.count():
            it = self.chain_list.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        for c in self._chain_rows:
            box = _ChainBox(c, self)
            b = QVBoxLayout(box)
            steps = c.get("steps") or []
            text = "\n".join("  %d. %s" % (i + 1, s.get("type", "?"))
                             for i, s in enumerate(steps)) or "（空）"
            lab = QLabel(text)
            lab.setStyleSheet("font-family: Consolas, monospace; color:#475569")
            b.addWidget(QLabel(str(c.get("description", ""))))
            b.addWidget(lab)
            box.setProperty("chain_id", c.get("id"))
            box.clicked.connect(lambda checked=False, b=box: self._select_chain_box(b))

            def _open_editor(b=box):
                self._select_chain_box(b)
                self.edit_chain()

            box.double_clicked.connect(_open_editor)
            self.chain_list.addWidget(box)

    def _select_chain_box(self, box: _ChainBox) -> None:
        for i in range(self.chain_list.count()):
            w = self.chain_list.itemAt(i).widget()
            if isinstance(w, _ChainBox):
                w.set_selected(w is box)

    def _selected_chain(self):
        for i in range(self.chain_list.count()):
            w = self.chain_list.itemAt(i).widget()
            if getattr(w, "_et_selected", False):
                return getattr(w, "_et_chain", None)
        return None

    def add_chain(self) -> None:
        self._chain_rows.append({
            "id": "chain.custom.%d" % (len(self._chain_rows) + 1),
            "name": "新事件链", "description": "",
            "trigger": {"type": "manual"},
            "steps": [{"type": "action", "id": "clipboard.copy"},
                      {"type": "action", "id": "text.strip", "input": "{clipboard}"},
                      {"type": "action", "id": "input.paste", "input": "{last.output}"}]})
        self._rebuild_chain_list()

    def edit_chain(self) -> None:
        c = self._selected_chain()
        if not c:
            self.status.setText("请先在列表中点击选中一条事件链")
            return
        dlg = ChainEditorDialog(c, self.service, self)
        # QDialogButtonBox.Accepted 不是合法的对话框返回值常量（该属性不存在，
        # 旧代码在这里直接 AttributeError），必须用 QDialog.Accepted
        if dlg.exec() == QDialog.Accepted:
            new = dlg.result_chain()
            for i, old in enumerate(self._chain_rows):
                if old is c or old.get("id") == c.get("id"):
                    self._chain_rows[i] = new
                    break
            self._rebuild_chain_list()

    def del_chain(self) -> None:
        c = self._selected_chain()
        if c:
            self._chain_rows = [x for x in self._chain_rows if x.get("id") != c.get("id")]
            self._rebuild_chain_list()

    def run_chain(self) -> None:
        c = self._selected_chain()
        if not c:
            self.status.setText("请先选中一条事件链")
            return
        self.service.run_chain(c.get("id", ""))
        self.status.setText("已在后台执行：%s" % c.get("name"))

    # ------------------------------------------------------------ 简单设置页
    @staticmethod
    def _trigger_form_cfg(cfg: Dict[str, Any]) -> Dict[str, Any]:
        """trigger 配置 -> 表单扁平字段：把「呼出面板」热键展开成两个可编辑项。

        表单字段是扁平 key-value，而热键在配置里是 trigger.hotkeys 列表
        （id=panel 的一项）。这里拆开进表单，_collect() 再合回去。
        """
        trigger = dict(cfg.get("trigger", {}) or {})
        hk = next((h for h in (trigger.get("hotkeys") or []) if h.get("id") == "panel"), {})
        trigger["hotkey_panel_enabled"] = bool(hk.get("enabled", False))
        trigger["hotkey_panel_combo"] = str(hk.get("combo", "ctrl+alt+q"))
        return trigger

    def _build_simple_tabs(self, cfg: Dict[str, Any]) -> None:
        self.trigger_page = FormPage([
            Field("middle_button_enabled", "启用鼠标触发", "bool",
                  "关闭后只能用下方键盘热键呼出面板"),
            Field("mouse_button", "鼠标触发键", "enum", ["middle", "x1", "x2"],
                  "中键=滚轮按下；X1/X2=鼠标侧键（多为后退/前进键）。"
                  "短按呼出、按住拖动滚动的判定对所有键一致；未选中的按键保留系统原生行为",
                  labels={"middle": "中键（默认）", "x1": "侧键 X1（后退）", "x2": "侧键 X2（前进）"}),
            Field("middle_click_mode", "触发键消费模式", "enum",
                  ["swallow_tap", "native_passthrough"],
                  "swallow_tap=吞掉原生按键避免误触（默认）；native_passthrough=完全保留原生按键行为",
                  labels={"swallow_tap": "吞掉按键避免误触（默认）",
                          "native_passthrough": "保留原生按键行为"}),
            Field("tap_max_ms", "短按上限(ms)", "int"),
            Field("drag_threshold_px", "拖动判定阈值(px)", "int"),
            Field("drag_scroll_enabled", "拖动时模拟滚动", "bool",
                  "swallow_tap 模式下由本程序用滚轮事件还原拖动滚动手感"),
            Field("scroll_ticks_per_pixel", "滚动换算(每像素 tick)", "float"),
            Field("notify_enabled", "显示右下角通知", "bool",
                  "关闭后成功和失败提示都不弹出；详细记录仍保留在日志中"),
            Field("hotkey_panel_enabled", "启用键盘呼出热键", "bool",
                  "全局组合键呼出动作面板，与鼠标触发相互独立，可同时开启"),
            Field("hotkey_panel_combo", "呼出热键组合", "text",
                  "用 + 连接修饰键与主键，如 ctrl+alt+q、win+f1、ctrl+shift+1；保存时校验")],
            self._trigger_form_cfg(cfg))
        self.tabs.addTab(self.trigger_page, "触发设置")

        panel_cfg = dict(cfg.get("panel", {}))
        panel_cfg["group_order"] = self._panel_group_order(cfg)
        self.panel_page = FormPage([
            Field("display_mode", "展示模式", "enum", ["tabs", "scroll"],
                  labels={"tabs": "多 Tab 分类（分页，无纵向滚动）", "scroll": "全部分组（连续滚动）"}),
            Field("max_rows", "每页最多行数", "int"),
            Field("columns", "网格列数", "int"),
            Field("cell_width", "格子宽", "int"),
            Field("cell_height", "格子高", "int"),
            Field("opacity", "不透明度", "float"),
            Field("show_search", "显示搜索框", "bool"),
            Field("show_groups", "显示分组标题", "bool"),
            Field("show_icon", "显示图标", "bool"),
            Field("cursor_offset", "光标偏移(px)", "int"),
            Field("flip_at_edges", "越界自动翻转", "bool")],
            panel_cfg)
        self.tabs.addTab(self.panel_page, "面板样式")
        self.group_order_page = FormPage([
            Field("group_order", "大分类顺序", "group_order",
                  hint="拖动或选中后上移/下移；第一项为默认 Tab。文本输入可置顶。识别到软件规则时，其 ▸ 专属分类自动显示在公共分类之前。")], panel_cfg)
        self.tabs.addTab(self.group_order_page, "分类排序")

        ocr_page = QWidget()
        ocr_lay = QVBoxLayout(ocr_page)
        self.ocr_info = QLabel("", ocr_page)
        self.ocr_info.setWordWrap(True)
        ocr_lay.addWidget(self.ocr_info)
        self.ocr_form_page = FormPage([
            Field("enabled", "启用 OCR", "bool"),
            Field("debounce_ms", "防抖(ms)", "int"),
            Field("region", "识别区域", "enum", ["client", "window", "screen"]),
            Field("max_width", "最大宽度(px)", "int"),
            Field("cache_ttl_ms", "缓存有效期(ms)", "int")],
            cfg.get("ocr", {}))
        ocr_lay.addWidget(self.ocr_form_page, 1)
        hint = QLabel("说明：安装包包含 RapidOCR 与识别模型。截图识别失败时会在结果窗口显示状态；"
                      "后台 OCR 首次初始化可能需要数秒。")
        hint.setWordWrap(True)
        hint.setStyleSheet("color:#475569")
        ocr_lay.addWidget(hint)
        self.tabs.addTab(ocr_page, "OCR 设置")

        from .question_bank_page import LocalModelPage, QuestionBankPage
        self.question_bank_page = QuestionBankPage(cfg.get("question_banks", []), self,
            answer_question=getattr(self.service, "answer_question", None),
            answer_content_only=cfg.get("llm", {}).get("answer_content_only", False))
        self.tabs.addTab(self.question_bank_page, "知识库")
        self.local_model_page = LocalModelPage(cfg.get("llm", {}), self.service.ccswitch_model, self)
        self.tabs.addTab(self.local_model_page, "本机模型")

        self.text_page = FormPage([
            Field("default_mode", "默认输入模式", "enum", ["paste", "type"],
                  "paste=写剪贴板再 Ctrl+V（兼容性最好）；type=逐字符键入，Esc 中断；中英数字原样输出"),
            Field("speed_cps", "手打速度(字符/秒)", "int"),
            Field("jitter_ms", "手打随机抖动(ms)", "int"),
            Field("restore_clipboard", "粘贴后恢复原剪贴板", "bool",
                  "原剪贴板没有文本（图片/文件）时不会恢复，以免破坏数据"),
            Field("paste_restore_ms", "恢复延迟(ms)", "int"),
            Field("ensure_foreground", "输入前自动切回目标窗口", "bool")],
            cfg.get("text_input", {}))
        self.tabs.addTab(self.text_page, "文本输入")

        self.privacy_page = FormPage([
            Field("exclude_from_capture", "截图/录屏时隐藏 Easy-Tools", "bool",
                  "开启后，动作弹窗、设置页和应用对话框会从 Windows 支持的截图/录屏捕获中排除。"
                  "托盘通知也会自动关闭。依赖桌面窗口管理器（DWM）；不防止摄像拍屏，"
                  "也不能保证拦截所有第三方采集方式。")],
            cfg.get("privacy", {}))
        self.tabs.addTab(self.privacy_page, "隐私保护")

        self._build_data_tab()

    def _build_data_tab(self) -> None:
        tab = QWidget()
        lay = QVBoxLayout(tab)
        lay.addWidget(QLabel("配置文件：%s" % config_path()))
        lay.addWidget(QLabel("数据目录：%s" % data_dir()))

        bar = QHBoxLayout()
        exp = QPushButton("导出到文件", tab)
        exp.clicked.connect(self.export_config)
        imp = QPushButton("从文件导入", tab)
        imp.clicked.connect(self.import_config)
        opendir = QPushButton("打开配置目录", tab)
        opendir.clicked.connect(self.open_data_dir)
        for b in (exp, imp, opendir):
            bar.addWidget(b)
        bar.addStretch(1)
        lay.addLayout(bar)

        lay.addWidget(QLabel("配置预览（JSON）"))
        self.json_view = QTextBrowser(tab)
        lay.addWidget(self.json_view, 1)
        self.tabs.addTab(tab, "数据/导入导出")

    def export_config(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "导出配置", "easy-tools-config.json",
                                              "JSON 文件 (*.json)")
        if not path:
            return
        with open(path, "w", encoding="utf-8") as f:
            f.write(self.store.export_json())
        self.status.setText("已导出到 %s" % path)

    def import_config(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "导入配置", "", "JSON 文件 (*.json)")
        if not path:
            return
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
        errs = self.store.import_json(text)
        if errs:
            QMessageBox.warning(self, "导入失败", "\n".join(errs))
            return
        self.reload_from_store()
        self.status.setText("已导入并立即生效")

    def open_data_dir(self) -> None:
        import os
        os.startfile(str(data_dir()))          # type: ignore[attr-defined]

    # ------------------------------------------------------------ 载入/保存
    @staticmethod
    def _panel_group_order(cfg):
        groups = list(cfg.get("panel", {}).get("group_order", ["文本输入"]))
        groups += [str(a.get("group") or "未分组") for a in cfg.get("actions", []) if a.get("zone", "public") == "public"]
        groups += ["▸ " + str(r.get("name", "")) for r in cfg.get("rules", [])]
        return list(dict.fromkeys(groups))

    def reload_from_store(self) -> None:
        cfg = self.store.get()
        self.action_editor.set_rows(cfg.get("actions", []))
        self.snippet_editor.set_rows(cfg.get("snippets", []))
        self.rule_editor.set_rows(cfg.get("rules", []))
        self._chain_rows = [dict(c) for c in cfg.get("chains", [])]
        self._rebuild_chain_list()
        # 触发页的表单值来自 _trigger_form_cfg 摊平后的字段（含热键两项），
        # 不能直接用原始 trigger 段回填
        trigger_flat = self._trigger_form_cfg(cfg)
        for k, _get, setter in self.trigger_page._getters:
            if k in trigger_flat:
                setter(trigger_flat[k])
        for page, key in ((self.panel_page, "panel"),
                          (self.ocr_form_page, "ocr"), (self.text_page, "text_input"),
                          (self.privacy_page, "privacy")):
            for k, _get, setter in page._getters:
                if k in cfg.get(key, {}):
                    setter(cfg[key][k])
        for k, _get, setter in self.group_order_page._getters:
            if k == "group_order":
                setter(self._panel_group_order(cfg))
        self.question_bank_page.set_rows(cfg.get("question_banks", []))
        self.question_bank_page.answer_content_only.setChecked(bool(cfg.get("llm", {}).get("answer_content_only", False)))
        self.local_model_page.set_settings(cfg.get("llm", {}))
        st = self.service.ocr.status()
        self.ocr_info.setText(
            "后端：%s ｜ 状态：%s ｜ 原因：%s ｜ 已识别字数：%s ｜ 上次耗时：%s ms" % (
                st.get("backend"), "就绪" if st.get("ready") else "不可用",
                st.get("reason"), st.get("text_len"), st.get("last_duration_ms")))
        self.json_view.setPlainText(json.dumps(cfg, ensure_ascii=False, indent=2))
        self.status.setText(self.service.status_text())

    def _collect(self) -> Dict[str, Any]:
        cfg = self.store.get()
        actions = self.action_editor.rows()
        for a in actions:
            p = a.get("params")
            if isinstance(p, str):
                p = p.strip()
                p = json.loads(p) if p else {}
                if not isinstance(p, dict):
                    raise ValueError("动作「%s」的参数必须为 JSON 对象" % a.get("name", a.get("id", "")))
                a["params"] = p
            elif not isinstance(p, dict):
                a["params"] = {}
            a.setdefault("order", 100)
        cfg["actions"] = actions
        cfg["snippets"] = self.snippet_editor.rows()
        rules = self.rule_editor.rows()
        for r in rules:
            ids = r.get("action_ids") or []
            if ids and isinstance(ids[0], str) and "（" in ids[0]:
                r["action_ids"] = [x.rsplit("（", 1)[-1].rstrip("）") for x in ids]
        cfg["rules"] = rules
        cfg["chains"] = self._chain_rows
        # 触发页：把两个热键表单项合回 trigger.hotkeys（id=panel 的一项）
        trigger = self.trigger_page.collect()
        hk_enabled = bool(trigger.pop("hotkey_panel_enabled", False))
        hk_combo = str(trigger.pop("hotkey_panel_combo", "")).strip().lower()
        if hk_enabled and hk_combo:
            from ..win32.input import parse_combo
            try:
                parse_combo(hk_combo)
            except Exception as e:
                raise ValueError("呼出热键「%s」无法解析：%s" % (hk_combo, e))
        hotkeys = [dict(h) for h in (cfg.get("trigger", {}).get("hotkeys") or [])]
        hit = False
        for h in hotkeys:
            if h.get("id") == "panel":
                h["enabled"] = bool(hk_enabled)
                if hk_combo:
                    h["combo"] = hk_combo
                hit = True
        if not hit:
            hotkeys.append({"id": "panel", "name": "呼出面板",
                            "combo": hk_combo or "ctrl+alt+q",
                            "enabled": bool(hk_enabled)})
        trigger["hotkeys"] = hotkeys
        cfg["trigger"] = trigger
        cfg["panel"] = self.panel_page.collect()
        cfg["panel"]["group_order"] = self.group_order_page.collect()["group_order"]
        cfg["ocr"] = self.ocr_form_page.collect()
        cfg["text_input"] = self.text_page.collect()
        cfg["privacy"] = self.privacy_page.collect()
        cfg["question_banks"] = self.question_bank_page.collect()
        cfg["llm"] = self.local_model_page.collect()
        cfg["llm"]["answer_content_only"] = self.question_bank_page.answer_content_only.isChecked()
        return cfg

    def save(self) -> None:
        if getattr(self, "_saving", False):
            return
        try:
            cfg = self._collect()
        except Exception as e:
            QMessageBox.warning(self, "无法保存", "配置内容有误：%s" % e)
            return
        self._saving = True
        self.save_btn.setEnabled(False)
        self.reload_btn.setEnabled(False)
        self.tabs.setEnabled(False)
        self.status.setText("后台保存并应用配置中…")
        store = self.store
        if not submit(lambda: store.update(cfg), self._save_finished):
            self._saving = False
            self.save_btn.setEnabled(True)
            self.reload_btn.setEnabled(True)
            self.tabs.setEnabled(True)
            self.status.setText("后台任务较多，请稍后保存。")

    @Slot(object, str)
    def _save_finished(self, errors, error):
        self._saving = False
        self.save_btn.setEnabled(True)
        self.reload_btn.setEnabled(True)
        self.tabs.setEnabled(True)
        if error or errors:
            detail = error or "；".join(errors)
            self.status.setText("未保存：" + detail)
            QMessageBox.warning(self, "配置未保存", detail)
            return
        self.reload_from_store()
        self.status.setText("已保存并立即生效 ｜ " + self.service.status_text())



if __name__ == "__main__":
    print("settings 是 UI 模块，需在 Qt 环境中使用")
