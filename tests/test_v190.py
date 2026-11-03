"""v1.9.0 单元测试：U 盘读写测速（usbperf.py）。

测的是「判定逻辑」而不是真实磁盘 IO —— 磁盘快慢在 CI 上不可重复，
但分级阈值、提示生成、历史对比这些是纯函数，必须钉死。

覆盖的坑：
- 写读比分级边界（0.15 / 0.3 / 0.6）
- 「插在 USB 2.0 口」只在 USB 盘上判，内置盘不该出现这条提示
- 历史对比只在写入真的掉了 30% 以上才提，掉 5% 属于正常波动
- 路径规范化：`X:` / `X:\\` / `X:/` 都要拼成 `X:\\`（否则 GetDiskFreeSpaceExW 静默返回 0）
- 测试文件大小下限 128 MB（64 MB 时系统开销占比过高）
"""
from __future__ import annotations

import os
import tempfile
import unittest
from unittest import mock

from app.core import usbperf


def make_target(letter: str = "E:", label: str = "TEST",
                bus: str = "USB", total: int = 32 << 30,
                free: int = 16 << 30, drive_type: int = 3) -> usbperf.Target:
    return usbperf.Target(letter=letter, label=label, fs="exFAT",
                          total=total, free=free, bus=bus,
                          drive_type=drive_type)


def make_sample(read_mbs: float = 300.0, write_mbs: float = 60.0,
                iops: float = 2000.0, when: str = "2026-10-22 10:00",
                size_mb: int = 256) -> usbperf.Sample:
    return usbperf.Sample(size_mb=size_mb, write_mbs=write_mbs,
                          read_mbs=read_mbs, rand_read_iops=iops,
                          rand_read_mbs=iops * 4096 / 2 ** 20,
                          seconds=4.0, when=when, bus="USB")


def make_report(read_mbs: float = 300.0, write_mbs: float = 60.0,
                target: usbperf.Target | None = None,
                **kw) -> usbperf.Report:
    return usbperf.Report(target=target or make_target(),
                          sample=make_sample(read_mbs, write_mbs, **kw))


# ------------------------------------------------------------------ 盘信息
class TestTarget(unittest.TestCase):
    def test_root_always_backslash(self):
        for raw in ("E:", "E:\\", "E:/", "E:\\\\"):
            self.assertEqual(usbperf.Target(letter=raw).root, "E:\\")

    def test_is_usb_case_insensitive(self):
        self.assertTrue(usbperf.Target(letter="E:", bus="USB").is_usb)
        self.assertTrue(usbperf.Target(letter="E:", bus="usb").is_usb)
        self.assertFalse(usbperf.Target(letter="C:", bus="NVMe").is_usb)

    def test_removable_uses_drive_type_not_bus(self):
        # 实测本机 USB 盘的 drive_type 全是 3，只能靠 bus 认 USB
        t = usbperf.Target(letter="H:", bus="USB", drive_type=3)
        self.assertTrue(t.is_usb)
        self.assertFalse(t.removable)

    def test_key_stable_across_drive_letters(self):
        a = make_target(letter="E:", label="UDISK", total=32 << 30)
        b = make_target(letter="I:", label="UDISK", total=32 << 30)
        self.assertEqual(a.key, b.key)      # 同一块盘换了盘符仍是同一块

    def test_key_differs_on_capacity(self):
        a = make_target(label="UDISK", total=32 << 30)
        b = make_target(label="UDISK", total=64 << 30)
        self.assertNotEqual(a.key, b.key)

    def test_text_and_detail(self):
        t = make_target(label="", total=32 << 30, free=16 << 30)
        self.assertIn("未命名", t.text)
        self.assertIn("USB", t.detail)
        self.assertIn("GB", t.detail)


