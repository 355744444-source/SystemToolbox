# -*- coding: utf-8 -*-
"""v1.13.0 单测：崩溃转储配置体检。

重点是把「转储类型 × 页面文件」这 5×N 个组合的判定钉死。
真机只能覆盖到当前那一台的一种配置，其余全靠构造 Snap。
"""
from __future__ import annotations

import os
import sys
import unittest
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core import dumpcfg as d  # noqa: E402

GB = 1024 ** 3
MB = 1024 ** 2


def snap(**kw) -> d.Snap:
    """默认给一台「健康的 32 GB 机器 + 2 GB 系统盘页面文件 + 小型转储」。"""
    base = dict(enabled=3, ram=32 * GB, kernel_total=int(1.5 * GB),
                pagefiles=[(r"C:\pagefile.sys", 2 * GB)],
                system_drive="C:", free_system=100 * GB,
                dump_file=r"%SystemRoot%\MEMORY.DMP",
                minidump_dir=r"%SystemRoot%\Minidump")
    base.update(kw)
    s = d.Snap(**base)
    s.scanned_at = datetime(2026, 10, 23, 10, 0)
    return s


def topics(findings) -> dict:
    return {f.topic: f for f in findings}


def sev(findings, topic: str) -> str:
    f = topics(findings).get(topic)
    return f.severity if f else "-"


class TestTypeText(unittest.TestCase):
    def test_type_names(self):
        # 官方枚举：0 无 / 1 完整 / 2 内核 / 3 小型 / 4 同 3 / 7 自动
        cases = {0: "无（转储已关闭）", 1: "完整内存转储", 2: "内核内存转储",
                 3: "小型内存转储", 4: "小型内存转储", 7: "自动内存转储"}
        for val, text in cases.items():
            self.assertEqual(snap(enabled=val).type_text, text, val)

    def test_unknown_type(self):
        s = snap(enabled=9)
        self.assertTrue(s.disabled)
        self.assertIn("未知", s.type_text)

    def test_active_dump(self):
        s = snap(enabled=1, filter_pages=1)
        self.assertTrue(s.is_active_dump)
        self.assertEqual(s.type_text, "活动内存转储")

    def test_is_complete_only_one(self):
        # 4 与 3 等价，是小型转储；写成完整转储是经典笔误
        self.assertTrue(snap(enabled=1).is_complete)
        self.assertFalse(snap(enabled=4).is_complete)
        self.assertFalse(snap(enabled=2).is_complete)


class TestNeedPagefile(unittest.TestCase):
    def test_complete_needs_ram_plus_257mb(self):
        need, note = snap(enabled=1, ram=8 * GB).need_pagefile()
        self.assertEqual(need, 8 * GB + 257 * MB)
        self.assertIn("257", note)

    def test_kernel_uses_kernel_total_plus_30pct(self):
        need, note = snap(enabled=2, kernel_total=int(1.5 * GB)).need_pagefile()
        self.assertEqual(need, int(1.5 * GB * 1.3))
        self.assertIn("30%", note)

    def test_kernel_floor_512mb(self):
        # 内核占用读到 0 时不能算出「不需要页面文件」
        need, _ = snap(enabled=2, kernel_total=0).need_pagefile()
        self.assertEqual(need, int(512 * MB * 1.3))

    def test_small_needs_2mb(self):
        need, _ = snap(enabled=3).need_pagefile()
        self.assertEqual(need, 2 * MB)

    def test_disabled_needs_nothing(self):
        need, _ = snap(enabled=0).need_pagefile()
        self.assertEqual(need, 0)


class TestDisabled(unittest.TestCase):
    def test_off_is_crit(self):
        f = d.evaluate(snap(enabled=0))
        self.assertEqual(sev(f, "dump-off"), "crit")

    def test_off_has_advice(self):
        f = topics(d.evaluate(snap(enabled=0)))["dump-off"]
        self.assertTrue(f.advice)
        self.assertTrue(any("自动内存转储" in a for a in f.advice))

    def test_off_skips_capacity_check(self):
        # 关掉了就别再报「页面文件不够」
        f = d.evaluate(snap(enabled=0, pagefiles=[]))
        self.assertNotIn("pf-size", topics(f))

    def test_odd_value_warns(self):
        f = d.evaluate(snap(enabled=9))
        self.assertEqual(sev(f, "dump-odd"), "warn")


