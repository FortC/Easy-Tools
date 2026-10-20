import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QCheckBox, QHeaderView

from easy_tools.ui.question_bank_page import QuestionBankPage
from easy_tools.ui.theme import settings_qss


_app = QApplication.instance() or QApplication([])


def test_question_bank_path_remains_available_in_full_and_checkbox_is_styled():
    full_path = r"E:\very\long\user\profile\folder\question_banks\exam-bank-2026.xlsx"
    page = QuestionBankPage([{
        "id": "bank-1", "name": "考试题库", "path": full_path,
        "enabled": True, "entry_count": 32,
    }])

    path_item = page.table.item(0, 4)
    checkbox = page.table.cellWidget(0, 0).findChild(QCheckBox)
    assert path_item.text() == full_path
    assert path_item.toolTip() == full_path
    assert path_item.data(Qt.UserRole) == full_path
    assert checkbox.isChecked()
    assert page.table.horizontalHeader().sectionResizeMode(4) == QHeaderView.Stretch
    assert "QCheckBox::indicator:checked" in settings_qss()
    page.close()
