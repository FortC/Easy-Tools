from pathlib import Path

from openpyxl import Workbook

from easy_tools.core.question_bank import QuestionAnswerService, load_question_bank
from easy_tools.config.schema import load_defaults, normalize, validate


def test_excel_question_bank_and_original_answer(tmp_path):
    path = tmp_path / "bank.xlsx"
    wb = Workbook()
    ws = wb.active
    ws.title = "基础题"
    ws.append(["问题", "答案"])
    ws.append(["牛顿第二定律是什么？", "力等于质量乘以加速度（F=ma）。\n这是原答案第二行。"])
    wb.save(path)

    entries = load_question_bank(path)
    assert entries[0].row == "基础题!2"
    assert "原答案第二行" in entries[0].answer

    class Store:
        def section(self, key, default=None):
            if key == "question_banks":
                return [{"id": "b1", "name": "物理题库", "path": str(path), "enabled": True}]
            return default

    result = QuestionAnswerService(Store()).answer("请问牛顿第二定律是什么？")
    assert result["matched"] is True
    assert result["answer"] == entries[0].answer
    assert result["generated"] is False
    assert result["matches"][0]["source"] == "物理题库"


def test_non_excel_question_bank_is_rejected(tmp_path):
    import pytest
    path = tmp_path / "bank.docx"
    path.write_bytes(b"not-an-excel-file")
    with pytest.raises(ValueError, match="Excel"):
        load_question_bank(path)


def test_excel_uses_correct_answer_column_not_question_type(tmp_path):
    path = tmp_path / "choice-bank.xlsx"
    wb = Workbook()
    ws = wb.active
    ws.append(["题干", "题型", "选项A", "选项B", "正确答案"])
    ws.append(["下列哪个是质数？", "单选", "9", "11", "B"])
    wb.save(path)

    entry = load_question_bank(path)[0]
    assert entry.question == "下列哪个是质数？"
    assert entry.answer == "B. 11"


def test_choice_bank_without_answer_header_is_rejected(tmp_path):
    path = tmp_path / "missing-answer.xlsx"
    wb = Workbook()
    ws = wb.active
    ws.append(["题干", "选项A", "选项B"])
    ws.append(["2+2=?", "3", "4"])
    wb.save(path)

    import pytest
    with pytest.raises(ValueError, match="没有找到正确答案列"):
        load_question_bank(path)


def test_actual_bank_headers_prioritize_correct_answer_and_numbered_options(tmp_path):
    path = tmp_path / "numbered-options.xlsx"
    wb = Workbook()
    ws = wb.active
    ws.append(["题目名称", "题目类型", "题目难度", "答案1", "答案2", "答案3", "正确答案", "答案", "解析"])
    ws.append(["中国的首都是哪里？", "单选", "容易", "上海", "北京", "广州", "B", "A", "干扰解析"])
    ws.append(["哪些数是质数？", "多选", "容易", "2", "4", "5", "Ａ、Ｃ", "B", "干扰解析"])
    wb.save(path)
    entries = load_question_bank(path)
    assert entries[0].answer == "B. 北京"
    assert entries[0].answer_column == "正确答案"
    assert entries[0].answer_raw == "B"
    assert entries[1].answer == "A. 2\nC. 5"


def test_english_correct_answer_beats_generic_answer_column(tmp_path):
    path = tmp_path / "english.xlsx"
    wb = Workbook()
    wb.active.append(["question", "答案", "Correct Answer"])
    wb.active.append(["2 + 2?", "3", "4"])
    wb.save(path)
    assert load_question_bank(path)[0].answer == "4"


def test_unrecognized_question_header_never_falls_back_to_question_type(tmp_path):
    import pytest
    path = tmp_path / "unknown-header.xlsx"
    wb = Workbook()
    wb.active.append(["未知题干字段", "题目类型", "正确答案"])
    wb.active.append(["2 + 2?", "单选", "4"])
    wb.save(path)
    with pytest.raises(ValueError, match="题目列"):
        load_question_bank(path)


def test_verified_correct_answer_cannot_be_overwritten_by_model():
    from types import SimpleNamespace
    from unittest.mock import Mock
    from easy_tools.core.app import AppService
    from easy_tools.core.question_bank import format_bank_evidence

    evidence = [{"question": "中国的首都是哪里？", "answer": "B. 北京", "score": 1.0,
                 "answer_column": "正确答案", "answer_raw": "B"}]
    for provider in ("builtin", "ccswitch"):
        service = object.__new__(AppService)
        service.store = SimpleNamespace(section=lambda *args: {
            "enabled": True, "prefer_bank_answer": False, "provider": provider})
        service.builtin_model = Mock()
        service.ccswitch_model = Mock()
        assert service.answer_question("中国的首都是哪里？", evidence) == "B. 北京"
        service.builtin_model.answer.assert_not_called()
        service.ccswitch_model.answer.assert_not_called()
    text = format_bank_evidence(evidence)
    assert "答案来源列：正确答案" in text
    assert "答案列原值：B" in text


def test_config_rejects_more_than_three_enabled_banks():
    cfg = normalize(load_defaults())
    cfg["question_banks"] = [
        {"id": str(i), "name": f"题库{i}", "path": f"C:/bank{i}.xlsx", "enabled": True}
        for i in range(4)]
    assert "最多同时启用 3 个知识库" in validate(cfg)