class TestCompleteDump(unittest.TestCase):
    def test_2gb_pf_for_32gb_ram_is_crit(self):
        s = snap(enabled=1, ram=32 * GB,
                 pagefiles=[(r"C:\pagefile.sys", 2 * GB)])
        f = topics(d.evaluate(s))
        self.assertEqual(f["pf-size"].severity, "crit")
        self.assertIn("写不出来", f["pf-size"].title)
        # 差额要说清
        self.assertIn("30.25 GB", f["pf-size"].detail)

    def test_enough_pf_is_ok(self):
        s = snap(enabled=1, ram=8 * GB,
                 pagefiles=[(r"C:\pagefile.sys", 9 * GB)])
        self.assertEqual(sev(d.evaluate(s), "pf-size"), "ok")

    def test_exactly_need_is_ok(self):
        # 边界：刚好等于 RAM + 257 MB 应当通过
        need = 8 * GB + 257 * MB
        s = snap(enabled=1, ram=8 * GB,
                 pagefiles=[(r"C:\pagefile.sys", need)])
        self.assertEqual(sev(d.evaluate(s), "pf-size"), "ok")

    def test_one_byte_short_is_crit(self):
        need = 8 * GB + 257 * MB
        s = snap(enabled=1, ram=8 * GB,
                 pagefiles=[(r"C:\pagefile.sys", need - 1)])
        self.assertEqual(sev(d.evaluate(s), "pf-size"), "crit")

    def test_pf_on_other_drive_is_crit(self):
        # 页面文件只在 D 盘，完整转储照样写不出来
        s = snap(enabled=1, ram=8 * GB,
                 pagefiles=[(r"D:\pagefile.sys", 16 * GB)])
        f = topics(d.evaluate(s))
        self.assertEqual(f["pf-drive"].severity, "crit")
        self.assertNotIn("pf-size", f)

    def test_disk_space_short_warns(self):
        s = snap(enabled=1, ram=8 * GB,
                 pagefiles=[(r"C:\pagefile.sys", 9 * GB)],
                 free_system=2 * GB)
        f = topics(d.evaluate(s))
        self.assertEqual(f["disk-space"].severity, "warn")


class TestKernelDump(unittest.TestCase):
    def test_small_pf_warns(self):
        s = snap(enabled=2, kernel_total=int(1.5 * GB),
                 pagefiles=[(r"C:\pagefile.sys", 512 * MB)])
        f = topics(d.evaluate(s))
        self.assertEqual(f["pf-size"].severity, "warn")
        self.assertIn("内核", f["pf-size"].detail)

    def test_enough_pf_ok(self):
        s = snap(enabled=2, kernel_total=int(1.5 * GB),
                 pagefiles=[(r"C:\pagefile.sys", 2 * GB)])
        self.assertEqual(sev(d.evaluate(s), "pf-size"), "ok")

    def test_auto_dump_same_rule_as_kernel(self):
        s = snap(enabled=7, kernel_total=int(1.5 * GB),
                 pagefiles=[(r"C:\pagefile.sys", 512 * MB)])
        self.assertEqual(sev(d.evaluate(s), "pf-size"), "warn")

    def test_no_pf_on_system_drive_warns(self):
        s = snap(enabled=2, pagefiles=[(r"D:\pagefile.sys", 4 * GB)])
        self.assertEqual(sev(d.evaluate(s), "pf-drive"), "warn")


class TestSmallDump(unittest.TestCase):
    def test_2gb_pf_ok(self):
        self.assertEqual(sev(d.evaluate(snap(enabled=3)), "pf-size"), "ok")

    def test_no_pf_crit(self):
        s = snap(enabled=3, pagefiles=[])
        f = topics(d.evaluate(s))
        self.assertEqual(f["pf-size"].severity, "crit")

    def test_small_dump_ignores_disk_space(self):
        # 256 KB 的转储不看剩余空间，别误报
        s = snap(enabled=3, free_system=100 * MB)
        self.assertNotIn("disk-space", topics(d.evaluate(s)))


