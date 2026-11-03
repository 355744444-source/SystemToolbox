# -*- coding: utf-8 -*-
"""v1.6.0 新增：进程级采样（procs）与组件健康（component）。

这两个模块都读真实系统状态，所以测试分两层：
- 纯逻辑（解析、排序、判定）用合成数据，结果确定
- 真实系统调用只断言"不抛异常 / 自洽 / 口径正确"，不断言具体数值
  （客户机器上的设备和补丁各不相同，写死数字必然误报）
"""
from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime, timedelta

from app.core import component, procs


class TestProcModel(unittest.TestCase):
    """Pro::uptime_text / started_text 的边界。"""

    def test_uptime_units(self):
        now = datetime.now()
        cases = [(30, "30 秒"), (180, "3 分"), (7200, "2.0 小时"),
                 (259200, "3.0 天")]
        for secs, want in cases:
            p = procs.Proc(pid=1, name="x", started=now - timedelta(seconds=secs))
            self.assertEqual(p.uptime_text, want, secs)

    def test_uptime_none(self):
        p = procs.Proc(pid=1, name="x")
        self.assertEqual(p.uptime_text, "—")
        self.assertEqual(p.started_text, "—")

    def test_started_text_format(self):
        p = procs.Proc(pid=1, name="x", started=datetime(2026, 3, 4, 5, 6))
        self.assertEqual(p.started_text, "03-04 05:06")


class TestProcSorting(unittest.TestCase):
    def _rep(self):
        rows = [
            procs.Proc(pid=1, name="a", cpu=1.0, private_mb=900, handles=10),
            procs.Proc(pid=2, name="b", cpu=9.0, private_mb=100, handles=50),
            procs.Proc(pid=3, name="c", cpu=5.0, private_mb=500, handles=30),
        ]
        return procs.ProcReport(rows=rows, warm=True, total=3, ncpu=8)

    def test_sort_cpu(self):
        names = [p.name for p in self._rep().sorted_by("cpu")]
        self.assertEqual(names, ["b", "c", "a"])

    def test_sort_mem_uses_private(self):
        names = [p.name for p in self._rep().sorted_by("mem")]
        self.assertEqual(names, ["a", "c", "b"])

    def test_sort_handles(self):
        names = [p.name for p in self._rep().sorted_by("handles")]
        self.assertEqual(names, ["b", "c", "a"])

    def test_limit(self):
        self.assertEqual(len(self._rep().sorted_by("cpu", limit=2)), 2)

    def test_empty(self):
        self.assertEqual(procs.ProcReport().sorted_by("cpu"), [])


class TestProcSampler(unittest.TestCase):
    def test_warm_flag_and_bounds(self):
        s = procs.ProcSampler()
        r1 = s.sample()
        self.assertFalse(r1.warm, "首次采样没有历史，warm 必须为 False")
        self.assertEqual(sum(p.cpu for p in r1.rows), 0.0, "首次 CPU 应全为 0")
        r2 = s.sample()
        self.assertTrue(r2.warm)
        self.assertGreater(r2.total, 5)
        # 全机口径：所有进程加起来不可能超过 100%（+ 5% 采样误差）
        self.assertLessEqual(sum(p.cpu for p in r2.rows), 105.0)
        self.assertGreaterEqual(sum(p.cpu for p in r2.rows), 0.0)

    def test_reset_clears_history(self):
        s = procs.ProcSampler()
        s.sample()
        s.reset()
        self.assertFalse(s.sample().warm)

    def test_memory_fields_sane(self):
        s = procs.ProcSampler()
        s.sample()
        rep = s.sample()
        for p in rep.rows:
            self.assertGreaterEqual(p.mem_mb, 0.0)
            self.assertGreaterEqual(p.private_mb, 0.0)
            self.assertGreaterEqual(p.threads, 0)
            self.assertGreaterEqual(p.handles, 0)
        # 至少自己这个进程能被读到（有名字、有线程数）
        me = [p for p in rep.rows if p.pid == os.getpid()]
        self.assertTrue(me, "枚举结果里应当包含当前进程")
        self.assertGreater(me[0].threads, 0)


class TestCbsParsing(unittest.TestCase):
    """CBS.log 解析：噪音与真错误必须分开。"""

    def _parse(self, text: str) -> dict:
        fd, path = tempfile.mkstemp(suffix=".log")
        os.close(fd)
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(text)
            return component.parse_cbs(path)
        finally:
            os.unlink(path)

    def test_noise_not_counted_as_bad(self):
        """0x800f0805 是每次枚举包都会写的常态，不能算异常。"""
        info = self._parse(
            "2026-10-22 09:11:46, Info                  CBS    "
            "Failed to internally open package. "
            "[HRESULT = 0x800f0805 - CBS_E_INVALID_PACKAGE]\n")
        self.assertEqual(info["bad"], 0)
        self.assertEqual(info["noise"], 1)

    def test_info_level_without_hresult_ignored(self):
        """Info 级、没有 HRESULT 的普通记录（含 does not have an install time）不算错。"""
        info = self._parse(
            "2026-10-22 09:11:46, Info                  CBS    "
            "Package: Foo-Package~amd64~~1.0 does not have an install time\n")
        self.assertEqual(info["bad"], 0)
        self.assertEqual(info["noise"], 0)

    def test_error_level_counted(self):
        info = self._parse(
            "2026-10-22 09:11:46, Error                 CSI    "
            "Failed to resolve source [HRESULT = 0x800f081f]\n")
        self.assertEqual(info["bad"], 1)
        self.assertEqual(info["errors"][0].hresult, "0x800f081f")

    def test_known_bad_hresult_at_info_level(self):
        """CBS_E_SOURCE_MISSING 即使写成 Info 级也算真问题。"""
        info = self._parse(
            "2026-10-22 09:11:46, Info                  CBS    "
            "Missing source [HRESULT = 0x800f081f - CBS_E_SOURCE_MISSING]\n")
        self.assertEqual(info["bad"], 1)

    def test_cannot_repair_counted(self):
        info = self._parse(
            "2026-10-22 09:11:46, Info                  CSI    "
            "0000002a Cannot repair member file [l:22]\"foo.dll\"\n")
        self.assertEqual(info["cannot_repair"], 1)
        self.assertEqual(info["bad"], 1)

    def test_sr_lines(self):
        info = self._parse(
            "2026-10-22 09:11:46, Info                  CSI    0000000f [SR] "
            "Verify complete\n")
        self.assertEqual(info["sr"], 1)

    def test_missing_file_is_safe(self):
        info = component.parse_cbs(os.path.join(tempfile.gettempdir(),
                                                "no_such_cbs_12345.log"))
        self.assertFalse(info["exists"])
        self.assertEqual(info["bad"], 0)
        self.assertEqual(info["errors"], [])

    def test_tidy_strips_prefix(self):
        s = component._tidy(
            "2026-10-22 09:11:46, Info                  CBS    Failed to "
            "open [HRESULT = 0x800f0805]")
        self.assertNotIn("2026-10-22", s)
        self.assertNotIn("Info", s)
        self.assertIn("Failed to open", s)


