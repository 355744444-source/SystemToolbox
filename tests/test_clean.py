# -*- coding: utf-8 -*-
"""清理 / 磁盘 / 注册表 / 残留 模块的回归测试（stdlib unittest）。

安全约定：所有删除测试都在临时目录里自建的文件上做，
不触碰任何真实用户数据。
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app.core import cleaner, disk, registry_clean as reg, residue, space  # noqa: E402


def _touch(path: str, size: int = 0, age_hours: float = 0.0) -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(b"\0" * size)
    if age_hours:
        old = os.path.getmtime(path) - age_hours * 3600
        os.utime(path, (old, old))
    return path


class TestRulesSafety(unittest.TestCase):
    """规则表是清理功能的最后一道闸：路径必须落在受控根目录里。"""

    def _allowed_roots(self):
        roots = []
        for p in ("%SystemRoot%", "%LOCALAPPDATA%", "%APPDATA%", "%ProgramData%"):
            real = os.path.expandvars(p)
            if "%" not in real:
                roots.append(os.path.normcase(os.path.normpath(real)).rstrip("\\"))
        return roots

    def test_all_paths_under_controlled_roots(self):
        allowed = self._allowed_roots()
        self.assertTrue(allowed, "环境变量没展开，测试无效")
        for rule in cleaner.RULES:
            for raw in rule.paths + rule.exact_files:
                expanded = os.path.expandvars(raw)
                self.assertNotIn("%", expanded, f"{rule.key} 的变量未展开: {raw}")
                self.assertTrue(os.path.isabs(expanded), f"{rule.key} 用了相对路径: {raw}")
                low = os.path.normcase(os.path.normpath(expanded))
                self.assertTrue(
                    any(low.startswith(a + os.sep) or low == a for a in allowed),
                    f"{rule.key} 指向了受控根之外: {expanded}")

    def test_no_personal_folders(self):
        """绝不允许把桌面/文档/下载/图片这类个人目录写进清理规则。"""
        banned = ("\\desktop", "\\documents", "\\downloads", "\\pictures",
                  "\\videos", "\\music", "\\onedrive", "\\favorites")
        for rule in cleaner.RULES:
            for raw in rule.paths + rule.exact_files:
                low = os.path.expandvars(raw).lower()
                for b in banned:
                    self.assertNotIn(b, low, f"{rule.key} 命中了个人目录关键词 {b}")

    def test_keys_unique_and_defaults_sane(self):
        keys = [r.key for r in cleaner.RULES]
        self.assertEqual(len(keys), len(set(keys)))
        # 回收站清空这类破坏性操作不允许默认勾选
        for r in cleaner.RULES:
            if r.risk == "中":
                self.assertFalse(r.default_on, f"{r.key} 风险为中却默认勾选")


class TestScanAndDelete(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="cls_test_")
        self.root = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def _rule(self, **kw):
        base = dict(key="t", name="测试", desc="", paths=(self.root,))
        base.update(kw)
        return cleaner.CleanRule(**base)

    def test_scan_counts_and_filter(self):
        _touch(os.path.join(self.root, "a.tmp"), 100)
        _touch(os.path.join(self.root, "sub", "b.tmp"), 200)
        _touch(os.path.join(self.root, "c.keep"), 50)
        res = cleaner.scan_rule(self._rule(patterns=("*.tmp",)))
        self.assertEqual(res.count, 2)
        self.assertEqual(res.total_size, 300)
        self.assertEqual(len(res.files), 2)

    def test_min_age_skips_fresh_files(self):
        _touch(os.path.join(self.root, "old.tmp"), 10, age_hours=3)
        _touch(os.path.join(self.root, "new.tmp"), 10)
        res = cleaner.scan_rule(self._rule(patterns=("*.tmp",), min_age_hours=1))
        self.assertEqual(res.count, 1)
        self.assertEqual(os.path.basename(res.files[0][0]), "old.tmp")

    def test_collect_matches_scan(self):
        for i in range(5):
            _touch(os.path.join(self.root, f"f{i}.tmp"), 10 * (i + 1))
        rule = self._rule(patterns=("*.tmp",))
        res = cleaner.scan_rule(rule)
        files = cleaner.collect_files(rule)
        self.assertEqual(len(files), res.count)
        self.assertEqual(sum(s for _p, s in files), res.total_size)

    def test_permanent_delete(self):
        paths = [_touch(os.path.join(self.root, f"d{i}.tmp"), 10) for i in range(3)]
        rep = cleaner.delete_files([(p, 10) for p in paths], use_recycle=False)
        self.assertEqual(rep.deleted, 3)
        self.assertEqual(rep.freed, 30)
        for p in paths:
            self.assertFalse(os.path.exists(p))

    def test_recycle_bin_delete(self):
        path = _touch(os.path.join(self.root, "recycle_me.tmp"), 12)
        rep = cleaner.delete_files([(path, 12)], use_recycle=True)
        if not rep.deleted and os.path.exists(path):
            self.skipTest("当前环境不允许调用 Shell 回收站接口")
        self.assertFalse(os.path.exists(path))
        self.assertEqual(rep.freed, 12)

    def test_empty_dir_pruned(self):
        sub = os.path.join(self.root, "junk", "deep")
        _touch(os.path.join(sub, "x.tmp"), 4)
        rule = self._rule(patterns=("*.tmp",))
        res = cleaner.scan_rule(rule)
        cleaner.delete_files(cleaner.collect_files(rule), use_recycle=False)
        cleaner.prune_empty_dirs([res])
        self.assertFalse(os.path.exists(sub))

    def test_human_size(self):
        self.assertEqual(cleaner.human_size(0), "0 B")
        self.assertEqual(cleaner.human_size(999), "999 B")
        self.assertEqual(cleaner.human_size(1536), "1.5 KB")
        self.assertIn("MB", cleaner.human_size(5 * 1024 ** 2 + 512 * 1024))
        self.assertIn("GB", cleaner.human_size(30 * 1024 ** 3))


class TestRecycleBin(unittest.TestCase):
    def test_info(self):
        size, items = cleaner.recycle_bin_info()
        self.assertGreaterEqual(size, 0)
        self.assertGreaterEqual(items, 0)


class TestSpace(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="cls_space_")
        self.root = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def test_find_large_files(self):
        _touch(os.path.join(self.root, "small.bin"), 1024)
        big = _touch(os.path.join(self.root, "sub", "big.bin"), 3 * 1024 * 1024)
        found = space.find_large_files(self.root, 1 * 1024 * 1024)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0][0], big)
        self.assertGreaterEqual(found[0][1], 3 * 1024 * 1024)

    def test_analyze_dir_sizes(self):
        _touch(os.path.join(self.root, "a", "1.bin"), 1000)
        _touch(os.path.join(self.root, "a", "2.bin"), 2000)
        _touch(os.path.join(self.root, "b", "3.bin"), 500)
        _touch(os.path.join(self.root, "top.bin"), 100)
        node = space.analyze_dir(self.root)
        self.assertEqual(node.size, 3600)
        self.assertEqual(len(node.children), 2)
        self.assertEqual(node.children[0].name, "a")
        self.assertEqual(node.children[0].size, 3000)
        self.assertEqual(node.children[1].size, 500)

    def test_top_dirs_sorted(self):
        _touch(os.path.join(self.root, "x", "f.bin"), 5000)
        _touch(os.path.join(self.root, "y", "f.bin"), 1000)
        node = space.analyze_dir(self.root)
        top = space.top_dirs(node)
        self.assertEqual([t.name for t in top], ["x", "y"])


class TestDisk(unittest.TestCase):
    def test_volumes(self):
        vols = disk.list_volumes()
        self.assertTrue(vols, "至少应检测到一个本地卷")
        for v in vols:
            self.assertRegex(v.letter, r"^[A-Z]:$")
            self.assertGreaterEqual(v.total, 0)
            self.assertLessEqual(v.free, v.total)
            self.assertIn(v.is_ssd, (True, False, None))
            self.assertTrue(v.media_text)

    def test_build_action(self):
        v = disk.Volume(letter="C:", total=100, free=50, drive_type=3, is_ssd=True)
        opt = disk.build_action(v, "optimize")
        self.assertIn("/O", opt.cmd)
        trim = disk.build_action(v, "retrim")
        self.assertIsNotNone(trim)
        v2 = disk.Volume(letter="D:", total=100, free=50, drive_type=3, is_ssd=False)
        self.assertIsNone(disk.build_action(v2, "retrim"),
                          "机械盘不应提供 TRIM 动作")

    def test_parse_percent(self):
        self.assertEqual(disk.parse_defrag_percent("已完成 42% 的碎片整理"), 42)
        self.assertEqual(disk.parse_defrag_percent("Progress: 7%"), 7)
        self.assertIsNone(disk.parse_defrag_percent("没有百分比"))
        # 分析报告里的碎片率不能被当成进度
        self.assertIsNone(disk.parse_defrag_percent("总碎片空间      = 18%"))


class TestRegistry(unittest.TestCase):
    def test_expand_path(self):
        self.assertEqual(
            reg.expand_path('"C:\\Program Files\\Foo\\uninst.exe" /S'),
            "C:\\Program Files\\Foo\\uninst.exe")
        self.assertEqual(reg.expand_path("C:\\Tools\\app.exe --silent"),
                         "C:\\Tools\\app.exe")
        self.assertEqual(reg.expand_path("D:\\a b\\setup.cmd /q"),
                         "D:\\a b\\setup.cmd")
        expanded = reg.expand_path("%SystemRoot%\\system32\\cmd.exe")
        self.assertTrue(expanded.lower().endswith("cmd.exe"))
        self.assertNotIn("%", expanded)

    def test_scan_all_returns_list(self):
        issues = reg.scan_all()
        self.assertIsInstance(issues, list)
        for it in issues:
            self.assertIn(it.category, reg.CATEGORIES)
            self.assertIn(it.risk, ("低", "中"))
            self.assertTrue(it.full_path.startswith(("HKCU\\", "HKLM\\")))
            self.assertTrue(it.reason)

    def test_delete_missing_is_ok(self):
        fake = reg.RegIssue(hive="HKCU", subkey=r"Software\CrashLogSuite__nope__",
                            label="x", category="启动项失效", reason="",
                            delete_key=True)
        ok, _msg = reg.delete_issue(fake)
        self.assertTrue(ok, "删除不存在的键应当视为已达成")

    def test_export_empty(self):
        ok, msg = reg.export_backup([], os.path.join(tempfile.gettempdir(), "x.reg"))
        self.assertFalse(ok)


class TestResidue(unittest.TestCase):
    def test_similar(self):
        self.assertTrue(residue._similar("Adobe Photoshop 2021", "Photoshop"))
        self.assertTrue(residue._similar("WeChat", "wechat"))
        self.assertTrue(residue._similar("Microsoft Visual Studio 2022",
                                         "Visual Studio"))
        self.assertFalse(residue._similar("Ludashi", "WeChat"))

    def test_norm_strips_vendor_noise(self):
        self.assertEqual(residue._norm("Topaz Labs LLC"), "topaz labs")
        self.assertNotIn("inc", residue._norm("Foo Inc"))

    def test_system_dirs_blocked(self):
        for p in (r"C:\Windows\System32", r"C:\Program Files\WindowsApps\X",
                  r"C:\ProgramData\Templates", r"C:\Program Files\Microsoft SDKs"):
            if os.path.isdir(p):
                self.assertTrue(residue._blocked(p), f"未拦下系统目录 {p}")

    def test_installed_index_not_empty(self):
        idx = residue.installed_index()
        self.assertGreater(idx.count, 0, "应能读到已安装软件清单")
        self.assertTrue(idx.matches("Microsoft Visual Studio") or idx.count > 5)


if __name__ == "__main__":
    unittest.main(verbosity=2)
