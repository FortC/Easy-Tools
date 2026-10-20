import json
import os
import threading
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from openpyxl import Workbook
from PySide6.QtCore import QEvent
from PySide6.QtWidgets import QApplication

from easy_tools.core.question_bank import (QuestionAnswerService, inspect_excel_bank,
    load_question_bank, mapping_ai_prompt, mapping_from_ai)
from easy_tools.ui.bank_columns_dialog import BankColumnsDialog
from easy_tools.ui.question_bank_page import QuestionBankPage

app = QApplication.instance() or QApplication([])


def workbook(path, rows):
    wb = Workbook()
    for row in rows:
        wb.active.append(row)
    wb.save(path)
    wb.close()


def test_manual_answer_content_and_mapping_change_invalidates_cache(tmp_path):
    path = tmp_path / "custom.xlsx"
    workbook(path, [["提问内容", "类型", "标记", "正确详情", "其他详情"],
                    ["中国的首都？", "单选", "B", "北京", "上海"]])
    bank = {"enabled": True, "path": str(path), "column_mapping": {"Sheet": {
        "header_row": 1, "question": 0, "answer": 2, "answer_content": 3, "options": {}}}}
    class Store:
        def section(self, key, default=None):
            return [bank] if key == "question_banks" else default
    service = QuestionAnswerService(Store())
    result = service.answer("中国的首都？")
    assert result["answer"] == "B. 北京"
    assert result["matches"][0]["answer_configured"] is True
    bank["column_mapping"]["Sheet"]["answer_content"] = 4
    assert service.answer("中国的首都？")["answer"] == "B. 上海"


def test_header_row_detection_and_no_header_manual_mapping(tmp_path):
    path = tmp_path / "titled.xlsx"
    workbook(path, [["题库标题"], ["题目名称", "正确答案"], ["2+2?", "4"]])
    assert inspect_excel_bank(path)["Sheet"]["header_row"] == 2
    assert load_question_bank(path)[0].answer == "4"
    raw = tmp_path / "raw.xlsx"
    workbook(raw, [["问题一", "答案一"], ["问题二", "答案二"]])
    entries = load_question_bank(raw, {"Sheet": {"header_row": 1, "has_header": False,
        "question": 0, "answer": 1, "options": {}}})
    assert len(entries) == 2
    assert entries[0].question == "问题一"


def test_ai_column_json_is_validated_and_only_headers_are_prompted():
    headers = ["提问内容", "正确详情", "标记", "类型"]
    prompt = mapping_ai_prompt(headers)
    assert "提问内容" in prompt and "类型" in prompt
    mapping = mapping_from_ai('```json\n{"question":"A","answer":"C",'
        '"answer_content":"B","options":{}}\n```', headers)
    assert mapping["answer"] == 2 and mapping["answer_content"] == 1
    with pytest.raises(ValueError):
        mapping_from_ai('{"question":"A","answer":"ZZ","options":{}}', headers)
    with pytest.raises(ValueError):
        mapping_from_ai('{"question":"A","answer":"A","options":{}}', headers)


def test_mapping_dialog_shows_actual_columns_and_saves(tmp_path):
    path = tmp_path / "bank.xlsx"
    workbook(path, [["题目名称", "题目类型", "答案1", "答案2", "正确答案"],
                    ["2+2?", "单选", "3", "4", "B"]])
    bank = {"id": "test", "path": str(path), "enabled": True}
    page = QuestionBankPage([bank])
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline and page.table.item(0, 3).text() != "正确答案":
        app.processEvents()
        time.sleep(0.01)
    assert page.table.item(0, 3).text() == "正确答案"
    dialog = BankColumnsDialog(bank)
    assert dialog.question.currentData() == 0
    assert dialog.answer.currentData() == 4
    assert dialog.options["B"].currentData() == 3
    dialog.accept()
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline and dialog.result_mapping() is None:
        app.processEvents()
        time.sleep(0.01)
    assert dialog.result_mapping()["Sheet"]["answer"] == 4
    assert dialog.entry_count == 1
    page.close()
    dialog.close()


def test_ai_identification_receives_no_body_and_is_safe_after_dialog_closes(tmp_path):
    path = tmp_path / "bank.xlsx"
    workbook(path, [["题目名称", "正确答案"], ["PRIVATE_QUESTION_BODY", "PRIVATE_ANSWER"]])
    gate = threading.Event()
    prompts = []
    completed = threading.Event()
    def model(prompt, evidence):
        prompts.append(prompt)
        gate.wait(3)
        completed.set()
        return json.dumps({"question": "A", "answer": "B", "options": {}})
    dialog = BankColumnsDialog({"path": str(path)}, model)
    dialog.ai_detect()
    dialog.reject()
    dialog.deleteLater()
    app.sendPostedEvents(None, QEvent.DeferredDelete)
    gate.set()
    assert completed.wait(3)
    until = time.monotonic() + 0.1
    while time.monotonic() < until:
        app.processEvents()
    assert prompts and "PRIVATE_QUESTION_BODY" not in prompts[0] and "PRIVATE_ANSWER" not in prompts[0]
