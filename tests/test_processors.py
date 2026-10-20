"""剪贴板处理器与表格解析的单元测试。

对应方案验收第 4、5 条：框选数据 → 去空行 + 转置 → 回填，
以及事件链示例"复制 → 清理 → 粘贴"。这些逻辑是纯函数，
所以能在没有剪贴板/前台窗口的测试环境里被完整验证。
"""

from __future__ import annotations

import unittest

from easy_tools.core.actions.processors import (all_processors, eval_condition,
                                                 looks_like_table, parse_table,
                                                 detect_delimiter, get_processor)


def run(pid: str, text: str, args=None) -> str:
    p = get_processor(pid)
    assert p is not None, "处理器未注册: %s" % pid
    return p.run(text, args or {})


SAMPLE_TSV = "姓名\t年龄\t城市\n张三\t28\t北京\n李四\t34\t上海\n王五\t41\t北京"


class TestTextProcessors(unittest.TestCase):
    def test_strip(self):
        self.assertEqual(run("text.strip", "  hi  \n"), "hi")

    def test_drop_blank_lines(self):
        self.assertEqual(run("text.drop_blank_lines", "a\n\n  \nb\n"), "a\nb")

    def test_collapse_spaces(self):
        self.assertEqual(run("text.collapse_spaces", "a    b\t\tc"), "a b c")

    def test_remove_invisible(self):
        self.assertEqual(run("text.remove_invisible", "a\u200bb\ufeffc"), "abc")

    def test_replace_regex(self):
        self.assertEqual(run("text.replace", "a1b22c", {"find": r"\d+", "replace": "#", "regex": True}),
                         "a#b#c")

    def test_add_line_numbers(self):
        self.assertEqual(run("text.add_line_numbers", "a\nb", {"start": 1, "pad": 2, "sep": ". "}),
                         " 1. a\n 2. b")


class TestTableParsing(unittest.TestCase):
    def test_detect_tab_from_excel_copy(self):
        self.assertEqual(detect_delimiter(SAMPLE_TSV), "\t")

    def test_parse_tsv(self):
        rows = parse_table(SAMPLE_TSV)
        self.assertEqual(len(rows), 4)
        self.assertEqual(rows[0], ["姓名", "年龄", "城市"])
        self.assertEqual(rows[1], ["张三", "28", "北京"])

    def test_parse_csv_with_quotes(self):
        rows = parse_table('a,b\n"x,1",y', ",")
        self.assertEqual(rows[1], ["x,1", "y"])

    def test_markdown_detected(self):
        md = "| a | b |\n| --- | --- |\n| 1 | 2 |"
        rows = parse_table(md)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0], ["a", "b"])

    def test_looks_like_table(self):
        self.assertTrue(looks_like_table(SAMPLE_TSV))
        self.assertFalse(looks_like_table("just one line"))
        self.assertFalse(looks_like_table(""))


class TestTableProcessors(unittest.TestCase):
    def test_transpose(self):
        # 源表第二行只有 1 列，转置后必须补齐空格，否则列数对不上
        self.assertEqual(run("table.transpose", "a\tb\nc\n1\t2"), "a\tc\t1\nb\t\t2\n")

    def test_transpose_square(self):
        out = run("table.transpose", SAMPLE_TSV)
        rows = parse_table(out)
        self.assertEqual(rows[0], ["姓名", "张三", "李四", "王五"])
        self.assertEqual(len(rows), 3)

    def test_to_csv(self):
        out = run("table.to_csv", SAMPLE_TSV)
        self.assertNotIn("\t", out)
        rows = parse_table(out, ",")
        self.assertEqual(rows[0], ["姓名", "年龄", "城市"])
        self.assertEqual(rows[1], ["张三", "28", "北京"])

    def test_to_csv_quotes_only_when_needed(self):
        self.assertEqual(run("table.to_csv", "a,b\nc,d"), "a,b\nc,d\n")
        self.assertIn('"', run("table.to_csv", 'x,"a,b"'))

    def test_to_markdown(self):
        out = run("table.to_markdown", "a\tb\n1\t2")
        self.assertEqual(out, "| a | b |\n| --- | --- |\n| 1 | 2 |")

    def test_markdown_roundtrip(self):
        md = run("table.to_markdown", SAMPLE_TSV)
        back = run("table.from_markdown", md)
        self.assertEqual(parse_table(back)[1], ["张三", "28", "北京"])

    def test_drop_empty_cols(self):
        self.assertEqual(run("table.remove_empty_cols", "a\t\nb\t\n"), "a\nb\n")

    def test_sort_by_column_numeric(self):
        out = run("table.sort_by_column", "name\tage\nb\t10\na\t2", {"col": 1})
        self.assertEqual(parse_table(out)[1], ["a", "2"])

    def test_unique_rows(self):
        self.assertEqual(parse_table(run("table.unique_rows", "a\nb\na")).__len__(), 2)

    def test_select_columns(self):
        out = run("table.select_columns", "a\tb\tc\n1\t2\t3", {"cols": "0,2"})
        self.assertEqual(parse_table(out)[0], ["a", "c"])

    def test_merge_and_split_columns(self):
        merged = run("table.merge_columns", "a\tb\tc\n1\t2\t3", {"cols": "0,1", "sep": "-"})
        self.assertEqual(parse_table(merged)[0], ["a-b", "c"])
        split = run("table.split_by_column", merged, {"col": 0, "sep": "-"})
        self.assertEqual(parse_table(split)[0], ["a", "b", "c"])


