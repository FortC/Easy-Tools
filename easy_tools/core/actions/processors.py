"""剪贴板处理器（方案 2.6）。

这些处理器**不是**为面板而写的一次性按钮，而是同时充当事件链步骤的纯函数：
    fn(text: str, args: dict) -> str
纯函数意味着可以在没有剪贴板、没有前台窗口的测试环境里完整验证，
验收标准第 4 条（框选 Excel → 去空行 + 转置 → 回填）才有真实底气。

表格解析的两个工程细节：
1. 自动识别分隔符：Excel 复制出来通常是 TAB + CRLF，用逗号切会得到一整行；
2. 引号处理：CSV 里 "a,b" 是一个字段，不处理就会把一行切成多列，切错列比不处理更糟。
"""

from __future__ import annotations

import csv
import io
import re
import threading
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Dict, List, Optional, Tuple

from ..log import get_logger

log = get_logger("processors")

ProcessorFn = Callable[[str, Dict[str, object]], str]


@dataclass
class Processor:
    id: str
    name: str
    category: str
    fn: ProcessorFn
    doc: str = ""
    args: Dict[str, object] = field(default_factory=dict)

    def run(self, text: str, args: Optional[Dict[str, object]] = None) -> str:
        merged = dict(self.args)
        merged.update(args or {})
        return self.fn(text or "", merged)


PROCESSORS: Dict[str, Processor] = {}
_lock = threading.RLock()


def register_processor(pid: str, name: str, category: str, doc: str = "",
                       args: Optional[Dict[str, object]] = None):
    """处理器注册装饰器。args 是默认参数，供配置中心渲染表单。"""
    def deco(fn: ProcessorFn) -> ProcessorFn:
        with _lock:
            if pid in PROCESSORS:
                raise ValueError("处理器重复注册：%s" % pid)
            PROCESSORS[pid] = Processor(id=pid, name=name, category=category, fn=fn,
                                        doc=doc, args=dict(args or {}))
        return fn
    return deco


def get_processor(pid: str) -> Optional[Processor]:
    return PROCESSORS.get(pid)


def all_processors() -> Dict[str, Processor]:
    with _lock:
        return dict(PROCESSORS)


# ====================================================================== 表格解析
_INVISIBLE = re.compile(r"[\u200b-\u200f\u202a-\u202e\u2060\ufeff\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _norm_newlines(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _split_lines(text: str) -> List[str]:
    return _norm_newlines(text).split("\n")


def detect_delimiter(text: str) -> str:
    """按第一行判定分隔符：TAB 优先于逗号（Excel 默认复制是 TAB）。"""
    lines = [l for l in _split_lines(text) if l.strip()]
    if not lines:
        return "\t"
    first = lines[0]
    if "\t" in first:
        return "\t"
    if first.count(",") >= first.count(";"):
        return ","
    return ";"


def parse_table(text: str, delimiter: Optional[str] = None) -> List[List[str]]:
    """解析成二维表。空文本返回 []。"""
    text = _norm_newlines(text)
    if not text.strip():
        return []
    if delimiter is None:
        # Markdown 表格优先按管道解析
        if "|" in text.split("\n")[0] and re.search(r"^\s*\|?\s*:?-{2,}", text, re.M):
            delimiter = "|"
        else:
            delimiter = detect_delimiter(text)
    if delimiter == "|":
        rows = []
        for line in _split_lines(text):
            if not line.strip():
                continue
            if re.match(r"^\s*\|?\s*:?-{2,}", line):   # 跳过分隔行
                continue
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            rows.append(cells)
        return rows
    return [row for row in csv.reader(io.StringIO(text), delimiter=delimiter)]


def render_table(rows: List[List[str]], delimiter: str = "\t",
                 quoting: bool = False) -> str:
    if not rows:
        return ""
    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=delimiter, lineterminator="\n",
                        quoting=csv.QUOTE_MINIMAL if quoting else csv.QUOTE_NONE,
                        quotechar='"', escapechar="\\")
    for row in rows:
        writer.writerow(["" if c is None else str(c) for c in row])
    return buf.getvalue()


def looks_like_table(text: str) -> bool:
    """至少两行且第一行列数 > 1，才算表格。单行文本不算。"""
    rows = parse_table(text)
    if len(rows) < 2:
        return False
    width = len(rows[0])
    if width < 2:
        return False
    return any(len(r) >= width for r in rows[1:])


