"""Local Word/Excel-friendly transformations; never touch a document directly."""
import re
import unicodedata
from decimal import Decimal, InvalidOperation

from .processors import register_processor, parse_table, render_table


def write_table(rows):
    return render_table(rows, quoting=True)


@register_processor('word.clean_paragraphs', '整理粘贴段落', 'word',
                    '清除零宽字符、行尾空格和多余空行，保留段落顺序')
def clean_paragraphs(text, args):
    text = re.sub(r'[\u200b-\u200f\ufeff]', '', text.replace('\r\n', '\n').replace('\r', '\n'))
    return re.sub(r'\n{3,}', '\n\n', '\n'.join(line.strip() for line in text.split('\n'))).strip()


@register_processor('word.join_wrapped', '合并段内断行', 'word',
                    '空行仍作为段落边界，中文行间不加空格', args={'separator': ' '})
def join_wrapped(text, args):
    paragraphs = re.split(r'\n\s*\n', text.replace('\r\n', '\n').replace('\r', '\n').strip())
    result = []
    for paragraph in paragraphs:
        lines = [line.strip() for line in paragraph.split('\n') if line.strip()]
        combined = ''
        for line in lines:
            sep = '' if combined and re.search(r'[\u4e00-\u9fff]$', combined) and re.match(r'[\u4e00-\u9fff]', line) else args['separator']
            combined += (sep if combined else '') + line
        result.append(combined)
    return '\n\n'.join(result)


@register_processor('word.remove_list_marks', '去除列表编号', 'word',
                    '删除行首数字、中文编号和项目符号，保留正文')
def remove_marks(text, args):
    return re.sub(r'(?m)^\s*(?:[-*•●]\s+|\d+[.、）)]\s*|[（(]\d+[）)]\s*|[一二三四五六七八九十]+、\s*)', '', text)


@register_processor('word.extract_emails', '提取邮箱并去重', 'word')
def extract_emails(text, args):
    return '\n'.join(dict.fromkeys(re.findall(r'[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}', text)))


@register_processor('table.trim_cells', '清理每个单元格空白', 'table', '保持文本及前导零')
def trim_cells(text, args):
    return write_table([[cell.strip() for cell in row] for row in parse_table(text)])


@register_processor('table.fill_down', '向下填充空单元格', 'table',
                    '仅填充指定列；不计算公式', args={'col': 0, 'header': True})
def fill_down(text, args):
    rows = parse_table(text)
    col = int(args['col'])
    if col < 0:
        raise ValueError('列序号不能小于 1')
    last = ''
    for row in rows[1:] if args['header'] else rows:
        row.extend([''] * max(0, col + 1 - len(row)))
        if row[col].strip():
            last = row[col]
        else:
            row[col] = last
    return write_table(rows)


@register_processor('table.filter_rows', '按列筛选行', 'table',
                    '按指定列保留或排除包含关键词的行',
                    args={'col': 0, 'keyword': '', 'exclude': False, 'header': True})
def filter_rows(text, args):
    rows = parse_table(text)
    start = 1 if args['header'] and rows else 0
    col = int(args['col'])
    if col < 0:
        raise ValueError('列序号不能小于 1')
    kept = rows[:start]
    for row in rows[start:]:
        hit = str(args['keyword']) in (row[col] if col < len(row) else '')
        if hit != bool(args['exclude']):
            kept.append(row)
    return write_table(kept)


@register_processor('table.column_summary', '统计指定列数值', 'table',
                    '非空非数字内容会报告，不静默当作零', args={'col': 0, 'header': True})
def column_summary(text, args):
    rows = parse_table(text)
    col = int(args['col'])
    if col < 0:
        raise ValueError('列序号不能小于 1')
    values = []
    for index, row in enumerate(rows[1:] if args['header'] else rows, 2 if args['header'] else 1):
        raw = row[col].strip() if col < len(row) else ''
        if not raw:
            continue
        try:
            value = Decimal(unicodedata.normalize('NFKC', raw).replace(',', ''))
            if not value.is_finite():
                raise InvalidOperation
            values.append(value)
        except InvalidOperation:
            raise ValueError(f'第 {index} 行不是有效数字，请检查输入') from None
    if not values:
        raise ValueError('该列没有数值')
    return write_table([['项目', '结果'], ['数量', str(len(values))], ['合计', str(sum(values))],
                        ['平均', str(sum(values) / len(values))], ['最小', str(min(values))], ['最大', str(max(values))]])
