"""Deliver bounded core tasks to QObject methods on the GUI thread."""
from PySide6.QtCore import QObject, Signal, Qt
from PySide6.QtWidgets import QApplication


class Delivery(QObject):
    ready = Signal(object, str)


def submit_task(manager, title, work, receiver, *, cancel=None, key=None):
    delivery = Delivery(QApplication.instance())
    delivery.ready.connect(receiver, Qt.QueuedConnection)
    delivery.ready.connect(delivery.deleteLater, Qt.QueuedConnection)
    task_id = manager.submit(title, work, key=key, cancel=cancel, finished=delivery.ready.emit)
    if not task_id:
        delivery.deleteLater()
    return task_id
