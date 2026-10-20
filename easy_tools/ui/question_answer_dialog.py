"""截图知识库问答结果窗口。"""
from PySide6.QtCore import QPoint, QRect, QSize
from PySide6.QtGui import QCursor, QGuiApplication
from PySide6.QtWidgets import (QApplication, QDialog, QHBoxLayout, QLabel,
                               QMessageBox, QPushButton, QPlainTextEdit,
                               QVBoxLayout)


def answer_popup_position(anchor: QRect, size: QSize, available: QRect, gap: int = 12) -> QPoint:
    width, height = size.width(), size.height()
    clamp_x = lambda value: max(available.left(), min(value, available.right() - width + 1))
    clamp_y = lambda value: max(available.top(), min(value, available.bottom() - height + 1))
    candidates = [QPoint(anchor.right() + gap, clamp_y(anchor.top())),
                  QPoint(anchor.left() - width - gap, clamp_y(anchor.top())),
                  QPoint(clamp_x(anchor.left()), anchor.bottom() + gap),
                  QPoint(clamp_x(anchor.left()), anchor.top() - height - gap)]
    for point in candidates:
        if available.contains(QRect(point, size)):
            return point
    # Very large selections may leave no free side; minimize overlap while
    # keeping the result reachable inside the screen's work area.
    fitted = [QPoint(clamp_x(point.x()), clamp_y(point.y())) for point in candidates]
    return min(fitted, key=lambda point: (
        QRect(point, size).intersected(anchor).width()
        * QRect(point, size).intersected(anchor).height()))


class QuestionAnswerDialog(QDialog):
    def position_near_selection(self, anchor: QRect | None = None):
        if anchor is None or anchor.isEmpty():
            anchor = QRect(QCursor.pos(), QSize(1, 1))
        screen = QGuiApplication.screenAt(anchor.center()) or QGuiApplication.primaryScreen()
        if screen is not None:
            self.move(answer_popup_position(anchor, self.size(), screen.availableGeometry()))

    def __init__(self, ocr_text: str, answer: str, sources: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("截图 / OCR 提问结果")
        self.setMinimumSize(380, 260)
        self.resize(460, 340)
        from .theme import settings_qss
        self.setStyleSheet(settings_qss())
        layout = QVBoxLayout(self)
        title = QLabel("提问结果" if answer else "没有可显示的答案")
        title.setStyleSheet("font-size:16px;font-weight:600;color:#243247")
        layout.addWidget(title)
        layout.addWidget(QLabel("问题 / OCR 识别文字"))
        self.ocr = QPlainTextEdit()
        self.ocr.setReadOnly(True)
        self.ocr.setMaximumHeight(100)
        self.ocr.setPlainText(ocr_text)
        layout.addWidget(self.ocr)
        layout.addWidget(QLabel("回答"))
        self.answer = QPlainTextEdit()
        self.answer.setReadOnly(True)
        self.answer.setPlainText(answer)
        layout.addWidget(self.answer, 1)
        if sources:
            source_label = QLabel("匹配来源：\n" + sources)
            source_label.setWordWrap(True)
            source_label.setStyleSheet("color:#475569;font-size:12px")
            layout.addWidget(source_label)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        copy = self.copy_button = QPushButton("复制答案")
        copy.setObjectName("PrimaryButton")
        copy.setMinimumWidth(96)
        copy.clicked.connect(lambda: QApplication.clipboard().setText(answer))
        close = self.close_button = QPushButton("关闭")
        close.setMinimumWidth(76)
        close.clicked.connect(self.close)
        buttons.addWidget(copy)
        buttons.addWidget(close)
        layout.addLayout(buttons)
