"""路径解析测试。

重点不是"路径长什么样"，而是**打包态能不能找到只读资源**：
开发态下 resource_root / package_dir / app_root 三者重合，
这类 bug 只有换成 _MEIPASS 才能暴露，所以这里显式模拟打包态来测。
"""

from __future__ import annotations

import json
import os
import sys
import unittest
from unittest import mock

from easy_tools.core import paths


class TestPaths(unittest.TestCase):
    """模拟打包态必须动 sys 全局属性，所以这里严格保存/还原。

    特别注意：sys.executable 是任何解释器都有的标准属性，**不能 del**——
    之前那版 tearDown 直接删掉它，导致后面所有用例拿不到解释器路径。
    """

    def setUp(self):
        self._saved = {k: getattr(sys, k) for k in ("frozen", "_MEIPASS", "executable")
                       if hasattr(sys, k)}

    def tearDown(self):
        for k in ("frozen", "_MEIPASS", "executable"):
            if hasattr(sys, k):
                delattr(sys, k)
        for k, v in self._saved.items():
            setattr(sys, k, v)

    def _freeze(self, meipass, exe_dir):
        sys.frozen = True
        sys._MEIPASS = str(meipass)
        sys.executable = str(exe_dir / "Easy-Tools.exe")

    def test_defaults_path_exists_in_dev_mode(self):
        p = paths.defaults_path()
        self.assertTrue(p.is_file(), "默认配置不存在：%s" % p)
        json.loads(p.read_text(encoding="utf-8"))

    def test_defaults_path_resolves_under_meipass_when_frozen(self):
        """打包态必须落到 _MEIPASS/easy_tools/config/defaults.json。

        这正是 spec 里 --add-data 的目标路径。写错会让打包版首次启动直接崩，
        而开发态测试永远发现不了——所以必须显式模拟 frozen。
        """
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            root = paths.Path(td)
            meipass = root / "_internal"
            (meipass / "easy_tools" / "config").mkdir(parents=True)
            shutil_src = paths.defaults_path()
            target = meipass / "easy_tools" / "config" / "defaults.json"
            target.write_text(shutil_src.read_text(encoding="utf-8"),
                              encoding="utf-8")
            self._freeze(meipass, root)
            p = paths.defaults_path()
            self.assertEqual(p, target)
            self.assertTrue(p.is_file(),
                            "打包态默认配置找不到：%s（spec 的 --add-data 目标要"
                            "与 package_dir() 的推导一致）" % p)

    def test_frozen_data_dir_goes_to_appdata(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            root = paths.Path(td)
            self._freeze(root / "_internal", root)
            with mock.patch.dict(os.environ, {"APPDATA": str(root / "Roaming")}):
                d = paths.data_dir()
            self.assertEqual(d, root / "Roaming" / "Easy-Tools")
            self.assertTrue(d.is_dir())

    def test_icon_resource_resolves_under_meipass(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            root = paths.Path(td)
            meipass = root / "_internal"
            meipass.mkdir(parents=True)
            (meipass / "app.ico").write_bytes(b"x")
            self._freeze(meipass, root)
            self.assertTrue((paths.resource_root() / "app.ico").is_file())


if __name__ == "__main__":
    unittest.main()
