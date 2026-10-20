"""Regression checks for non-blocking status and relevant-only OCR scheduling."""

from __future__ import annotations

from unittest.mock import patch

from easy_tools.core.context import ContextResolver, ContextState
from easy_tools.core.ocr import OcrService
from easy_tools.win32.window import WindowInfo


class _Store:
    def __init__(self, sections):
        self.sections = sections

    def section(self, name, default=None):
        return self.sections.get(name, default)


def test_ocr_status_does_not_initialize_onnx_backend():
    service = OcrService(_Store({"ocr": {"enabled": True}}))
    with patch("easy_tools.core.ocr.RapidOcrBackend.available",
               side_effect=AssertionError("status must not initialize OCR")):
        status = service.status()
    assert status["enabled"] is True
    assert status["ready"] is False
    assert status["loading"] is False
    assert status["reason"] == "等待后台首次识别"


def test_ocr_is_only_needed_when_it_can_change_the_rule_result():
    store = _Store({"rules": [{
        "id": "word-content", "match": "all", "processes": ["winword.exe"],
        "ocr_keywords": ["invoice"],
    }]})
    resolver = ContextResolver(store)
    matching_app = ContextState(WindowInfo(hwnd=1, process="winword.exe"))
    other_app = ContextState(WindowInfo(hwnd=2, process="notepad.exe"))
    assert resolver.needs_ocr(matching_app)
    assert not resolver.needs_ocr(other_app)


def test_any_rule_does_not_need_ocr_after_its_process_condition_matches():
    store = _Store({"rules": [{
        "id": "word-or-content", "match": "any", "processes": ["winword.exe"],
        "ocr_keywords": ["invoice"],
    }]})
    resolver = ContextResolver(store)
    assert not resolver.needs_ocr(ContextState(WindowInfo(hwnd=1, process="winword.exe")))
    assert resolver.needs_ocr(ContextState(WindowInfo(hwnd=2, process="notepad.exe")))
