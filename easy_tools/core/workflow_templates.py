"""Opt-in templates: never overwrite or inject entries into user configuration."""
import copy
import uuid


def _flow(name, ids, description='复制选区 → 本机处理 → 结果写入剪贴板'):
    return dict(name=name, description=description, trigger={'type': 'manual'},
                steps=[{'type': 'action', 'id': 'clipboard.copy'}] +
                [{'type': 'action', 'id': pid} for pid in ids] +
                [{'type': 'action', 'id': 'clipboard.set'}])


TEMPLATES = [
    _flow('Word · 整理粘贴段落', ['word.clean_paragraphs']),
    _flow('Word · 合并 PDF 段内断行', ['word.join_wrapped', 'word.clean_paragraphs']),
    _flow('Word · 去除列表编号', ['word.remove_list_marks']),
    _flow('Word · 提取邮箱名单', ['word.extract_emails']),
    _flow('Excel · 清理单元格空白', ['table.trim_cells']),
    _flow('Excel · 清理后整行去重', ['table.trim_cells', 'table.unique_rows']),
    _flow('Excel · 行列转置', ['table.transpose']),
    _flow('Excel · 向下填充第一列', ['table.fill_down']),
    _flow('Excel · 按列筛选', ['table.filter_rows']),
    _flow('Excel · 数值统计', ['table.column_summary']),
    _flow('Excel · 表格转 Markdown', ['table.to_markdown']),
    _flow('Excel · Markdown 转表格', ['table.from_markdown']),
    dict(name='Office · 快捷键宏', description='检查快捷键和目标软件后逐个授权；按键会改变文档',
         trigger={'type': 'manual'}, steps=[dict(type='action', id='office.macro',
             args=dict(sequence='ctrl+b', trusted=False))]),
    dict(name='脚本 · 自定义 Python 处理', description='独立进程，需授权；拥有当前账户权限',
         trigger={'type': 'manual'}, steps=[dict(type='python', input='{clipboard}',
             code='result = input_text.strip()', trusted=False, timeout_seconds=5)]),
]


def instantiate(index):
    value = copy.deepcopy(TEMPLATES[index])
    value['id'] = 'chain.' + uuid.uuid4().hex[:12]
    return value
