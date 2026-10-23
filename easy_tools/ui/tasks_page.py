from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import QWidget, QVBoxLayout, QLabel, QListWidget, QListWidgetItem, QPushButton, QPlainTextEdit


class TasksPage(QWidget):
    def __init__(self, manager, parent=None):
        super().__init__(parent)
        self.manager = manager
        layout = QVBoxLayout(self)
        note = QLabel('查看动作、工作流和办公处理任务。历史只保留状态与耗时，不保存处理原文。\n停止会中断后续步骤；已完成的文档修改、按键操作不会自动撤销。')
        note.setWordWrap(True)
        layout.addWidget(note)
        self.list = QListWidget()
        self.list.currentItemChanged.connect(lambda *_: self.details())
        layout.addWidget(self.list, 1)
        self.detail = QPlainTextEdit()
        self.detail.setReadOnly(True)
        self.detail.setMaximumHeight(170)
        self.detail.setPlaceholderText('选中任务可查看每步状态与耗时；不保留处理原文')
        layout.addWidget(self.detail)
        self._snapshot = []
        button = QPushButton('停止选中任务')
        button.clicked.connect(self.cancel)
        layout.addWidget(button)
        self.timer = QTimer(self)
        self.timer.setInterval(500)
        self.timer.timeout.connect(self.refresh)

    def showEvent(self, event):
        self.refresh()
        self.timer.start()
        super().showEvent(event)

    def hideEvent(self, event):
        self.timer.stop()
        super().hideEvent(event)

    def refresh(self):
        selected = self.list.currentItem()
        selected_id = selected.data(Qt.UserRole) if selected else None
        self.list.blockSignals(True)
        self.list.clear()
        names = dict(queued='等待', running='执行中', cancelled='已停止', failed='失败', succeeded='完成')
        self._snapshot = self.manager.snapshot()
        for task in self._snapshot:
            item = QListWidgetItem(f"{task['title']}\n{names[task['state']]} · {task['seconds']} 秒")
            item.setData(Qt.UserRole, task['id'])
            self.list.addItem(item)
            if task['id'] == selected_id:
                self.list.setCurrentItem(item)
        self.list.blockSignals(False)
        self.details()

    def details(self):
        item = self.list.currentItem()
        task_id = item.data(Qt.UserRole) if item else None
        task = next((t for t in self._snapshot if t['id'] == task_id), {})
        names = dict(running='执行中', succeeded='完成', failed='失败', cancelled='已停止')
        from .chain_editor import describe_step
        lines = [f"{i + 1}. {describe_step({'type': 'action', 'id': step['step']})} · "
                 f"{names.get(step['status'], step['status'])} · {step['seconds']} 秒"
                 for i, step in enumerate(task.get('trace', []))]
        self.detail.setPlainText('\n'.join(lines))

    def cancel(self):
        item = self.list.currentItem()
        if item:
            self.manager.cancel(item.data(Qt.UserRole))
            self.refresh()