def _as_int(v: object, default: int) -> int:
    try:
        return int(float(str(v)))
    except (TypeError, ValueError):
        return default


def _round(v: float, digits: int, mode: str) -> float:
    import math
    factor = 10 ** digits
    if mode == "floor":
        return math.floor(v * factor) / factor
    if mode == "ceil":
        return math.ceil(v * factor) / factor
    return round(v, digits)


# ====================================================================== text
@register_processor("text.strip", "去首尾空白", "text", "整段文本首尾去空白")
def _p_strip(text, args):
    return text.strip()


@register_processor("text.drop_blank_lines", "去空行", "text", "删除只含空白字符的行")
def _p_drop_blank(text, args):
    return "\n".join(l for l in _split_lines(text) if l.strip())


@register_processor("text.collapse_spaces", "合并多空格", "text", "连续空格/制表符合并为一个空格")
def _p_collapse(text, args):
    return re.sub(r"[ \t]{2,}", " ", text)


@register_processor("text.remove_invisible", "去不可见字符", "text", "零宽/方向控制/BOM 等")
def _p_invisible(text, args):
    return _INVISIBLE.sub("", text)


@register_processor("text.replace", "查找替换", "text", "支持正则", args={"find": "", "replace": "", "regex": False})
def _p_replace(text, args):
    find = str(args.get("find", ""))
    rep = str(args.get("replace", ""))
    if not find:
        return text
    if args.get("regex"):
        try:
            return re.sub(find, rep, text)
        except re.error as e:
            raise ValueError("正则非法：%s" % e)
    return text.replace(find, rep)


@register_processor("text.join_lines", "合并为一行", "text", "用分隔符连接所有行", args={"sep": " "})
def _p_join(text, args):
    return str(args.get("sep", " ")).join(l.strip() for l in _split_lines(text) if l.strip())


@register_processor("text.add_line_numbers", "添加行号", "text", "按行编号", args={"start": 1, "sep": ". ", "pad": 3})
def _p_lineno(text, args):
    start = _as_int(args.get("start", 1), 1)
    sep = str(args.get("sep", ". "))
    pad = max(0, _as_int(args.get("pad", 3), 3))
    out = []
    for i, line in enumerate(_split_lines(text)):
        out.append("%s%s%s" % (str(start + i).rjust(pad), sep, line))
    return "\n".join(out)


@register_processor("table.remove_empty_cols", "删除空列", "table", "整列为空的列删掉")
def _p_rm_empty_cols(text, args):
    rows = parse_table(text)
    if not rows:
        return text
    width = max(len(r) for r in rows)
    grid = [list(r) + [""] * (width - len(r)) for r in rows]
    keep = [j for j in range(width) if any((grid[i][j] or "").strip() for i in range(len(grid)))]
    if not keep:
        return ""
    return render_table([[grid[i][j] for j in keep] for i in range(len(grid))], detect_delimiter(text))


# ====================================================================== table
@register_processor("table.transpose", "表格转置", "table", "行列互换，自动补齐不等长")
def _p_transpose(text, args):
    rows = parse_table(text, args.get("delimiter") or None)
    if not rows:
        return text
    width = max(len(r) for r in rows)
    grid = [list(r) + [""] * (width - len(r)) for r in rows]
    return render_table([[grid[i][j] for i in range(len(grid))] for j in range(width)],
                        args.get("delimiter") or detect_delimiter(text))


@register_processor("table.to_csv", "转 CSV", "table", "逗号分隔", args={"delimiter": ","})
def _p_to_csv(text, args):
    rows = parse_table(text)
    return render_table(rows, str(args.get("delimiter", ",")), quoting=True) if rows else text


@register_processor("table.to_tsv", "转 TSV", "table", "制表符分隔")
def _p_to_tsv(text, args):
    rows = parse_table(text)
    return render_table(rows, "\t") if rows else text


@register_processor("table.to_markdown", "转 Markdown 表格", "table", "首行作表头")
def _p_to_md(text, args):
    rows = parse_table(text)
    if not rows:
        return text
    def esc(cell):
        return str(cell).replace("|", "\\|").replace("\n", " ")
    head = rows[0]
    out = ["| " + " | ".join(esc(c) for c in head) + " |",
           "|" + "|".join([" --- "] * len(head)) + "|"]
    for r in rows[1:]:
        r = list(r) + [""] * (len(head) - len(r))
        out.append("| " + " | ".join(esc(c) for c in r[:len(head)]) + " |")
    return "\n".join(out)


