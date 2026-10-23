"""Inline branches with a small list-compatible API for the workflow editor."""
from PySide6.QtCore import Signal
from PySide6.QtWidgets import QTreeWidget, QTreeWidgetItem


class StepTree(QTreeWidget):
    currentRowChanged = Signal(int)

    def __init__(self, describe, parent=None):
        super().__init__(parent)
        self.describe = describe
        self.setObjectName('WorkflowSteps')
        self.records = []
        self.branches = []
        self.setHeaderHidden(True)
        self.setMinimumWidth(260)
        self.setWordWrap(True)
        self.currentItemChanged.connect(lambda *_: self.currentRowChanged.emit(self.currentRow()))

    def set_steps(self, steps):
        self.clear()
        self.records, self.branches = [], []

        def add(container, parent):
            for index, step in enumerate(container):
                item = QTreeWidgetItem(parent, [f'{index + 1}. {self.describe(step)}'])
                self.records.append((container, index, item))
                for key, label in [('then', '满足条件'), ('else', '否则'), ('steps', '重复执行')]:
                    if (step.get('type') == 'condition' and key in ('then', 'else') or
                            step.get('type') == 'loop' and key == 'steps'):
                        child = step.setdefault(key, [])
                        branch = QTreeWidgetItem(item, [label + (' · 点击此处添加步骤' if not child else '')])
                        self.branches.append((step, key, child, branch))
                        add(child, branch)
        add(steps, self.invisibleRootItem())
        self.expandAll()

    def currentRow(self):
        item = self.currentItem()
        return next((i for i, (_, _, node) in enumerate(self.records) if node is item), -1)

    def setCurrentRow(self, row):
        if 0 <= row < len(self.records):
            self.setCurrentItem(self.records[row][2])

    def location(self, row):
        return self.records[row][:2] if 0 <= row < len(self.records) else (None, -1)

    def insertion_target(self, root):
        for _, _, container, item in self.branches:
            if self.currentItem() is item:
                return container
        container, _ = self.location(self.currentRow())
        return root if container is None else container

    def select_step(self, step):
        for container, index, item in self.records:
            if container[index] is step:
                self.setCurrentItem(item)
                return

    def select_branch(self, step, key):
        for owner, branch_key, _, item in self.branches:
            if owner is step and branch_key == key:
                self.setCurrentItem(item)
                return
