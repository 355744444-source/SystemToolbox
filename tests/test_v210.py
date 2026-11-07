# -*- coding: utf-8 -*-
"""v2.1.0：开机与登录诊断（boot）。

这一版的判断全部依赖「字段怎么读」和「跟谁比」，所以单测集中在四块：

1. **字段口径**：事件 100 的 40 多个计时字段里，哪些属于 MainPath、
   哪些在 BootTime 之外；101/102/103/109 与 104/110 是两种不同的字段模板。
2. **拆段算术**：八段相加必须等于 MainPath + PostBoot，
   否则页面上「占开机比」一列就是错的（超过 100% 的那种错）。
3. **聚合与分级**：同名慢项要合并、按「多花了多久」排序；
   只出现一次的不许升级成「很慢」，否则健康机器一进页面就一片红。
4. **结论文案**：没有数据 / 没权限 / 有数据三种情况下，
   headline 与 findings 都不能说谎。
"""
from __future__ import annotations

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))

import boot_fixtures as F                                     # noqa: E402

from app.core import boot                                     # noqa: E402


def one_scan(*xmls):
    """把若干事件 XML 直接喂给 scan（不碰真机通道）。"""
    return boot.scan(events=F.parse(list(xmls)), with_registry=False)


class DurTextTest(unittest.TestCase):
    def test_millisecond_scale(self):
        self.assertEqual(boot.dur_text(0), "0 毫秒")
        self.assertEqual(boot.dur_text(850), "850 毫秒")

    def test_second_scale_keeps_one_decimal(self):
        self.assertEqual(boot.dur_text(1500), "1.5 秒")
        self.assertEqual(boot.dur_text(59900), "59.9 秒")

    def test_minute_scale(self):
        self.assertEqual(boot.dur_text(60000), "1 分")
        self.assertEqual(boot.dur_text(135000), "2 分 15 秒")

    def test_negative_clamped(self):
        self.assertEqual(boot.dur_text(-5), "0 毫秒")

    def test_sec_text_is_always_seconds(self):
        # 表格列统一用秒，便于按数值排序与右对齐
        self.assertEqual(boot.sec_text(20632), "20.6 秒")


class ParseBootTest(unittest.TestCase):
    def test_reads_all_basic_fields(self):
        rep = one_scan(F.boot_xml(boot_ms=307275, main_ms=217594, post_ms=89681))
        self.assertEqual(rep.count, 1)
        r = rep.latest
        self.assertEqual(r.boot_ms, 307275)
        self.assertEqual(r.main_ms, 217594)
        self.assertEqual(r.post_ms, 89681)
        self.assertEqual(r.apps, 14)

    def test_time_comes_from_boot_end_time(self):
        # BootEndTime 才是「这次开机结束」的时刻，比事件记录时间更贴
        rep = one_scan(F.boot_xml(when="2020-05-06T01:02:03.1234567Z"))
        self.assertEqual(rep.latest.when.strftime("%Y-%m-%d"), "2020-05-06")

    def test_utc_is_converted_to_local(self):
        # 事件里是 UTC（带 Z），页面显示的是本地时间，差 8 小时
        rep = one_scan(F.boot_xml(when="2020-05-06T00:00:00.0000000Z"))
        self.assertEqual(rep.latest.when.strftime("%H:%M"), "08:00")

    def test_missing_fields_are_zero_not_crash(self):
        # 老系统确实少字段；缺了就给 0，不能让整页挂掉
        xml = F.boot_xml(extra={"BootExplorerInitTime": "", "BootSmssInitTime": ""})
        rep = one_scan(xml)
        self.assertEqual(rep.count, 1)
        fields = rep.latest.fields
        self.assertEqual(boot._int(fields, "BootExplorerInitTime"), 0)

    def test_garbage_numbers_become_zero(self):
        rep = one_scan(F.boot_xml(extra={"BootDriverInitTime": "abc"}))
        self.assertEqual(boot._int(rep.latest.fields, "BootDriverInitTime"), 0)

    def test_boot_without_total_time_is_dropped(self):
        rep = one_scan(F.boot_xml(boot_ms=0, main_ms=0, post_ms=0))
        self.assertEqual(rep.count, 0)

    def test_total_rebuilt_when_phases_exceed_it(self):
        # 快速启动下有些系统 BootTime 只记了 MainPath，直接显示会「两段之和 > 总时长」
        rep = one_scan(F.boot_xml(boot_ms=1000, main_ms=40000, post_ms=20000))
        self.assertEqual(rep.latest.boot_ms, 60000)

    def test_degradation_flag(self):
        rep = one_scan(F.boot_xml(degraded=True))
        self.assertTrue(rep.latest.degraded)
        self.assertEqual(rep.degraded_count, 1)