@register_processor("table.from_markdown", "Markdown 转表格", "table", "还原为 TSV")
def _p_from_md(text, args):
    rows = parse_table(text, "|")
    return render_table(rows, args.get("delimiter") or "\t") if rows else text


@register_processor("table.sort_by_column", "按列排序", "table", "args: col 从 0 起, desc 倒序", args={"col": 0, "desc": False})
def _p_sort_col(text, args):
    rows = parse_table(text)
    if len(rows) < 2:
        return text
    head, body = rows[0], rows[1:]
    col = _as_int(args.get("col", 0), 0)
    if col >= (len(head) if head else 0):
        return text

    def key(r):
        return (r[col] if col < len(r) else "")
    try:
        body.sort(key=lambda r: float(key(r)), reverse=bool(args.get("desc")))
    except ValueError:
        body.sort(key=key, reverse=bool(args.get("desc")))
    return render_table([head] + body, detect_delimiter(text))


@register_processor("table.unique_rows", "整行去重", "table", "保留首次出现")
def _p_unique_rows(text, args):
    rows = parse_table(text)
    if not rows:
        return text
    seen, out = set(), []
    for r in rows:
        k = tuple(x.strip() for x in r)
        if k in seen:
            continue
        seen.add(k)
        out.append(r)
    return render_table(out, detect_delimiter(text))


@register_processor("table.select_columns", "选取列", "table", "args: cols 形如 0,2,3", args={"cols": ""})
def _p_select_cols(text, args):
    rows = parse_table(text)
    if not rows:
        return text
    cols = [int(x) for x in str(args.get("cols", "")).replace("，", ",").split(",") if x.strip().lstrip("-").isdigit()]
    cols = [c for c in cols if 0 <= c < max(len(r) for r in rows)]
    if not cols:
        return text
    return render_table([[r[c] if c < len(r) else "" for c in cols] for r in rows],
                        detect_delimiter(text))


@register_processor("table.split_by_column", "按列拆分", "table", "把某一列拆成多列（按分隔符）", args={"col": 0, "sep": ","})
def _p_split_col(text, args):
    rows = parse_table(text)
    if not rows:
        return text
    col = _as_int(args.get("col", 0), 0)
    sep = str(args.get("sep", ","))
    out = []
    for r in rows:
        if col < len(r):
            head, tail = r[:col], r[col + 1:]
            out.append(head + [p.strip() for p in r[col].split(sep)] + tail)
        else:
            out.append(r)
    return render_table(out, detect_delimiter(text))


@register_processor("table.merge_columns", "合并列", "table", "把若干列合并成一列", args={"cols": "0,1", "sep": "-"})
def _p_merge_cols(text, args):
    rows = parse_table(text)
    if not rows:
        return text
    cols = sorted(int(x) for x in str(args.get("cols", "0,1")).split(",") if x.strip().isdigit())
    sep = str(args.get("sep", "-"))
    out = []
    for r in rows:
        merged = sep.join((r[c] if c < len(r) else "") for c in cols)
        rest = [c for i, c in enumerate(r) if i not in cols]
        out.append([merged] + rest)
    return render_table(out, detect_delimiter(text))


# ====================================================================== number
@register_processor("number.thousands_sep", "加千分位", "number",
                    "整格纯数字才格式化；普通文本行格式化其中所有数字串", args={"decimals": None})
def _p_thousands(text, args):
    """Excel 复制出来是 TSV，按整格处理才安全——否则 "2026-10-19" 这类
    单元格会被拆成 2,026-10-19。纯文本行没有单元格边界，才做行内替换。"""
    dec = args.get("decimals")

    def _fmt_token(token: str) -> str:
        neg = token.startswith("-")
        s2 = token.lstrip("-")
        if "." in s2:
            ip, fp = s2.split(".", 1)
            if dec is not None:
                fp = ("%." + str(_as_int(dec, 0)) + "f") % float("0." + fp)
        else:
            ip, fp = s2, None
        ip = "{:,}".format(int(ip))          # 纯整数分支也必须赋 ip，否则 UnboundLocalError
        res = ip + ("." + fp if fp is not None else "")
        return ("-" + res) if neg else res

    def fmt_cell(cell: str) -> str:
        s = cell.strip()
        if not re.fullmatch(r"-?\d+(\.\d+)?", s):
            return cell
        return _fmt_token(s)

    def fmt_inline(line: str) -> str:
        return re.sub(r"-?\d+(?:\.\d+)?", lambda m: _fmt_token(m.group(0)), line)

    return "\n".join("\t".join(fmt_cell(c) for c in l.split("\t")) if "\t" in l else fmt_inline(l)
                     for l in _split_lines(text))


