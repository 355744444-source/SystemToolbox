# -*- coding: utf-8 -*-
"""系统体检四块核心（devices / upgrade / perf / security）单测。

原则：只测**能离线判定**的东西 —— 解析、判定表、统计口径、结构自洽。
真机相关的部分只做「形状检查」，不假设一定有几台设备、几个风险项。
"""
from __future__ import annotations

import os
import sys
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app.core import devices as dv                           # noqa: E402
from app.core import perf, security, upgrade as up           # noqa: E402


class TestDevText(unittest.TestCase):
    """INF 字符串引用是最容易翻车的地方：`@input.inf,%token%;文本`。"""

    def test_inf_with_fallback(self):
        self.assertEqual(
            dv.dev_text("@usb.inf,%usb\\composite.devicedesc%;USB Composite Device"),
            "USB Composite Device")

    def test_inf_without_fallback_uses_strings_section(self):
        # 没有分号回退时要去 INF 的 [Strings] 里查；查不到就返回空，
        # 绝不能退化成「取最后一段」——那会得到 "oem99.inf" 当设备名
        got = dv.dev_text("@oem_does_not_exist.inf,%nope%;")
        self.assertEqual(got, "")

    def test_plain_text_passthrough(self):
        self.assertEqual(dv.dev_text("Realtek Gaming 2.5GbE"), "Realtek Gaming 2.5GbE")
        self.assertEqual(dv.dev_text(None), "")
        self.assertEqual(dv.dev_text("  "), "")


class TestStateMachine(unittest.TestCase):
    def _st(self, flags=0, problem=0, ok=True):
        d = dv.Device(name="x")
        dv._apply_state(d, flags, problem, ok)
        return d

    def test_problem_zero_is_ok_even_without_started_flag(self):
        # Motherboard resources / System timer 这类资源节点不报 DN_STARTED，
        # 但设备管理器里没有感叹号 —— 不能当故障
        d = self._st(flags=0x1800000, problem=0)
        self.assertEqual(d.level, "ok")
        self.assertEqual(d.status, "正常")

    def test_disabled(self):
        d = self._st(flags=dv.DN_DISABLED, problem=22)
        self.assertEqual(d.level, "warn")
        self.assertEqual(d.status, "已被禁用")

    def test_absent_codes(self):
        for code in dv.ABSENT_CODES:
            d = self._st(problem=code)
            self.assertFalse(d.present)
            self.assertEqual(d.level, "info")

    def test_real_fault_is_crit(self):
        for code in (10, 28, 31, 39, 43):
            d = self._st(problem=code)
            self.assertEqual(d.level, "crit", code)
            self.assertIn(str(code), d.hint)

    def test_reboot_is_not_a_fault(self):
        for code in dv.REBOOT_CODES:
            self.assertEqual(self._st(problem=code).level, "info", code)

    def test_removed_node(self):
        d = self._st(problem=0, ok=False)
        self.assertEqual(d.status, "已移除")
        self.assertFalse(d.present)

    def test_every_problem_has_text(self):
        for code, (cn, hint) in dv.PROBLEM_CN.items():
            self.assertTrue(cn and hint, code)
        self.assertGreaterEqual(len(dv.PROBLEM_CN), 40)


class TestDeviceScan(unittest.TestCase):
    def test_shapes(self):
        items = dv.devices()
        self.assertIsInstance(items, list)
        for d in items[:40]:
            self.assertTrue(d.name, "设备名不能为空")
            self.assertIn("\\", d.instance)
            self.assertIn(d.level, ("ok", "info", "warn", "crit"))

    def test_sorted_problems_first(self):
        items = dv.devices()
        rank = {"crit": 0, "warn": 1, "info": 2, "ok": 3}
        ranks = [rank[d.level] for d in items]
        self.assertEqual(ranks, sorted(ranks), "问题设备应排在前面")

    def test_problem_devices_excludes_absent(self):
        for d in dv.problem_devices():
            self.assertTrue(d.present)
            self.assertIn(d.level, ("crit", "warn"))


class TestDrivers(unittest.TestCase):
    def test_dedup_and_fields(self):
        drvs = dv.drivers()
        self.assertIsInstance(drvs, list)
        keys = [(d.desc.lower(), d.version, d.inf.lower()) for d in drvs]
        self.assertEqual(len(keys), len(set(keys)), "同一 INF 不应重复出现")
        for d in drvs[:40]:
            self.assertTrue(d.desc)
            self.assertGreaterEqual(d.instances, 1)

    def test_date_parse(self):
        self.assertGreater(dv._date_ts("6-21-2006"), 0)
        self.assertGreater(dv._date_ts("2026-10-10"), 0)
        self.assertEqual(dv._date_ts(""), 0.0)
        self.assertEqual(dv._date_ts("不是日期"), 0.0)

    def test_third_party_flag(self):
        for d in dv.drivers():
            if d.provider:
                self.assertEqual(d.third_party,
                                 "microsoft" not in d.provider.lower(), d.provider)

    def test_link_devices_counts(self):
        items = dv.devices()
        drvs = dv.drivers()
        dv.link_devices(items, drvs)
        self.assertTrue(any(d.devices > 0 for d in drvs),
                        "至少应该有关联到设备的驱动")

    def test_stats_consistency(self):
        st = dv.stats()
        self.assertGreater(st.devices, 0)
        self.assertGreater(st.drivers, 0)
        self.assertEqual(len(st.rows), 3)
        self.assertGreaterEqual(st.removed, 0)
        self.assertLessEqual(st.problems + st.disabled + st.absent + st.removed,
                             st.devices)


