"""Discover local office tools and compare input/output before copying."""
from PySide6.QtCore import Slot, QSize
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
                               QListWidget, QListWidgetItem, QPushButton, QDialog,
                               QPlainTextEdit, QSplitter, QComboBox)
from ..core.actions.processors import all_processors
from ..win32 import clipboard
from .parameters import ParameterForm
from .task_bridge import submit_task


class ToolPreviewDialog(QDialog):
    def __init__(self, processor, service, parent=None):
        super().__init__(parent)
        self.processor, self.service = processor, service
        self._task = None
        self._closed = False
        self._output = ''
        self.setWindowTitle(processor.name + ' · 处理预览')
        self.resize(900, 650)
        layout = QVBoxLayout(self)
        title = QLabel(processor.name)
        title.setObjectName('TaskTitle')
        layout.addWidget(title)
        hint = QLabel(processor.doc or '在左侧粘贴内容，预览后复制结果。处理在本机完成。')
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.params = ParameterForm(processor.args)
        self.params.changed.connect(self.invalidate)
        layout.addWidget(self.params)
        split = QSplitter()
        self.input = QPlainTextEdit()
        self.input.setPlaceholderText('粘贴 Word 段落或 Excel 单元格（最多 100 万字符）')
        self.output = QPlainTextEdit()
        self.output.setReadOnly(True)
        self.output.setPlaceholderText('处理结果预览（最多展示 2 万字符）')
        for editor, label in ((self.input, '原始内容'), (self.output, '处理结果')):
            host = QWidget()
            column = QVBoxLayout(host)
            column.setContentsMargins(0, 0, 0, 0)
            column.addWidget(QLabel(label))
            column.addWidget(editor)
            split.addWidget(host)
        layout.addWidget(split, 1)
        self.status = QLabel('本机处理 · 不修改原文档或剪贴板')
        layout.addWidget(self.status)
        bar = QHBoxLayout()
        self.run = QPushButton('预览结果')
        self.run.setObjectName('PrimaryButton')
        self.run.clicked.connect(self.preview)
        self.copy = QPushButton('复制完整结果')
        self.copy.setEnabled(False)
        self.copy.clicked.connect(self.copy_result)
        self.cancel = QPushButton('停止')
        self.cancel.clicked.connect(lambda: service.tasks.cancel(self._task))
        self.cancel.setEnabled(False)
        for button in (self.run, self.copy, self.cancel):
            bar.addWidget(button)
        layout.addLayout(bar)
        self.input.textChanged.connect(self.invalidate)

    def invalidate(self):
        self._output = ''
        self.copy.setEnabled(False)
        self.output.clear()
        self.status.setText('内容或选项已改变，请重新预览')

    def preview(self):
        if self._task:
            return
        text, params = self.input.toPlainText(), self.params.collect()
        if len(text) > 1_000_000:
            self.status.setText('内容过大，请分批处理（每批最多 100 万字符）')
            return
        self.run.setEnabled(False)
        self.input.setReadOnly(True)
        self.params.setEnabled(False)
        self.copy.setEnabled(False)
        self.cancel.setEnabled(True)
        self.status.setText('正在后台处理…')
        processor = self.processor
        from ..core.processor_worker import run_processor
        self._task = submit_task(self.service.tasks, processor.name,
                                 lambda cancel: run_processor(processor.id, text, params, cancel), self._processed)
        if not self._task:
            self._processed(None, '任务队列已满，请稍后重试')

    @Slot(object, str)
    def _processed(self, value, error):
        self._task = None
        if self._closed:
            return
        self.run.setEnabled(True)
        self.input.setReadOnly(False)
        self.params.setEnabled(True)
        self.cancel.setEnabled(False)
        self._output = '' if error else str(value or '')
        self.output.setPlainText(self._output[:20000])
        self.copy.setEnabled(not error and bool(self._output))
        self.status.setText(error or f'处理完成：{len(self._output)} 字符；预览最多 2 万字符，可复制完整结果')

    def copy_result(self):
        self.status.setText('结果已复制' if clipboard.set_text(self._output) else '复制失败，请重试')

    def done(self, result):
        self._closed = True
        if self._task:
            self.service.tasks.cancel(self._task)
        self.input.clear()
        self.output.clear()
        self._output = ''
        super().done(result)


class OfficeToolsPage(QWidget):
    def __init__(self, service, parent=None):
        super().__init__(parent)
        self.service = service
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 16, 24, 18)
        layout.setSpacing(12)
        title = QLabel('把重复的办公处理，变成几次点击')
        title.setObjectName('TaskTitle')
        layout.addWidget(title)
        hint = QLabel('文字与表格处理在本机执行。粘贴内容 → 配置选项 → 比较结果 → 复制使用。\n'
                      'Word / Excel / WPS 均可通过复制文字或表格使用；原文档保持由你控制。')
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.search = QLineEdit()
        self.search.setPlaceholderText('搜索任务，例如：段落、断行、去重、列、日期…')
        self.search.textChanged.connect(self.refresh)
        search_bar = QHBoxLayout()
        search_bar.addWidget(self.search, 1)
        self.category = QComboBox()
        for label, value in [('全部任务', ''), ('Word 文字', 'word'), ('Excel 表格', 'table'),
                             ('文字清理', 'text'), ('名单与数据', 'data'), ('数字', 'number'), ('日期', 'date')]:
            self.category.addItem(label, value)
        self.category.currentIndexChanged.connect(self.refresh)
        search_bar.addWidget(self.category)
        layout.addLayout(search_bar)
        self.list = QListWidget()
        self.list.setObjectName('OfficeToolList')
        self.list.setSpacing(4)
        self.list.setWordWrap(True)
        self.list.setIconSize(QSize(22, 22))
        self.list.itemDoubleClicked.connect(self.open_selected)
        layout.addWidget(self.list, 1)
        button = QPushButton('打开处理预览')
        button.setObjectName('PrimaryButton')
        button.clicked.connect(self.open_selected)
        layout.addWidget(button)
        self.refresh()

    def refresh(self):
        from PySide6.QtCore import Qt
        self.list.clear()
        keyword = self.search.text().strip().lower()
        processors = all_processors()
        ordered = sorted(processors, key=lambda pid: (0 if pid.startswith('word.') else 1 if pid.startswith('table.') else 2, processors[pid].name))
        from . import icons
        for pid in ordered:
            processor = processors[pid]
            if self.category.currentData() and processor.category != self.category.currentData():
                continue
            if keyword and keyword not in (processor.name + processor.doc + pid).lower():
                continue
            item = QListWidgetItem(processor.name + '\n' + (processor.doc or '本地处理 · 可用于工作流'))
            item.setData(Qt.UserRole, pid)
            item.setIcon(icons.icon('file-spreadsheet' if pid.startswith('table.') else 'file-text', '#2458c5', 22))
            item.setSizeHint(QSize(200, 70))
            self.list.addItem(item)
        if self.list.count():
            self.list.setCurrentRow(0)

    def open_selected(self, *_):
        from PySide6.QtCore import Qt
        item = self.list.currentItem()
        if item:
            ToolPreviewDialog(all_processors()[item.data(Qt.UserRole)], self.service, self).exec()