class PhaseTest(unittest.TestCase):
    def test_phases_sum_equals_main_plus_post(self):
        rep = one_scan(F.boot_xml(boot_ms=62000, main_ms=41000, post_ms=21000))
        r = rep.latest
        total = sum(p.ms for p in r.phases if p.in_boot)
        self.assertEqual(total, r.main_ms + r.post_ms)
        self.assertEqual(total, r.boot_ms)
        # MainPath 的几段（不含登录后后台）相加 == MainPath
        main_only = sum(p.ms for p in r.phases if p.in_boot and p.in_main)
        self.assertEqual(main_only, r.main_ms)

    def test_loader_is_outside_boot_time(self):
        rep = one_scan(F.boot_xml())
        loader = [p for p in rep.latest.phases if p.key == "loader"][0]
        self.assertFalse(loader.in_boot)
        # OSLoaderDuration 是 Windows 徽标之前的时间，不该算进 MainPath
        self.assertEqual(loader.ms, 1185)

    def test_unexplained_is_residual_not_negative(self):
        # 字段合计超过 MainPath 时（口径不一致的机器）残差必须归零，不能出负数
        rep = one_scan(F.boot_xml(main_ms=1000, extra={
            "BootSmssInitTime": 50000}))
        self.assertGreaterEqual(rep.latest.unexplained_ms, 0)
        self.assertGreaterEqual(rep.latest.attributed_ms - rep.latest.main_ms, 0)

    def test_all_zero_fields_go_into_residual(self):
        rep = one_scan(F.boot_xml(main_ms=20000, extra={
            "BootKernelInitTime": 0, "BootDriverInitTime": 0,
            "BootDevicesInitTime": 0, "BootPrefetchInitTime": 0,
            "BootAutoChkTime": 0, "BootSmssInitTime": 0,
            "BootCriticalServicesInitTime": 0,
            "BootUserProfileProcessingTime": 0,
            "BootMachineProfileProcessingTime": 0,
            "BootExplorerInitTime": 0}))
        rest = [p for p in rep.latest.phases if p.key == "rest"][0]
        self.assertEqual(rest.ms, 20000)

    def test_attributed_never_exceeds_main(self):
        for main in (500, 5000, 50000):
            rep = one_scan(F.boot_xml(main_ms=main))
            self.assertLessEqual(rep.latest.attributed_ms, main + 1)

    def test_main_ratio(self):
        rep = one_scan(F.boot_xml(boot_ms=60000, main_ms=45000, post_ms=15000))
        self.assertAlmostEqual(rep.latest.main_ratio, 0.75, places=3)


class SlowItemTest(unittest.TestCase):
    def test_application_fields(self):
        rep = one_scan(F.boot_xml(), F.slow_xml(
            101, "MsMpEng.exe", 20632, 16332,
            friendly="Antimalware Service Executable", version="3.0.8402.0",
            path=r"C:\Program Files\x\MsMpEng.exe"))
        it = rep.slow[0]
        self.assertEqual(it.kind, "应用")
        self.assertEqual(it.name, "MsMpEng.exe")
        self.assertEqual(it.total_ms, 20632)
        self.assertEqual(it.degrade_ms, 16332)
        self.assertEqual(it.title, "Antimalware Service Executable")

    def test_kind_mapping_per_event_id(self):
        want = {101: "应用", 102: "驱动", 103: "服务", 109: "设备",
                104: "阶段", 110: "阶段"}
        for eid, kind in want.items():
            rep = one_scan(F.boot_xml(), F.slow_xml(eid, "x%d" % eid, 9000, 8000))
            self.assertEqual(rep.slow[0].kind, kind, eid)

    def test_five_field_phase_event(self):
        # 104/110 只有 Name + 两个耗时，没有 Path/Version
        rep = one_scan(F.boot_xml(), F.slow_xml(110, "SMSSInit", 12000, 9000))
        it = rep.slow[0]
        self.assertEqual(it.name, "SMSSInit")
        self.assertEqual(it.path, "")
        self.assertEqual(it.degrade_ms, 9000)

    def test_title_falls_back_to_name_then_placeholder(self):
        rep = one_scan(F.boot_xml(), F.slow_xml(103, "windefend", 326, 234))
        self.assertEqual(rep.slow[0].title, "windefend")

    def test_quoted_path_is_stripped(self):
        rep = one_scan(F.boot_xml(), F.slow_xml(
            102, "a.sys", 9000, 8000, path='"C:\\Windows\\System32\\drivers\\a.sys"'))
        self.assertFalse(rep.slow[0].path.startswith('"'))

    def test_event_without_name_is_dropped(self):
        # 名字、友好名、路径全空 → 这一条没有任何信息量
        rep = one_scan(F.boot_xml(), F.slow_xml(101, "", 9000, 8000))
        self.assertEqual(rep.slow, [])


