"""Small typed parameter forms shared by tools and workflow steps."""
from PySide6.QtWidgets import QWidget, QFormLayout, QCheckBox, QSpinBox, QDoubleSpinBox, QLineEdit
from PySide6.QtCore import Signal

LABELS = {'col': '第几列', 'cols': '列序号（逗号分隔，内部从 0 开始）',
          'header': '第一行是表头', 'keyword': '关键词', 'exclude': '排除匹配行',
          'find': '查找内容', 'replace': '替换为', 'regex': '使用正则表达式',
          'sep': '分隔符', 'separator': '断行连接符', 'desc': '倒序排列',
          'ignore_case': '忽略大小写', 'start': '起始编号', 'step': '编号间隔',
          'decimals': '小数位数', 'mode': '模式', 'fmt': '日期格式', 'width': '宽度',
          'char': '填充字符', 'delimiter': '分隔符', 'pad': '编号宽度', 'numeric': '按数值排序'}


class ParameterForm(QWidget):
    changed = Signal()
    def __init__(self, defaults=None, values=None, parent=None):
        super().__init__(parent)
        self.fields = {}
        self.extra = {k: v for k, v in (values or {}).items() if k not in (defaults or {})}
        form = QFormLayout(self)
        form.setContentsMargins(0, 0, 0, 0)
        for key, default in (defaults or {}).items():
            value = (values or {}).get(key, default)
            if isinstance(default, bool):
                widget = QCheckBox('启用')
                widget.setChecked(bool(value))
                read = widget.isChecked
            elif isinstance(default, int):
                widget = QSpinBox()
                widget.setRange(1 if key == 'col' else -100000, 100000)
                widget.setValue(int(value) + (1 if key == 'col' else 0))
                read = lambda w=widget, k=key: w.value() - (1 if k == 'col' else 0)
            elif isinstance(default, float):
                widget = QDoubleSpinBox()
                widget.setRange(-100000, 100000)
                widget.setValue(float(value))
                read = widget.value
            else:
                widget = QLineEdit(str(value))
                read = widget.text
            form.addRow(LABELS.get(key, key), widget)
            signal = widget.toggled if isinstance(widget, QCheckBox) else widget.valueChanged if isinstance(widget, (QSpinBox, QDoubleSpinBox)) else widget.textChanged
            signal.connect(lambda *_: self.changed.emit())
            self.fields[key] = read

    def collect(self):
        return dict(self.extra, **{key: read() for key, read in self.fields.items()})