class TestCrashHistory(unittest.TestCase):
    def _bc(self, when, code=0x9F):
        return d.CrashEvent(when, "BugCheck", 1001, "bugcheck", code)

    def _pw(self, when, code=0):
        return d.CrashEvent(when, "Microsoft-Windows-Kernel-Power", 41,
                            "power", code)

    def test_bugcheck_without_dump_is_crit(self):
        s = snap(crashes=[self._bc(datetime(2026, 10, 1, 3, 0))])
        f = topics(d.evaluate(s))
        self.assertEqual(f["no-dump"].severity, "crit")
        self.assertIn("0x0000009F", f["no-dump"].detail)

    def test_bugcheck_with_dump_is_info(self):
        s = snap(crashes=[self._bc(datetime(2026, 10, 1, 3, 0))],
                 dumps=[d.DumpFile(r"C:\Windows\Minidump\Mini100126-01.dmp",
                                   1 * MB, datetime(2026, 10, 1, 3, 1), "small")])
        f = topics(d.evaluate(s))
        self.assertIn("bugcheck-history", f)
        self.assertNotIn("no-dump", f)

    def test_power_loss_only_is_not_blue_screen(self):
        s = snap(crashes=[self._pw(datetime(2026, 10, 20, 16, 59)),
                          self._pw(datetime(2026, 10, 20, 16, 57))])
        f = topics(d.evaluate(s))
        self.assertEqual(f["power-loss"].severity, "info")
        self.assertIn("不是蓝屏", f["power-loss"].title)
        self.assertNotIn("no-dump", f)

    def test_power_event_with_nonzero_code_is_bugcheck_ish(self):
        # 41 且 BugcheckCode != 0 时不能算「纯掉电」
        s = snap(crashes=[self._pw(datetime(2026, 10, 20, 16, 59), 0x1E)])
        self.assertEqual(s.power_losses, [])
        self.assertNotIn("power-loss", topics(d.evaluate(s)))

    def test_no_crash_at_all_is_ok(self):
        f = topics(d.evaluate(snap(crashes=[])))
        self.assertEqual(f["no-crash"].severity, "ok")

    def test_crash_event_text(self):
        bc = self._bc(datetime(2026, 10, 1, 3, 0))
        self.assertEqual(bc.code_text, "0x0000009F")
        self.assertIn("蓝屏", bc.kind_text)
        pw = self._pw(datetime(2026, 10, 1, 3, 0))
        self.assertEqual(pw.code_text, "非蓝屏")
        self.assertIn("意外关机", pw.kind_text)

    def test_table_short_fields(self):
        # 表格列宽有限：年份去掉、provider 去前缀，否则必被省略号
        c = self._pw(datetime(2026, 10, 20, 16, 59, 10))
        self.assertEqual(c.time_short, "10-20 16:59:10")
        self.assertEqual(c.provider_short, "Kernel-Power")
        self.assertLessEqual(len(c.time_short), 14)
        c2 = d.CrashEvent(None, "BugCheck", 1001, "bugcheck", 0x9F)
        self.assertEqual(c2.provider_short, "BugCheck")
        self.assertEqual(c2.time_short, "—")


