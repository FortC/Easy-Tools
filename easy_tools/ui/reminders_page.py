"""Reminder configuration and protected, application-owned reminder inbox."""
import copy
import uuid
from datetime import datetime
from PySide6.QtCore import QObject, Signal, Slot, Qt, QDateTime, QTime
from PySide6.QtWidgets import (QWidget, QDialog, QVBoxLayout, QHBoxLayout, QFormLayout,
                               QLabel, QLineEdit, QComboBox, QSpinBox, QCheckBox,
                               QListWidget, QPushButton, QDialogButtonBox, QDateTimeEdit, QTimeEdit)
from ..core.reminders import validate_reminders


class ReminderEditor(QDialog):
    def __init__(self, item=None, parent=None):
        super().__init__(parent)
        self.item = copy.deepcopy(item or {})
        self.setWindowTitle('定时提醒')
        self.resize(500, 330)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.title = QLineEdit(self.item.get('title', ''))
        self.title.setMaxLength(500)
        self.title.setPlaceholderText('例如：检查合同附件、休息一下、提交日报')
        form.addRow('提醒内容', self.title)
        self.mode = QComboBox()
        for label, value in [('指定时间（一次）', 'once'), ('每天', 'daily'), ('间隔重复', 'interval')]:
            self.mode.addItem(label, value)
        self.mode.setCurrentIndex(max(0, self.mode.findData(self.item.get('mode', 'once'))))
        form.addRow('提醒方式', self.mode)
        self.at = QDateTimeEdit(QDateTime.currentDateTime().addSecs(600))
        self.at.setCalendarPopup(True)
        self.at.setDisplayFormat('yyyy-MM-dd HH:mm')
        self.daily = QTimeEdit(QTime(9, 0))
        self.daily.setDisplayFormat('HH:mm')
        self.interval = QSpinBox()
        self.interval.setRange(1, 10080)
        self.interval.setSuffix(' 分钟')
        self.interval.setValue(int(self.item.get('seconds', 3600)) // 60)
        if self.item.get('mode') == 'once':
            self.at.setDateTime(QDateTime.fromString(self.item.get('at', ''), Qt.ISODate))
        elif self.item.get('mode') == 'daily':
            self.daily.setTime(QTime.fromString(self.item.get('at', ''), 'HH:mm'))
        for label, widget in [('日期与时间', self.at), ('每天时间', self.daily), ('提醒间隔', self.interval)]:
            form.addRow(label, widget)
        self.enabled = QCheckBox('启用提醒')
        self.enabled.setChecked(self.item.get('enabled', True))
        form.addRow('', self.enabled)
        layout.addLayout(form)
        note = QLabel('按本机时间提醒；需要程序保持运行。休眠或关机期间不会唤醒电脑；错过超过一分钟的一次提醒不补发。提醒不会执行宏或脚本。')
        note.setWordWrap(True)
        layout.addWidget(note)
        self.error = QLabel()
        self.error.setWordWrap(True)
        layout.addWidget(self.error)
        box = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        box.button(QDialogButtonBox.Save).setText('保存')
        box.button(QDialogButtonBox.Cancel).setText('取消')
        box.accepted.connect(self.save)
        box.rejected.connect(self.reject)
        layout.addWidget(box)
        def visibility():
            for widget, mode in [(self.at, 'once'), (self.daily, 'daily'), (self.interval, 'interval')]:
                form.setRowVisible(widget, self.mode.currentData() == mode)
        self.mode.currentIndexChanged.connect(visibility)
        visibility()

    def value(self):
        mode = self.mode.currentData()
        at = self.at.dateTime().toString('yyyy-MM-ddTHH:mm:00') if mode == 'once' else self.daily.time().toString('HH:mm')
        value = dict(id=self.item.get('id') or uuid.uuid4().hex, title=self.title.text().strip(),
                     mode=mode, at=at, seconds=self.interval.value() * 60, enabled=self.enabled.isChecked())
        if self.item.get('at') == at and self.item.get('mode') == mode and self.item.get('fired_at'):
            value['fired_at'] = self.item['fired_at']
        return value

    def save(self):
        value = self.value()
        errors = validate_reminders([value])
        if not errors and value['mode'] == 'once' and not value.get('fired_at') and datetime.fromisoformat(value['at']) <= datetime.now():
            errors = ['一次提醒请选择将来的时间']
        if errors:
            self.error.setText('；'.join(errors))
        else:
            self.accept()


class RemindersPage(QWidget):
    def __init__(self, store, parent=None):
        super().__init__(parent)
        self.store = store
        self._rows = []
        layout = QVBoxLayout(self)
        note = QLabel('提醒在应用内显示，并继承隐私模式。新增或编辑后，点击底部“保存并立即生效”。\n仅在 Easy-Tools 运行期间生效；不会后台执行脚本或向外发送提醒内容。')
        note.setWordWrap(True)
        layout.addWidget(note)
        self.list = QListWidget()
        self.list.itemDoubleClicked.connect(lambda _: self.edit())
        layout.addWidget(self.list, 1)
        bar = QHBoxLayout()
        for text, slot in [('新增提醒', lambda: self.edit(True)), ('编辑选中', self.edit), ('删除选中', self.remove)]:
            button = QPushButton(text)
            button.clicked.connect(slot)
            bar.addWidget(button)
        layout.addLayout(bar)

    def set_rows(self, rows):
        self._rows = copy.deepcopy(rows)
        self.list.clear()
        names = {'once': '一次', 'daily': '每天', 'interval': '间隔'}
        for item in self._rows:
            state = '已触发' if item.get('fired_at') else '启用' if item.get('enabled', True) else '暂停'
            when = f"{item.get('seconds', 0) // 60} 分钟" if item.get('mode') == 'interval' else item.get('at', '')
            self.list.addItem(f"{item.get('title', '')}\n{state} · {names.get(item.get('mode'), '?')} · {when}")

    def edit(self, add=False):
        row = self.list.currentRow()
        if not add and row < 0:
            return
        if add and len(self._rows) >= 100:
            return
        dialog = ReminderEditor(None if add else self._rows[row], self)
        if dialog.exec() == QDialog.Accepted:
            if add:
                self._rows.append(dialog.value())
            else:
                self._rows[row] = dialog.value()
            self.set_rows(self._rows)

    def remove(self):
        row = self.list.currentRow()
        if row >= 0:
            self._rows.pop(row)
            self.set_rows(self._rows)

    def collect(self):
        rows = copy.deepcopy(self._rows)
        current = {r['id']: r for r in self.store.section('reminders', [])}
        for row in rows:
            saved = current.get(row['id'], {})
            if saved.get('at') == row.get('at') and saved.get('fired_at'):
                row['fired_at'] = saved['fired_at']
        return rows


class ReminderInbox(QObject):
    requested = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.dialog = QDialog()
        self.dialog.setWindowTitle('办公提醒 · Easy-Tools')
        self.dialog.resize(460, 320)
        layout = QVBoxLayout(self.dialog)
        layout.addWidget(QLabel('到时间了 · 最近 20 条提醒（关闭后清除）'))
        self.list = QListWidget()
        self.list.setWordWrap(True)
        layout.addWidget(self.list)
        button = QPushButton('已知晓，清除提醒')
        button.clicked.connect(self.dialog.close)
        layout.addWidget(button)
        self.dialog.finished.connect(lambda _: self.list.clear())
        self.requested.connect(self.show, Qt.QueuedConnection)

    @Slot(str)
    def show(self, text):
        self.list.insertItem(0, text)
        while self.list.count() > 20:
            self.list.takeItem(20)
        self.dialog.show()
        self.dialog.raise_()
