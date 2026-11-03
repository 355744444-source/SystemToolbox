# -*- coding: utf-8 -*-
"""v1.17.0：本机蓝屏记录（bugchecks）。

重点是三条容易出错的规则：
1. 蓝屏码在 DUMP_HEADER64 的 **0x38**，不是网上流传的 0x400；
2. 32 位转储的码在 0x28，参数是 4 个 32 位；
3. 转储与事件按时间合并时，不能把两件不同的事并成一条，也不能漏掉只有事件的那次。
"""
from __future__ import annotations

import os
import shutil
import struct
import tempfile
import unittest
from datetime import datetime, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

from app.core import bugchecks  # noqa: E402
from app.core import dumpcfg, knowledge as kb  # noqa: E402
from app.core import minidump  # noqa: E402

FIX = os.path.join(ROOT, "tests", "fixtures")
F64 = os.path.join(FIX, "kernel_dpc_watchdog_133.dmp")     # DU64, 0x133
F32 = os.path.join(FIX, "kernel_x86_7e.dmp")              # 32 位, 0x7E
FMDMP = os.path.join(FIX, "user_appcrash_av.dmp")         # MDMP，无内核码


def make_dump(path: str, code: int, params=(0, 0, 0, 0), is64: bool = True,
              size: int = 0x60, sign: bytes = b"PAGE") -> str:
    """造一个只有文件头的假转储，用于验证解析与合并。"""
    buf = bytearray(max(size, 0x60))
    buf[:4] = sign
    buf[4:8] = b"DU64" if is64 else b"DUMP"
    if is64:
        struct.pack_into("<I", buf, 0x38, code)
        for i, v in enumerate(params[:4]):
            struct.pack_into("<Q", buf, 0x40 + i * 8, v)
        struct.pack_into("<I", buf, 0x30, 0x8664)
        struct.pack_into("<I", buf, 0x34, 16)
    else:
        struct.pack_into("<I", buf, 0x28, code)
        for i, v in enumerate(params[:4]):
            struct.pack_into("<I", buf, 0x2C + i * 4, v)
        struct.pack_into("<I", buf, 0x20, 0x014C)
        struct.pack_into("<I", buf, 0x24, 4)
    with open(path, "wb") as f:
        f.write(bytes(buf))
    return path


def ev(kind: str, code: int, when: datetime, event_id: int = 0,
       params=(), provider: str = "") -> dumpcfg.CrashEvent:
    return dumpcfg.CrashEvent(
        time=when, provider=provider or (
            "Microsoft-Windows-Kernel-Power" if event_id == 41
            else "Microsoft-Windows-WER-SystemErrorReporting"),
        event_id=event_id or (41 if kind == "power" else 1001),
        kind=kind, code=code, params=tuple(params), text="")