class TestLiveKernelCross(unittest.TestCase):
    """活动内核报告与意外关机时间贴合 → 说明是驱动挂起，不是掉电。"""

    def test_nearby_report_rewrites_detail(self):
        import tempfile
        tmp = tempfile.mkdtemp(prefix="cls_dc_")
        path = os.path.join(tmp, "ResourceTimeout-20261020-1655.dmp")
        with open(path, "wb") as fh:
            fh.write(b"\x00" * 16)
        when = datetime.fromtimestamp(os.stat(path).st_mtime)
        s = snap(crashes=[d.CrashEvent(when + timedelta(minutes=4),
                                       "Microsoft-Windows-Kernel-Power", 41,
                                       "power", 0)],
                 live_reports=[path])
        f = topics(d.evaluate(s))["power-loss"]
        self.assertIn("活动内核报告", f.detail)
        self.assertTrue(any("TDR" in a for a in f.advice))
        self.assertFalse(d.nearby_live_reports(
            s, d.CrashEvent(when + timedelta(hours=5)), 45))

    def test_far_report_does_not_rewrite(self):
        import tempfile
        tmp = tempfile.mkdtemp(prefix="cls_dc_")
        path = os.path.join(tmp, "x.dmp")
        with open(path, "wb") as fh:
            fh.write(b"\x00")
        s = snap(crashes=[d.CrashEvent(datetime(2020, 1, 1),
                                       "Microsoft-Windows-Kernel-Power", 41,
                                       "power", 0)],
                 live_reports=[path])
        self.assertNotIn("活动内核报告", topics(d.evaluate(s))["power-loss"].detail)


class TestConfigDetails(unittest.TestCase):
    def test_log_event_off_warns(self):
        self.assertEqual(sev(d.evaluate(snap(log_event=0)), "log-event"), "warn")

    def test_overwrite_off_only_when_dump_exists(self):
        dump = d.DumpFile(r"C:\Windows\MEMORY.DMP", 1 * GB,
                          datetime(2026, 10, 1), "kernel")
        self.assertIn("no-overwrite", topics(d.evaluate(snap(
            overwrite=0, dumps=[dump]))))
        self.assertNotIn("no-overwrite", topics(d.evaluate(snap(overwrite=0))))

    def test_auto_reboot_note(self):
        self.assertIn("auto-reboot", topics(d.evaluate(snap(auto_reboot=1))))
        self.assertNotIn("auto-reboot", topics(d.evaluate(snap(auto_reboot=0))))

    def test_auto_reboot_silent_when_disabled(self):
        self.assertNotIn("auto-reboot", topics(d.evaluate(snap(
            auto_reboot=1, enabled=0))))

    def test_dumpdir_missing_is_crit(self):
        s = snap(enabled=2, dump_file=r"C:\__no_such_dir__\MEMORY.DMP")
        self.assertEqual(sev(d.evaluate(s), "dumpdir"), "crit")

    def test_minidump_dir_missing_is_info(self):
        s = snap(enabled=3, minidump_dir=r"C:\__no_such_mdir__")
        self.assertEqual(sev(d.evaluate(s), "mdir"), "info")

    def test_active_dump_note(self):
        self.assertIn("active-dump", topics(d.evaluate(snap(
            enabled=1, filter_pages=1,
            pagefiles=[(r"C:\pagefile.sys", 40 * GB)]))))

    def test_bitlocker_filter_never_reported(self):
        # 本机实测：C: 完全解密，DumpFilters 里照样有 dumpfve.sys。
        # 按它判断加密是假阳性，这条 finding 已删除，不许再回来。
        f = d.evaluate(snap(dump_filters=["dumpfve.sys"]))
        self.assertFalse(any("locker" in x.topic.lower() for x in f))
        self.assertFalse(any("BitLocker" in x.title for x in f))


class TestVerdict(unittest.TestCase):
    def test_crit_wins(self):
        lv, txt = d.verdict(d.evaluate(snap(enabled=0)))
        self.assertEqual(lv, "crit")
        self.assertIn("留不下证据", txt)

    def test_warn(self):
        s = snap(enabled=2, kernel_total=int(1.5 * GB),
                 pagefiles=[(r"C:\pagefile.sys", 512 * MB)])
        lv, _txt = d.verdict(d.evaluate(s))
        self.assertEqual(lv, "warn")

    def test_ok(self):
        lv, txt = d.verdict(d.evaluate(snap()))
        self.assertEqual(lv, "ok")
        self.assertIn("正常", txt)

    def test_empty(self):
        self.assertEqual(d.verdict([])[0], "dim")

    def test_every_finding_has_title_and_severity(self):
        for cfg in (snap(), snap(enabled=0), snap(enabled=1),
                    snap(enabled=7, pagefiles=[]), snap(log_event=0,
                                                       overwrite=0,
                                                       auto_reboot=1)):
            for f in d.evaluate(cfg):
                self.assertIn(f.severity, ("crit", "warn", "info", "ok"))
                self.assertTrue(f.title)
                self.assertTrue(f.topic)
                self.assertIsInstance(f.advice, list)


