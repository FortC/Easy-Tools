"""日志模块测试。

存在理由很直接：曾出现"程序运行正常但日志文件一个字节都没有"的故障，
而当时 79 个测试全绿。原因是从没断言过**文件真的被创建**——
只看控制台输出永远发现不了这个问题。
"""

from __future__ import annotations

import logging
import os
import tempfile
import unittest

from easy_tools.core import log as logmod


class TestFileLogging(unittest.TestCase):
    def _reset(self):
        logmod._file_added = False
        logmod._stream_added = False
        root = logging.getLogger("easy_tools")
        for h in list(root.handlers):
            root.removeHandler(h)
            try:
                h.close()
            except Exception:
                pass

    def setUp(self):
        self._reset()

    def tearDown(self):
        self._reset()

    def test_configure_without_path_still_creates_file(self):
        """configure() 不传路径也必须自己解析默认日志文件并创建。

        历史故障回归：import 阶段 get_logger() 先调 configure(None)，
        RotatingFileHandler(None) 抛异常被吞，之后再也不会补装文件 handler。
        """
        logmod.configure()
        self.assertTrue(logmod.has_file_handler(),
                        "configure() 后没有文件 handler，日志会全部丢失")
        logmod.get_logger("t").info("hello-from-test")
        from easy_tools.core.paths import log_path
        p = log_path()
        self.assertTrue(p.is_file(), "日志文件未创建：%s" % p)
        self.assertIn("hello-from-test", p.read_text(encoding="utf-8"))

    def test_configure_with_explicit_path(self):
        with tempfile.TemporaryDirectory() as td:
            target = os.path.join(td, "x.log")
            logmod.configure(log_file=target)
            logmod.get_logger("t2").warning("explicit")
            self.assertTrue(os.path.isfile(target))
            with open(target, encoding="utf-8") as f:
                content = f.read()
            self.assertIn("explicit", content)
            # Windows 上 handler 持有文件句柄，不先关闭的话
            # TemporaryDirectory 退出时会 PermissionError。
            self._reset()

    def test_configure_is_idempotent(self):
        logmod.configure()
        n1 = len(logging.getLogger("easy_tools").handlers)
        logmod.configure()
        n2 = len(logging.getLogger("easy_tools").handlers)
        self.assertEqual(n1, n2, "重复 configure 不应叠加 handler")


if __name__ == "__main__":
    unittest.main()