@register_processor("number.fixed_decimals", "统一小数位", "number", "args: decimals 位数, mode round/floor/ceil",
                    args={"decimals": 2, "mode": "round"})
def _p_decimals(text, args):
    d = _as_int(args.get("decimals", 2), 2)
    mode = str(args.get("mode", "round"))

    def fmt(cell: str) -> str:
        s = cell.strip()
        if not re.fullmatch(r"-?\d+(\.\d+)?", s):
            return cell
        return ("%." + str(d) + "f") % _round(float(s), d, mode)

    return "\n".join("\t".join(fmt(c) for c in l.split("\t")) if "\t" in l else fmt(l)
                     for l in _split_lines(text))


@register_processor("number.strip_sep", "去千分位", "number", "1234.5 -> 1234.5")
def _p_strip_sep(text, args):
    return re.sub(r"(?<=\d),(?=\d{3}(\D|$))", "", text)


@register_processor("number.sum", "求和", "number", "把所有数字加起来")
def _p_sum(text, args):
    total, count = 0.0, 0
    for line in _split_lines(text):
        for cell in re.split(r"[\t,;\s]+", line):
            if re.fullmatch(r"-?\d+(\.\d+)?", cell.strip()):
                total += float(cell)
                count += 1
    if not count:
        return text
    return str(int(total)) if float(total).is_integer() else str(round(total, 10))


# ====================================================================== case
@register_processor("case.upper", "转大写", "case")
def _p_upper(text, args):
    return text.upper()


@register_processor("case.lower", "转小写", "case")
def _p_lower(text, args):
    return text.lower()


@register_processor("case.title", "首字母大写", "case", "按词首字母")
def _p_title(text, args):
    return re.sub(r"[A-Za-z]+", lambda m: m.group(0).capitalize(), text)


@register_processor("case.camel", "驼峰", "case", "foo_bar_baz -> fooBarBaz")
def _p_camel(text, args):
    parts = re.split(r"[^A-Za-z0-9]+", text.strip())
    parts = [p for p in parts if p]
    if not parts:
        return text
    return parts[0].lower() + "".join(p.capitalize() for p in parts[1:])


@register_processor("case.snake", "下划线", "case", "fooBarBaz -> foo_bar_baz")
def _p_snake(text, args):
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", text.strip())
    return re.sub(r"[^A-Za-z0-9]+", "_", s).strip("_").lower()


@register_processor("case.kebab", "短横线", "case", "fooBarBaz -> foo-bar-baz")
def _p_kebab(text, args):
    return re.sub(r"[^A-Za-z0-9]+", "-", re.sub(r"([a-z0-9])([A-Z])", r"\1-\2", text.strip())).strip("-").lower()


@register_processor("case.reverse_words", "反转词序", "case")
def _p_rev_words(text, args):
    return " ".join(reversed(text.split()))


# ====================================================================== date
_DATE_FORMATS = ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d", "%Y%m%d", "%d/%m/%Y", "%m/%d/%Y",
                 "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y年%m月%d日")


@register_processor("date.now", "当前时间", "date", "args: fmt 形如 %Y-%m-%d", args={"fmt": "%Y-%m-%d %H:%M:%S"})
def _p_now(text, args):
    return datetime.now().strftime(str(args.get("fmt", "%Y-%m-%d %H:%M:%S")))


@register_processor("date.format", "日期格式化", "date", "识别常见日期格式后重排", args={"fmt": "%Y-%m-%d"})
def _p_date_fmt(text, args):
    fmt = str(args.get("fmt", "%Y-%m-%d"))
    src = args.get("source_fmt")
    def conv(m):
        raw = m.group(0)
        for f in ([src] if src else []) + list(_DATE_FORMATS):
            try:
                return datetime.strptime(raw, f).strftime(fmt)
            except (ValueError, TypeError):
                continue
        return raw
    out = re.sub(r"\d{4}[-/.]\d{1,2}[-/.]\d{1,2}(?:[ T]\d{1,2}:\d{2}(?::\d{2})?)?", conv, text)
    return out


@register_processor("date.weekday", "星期几", "date", "追加中文星期")
def _p_weekday(text, args):
    names = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
    return datetime.now().strftime(names[datetime.now().weekday()])