class AggregateTest(unittest.TestCase):
    def test_same_name_merges_across_boots(self):
        rep = one_scan(
            F.boot_xml(when="2019-08-29T10:51:02Z"),
            F.boot_xml(when="2019-08-28T10:51:02Z"),
            F.slow_xml(101, "MsMpEng.exe", 20632, 16332,
                       when="2019-08-29T10:51:02Z"),
            F.slow_xml(101, "MsMpEng.exe", 17000, 14000,
                       when="2019-08-28T10:51:02Z"))
        self.assertEqual(len(rep.slow), 1)
        it = rep.slow[0]
        self.assertEqual(it.count, 2)
        self.assertEqual(it.worst_ms, 20632)
        self.assertEqual(it.total_degrade, 30332)
        self.assertEqual(it.verdict, "两次都慢")

    def test_same_name_different_kind_stays_separate(self):
        # 「nvlddmkm」既可能是驱动也可能是设备，类别不同不能合并
        rep = one_scan(F.boot_xml(),
                       F.slow_xml(102, "same.sys", 9000, 8000),
                       F.slow_xml(109, "same.sys", 9000, 8000))
        self.assertEqual(len(rep.slow), 2)

    def test_sorted_by_total_degrade(self):
        rep = one_scan(F.boot_xml(),
                       F.slow_xml(101, "small.exe", 2000, 1000),
                       F.slow_xml(101, "big.exe", 30000, 22000))
        self.assertEqual([s.title for s in rep.slow], ["big.exe", "small.exe"])

    def test_single_occurrence_verdict(self):
        rep = one_scan(F.boot_xml(), F.slow_xml(102, "a.sys", 9000, 8000))
        self.assertEqual(rep.slow[0].verdict, "单次记录")
        self.assertEqual(rep.slow[0].verdict_token, "dim")

    def test_repeat_verdict_token_is_warn_not_crit(self):
        # 两次各拖 15 秒还没到「很慢」的门槛，判定列不该涂成红色
        rep = one_scan(F.boot_xml(when="2019-08-29T10:51:02Z"),
                       F.boot_xml(when="2019-08-28T10:51:02Z"),
                       F.slow_xml(101, "a.exe", 20632, 16332,
                                  when="2019-08-29T10:51:02Z"),
                       F.slow_xml(101, "a.exe", 17000, 14000,
                                  when="2019-08-28T10:51:02Z"))
        self.assertEqual(rep.slow[0].verdict_token, "warn")

    def test_orphan_slow_is_counted(self):
        # 慢项距离任何一次开机都超过配对窗口（100 事件被日志滚动掉了）
        rep = one_scan(F.boot_xml(when="2019-08-29T10:51:02Z"),
                       F.slow_xml(101, "a.exe", 9000, 8000,
                                  when="2019-06-01T10:51:02Z"))
        self.assertEqual(rep.orphan_slow, 1)
        self.assertTrue(any("配不到" in p for p in rep.problems))

    def test_slow_without_any_boot_record_still_listed(self):
        rep = one_scan(F.slow_xml(101, "a.exe", 9000, 8000))
        self.assertEqual(rep.count, 0)
        self.assertEqual(len(rep.slow), 1)


