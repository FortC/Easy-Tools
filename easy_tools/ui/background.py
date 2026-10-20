"""Bounded daemon jobs; results always return to a QObject GUI receiver."""
from __future__ import annotations
import threading
from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import QApplication

_active = threading.BoundedSemaphore(2)
_pending = threading.BoundedSemaphore(8)


class _Delivery(QObject):
    ready = Signal(object, str)


def submit(work, receiver) -> bool:
    """Call on the GUI thread with a bound QObject method as receiver."""
    if not _pending.acquire(blocking=False):
        return False
    delivery = _Delivery(QApplication.instance())
    delivery.ready.connect(receiver, Qt.QueuedConnection)
    delivery.ready.connect(delivery.deleteLater, Qt.QueuedConnection)

    def worker():
        try:
            with _active:
                try:
                    value, error = work(), ""
                except Exception as exc:
                    value, error = None, str(exc)
            try:
                delivery.ready.emit(value, error)
            except RuntimeError:
                pass  # QApplication already destroyed during shutdown.
        finally:
            _pending.release()

    threading.Thread(target=worker, name="ui-background", daemon=True).start()
    return True