# ====================================================================== data
@register_processor("data.dedupe", "按行去重", "data", "args: ignore_case 忽略大小写", args={"ignore_case": True})
def _p_dedupe(text, args):
    ic = bool(args.get("ignore_case", True))
    seen, out = set(), []
    for line in _split_lines(text):
        k = line.strip().lower() if ic else line
        if k in seen:
            continue
        seen.add(k)
        out.append(line)
    return "\n".join(out)


@register_processor("data.dedupe_by_column", "按列去重", "data", "args: col 从 0 起", args={"col": 0})
def _p_dedupe_col(text, args):
    rows = parse_table(text)
    if not rows:
        return text
    col = _as_int(args.get("col", 0), 0)
    seen, out = set(), []
    for r in rows:
        k = (r[col].strip().lower() if col < len(r) else "")
        if k in seen:
            continue
        seen.add(k)
        out.append(r)
    return render_table(out, detect_delimiter(text))


@register_processor("data.sort_lines", "行排序", "data", "args: desc 倒序, numeric 数字序", args={"desc": False, "numeric": False})
def _p_sort_lines(text, args):
    lines = [l for l in _split_lines(text) if l.strip()]
    if args.get("numeric"):
        def k(l):
            try:
                return (0, float(l.strip()))
            except ValueError:
                return (1, 0.0)
    else:
        def k(l):
            return l.strip()
    return "\n".join(sorted(lines, key=k, reverse=bool(args.get("desc"))))


@register_processor("data.add_numbers", "编号填充", "data", "args: start 起始, step 步长", args={"start": 1, "step": 1})
def _p_add_numbers(text, args):
    start = _as_int(args.get("start", 1), 1)
    step = _as_int(args.get("step", 1), 1)
    lines = _split_lines(text)
    return "\n".join(("%d%s%s" % (start + i * step, "\t", l)) if l.strip() else l
                     for i, l in enumerate(lines))


@register_processor("data.padding", "对齐填充", "data", "args: width 宽度, char 填充, mode l/r", args={"width": 20, "char": " ", "mode": "r"})
def _p_padding(text, args):
    w = _as_int(args.get("width", 20), 20)
    ch = str(args.get("char", " "))[:1] or " "
    mode = str(args.get("mode", "r"))
    return "\n".join(l.ljust(w, ch) if mode == "r" else l.rjust(w, ch)
                     for l in _split_lines(text))


@register_processor("data.normalize_width", "规范化全半角", "data", "全角转半角")
def _p_norm_width(text, args):
    out = []
    for ch in text:
        code = ord(ch)
        if code == 0x3000:
            out.append(" ")
        elif 0xFF01 <= code <= 0xFF5E:
            out.append(chr(code - 0xFEE0))
        else:
            out.append(ch)
    return "".join(out)


# ====================================================================== 条件表达式（事件链用）
def eval_condition(expr: str, text: str, extra: Optional[Dict[str, object]] = None) -> bool:
    """事件链条件求值。刻意只支持可枚举的少数几个，不引入 eval/表达式引擎。

    支持：always / never / has_table / is_empty / is_number /
          contains:<子串> / ncontains:<子串> / regex:<表达式> / len_gt:<n>
    """
    e = (expr or "").strip()
    low = e.lower()
    t = text or ""
    if low in ("always", "true", "1"):
        return True
    if low in ("never", "false", "0"):
        return False
    if low == "has_table":
        return looks_like_table(t)
    if low == "is_empty":
        return not t.strip()
    if low == "is_number":
        return bool(re.fullmatch(r"\s*-?\d+(\.\d+)?\s*", t))
    if low.startswith("contains:"):
        return e.split(":", 1)[1].lower() in t.lower()
    if low.startswith("ncontains:"):
        return e.split(":", 1)[1].lower() not in t.lower()
    if low.startswith("regex:"):
        try:
            return bool(re.search(e.split(":", 1)[1], t))
        except re.error as ex:
            raise ValueError("条件正则非法：%s" % ex)
    if low.startswith("len_gt:"):
        try:
            return len(t) > int(e.split(":", 1)[1])
        except ValueError:
            return False
    if low.startswith("var:"):
        return bool((extra or {}).get(e.split(":", 1)[1]))
    raise ValueError("未知条件表达式：%s" % expr)


CONDITION_HELP = [
    "always", "never", "has_table", "is_empty", "is_number",
    "contains:关键词", "ncontains:关键词", "regex:表达式", "len_gt:数字", "var:变量名",
]