class LevelTest(unittest.TestCase):
    def test_fast_boot_is_ok(self):
        rep = one_scan(F.boot_xml(boot_ms=22000, main_ms=14000, post_ms=8000))
        self.assertEqual(rep.level, "ok")
        self.assertEqual(rep.findings()[0].severity, "中")

    def test_slow_boot_is_warn(self):
        rep = one_scan(F.boot_xml(boot_ms=62000, main_ms=38000, post_ms=24000))
        self.assertEqual(rep.level, "warn")

    def test_very_slow_boot_is_crit(self):
        rep = one_scan(F.boot_xml(boot_ms=100000, main_ms=70000, post_ms=30000))
        self.assertEqual(rep.level, "crit")
        self.assertEqual(rep.findings()[0].severity, "临界")

    def test_slow_post_boot_alone_is_warn(self):
        # 进系统前很快、桌面出来后很慢：这是启动项问题，不是驱动问题
        rep = one_scan(F.boot_xml(boot_ms=40000, main_ms=6000, post_ms=34000))
        self.assertEqual(rep.level, "warn")

    def test_single_slow_item_does_not_make_machine_crit(self):
        rep = one_scan(F.boot_xml(boot_ms=62000, main_ms=38000, post_ms=24000),
                       F.slow_xml(101, "a.exe", 20632, 16332))
        self.assertEqual(rep.level, "warn")

    def test_no_records_is_none(self):
        rep = one_scan()
        self.assertEqual(rep.level, "none")


class HeadlineTest(unittest.TestCase):
    def test_headline_mentions_both_halves(self):
        rep = one_scan(F.boot_xml(boot_ms=62000, main_ms=38000, post_ms=24000))
        text = rep.headline
        self.assertIn("最近一次开机", text)
        self.assertIn("进系统前", text)
        self.assertIn("桌面出来后", text)

    def test_short_line_is_one_line(self):
        rep = one_scan(F.boot_xml())
        self.assertNotIn("\n", rep.short_line)
        self.assertIn("最近开机", rep.short_line)

    def test_baseline_needs_enough_samples(self):
        # 只有一次记录时没有基线，别拿自己跟自己比
        rep = one_scan(F.boot_xml(boot_ms=62000))
        self.assertEqual(rep.baseline_ms, 62000)
        rep2 = one_scan(*[F.boot_xml(boot_ms=40000 + i * 1000,
                                     when="2019-08-2%dT10:51:02Z" % (9 - i))
                          for i in range(4)])
        self.assertNotEqual(rep2.baseline_ms, rep2.latest.boot_ms)

    def test_advice_points_at_worst_phase(self):
        rep = F.make_scan()
        ph = rep.worst_phase
        self.assertIsNotNone(ph)
        self.assertTrue(any(ph.label in a for a in rep.advice))

    def test_advice_capped(self):
        self.assertLessEqual(len(F.make_scan().advice), 5)

    def test_empty_scan_headline_is_actionable(self):
        rep = boot.BootScan(error="通道里没有记录", admin=True)
        self.assertIn("通道里没有记录", rep.headline)
        self.assertEqual(rep.advice, [])

    def test_error_finding_mentions_admin(self):
        rep = boot.BootScan(admin=False,
                            error="读不到开机诊断记录：当前不是管理员")
        f = rep.findings()[0]
        self.assertEqual(f.severity, "高")
        self.assertTrue(any("管理员" in a for a in f.advice))


class MiscTest(unittest.TestCase):
    def test_shutdown(self):
        rep = one_scan(F.shutdown_xml(ms=25058))
        self.assertEqual(len(rep.misc), 1)
        m = rep.misc[0]
        self.assertEqual(m.kind, "关机")
        self.assertEqual(m.ms, 25058)
        self.assertEqual(m.ms_text, "25.1 秒")

    def test_sleep_splits_suspend_and_resume(self):
        rep = one_scan(F.sleep_xml(suspend_ms=4000, resume_ms=12000))
        m = rep.misc[0]
        self.assertEqual(m.kind, "睡眠")
        self.assertEqual(m.ms, 16000)
        self.assertIn("挂起 4.0 秒", m.detail)
        self.assertIn("恢复 12.0 秒", m.detail)

    def test_shutdown_detail_rows(self):
        rep = one_scan(F.shutdown_xml(),
                       F.slow_xml(201, "soffice.bin", 18200, 16100,
                                  friendly="OpenOffice.org"))
        detail = [m for m in rep.misc if m.sub == "应用拖时间"]
        self.assertEqual(len(detail), 1)
        self.assertEqual(detail[0].detail, "OpenOffice.org")

    def test_slow_shutdown_raises_finding(self):
        rep = one_scan(F.boot_xml(), F.shutdown_xml(ms=65000))
        topics = [f.topic for f in rep.findings()]
        self.assertIn("关机", topics)

    def test_misc_sorted_newest_first(self):
        rep = one_scan(F.shutdown_xml(when="2019-08-29T10:45:00Z"),
                       F.sleep_xml(when="2019-08-29T09:00:00Z"))
        self.assertGreaterEqual(rep.misc[0].when, rep.misc[1].when)