# ------------------------------------------------------------------ 分级
class TestGrades(unittest.TestCase):
    def test_read_grade_boundaries(self):
        cases = [(1200, "NVMe 级"), (500, "USB 3.2 / SATA SSD 级"),
                 (200, "USB 3.x 正常"), (50, "USB 3.0 偏慢"),
                 (20, "USB 2.0 高速"), (5, "低于 USB 2.0 水平")]
        for v, expect in cases:
            rep = make_report(read_mbs=v)
            self.assertEqual(rep.read_grade[0], expect, f"{v} MB/s")

    def test_write_grade_boundaries(self):
        cases = [(900, "NVMe 级"), (400, "SSD 级"), (150, "USB 3.x 良好"),
                 (50, "USB 3.x 一般"), (20, "USB 2.0 高速"), (5, "写入极慢")]
        for v, expect in cases:
            rep = make_report(write_mbs=v)
            self.assertEqual(rep.write_grade[0], expect, f"{v} MB/s")

    def test_ratio_grade_boundaries(self):
        cases = [(0.8, "优秀", "ok"), (0.45, "正常", "ok"),
                 (0.19, "写入偏慢", "warn"), (0.05, "写入受限", "warn")]
        for ratio, label, role in cases:
            rep = make_report(read_mbs=100, write_mbs=100 * ratio)
            self.assertAlmostEqual(rep.sample.ratio, ratio, places=3)
            self.assertEqual(rep.ratio_grade, (label, role))

    def test_ratio_zero_when_no_read(self):
        rep = make_report(read_mbs=0, write_mbs=50)
        self.assertEqual(rep.sample.ratio, 0.0)
        self.assertEqual(rep.ratio_grade[0], "-")

    def test_level_warn_on_slow_write(self):
        # 实测那块 USB 盘：读 329 / 写 59 → 写和写读比都偏低
        rep = make_report(read_mbs=329, write_mbs=59)
        self.assertEqual(rep.read_grade[1], "ok")
        self.assertEqual(rep.write_grade[1], "warn")
        self.assertEqual(rep.level, "warn")

    def test_level_ok_on_fast_disk(self):
        rep = make_report(read_mbs=4500, write_mbs=4000, iops=10000,
                          target=make_target(bus="NVMe"))
        self.assertEqual(rep.level, "ok")

    def test_level_bad_when_error(self):
        rep = make_report()
        rep.errors.append("写入失败")
        self.assertEqual(rep.level, "bad")

    def test_headline_has_numbers(self):
        rep = make_report(read_mbs=328, write_mbs=58)
        h = rep.headline
        self.assertIn("328", h)
        self.assertIn("58", h)


# ------------------------------------------------------------------ 提示
class TestHints(unittest.TestCase):
    def test_usb2_port_hint_only_for_usb(self):
        rep = make_report(read_mbs=32, write_mbs=25)
        self.assertTrue(any("USB 2.0 口" in h for h in rep.hints))

    def test_no_usb2_hint_for_internal_disk(self):
        rep = make_report(read_mbs=32, write_mbs=25,
                          target=make_target(bus="SATA"))
        self.assertFalse(any("USB 2.0 口" in h for h in rep.hints))

    def test_slow_write_hint(self):
        rep = make_report(read_mbs=329, write_mbs=59)
        hints = rep.hints
        self.assertTrue(any("SLC" in h or "写入" in h for h in hints),
                        hints)

    def test_no_slow_write_hint_when_balanced(self):
        rep = make_report(read_mbs=320, write_mbs=300)
        self.assertFalse(any("SLC" in h for h in rep.hints))

    def test_aging_hint_on_30pct_drop(self):
        rep = make_report(read_mbs=300, write_mbs=40)
        rep.prev = make_sample(read_mbs=300, write_mbs=100,
                               when="2026-09-01 09:00")
        self.assertTrue(any("下降" in h for h in rep.hints), rep.hints)

    def test_no_aging_hint_on_small_drop(self):
        rep = make_report(read_mbs=300, write_mbs=95)
        rep.prev = make_sample(read_mbs=300, write_mbs=100)
        self.assertFalse(any("下降" in h for h in rep.hints), rep.hints)

    def test_leftover_file_mentioned(self):
        rep = make_report()
        rep.cleaned = False
        rep.leftover = "E:\\SystemToolbox_speedtest.bin"
        self.assertTrue(any("临时文件" in h for h in rep.hints))

    def test_no_hints_without_data(self):
        rep = usbperf.Report(target=make_target(),
                             sample=usbperf.Sample())
        self.assertEqual(rep.hints, [])