class TestNumberProcessors(unittest.TestCase):
    def test_thousands_sep(self):
        self.assertEqual(run("number.thousands_sep", "1234567"), "1,234,567")

    def test_thousands_sep_negative_and_float(self):
        self.assertEqual(run("number.thousands_sep", "-1234567.25"), "-1,234,567.25")

    def test_thousands_inline_text(self):
        self.assertEqual(run("number.thousands_sep", "abc 12345 def"), "abc 12,345 def")

    def test_thousands_does_not_touch_dates_in_cells(self):
        # TSV 场景按整格处理，日期不能被拆成 2,026-10-19
        self.assertEqual(run("number.thousands_sep", "2026-10-19\t12345"), "2026-10-19\t12,345")

    def test_fixed_decimals(self):
        self.assertEqual(run("number.fixed_decimals", "1\t2.345", {"decimals": 2}), "1.00\t2.35")

    def test_sum(self):
        self.assertEqual(run("number.sum", "1\n2\n3.5"), "6.5")

    def test_strip_sep(self):
        self.assertEqual(run("number.strip_sep", "1,234,567"), "1234567")


class TestCaseProcessors(unittest.TestCase):
    def test_case_conversions(self):
        self.assertEqual(run("case.upper", "ab"), "AB")
        self.assertEqual(run("case.lower", "AB"), "ab")
        self.assertEqual(run("case.title", "hello world"), "Hello World")
        self.assertEqual(run("case.camel", "foo_bar_baz"), "fooBarBaz")
        self.assertEqual(run("case.snake", "fooBarBaz"), "foo_bar_baz")
        self.assertEqual(run("case.kebab", "fooBarBaz"), "foo-bar-baz")
        self.assertEqual(run("case.reverse_words", "a b c"), "c b a")


class TestDateProcessors(unittest.TestCase):
    def test_date_format(self):
        self.assertEqual(run("date.format", "会议 2026/10/19 举行", {"fmt": "%Y-%m-%d"}),
                         "会议 2026-10-19 举行")

    def test_date_format_invalid_passthrough(self):
        self.assertEqual(run("date.format", "没有日期"), "没有日期")


class TestDataProcessors(unittest.TestCase):
    def test_dedupe_ignores_case(self):
        self.assertEqual(run("data.dedupe", "A\na\nb"), "A\nb")

    def test_dedupe_keep_order(self):
        self.assertEqual(run("data.sort_lines", "c\na\nb"), "a\nb\nc")

    def test_dedupe_by_column(self):
        out = run("data.dedupe_by_column", "k\tv\n1\ta\n1\tb\n2\tc", {"col": 0})
        self.assertEqual(len(parse_table(out)), 3)

    def test_add_numbers(self):
        # 原文本末尾换行必须保留，否则回填到编辑器会吞掉最后一行
        self.assertEqual(run("data.add_numbers", "a\nb\n", {"start": 1, "step": 1}), "1\ta\n2\tb\n")

    def test_normalize_width(self):
        self.assertEqual(run("data.normalize_width", "ＡＢＣ１２３"), "ABC123")


class TestConditions(unittest.TestCase):
    def test_conditions(self):
        self.assertTrue(eval_condition("always", "x"))
        self.assertFalse(eval_condition("never", "x"))
        self.assertTrue(eval_condition("has_table", SAMPLE_TSV))
        self.assertFalse(eval_condition("has_table", "单行"))
        self.assertTrue(eval_condition("is_empty", "   "))
        self.assertTrue(eval_condition("is_number", " 42 "))
        self.assertTrue(eval_condition("contains:北京", "我在北京"))
        self.assertFalse(eval_condition("ncontains:北京", "我在北京"))
        self.assertTrue(eval_condition("regex:\\d{3}", "abc123"))
        self.assertTrue(eval_condition("len_gt:2", "abcd"))

    def test_unknown_condition_raises(self):
        with self.assertRaises(ValueError):
            eval_condition("wat", "x")


class TestRegistryIntegrity(unittest.TestCase):
    def test_unique_ids_and_naming(self):
        procs = all_processors()
        self.assertGreater(len(procs), 30)
        for pid, p in procs.items():
            self.assertEqual(pid.split(".")[0], p.category,
                             "处理器 id 前缀必须与 category 一致: %s" % pid)
            self.assertTrue(p.name)

    def test_defaults_reference_real_processors(self):
        """默认配置里用到的处理器必须真实存在，否则动作点了就报错。"""
        from easy_tools.config.schema import load_defaults
        used = {a["params"]["processor"] for a in load_defaults()["actions"]
                if a.get("kind") == "clipboard.processor"}
        missing = used - set(all_processors())
        self.assertFalse(missing, "默认配置引用了未注册处理器: %s" % sorted(missing))


if __name__ == "__main__":
    unittest.main()