class RowTest(unittest.TestCase):
    def test_boot_rows_compare_with_baseline(self):
        rep = F.make_scan(count=6, boot_ms=62000)
        rows = boot.boot_rows(rep)
        self.assertEqual(len(rows), rep.count)
        last = rows[0]
        self.assertTrue(last[7].startswith(("比基线慢", "比基线快", "持平")))
        self.assertIs(last[8], rep.records[0])

    def test_phase_rows_share_only_for_boot_time_parts(self):
        rows = boot.phase_rows(F.make_scan())
        loader = [r for r in rows if r[0] == "引导加载"][0]
        self.assertEqual(loader[2], "—")          # 在 BootTime 之外，没有占比
        post = [r for r in rows if r[0] == "登录后后台"][0]
        self.assertTrue(post[2].endswith("%"))    # 登录后后台是 BootTime 的一部分
        kernel = [r for r in rows if r[0] == "内核初始化"][0]
        self.assertTrue(kernel[2].endswith("%"))

    def test_phase_rows_small_diff_not_colored(self):
        # 几十毫秒的差涂红涂绿都是噪声
        rep = F.make_scan()
        for r in boot.phase_rows(rep):
            if r[3].endswith("毫秒") and abs(int(r[3].split(" ")[0])) < 500:
                self.assertEqual(r[5], "")

    def test_slow_rows_shape(self):
        rows = boot.slow_rows(F.make_scan())
        self.assertTrue(rows)
        self.assertEqual(rows[0][0], "应用")
        self.assertIs(rows[0][8], F.make_scan().slow[0].__class__ and rows[0][8])

    def test_misc_rows_shape(self):
        rows = boot.misc_rows(F.make_scan())
        self.assertTrue(rows)
        self.assertIn(rows[0][0], ("关机", "睡眠"))


class FastStartupTest(unittest.TestCase):
    def test_registry_flag_is_wired(self):
        # 真机上这个值只读一次，这里只验证「读到布尔 / 读不到 None」两种都能走通
        val = boot.fast_startup_enabled()
        self.assertIn(val, (True, False, None))

    def test_flag_adds_advice(self):
        orig = boot.fast_startup_enabled
        boot.fast_startup_enabled = lambda: True
        try:
            rep = boot.scan(events=F.make_events(count=3), with_registry=True)
            self.assertTrue(rep.fast_startup)
            self.assertTrue(any("快速启动" in a for a in rep.advice))
        finally:
            boot.fast_startup_enabled = orig


class ChannelTest(unittest.TestCase):
    def test_channel_name_matches_event_source(self):
        self.assertEqual(boot.CHANNEL,
                         "Microsoft-Windows-Diagnostics-Performance/Operational")

    def test_admin_state_recorded(self):
        rep = boot.scan(events=[], with_registry=False)
        self.assertIsInstance(rep.admin, bool)

    def test_empty_channel_says_so(self):
        rep = boot.scan(events=[], with_registry=False)
        self.assertTrue(rep.error)
        self.assertEqual(rep.count, 0)

    def test_error_text_for_denied(self):
        msg = boot._error_text(RuntimeError("访问被拒绝：读取该通道需要管理员权限"))
        self.assertIn("管理员", msg)

    def test_error_text_for_missing_channel(self):
        msg = boot._error_text(RuntimeError("通道或文件不存在：xyz"))
        self.assertIn("未启用", msg)


if __name__ == "__main__":
    unittest.main()
