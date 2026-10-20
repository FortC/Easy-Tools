"""Win32 平台层（纯 ctypes，零第三方依赖）。

分层动机：方案选型表写的是 pywin32，但本机 Python 是 3.14，
pywin32 / onnxruntime 大概率没有 cp314 wheel。改用 stdlib ctypes 自封装后：
- 装包阶段零硬编译风险；
- PyInstaller 打包不需要额外 hidden-import 排查；
- 代价是底层样板代码变多，这部分被隔离在本包内，core 不感知。
"""