class TestPatches(unittest.TestCase):
    def test_regex(self):
        m = component._KB_RE.search("Package_31_for_KB5127694~31bf3856ad364e35~amd64~~10.0.1")
        self.assertIsNotNone(m)
        self.assertEqual(m.group(1), "5127694")
        self.assertIsNone(component._KB_RE.search("Package_for_RollupFix~amd64~~1.0"))

    def test_shape(self):
        for kb, when in component.patches():
            self.assertTrue(kb.startswith("KB"))
            self.assertRegex(when, r"^\d{4}-\d{2}-\d{2}$")


class TestWinsxs(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_counts_files_recursively(self):
        for i in range(3):
            with open(os.path.join(self.tmp, "a%d.bin" % i), "wb") as f:
                f.write(b"x" * 100)
        sub = os.path.join(self.tmp, "sub")
        os.makedirs(sub)
        with open(os.path.join(sub, "b.bin"), "wb") as f:
            f.write(b"y" * 50)
        size, files, cut = component.measure_winsxs(root=self.tmp)
        self.assertEqual(files, 4)
        self.assertEqual(size, 350)
        self.assertFalse(cut)

    def test_stop_flag_interrupts(self):
        for i in range(5):
            d = os.path.join(self.tmp, "d%d" % i)
            os.makedirs(d)
            with open(os.path.join(d, "f"), "wb") as f:
                f.write(b"z" * 10)
        size, files, cut = component.measure_winsxs(
            root=self.tmp, stop_flag=lambda: True)
        self.assertTrue(cut, "stop_flag 立即为真时必须报告已中断")
        self.assertEqual((size, files), (0, 0))

    def test_missing_root(self):
        size, files, cut = component.measure_winsxs(
            root=os.path.join(self.tmp, "nope"))
        self.assertEqual((size, files), (0, 0))


class TestReport(unittest.TestCase):
    def test_counts_and_headline(self):
        rep = component.Report(rows=[
            component.Row("a", "1", "crit"),
            component.Row("b", "2", "warn"),
            component.Row("c", "3", "ok"),
            component.Row("d", "4", "info"),
        ])
        self.assertEqual(rep.problems, 1)
        self.assertEqual(rep.warns, 1)
        self.assertIn("1 项需要处理", rep.headline)

    def test_headline_ok(self):
        rep = component.Report(rows=[component.Row("a", "1", "ok")])
        self.assertIn("正常", rep.headline)

    def test_level_text(self):
        self.assertEqual(component.Row("a", "1", "crit").level_text, "异常")
        self.assertEqual(component.Row("a", "1", "warn").level_text, "注意")


class TestScan(unittest.TestCase):
    """真实系统上的 scan（跳过 dism，减少对外部进程的依赖）。"""

    def test_scan_rows_complete(self):
        rep = component.scan(with_dism=False)
        self.assertGreaterEqual(len(rep.rows), 8)
        names = [r.name for r in rep.rows]
        for want in ("组件存储", "SFC 扫描记录", "组件服务错误", "挂起操作",
                     "组件日志", "累积更新", "最近补丁", "组件存储体积"):
            self.assertIn(want, names)
        for r in rep.rows:
            self.assertIn(r.level, ("ok", "warn", "crit", "info"))
            self.assertTrue(r.value, r.name)
        # 排序：crit 在前，ok 在后
        order = [component.LEVEL_ORDER[r.level] for r in rep.rows]
        self.assertEqual(order, sorted(order))

    def test_scan_without_dism_marks_unknown(self):
        rep = component.scan(with_dism=False)
        self.assertEqual(rep.dism_state, "unknown")

    def test_pending_reasons_shape(self):
        for reason in component.pending_reasons():
            self.assertIsInstance(reason, str)
            self.assertTrue(reason)
            # SessionsPending 在正常机器上常驻，绝不能作为「待重启」的依据
            self.assertNotIn("SessionsPending", reason)

    def test_ubr_format(self):
        rep = component.scan(with_dism=False)
        if rep.ubr:
            self.assertRegex(rep.ubr, r"\d+\.\d+")


if __name__ == "__main__":
    unittest.main()