class ReadHeaderTest(unittest.TestCase):
    """转储头解析：偏移错一位就全盘皆错，所以逐个 fixture 钉住。"""

    def test_kernel64_fixture(self):
        h = bugchecks.read_header(F64)
        self.assertIsNotNone(h)
        self.assertTrue(h.is64)
        self.assertEqual(h.code, 0x133)
        self.assertTrue(h.has_code)
        self.assertEqual(len(h.params), 4)
        self.assertEqual(h.cpus, 16)

    def test_kernel32_fixture(self):
        """32 位转储：码在 0x28，参数是 4 个 32 位（不是 64 位）。"""
        h = bugchecks.read_header(F32)
        self.assertIsNotNone(h)
        self.assertFalse(h.is64)
        self.assertEqual(h.code, 0x7E)
        self.assertEqual(h.params[0], 0xC0000005)

    def test_user_mode_mdmp_has_no_bugcheck(self):
        self.assertIsNone(bugchecks.read_header(FMDMP))

    def test_missing_and_wrong_inputs(self):
        self.assertIsNone(bugchecks.read_header(""))
        self.assertIsNone(bugchecks.read_header(r"C:\不存在\没有这个.dmp"))
        self.assertIsNone(bugchecks.read_header(FIX))          # 目录
        self.assertIsNone(bugchecks.read_header(__file__))     # 源码文件

    def test_tiny_file(self):
        d = tempfile.mkdtemp(prefix="cls_bc_")
        try:
            p = os.path.join(d, "tiny.dmp")
            with open(p, "wb") as f:
                f.write(b"PAGE")
            self.assertIsNone(bugchecks.read_header(p))
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_code_at_0x38_not_0x400(self):
        """把码写在 0x400、0x38 留 0 —— 必须读出「没有码」，证明不看 0x400。"""
        d = tempfile.mkdtemp(prefix="cls_bc_")
        try:
            p = make_dump(os.path.join(d, "wrong.dmp"), 0)
            with open(p, "r+b") as f:
                f.seek(0x400)
                f.write(struct.pack("<I", 0xDEAD))
            h = bugchecks.read_header(p)
            self.assertIsNotNone(h)
            self.assertEqual(h.code, 0, "0x400 处的值不能当蓝屏码")
            self.assertFalse(h.has_code)
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_out_of_range_code_marked_bad(self):
        d = tempfile.mkdtemp(prefix="cls_bc_")
        try:
            p = make_dump(os.path.join(d, "big.dmp"), 0x12345)
            h = bugchecks.read_header(p)
            self.assertEqual(h.code, 0)
            self.assertEqual(h.bad_code, 0x12345)
            self.assertFalse(h.has_code)
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_non_pagedump_signature(self):
        d = tempfile.mkdtemp(prefix="cls_bc_")
        try:
            p = make_dump(os.path.join(d, "x.dmp"), 0x133, sign=b"XXXX")
            self.assertIsNone(bugchecks.read_header(p))
        finally:
            shutil.rmtree(d, ignore_errors=True)


