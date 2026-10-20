from PySide6.QtCore import QRect, QSize
from easy_tools.ui.question_answer_dialog import answer_popup_position


def test_answer_popup_prefers_selection_right_and_keeps_clear():
    anchor = QRect(60, 80, 580, 90)
    size = QSize(460, 340)
    work = QRect(0, 0, 1500, 900)
    point = answer_popup_position(anchor, size, work)
    assert point.x() > anchor.right()
    assert work.contains(QRect(point, size))
    assert not anchor.intersects(QRect(point, size))


def test_answer_popup_flips_left_on_negative_coordinate_monitor():
    work = QRect(-1920, 0, 1920, 1080)
    anchor = QRect(-180, 800, 160, 100)
    size = QSize(460, 340)
    point = answer_popup_position(anchor, size, work)
    assert point.x() + size.width() < anchor.left()
    assert work.contains(QRect(point, size))