class TestHelpers(unittest.TestCase):
    def test_human(self):
        self.assertEqual(d.human(0), "0 B")
        self.assertEqual(d.human(512), "512 B")
        self.assertEqual(d.human(2048), "2 KB")
        self.assertEqual(d.human(2 * MB), "2.0 MB")
        self.assertEqual(d.human(2 * GB), "2.00 GB")

    def test_expand_nt_path(self):
        self.assertEqual(d._expand_nt(r"\??\C:\pagefile.sys"),
                         r"C:\pagefile.sys")
        self.assertEqual(d._expand_nt(r"\\?\C:\pagefile.sys"),
                         r"C:\pagefile.sys")
        self.assertEqual(d._expand_nt(r"C:\pagefile.sys"), r"C:\pagefile.sys")

    def test_pagefile_on_system(self):
        self.assertTrue(snap(pagefiles=[(r"C:\pagefile.sys", 1)]).pagefile_on_system)
        self.assertFalse(snap(pagefiles=[(r"D:\pagefile.sys", 1)]).pagefile_on_system)

    def test_pagefile_on_system_case_insensitive(self):
        s = snap(pagefiles=[(r"c:\pagefile.sys", 1)], system_drive="C:\\")
        self.assertTrue(s.pagefile_on_system)

    def test_dump_file_kind_guess(self):
        self.assertEqual(d._guess_dump_kind(1 * MB, "x.dmp"), "small")
        self.assertEqual(d._guess_dump_kind(10 * MB, "x.dmp"), "kernel")
        self.assertEqual(d._guess_dump_kind(2 * GB, "MEMORY.DMP"), "complete")

    def test_pagefile_facts_prefers_stat(self):
        import tempfile
        tmp = tempfile.mkdtemp(prefix="cls_pf_")
        path = os.path.join(tmp, "pagefile.sys")
        with open(path, "wb") as fh:
            fh.write(b"\x00" * 4096)
        got = d.pagefile_facts([], [path], fallback_total=999)
        self.assertEqual(got, [(path, 4096)])

    def test_pagefile_facts_fallback(self):
        # stat 全失败时用提交上限差值兜底，但要保留路径
        got = d.pagefile_facts([r"C:\pagefile.sys"], [], fallback_total=2 * GB)
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0][1], 2 * GB)

    def test_pagefile_facts_skips_system_managed_placeholder(self):
        # PagingFiles 里 "?:\pagefile.sys" 盘符未知，不能当真实路径去 stat
        got = d.pagefile_facts([r"?:\pagefile.sys"], [], fallback_total=0)
        self.assertEqual(got, [])

    def test_summary_line(self):
        line = d.summary_line(snap())
        self.assertIn("小型内存转储", line)
        self.assertIn("页面文件", line)

    def test_summary_disabled(self):
        self.assertIn("已关闭", d.summary_line(snap(enabled=0)))


class TestRealMachine(unittest.TestCase):
    """真机只读采集：字段能拿到、耗时可控。"""

    def test_read_quick(self):
        import time
        t0 = time.perf_counter()
        s = d.read(with_events=False)
        ms = (time.perf_counter() - t0) * 1000
        self.assertGreater(s.ram, 0)
        self.assertLess(ms, 500, "无事件采集应远快于 500 ms（实测约 3 ms）")

    def test_read_finds_config(self):
        s = d.read(with_events=False)
        self.assertIn(s.enabled, (0, 1, 2, 3, 4, 7),
                      "本机 CrashDumpEnabled 应在官方枚举内")
        self.assertEqual(s.type_text != "未知", True)

    def test_system_drive_has_free_space(self):
        s = d.read(with_events=False)
        self.assertGreater(s.free_system, 0)


if __name__ == "__main__":
    unittest.main()
