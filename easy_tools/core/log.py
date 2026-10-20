"""统一日志。

设计取舍：只写文件 + 控制台，不引入 logging.handlers 的复杂 handler，
因为 Easy-Tools 是单用户常驻托盘程序，日志主要用于事后排障，
滚动策略用 RotatingFileHandler 即可满足。

**踩过的坑（别改回去）**：`get_logger()` 会在模块 import 时自动调用
`configure()`，此时 `log_file` 还是 None。RotatingFileHandler(None) 抛异常
被 except 吞掉，于是文件 handler 永远没装上；等到 main() 里再调 configure()
时 `_configured` 已经是 True，直接 return——结果就是**打包后日志文件一个字节
都不会写**，而且程序看起来完全正常。

所以：configure() 必须自己把 None 解析成 paths.log_path()，
幂等判断也必须看"文件 handler 装没装"，而不是一个笼统的布尔。
"""

from __future__ import annotations

import logging
import logging.handlers
import threading

_lock = threading.Lock()
_stream_added = False
_file_added = False

FMT = "%(asctime)s %(levelname)-7s [%(name)s] %(message)s"


def has_file_handler() -> bool:
    """文件日志是否真的装上了。UI 的"关于"与自检会读它。"""
    return _file_added


def configure(level: int = logging.INFO, log_file=None) -> None:
    """幂等初始化根 logger。

    log_file 传 None 时解析成默认的 data_dir()/easy_tools.log——
    必须在这里解析，不能留给调用方：get_logger() 会在 import 阶段先调一次
    configure(None)，那时调用方还没有任何机会指定路径。
    """
    global _stream_added, _file_added
    with _lock:
        if log_file is None:
            try:
                from .paths import log_path
                log_file = log_path()
            except Exception:
                log_file = None

        root = logging.getLogger("easy_tools")
        root.setLevel(level)
        root.propagate = False
        fmt = logging.Formatter(FMT, datefmt="%H:%M:%S")

        if log_file is not None and not _file_added:
            try:
                fh = logging.handlers.RotatingFileHandler(
                    log_file, maxBytes=1_000_000, backupCount=3, encoding="utf-8"
                )
                fh.setFormatter(fmt)
                root.addHandler(fh)
                _file_added = True
            except Exception as e:
                # 只读目录/权限问题不该让程序起不来，但必须留痕：
                # 静默吞掉的后果是"程序看着正常却一个日志都没有"，排障时毫无线索。
                print("[Easy-Tools] 日志文件初始化失败：%r" % (e,), flush=True)

        if not _stream_added:
            sh = logging.StreamHandler()
            sh.setFormatter(fmt)
            root.addHandler(sh)
            _stream_added = True


def get_logger(name: str) -> logging.Logger:
    # 这里必须调 configure()：本函数在模块 import 阶段就会被调用（log = get_logger(...)），
    # 早于 main()。configure() 自身幂等，重复调用无副作用。
    if not (_file_added or _stream_added):
        configure()
    if name.startswith("easy_tools"):
        return logging.getLogger(name)
    return logging.getLogger("easy_tools." + name)