class CollectTest(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = tempfile.mkdtemp(prefix="cls_bc_")

    def tearDown(self) -> None:
        shutil.rmtree(self.dir, ignore_errors=True)

    def _dump(self, name: str, code: int, when: datetime, **kw) -> str:
        p = make_dump(os.path.join(self.dir, name), code, **kw)
        ts = when.timestamp()
        os.utime(p, (ts, ts))
        return p

    # ---------- 只有转储
    def test_dump_only(self):
        when = datetime(2026, 5, 1, 10, 0, 0)
        p = self._dump("a.dmp", 0x133, when)
        s = bugchecks.collect(with_events=False, paths=[(p, "minidump")])
        self.assertEqual(len(s.records), 1)
        c = s.records[0]
        self.assertEqual(c.code, 0x133)
        self.assertEqual(c.origin, "minidump")
        self.assertEqual(c.origin_text, "小型转储")
        self.assertEqual(c.name, "DPC_WATCHDOG_VIOLATION")
        self.assertTrue(c.name_cn)
        self.assertTrue(c.advice, "知识库建议应回填到记录上")
        self.assertEqual(c.dump_path, p)
        self.assertGreater(c.dump_size, 0)

    def test_live_report_label(self):
        p = self._dump("ResourceTimeout.dmp", 0x1CC, datetime(2026, 5, 1, 9, 0, 0))
        s = bugchecks.collect(with_events=False, paths=[(p, "live")])
        self.assertEqual(s.records[0].origin_text, "实况内核报告")
        self.assertEqual(s.records[0].note_text, "驱动挂起")

    def test_records_sorted_desc(self):
        old = self._dump("old.dmp", 0x7E, datetime(2026, 1, 1), is64=False)
        new = self._dump("new.dmp", 0x133, datetime(2026, 6, 1))
        s = bugchecks.collect(with_events=False,
                              paths=[(old, "minidump"), (new, "minidump")])
        self.assertEqual([c.code for c in s.records], [0x133, 0x7E])

    # ---------- 只有事件
    def test_event_without_dump(self):
        when = datetime(2026, 5, 1, 10, 0, 0)
        s = bugchecks.collect(events=[ev("bugcheck", 0x1E, when)],
                              paths=[])
        self.assertEqual(len(s.records), 1)
        c = s.records[0]
        self.assertEqual(c.origin, "event")
        self.assertEqual(c.code, 0x1E)
        self.assertEqual(c.dump_path, "")
        self.assertEqual(c.note_text, "转储已不在")
        self.assertIn("转储", c.note)

    def test_bugcheck_without_code_skipped(self):
        """1001 但没有码 = 没信息量，别占一行。"""
        s = bugchecks.collect(events=[ev("bugcheck", 0, datetime(2026, 5, 1))],
                              paths=[])
        self.assertEqual(s.records, [])

    def test_power_loss_kept_without_code(self):
        s = bugchecks.collect(events=[ev("power", 0, datetime(2026, 5, 1))],
                              paths=[])
        self.assertEqual(len(s.records), 1)
        self.assertFalse(s.records[0].has_code)
        self.assertEqual(s.records[0].code_text, "—")
        self.assertEqual(s.records[0].note_text, "掉电/硬关机")

    # ---------- 合并
    def test_bugcheck_merges_with_dump(self):
        when = datetime(2026, 5, 1, 10, 0, 0)
        p = self._dump("a.dmp", 0x133, when)
        s = bugchecks.collect(
            events=[ev("bugcheck", 0x133, when + timedelta(seconds=40))],
            paths=[(p, "minidump")])
        self.assertEqual(len(s.records), 1, "同一件事不该出现两行")
        c = s.records[0]
        self.assertEqual(c.origin, "minidump")
        self.assertEqual(c.event_id, 1001)
        self.assertTrue(c.provider)
        self.assertTrue(c.dump_path)

    def test_bugcheck_far_away_not_merged(self):
        when = datetime(2026, 5, 1, 10, 0, 0)
        p = self._dump("a.dmp", 0x133, when)
        s = bugchecks.collect(
            events=[ev("bugcheck", 0x1E, when + timedelta(minutes=30))],
            paths=[(p, "minidump")])
        self.assertEqual(len(s.records), 2)

    def test_reboot_linked_after_dump(self):
        """41 在转储之后 5 分钟 = 同一次故障的后续硬关机，不新增行。"""
        when = datetime(2026, 5, 1, 10, 0, 0)
        p = self._dump("a.dmp", 0x1CC, when)
        s = bugchecks.collect(
            events=[ev("power", 0, when + timedelta(minutes=5))],
            paths=[(p, "live")])
        self.assertEqual(len(s.records), 1)
        c = s.records[0]
        self.assertIsNotNone(c.reboot_time)
        self.assertIn("5分0秒后硬关机", c.reboot_text)
        self.assertIn("硬关机", c.note_text)

    def test_reboot_before_dump_not_linked(self):
        """41 在转储之前 = 不可能是它的后续，不能关联。"""
        when = datetime(2026, 5, 1, 10, 0, 0)
        p = self._dump("a.dmp", 0x133, when)
        s = bugchecks.collect(
            events=[ev("power", 0, when - timedelta(minutes=5))],
            paths=[(p, "minidump")])
        self.assertEqual(len(s.records), 2)
        self.assertIsNone(s.records[0].reboot_time)

    def test_reboot_outside_window_not_linked(self):
        when = datetime(2026, 5, 1, 10, 0, 0)
        p = self._dump("a.dmp", 0x133, when)
        s = bugchecks.collect(
            events=[ev("power", 0, when + timedelta(minutes=40))],
            paths=[(p, "minidump")])
        self.assertEqual(len(s.records), 2)

    def test_power_with_code_is_a_bugcheck(self):
        """41 的 BugcheckCode 非 0 = 关机前确实有蓝屏码。"""
        when = datetime(2026, 5, 1, 10, 0, 0)
        s = bugchecks.collect(events=[ev("power", 0x133, when)], paths=[])
        self.assertEqual(len(s.records), 1)
        c = s.records[0]
        self.assertTrue(c.has_code)
        self.assertEqual(c.code_text, "0x00000133")
        self.assertIn("41", c.note)

    # ---------- 汇总
    def test_reboot_links_nearest_dump_only(self):
        """窗口内有两个转储时，41 只能挂到时间最近的那个上。

        回归：早期实现没有 break，循环最后赋值，结果挂到了最早的那个 ——
        表里会显示「3 小时前的旧蓝屏硬关机」，与事实完全不符。
        """
        t = datetime(2026, 5, 1, 10, 0, 0)
        old = self._dump("old.dmp", 0x1E, t)
        new = self._dump("new.dmp", 0x133, t + timedelta(minutes=8))
        s = bugchecks.collect(
            events=[ev("power", 0, t + timedelta(minutes=10))],
            paths=[(old, "minidump"), (new, "minidump")])
        self.assertEqual(len(s.records), 2)
        by_code = {c.code: c for c in s.records}
        self.assertIsNotNone(by_code[0x133].reboot_time, "应关联 8 分钟前那次")
        self.assertIsNone(by_code[0x1E].reboot_time, "不能关联到更早那次")

    def test_second_reboot_is_its_own_row(self):
        """两次硬关机只隔 2 分钟：最近那次算「后续」，另一次单独成行。

        回归：早期实现让一个转储吞掉窗口内**所有** 41，把 16:59 那次
        非正常关机悄悄抹掉了 —— 表里看起来「只关机过一次」。
        """
        t = datetime(2026, 5, 1, 10, 0, 0)
        p = self._dump("a.dmp", 0x1CC, t)
        s = bugchecks.collect(
            events=[ev("power", 0, t + timedelta(minutes=4)),
                    ev("power", 0, t + timedelta(minutes=2))],
            paths=[(p, "live")])
        self.assertEqual(len(s.records), 2, "多出来的 41 不能被吞掉")
        dump = next(c for c in s.records if c.has_code)
        power = next(c for c in s.records if not c.has_code)
        self.assertEqual(dump.reboot_time, t + timedelta(minutes=2), "配最近的那条")
        self.assertEqual(power.time, t + timedelta(minutes=4))

    def test_merge_is_order_independent(self):
        """事件日志是倒序读出来的，合并结果不能跟着读取顺序变。"""
        t = datetime(2026, 5, 1, 10, 0, 0)
        p = self._dump("a.dmp", 0x1CC, t)
        evs = [ev("power", 0, t + timedelta(minutes=4)),
               ev("power", 0, t + timedelta(minutes=2)),
               ev("bugcheck", 0x1CC, t + timedelta(seconds=20))]
        a = bugchecks.collect(events=evs, paths=[(p, "live")])
        b = bugchecks.collect(events=list(reversed(evs)), paths=[(p, "live")])
        self.assertEqual(
            [(c.time, c.code, c.reboot_time) for c in a.records],
            [(c.time, c.code, c.reboot_time) for c in b.records])

    def test_reboot_merges_into_claimed_dump(self):
        """转储已被 1001 认领后，后续 41 仍要挂到同一行上。

        回归：早期实现用一个 used_dump 集合同时管「1001 去重」和「41 关联」，
        结果「转储 + 1001 + 41」这种最常见的三件套会被拆成两行 ——
        一行蓝屏、一行假「掉电/硬关机」。
        """
        t = datetime(2026, 5, 1, 10, 0, 0)
        p = self._dump("a.dmp", 0x133, t)
        s = bugchecks.collect(
            events=[ev("bugcheck", 0x133, t + timedelta(seconds=30)),
                    ev("power", 0, t + timedelta(minutes=3))],
            paths=[(p, "minidump")])
        self.assertEqual(len(s.records), 1, "一次蓝屏只能占一行")
        c = s.records[0]
        self.assertEqual(c.code, 0x133)
        self.assertEqual(c.reboot_time, t + timedelta(minutes=3))

    def test_duplicate_bugchecks_share_one_dump(self):
        """同一次蓝屏写了两条 1001（WER 补写）时，只占一行。"""
        t = datetime(2026, 5, 1, 10, 0, 0)
        p = self._dump("a.dmp", 0x1E, t)
        s = bugchecks.collect(
            events=[ev("bugcheck", 0x1E, t + timedelta(seconds=10)),
                    ev("bugcheck", 0x1E, t + timedelta(seconds=50))],
            paths=[(p, "minidump")])
        self.assertEqual(len(s.records), 1)
        self.assertEqual(s.records[0].origin, "minidump")

    def test_code_invalid_range_ignored(self):
        """1001 的 param1 若取到 0xC0000005 这类状态码，不能当蓝屏码显示。"""
        s = bugchecks.collect(
            events=[ev("bugcheck", 0xC0000005, datetime(2026, 5, 1))], paths=[])
        self.assertEqual(s.records, [], "越界值必须当作「没有码」，不能误报")

    def test_power41_invalid_code_degrades(self):
        """41 里的越界值同样丢弃，退化成「掉电/硬关机」而不是假蓝屏码。"""
        s = bugchecks.collect(
            events=[ev("power", 0x80000003, datetime(2026, 5, 1))], paths=[])
        self.assertEqual(len(s.records), 1)
        self.assertFalse(s.records[0].has_code)
        self.assertEqual(s.records[0].note_text, "掉电/硬关机")

    def test_max_boundary_code_kept(self):
        s = bugchecks.collect(
            events=[ev("bugcheck", 0xFFFF, datetime(2026, 5, 1))], paths=[])
        self.assertTrue(s.records[0].has_code)

    # ---------- 汇总
    def test_code_counts_and_summary(self):
        t = datetime(2026, 5, 1, 10, 0, 0)
        a = self._dump("a.dmp", 0x133, t)
        b = self._dump("b.dmp", 0x133, t - timedelta(days=1))
        c = self._dump("c.dmp", 0x7E, t - timedelta(days=2), is64=False)
        s = bugchecks.collect(with_events=False,
                              paths=[(a, "minidump"), (b, "minidump"), (c, "minidump")])
        self.assertEqual(s.code_counts, {0x133: 2, 0x7E: 1})
        self.assertIn("3 次异常记录", s.headline)
        self.assertIn("3 次有蓝屏码", s.headline)
        self.assertIn("0x00000133", s.short_line)
        self.assertEqual(s.latest.code, 0x133)

    def test_empty_summary(self):
        s = bugchecks.collect(with_events=False, paths=[])
        self.assertEqual(s.records, [])
        self.assertIn("没有", s.headline)
        self.assertIn("没有", s.short_line)
        self.assertIsNone(s.latest)


class FindingsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = tempfile.mkdtemp(prefix="cls_bc_")

    def tearDown(self) -> None:
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_empty_scan_finding(self):
        s = bugchecks.LocalScan()
        f = bugchecks.findings(s)
        self.assertEqual(len(f), 1)
        self.assertIn("没有蓝屏记录", f[0].title)
        self.assertTrue(f[0].advice)

    def test_repeated_code_flagged(self):
        t = datetime(2026, 5, 1, 10, 0, 0)
        paths = []
        for i in range(3):
            p = make_dump(os.path.join(self.dir, "a%d.dmp" % i), 0x1E)
            ts = (t - timedelta(days=i)).timestamp()
            os.utime(p, (ts, ts))
            paths.append((p, "minidump"))
        s = bugchecks.collect(with_events=False, paths=paths)
        titles = [x.title for x in bugchecks.findings(s)]
        self.assertTrue(any("重复出现 3 次" in t for t in titles), titles)

    def test_no_dump_finding(self):
        s = bugchecks.collect(events=[ev("bugcheck", 0x1E, datetime(2026, 5, 1))],
                              paths=[])
        titles = [x.title for x in bugchecks.findings(s)]
        self.assertTrue(any("没有转储文件" in t for t in titles), titles)

    def test_unknown_code_finding(self):
        s = bugchecks.collect(
            events=[ev("bugcheck", 0x7FFF, datetime(2026, 5, 1))], paths=[])
        titles = [x.title for x in bugchecks.findings(s)]
        self.assertTrue(any("知识库未收录" in t for t in titles), titles)

    def test_advice_is_list(self):
        """Finding.advice 必须是 list —— 传字符串会让卡片逐字竖排。"""
        s = bugchecks.collect(events=[ev("bugcheck", 0x1E, datetime(2026, 5, 1))],
                              paths=[])
        for f in bugchecks.findings(s):
            self.assertIsInstance(f.advice, list)
            self.assertIsInstance(f.severity, str)


class KnowledgeTest(unittest.TestCase):
    def test_0x1cc_known(self):
        """本机 2026-10-20 的实况报告就是 0x1CC，不能显示成「未收录」。"""
        info = kb.bugcheck_info(0x1CC)
        self.assertIsNotNone(info)
        self.assertEqual(info.name, "ATTEMPTED_WRITE_TO_READ_ONLY_MEMORY")
        self.assertTrue(info.name_cn)
        self.assertTrue(info.advice)

    def test_all_crash_codes_have_name(self):
        s = bugchecks.collect(events=[ev("bugcheck", 0x133, datetime(2026, 5, 1))],
                              paths=[])
        self.assertEqual(s.records[0].name_text, "DPC_WATCHDOG_VIOLATION")


class RobustnessTest(unittest.TestCase):
    """静默失败是这类工具最大的坑：什么都没报，用户以为「本机没问题」。"""

    def test_mdmp_skip_is_reported(self):
        """目录里有 dmp 但表里没行时，必须说明为什么。"""
        s = bugchecks.collect(with_events=False, paths=[(FMDMP, "minidump")])
        self.assertEqual(s.records, [])
        self.assertTrue(any("不是内核转储" in p for p in s.problems), s.problems)

    def test_event_read_failure_is_recorded(self):
        """事件日志读失败要留痕，且不能连累已经读到的转储记录。"""
        orig = bugchecks.dumpcfg.scan_crashes

        def boom(*_a, **_kw):
            raise RuntimeError("wevtapi 拒绝访问")

        bugchecks.dumpcfg.scan_crashes = boom
        try:
            s = bugchecks.collect(paths=[(F64, "minidump")])
        finally:
            bugchecks.dumpcfg.scan_crashes = orig
        self.assertIn("wevtapi", s.event_error)
        self.assertEqual(len(s.records), 1, "转储记录不该因事件读失败而丢失")
        self.assertEqual(s.records[0].code, 0x133)

    def test_cost_ms_not_injectable(self):
        """耗时是内部实测值，不接受外部传入（否则读数可以是假的）。"""
        with self.assertRaises(TypeError):
            bugchecks.collect(with_events=False, paths=[], cost_ms=1)  # type: ignore[call-arg]

    def test_cost_ms_measured(self):
        s = bugchecks.collect(with_events=False, paths=[(F64, "minidump")])
        self.assertGreaterEqual(s.cost_ms, 0)


class ConsistencyTest(unittest.TestCase):
    """和 minidump 模块的解析必须一致 —— 两处偏移写死的地方不能各说各话。"""

    def test_same_code_as_minidump_parser(self):
        for path in (F64, F32):
            h = bugchecks.read_header(path)
            rep = minidump.parse(path)
            if rep.bugcheck.code:
                self.assertEqual(h.code, rep.bugcheck.code,
                                 "%s 两处解析不一致" % os.path.basename(path))


if __name__ == "__main__":
    unittest.main()
