r"""Easy-Tools: Windows 桌面效率工具（中键动作面板 / 事件链 / Office 框选处理）。

分层约定（见 docs/设计方案.md 第 3 节）：
- core/      纯 Python 业务与系统层，**不依赖 Qt**，可在无 GUI 环境单测；
- ui/        PySide6 表现层，只做渲染与交互，不承载业务规则；
- config/    配置结构定义与默认配置；
- data/      运行时用户配置（开发态相对项目根，打包态落 %APPDATA%\Easy-Tools）。
"""

__version__ = "0.4.0"
APP_NAME = "Easy-Tools"