class TestCpuGeneration(unittest.TestCase):
    CASES = [
        ("12th Gen Intel(R) Core(TM) i7-12700K", "Intel", 12),
        ("Intel(R) Core(TM) i5-8250U CPU @ 1.60GHz", "Intel", 8),
        ("Intel(R) Core(TM) i7-9700 CPU @ 3.00GHz", "Intel", 9),
        ("Intel(R) Core(TM) i5-10210U CPU @ 1.60GHz", "Intel", 10),
        ("AMD Ryzen 5 3600 6-Core Processor", "AMD", 3),
        ("AMD Ryzen 7 5800X 8-Core Processor", "AMD", 5),
        ("AMD Ryzen 5 2600 Six-Core Processor", "AMD", 2),
        ("AMD Ryzen 9 7945HX with Radeon Graphics", "AMD", 7),
        ("Intel(R) Xeon(R) Silver 4210R CPU @ 2.40GHz", "Intel", 0),
        ("AMD Athlon 3000G", "AMD", 0),
        ("", "", 0),
    ]

    def test_vendor_and_generation(self):
        for name, vendor, gen in self.CASES:
            got_v, got_g, _text = up.cpu_generation(name)
            self.assertEqual((got_v, got_g), (vendor, gen), name)

    def test_amd_text_uses_series_not_model(self):
        # Ryzen 7 7735HS 属 7000 系，写成「Ryzen 7735 系」会误导
        self.assertIn("7000", up.cpu_generation("AMD Ryzen 7 7735HS")[2])


class TestUpgradeCheck(unittest.TestCase):
    def test_report(self):
        rep = up.check()
        self.assertGreaterEqual(len(rep.items), 7)
        for i in rep.items:
            self.assertIn(i.verdict, ("ok", "warn", "crit", "info"))
            self.assertTrue(i.name and i.current and i.required)
        self.assertEqual(rep.ready, not rep.blockers)
        self.assertTrue(rep.headline)
        self.assertGreater(rep.ram_gb, 0)
        self.assertGreater(rep.disk_total_gb, 0)

    def test_cpu_item_thresholds(self):
        # 4 代 Intel 必须判不满足，8 代必须判满足
        old = up._cpu_item("Intel(R) Core(TM) i7-4770 CPU @ 3.40GHz", 3400, 8)
        new = up._cpu_item("Intel(R) Core(TM) i7-8700 CPU @ 3.20GHz", 3200, 12)
        self.assertEqual(old.verdict, "crit")
        self.assertEqual(new.verdict, "ok")

    def test_low_ram_and_disk_are_crit_by_rule(self):
        self.assertGreaterEqual(up.MIN_RAM_GB, 4)
        self.assertGreaterEqual(up.MIN_DISK_GB, 64)


class TestPerf(unittest.TestCase):
    def test_memory_sane(self):
        pct, used, total = perf.memory()
        self.assertGreater(total, 0)
        self.assertLessEqual(used, total + 0.01)
        self.assertGreaterEqual(pct, 0)
        self.assertLessEqual(pct, 100.5)

    def test_if_octets_monotonic_shape(self):
        rx, tx = perf.if_octets()
        self.assertGreaterEqual(rx, 0)
        self.assertGreaterEqual(tx, 0)

    def test_sampler_flow(self):
        s = perf.Sampler()
        try:
            s.prime()
            time.sleep(0.25)
            pt = s.sample()
            self.assertGreater(pt.ts, 0)
            self.assertGreaterEqual(pt.cpu, 0)
            self.assertLessEqual(pt.cpu, 100)
            self.assertGreaterEqual(pt.mem, 0)
            self.assertLessEqual(pt.mem, 100)
            self.assertGreaterEqual(pt.net_in, 0)
            self.assertGreaterEqual(pt.disk, 0)
            self.assertLessEqual(pt.disk, 100)
        finally:
            s.close()

    def test_summarize(self):
        empty = perf.summarize([])
        self.assertEqual(empty["n"], 0)
        pts = [perf.Point(ts=1.0, cpu=10, mem=20, disk=5),
               perf.Point(ts=2.0, cpu=30, mem=40, disk=50)]
        sm = perf.summarize(pts)
        self.assertEqual(sm["n"], 2)
        self.assertAlmostEqual(sm["cpu_avg"], 20.0)
        self.assertAlmostEqual(sm["cpu_max"], 30.0)
        self.assertAlmostEqual(sm["disk_max"], 50.0)
        self.assertAlmostEqual(sm["secs"], 1.0)


class TestSecurity(unittest.TestCase):
    def test_product_state_decoding(self):
        ps = {"av": [{"name": "360 杀毒", "state": 0x061100},   # 启用且最新
                     {"name": "旧版杀软", "state": 0x000000}]}   # 未启用
        avs = security.active_av(ps)
        self.assertEqual(avs, ["360 杀毒"])

    def test_active_av_tolerates_garbage(self):
        self.assertEqual(security.active_av({}), [])
        self.assertEqual(security.active_av({"av": None}), [])
        self.assertEqual(security.active_av({"av": [{"state": "x"}]}), [])

    def test_scan_report(self):
        rep = security.scan()
        self.assertGreaterEqual(len(rep.rows), 8)
        for r in rep.rows:
            self.assertIn(r.level, ("ok", "info", "warn", "crit"), r.name)
            self.assertTrue(r.name and r.value)
        self.assertGreaterEqual(rep.score, 0)
        self.assertLessEqual(rep.score, 100)
        self.assertTrue(rep.headline)
        self.assertEqual(rep.risks, [r for r in rep.rows if r.level == "crit"])

    def test_score_drops_with_risks(self):
        rep = security.Report(rows=[security.Finding("a", "x", "crit"),
                                    security.Finding("b", "y", "ok")])
        self.assertLess(rep.score, 100)
        self.assertEqual(len(rep.risks), 1)


if __name__ == "__main__":
    unittest.main()
