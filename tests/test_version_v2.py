# -*- coding: utf-8 -*-
"""v2.0 版本资源：版本号唯一出处 + exe 版本资源读取。

版本号以前硬编码在 `core/report.py` 里，打包时 `version=None`，
于是 exe 属性（文件版本 / 公司名 / 版权）全空 —— 程序自己一点都不知道。
这里锁住两件事：版本号只有一份、读得回来。
"""
from __future__ import annotations

import os
import re
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app import version as ver  # noqa: E402
from app.core import report, verinfo  # noqa: E402


class VersionSourceTest(unittest.TestCase):

    def test_format(self):
        self.assertRegex(ver.VERSION, r"^\d+\.\d+\.\d+$")

    def test_tuple_is_four_ints(self):
        t = ver.version_tuple()
        self.assertEqual(len(t), 4)
        self.assertTrue(all(isinstance(x, int) for x in t))
        head = tuple(int(x) for x in ver.VERSION.split("."))
        self.assertEqual(t[:len(head)], head)

    def test_tuple_pads_short_versions(self):
        old = ver.VERSION
        try:
            ver.VERSION = "2.1"
            self.assertEqual(ver.version_tuple(), (2, 1, 0, 0))
            ver.VERSION = "2.0.0.1.9"
            self.assertEqual(ver.version_tuple(), (2, 0, 0, 1))
        finally:
            ver.VERSION = old

    def test_report_reexports_same_versions(self):
        """报告模块曾经自己定义 VERSION，两份会各说各话。"""
        self.assertEqual(report.VERSION, ver.VERSION)
        self.assertEqual(report.COMPANY, ver.COMPANY)

    def test_identity_strings(self):
        self.assertIn("振远", ver.COMPANY)
        self.assertTrue(ver.APP_NAME.isascii())
        self.assertTrue(ver.COPYRIGHT)
        self.assertGreaterEqual(ver.version_tuple(), (2, 0, 0, 0))

    def test_no_stale_version_literals(self):
        """除了 `app/version.py`，代码里不该再出现写死的 `x.y.z` 版本号。"""
        pattern = re.compile(r"""VERSION\s*=\s*["']\d+\.\d+\.\d+["']""")
        hits = []
        for base, _dirs, files in os.walk(os.path.join(ROOT, "app")):
            for fn in files:
                if not fn.endswith(".py"):
                    continue
                path = os.path.join(base, fn)
                if os.path.abspath(path) == os.path.abspath(ver.__file__):
                    continue
                with open(path, encoding="utf-8") as f:
                    if pattern.search(f.read()):
                        hits.append(os.path.relpath(path, ROOT))
        self.assertEqual(hits, [], "版本号只该在 app/version.py 里定义")


class VerInfoTest(unittest.TestCase):

    def setUp(self):
        if not verinfo.IS_WIN:
            self.skipTest("只在 Windows 上读版本资源")

    def test_missing_file_returns_empty(self):
        self.assertEqual(verinfo.read(os.path.join(ROOT, "不存在的.exe")), {})
        self.assertEqual(verinfo.describe(os.path.join(ROOT, "不存在的.exe")),
                         "无版本资源")

    def test_read_notepad(self):
        exe = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"),
                           "System32", "notepad.exe")
        if not os.path.isfile(exe):
            self.skipTest("本机没有 notepad.exe")
        info = verinfo.read(exe)
        self.assertIn("filevers", info)
        self.assertEqual(len(info["filevers"]), 4)
        self.assertTrue(info.get("CompanyName"))
        self.assertTrue(verinfo.describe(exe).startswith(
            info.get("ProductName", "?")))

    def test_read_self_python(self):
        """python.exe 也有版本资源，至少能读出四段数字。"""
        info = verinfo.read(sys.executable)
        self.assertIn("filevers", info)
        self.assertEqual(len(info["filevers"]), 4)


if __name__ == "__main__":
    unittest.main()