# ------------------------------------------------------------------ 常量
class TestConstants(unittest.TestCase):
    def test_min_size_is_128mb(self):
        # 实测 64 MB 时 47 MB/s、128 MB 时 58 MB/s，小文件会低估速度
        self.assertEqual(min(usbperf.SIZES), 128)

    def test_chunk_aligned(self):
        self.assertEqual(usbperf.CHUNK % usbperf.ALIGN, 0)
        self.assertEqual(usbperf.RAND_BLOCK % usbperf.ALIGN, 0)

    def test_test_file_name_fixed(self):
        self.assertEqual(usbperf.TEST_NAME, "SystemToolbox_speedtest.bin")


# ------------------------------------------------------------------ 历史
class TestHistory(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cls_usb_")
        self.addCleanup(self._cleanup)
        self.patch = mock.patch.object(
            usbperf, "HISTORY", os.path.join(self.tmp, "hist.json"))
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def _cleanup(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_empty_history(self):
        self.assertEqual(usbperf.load_history(), {})
        self.assertIsNone(usbperf.last_of(make_target()))
        self.assertEqual(usbperf.history_rows(make_target()), [])

    def test_remember_and_read_back(self):
        t = make_target()
        usbperf._remember(t, make_sample(read_mbs=300, write_mbs=60))
        hist = usbperf.load_history()
        self.assertIn(t.key, hist)
        last = usbperf.last_of(t)
        self.assertIsNotNone(last)
        self.assertAlmostEqual(last.write_mbs, 60, places=1)

    def test_keeps_only_last_8(self):
        t = make_target()
        for i in range(12):
            usbperf._remember(t, make_sample(write_mbs=50 + i))
        self.assertEqual(len(usbperf.load_history()[t.key]), 8)

    def test_history_rows_delta(self):
        t = make_target()
        usbperf._remember(t, make_sample(write_mbs=100, when="a"))
        usbperf._remember(t, make_sample(write_mbs=60, when="b"))
        usbperf._remember(t, make_sample(write_mbs=61, when="c"))
        rows = usbperf.history_rows(t)
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0][3], "")          # 第一条没有对比对象
        self.assertIn("-40", rows[1][3])           # 100 → 60
        self.assertEqual(rows[2][3], "基本持平")    # 60 → 61

    def test_history_keyed_by_disk_not_letter(self):
        a = make_target(letter="E:", label="UDISK", total=32 << 30)
        b = make_target(letter="I:", label="UDISK", total=32 << 30)
        usbperf._remember(a, make_sample(write_mbs=60))
        self.assertIsNotNone(usbperf.last_of(b))

    def test_broken_history_file_is_tolerated(self):
        path = os.path.expandvars(usbperf.HISTORY)
        with open(path, "w", encoding="utf-8") as f:
            f.write("{not json")
        self.assertEqual(usbperf.load_history(), {})


# ------------------------------------------------------------------ 入口
class TestRunGuards(unittest.TestCase):
    def test_missing_drive_reports_error(self):
        rep = usbperf.run("Z:", 128)
        self.assertTrue(rep.errors)
        self.assertEqual(rep.level, "bad")

    def test_scan_returns_targets(self):
        out = usbperf.scan()
        self.assertIsInstance(out, list)
        for t in out:
            self.assertTrue(t.letter.endswith(":"))
            self.assertGreater(t.total, 0)

    def test_free_bytes_accepts_all_spellings(self):
        # GetDiskFreeSpaceExW 只认 "X:\"；传 "X:/" 会返回 0 看起来像磁盘满
        a = usbperf.free_bytes("C:")
        b = usbperf.free_bytes("C:/")
        self.assertEqual(a, b)


if __name__ == "__main__":
    unittest.main()
