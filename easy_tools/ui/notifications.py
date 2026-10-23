"""Application-owned failure notices, covered by the global privacy filter."""
from PySide6.QtCore import QObject, Signal, Slot, Qt, QTimer
from PySide6.QtWidgets import QDialog, QVBoxLayout, QLabel, QPushButton


class ProtectedNotice(QObject):
    requested = Signal(str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.dialog = QDialog()
        self.dialog.setWindowTitle('Easy-Tools · 操作提示')
        self.dialog.resize(440, 230)
        layout = QVBoxLayout(self.dialog)
        self.text = QLabel()
        self.text.setTextFormat(Qt.PlainText)
        self.text.setWordWrap(True)
        layout.addWidget(self.text, 1)
        close = QPushButton('知道了')
        close.clicked.connect(self.dialog.close)
        layout.addWidget(close)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.dialog.close)
        self.dialog.finished.connect(lambda _: self.text.clear())
        self.requested.connect(self.show, Qt.QueuedConnection)

    @Slot(str, str)
    def show(self, level, message):
        self.text.setText(message[:2000])
        self.dialog.show()
        self.dialog.raise_()
        self.timer.start(60000)
