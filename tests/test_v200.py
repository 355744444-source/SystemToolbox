# -*- coding: utf-8 -*-
"""v2.0 全面体检编排层单测。

只测**能离线判定**的东西：归一规则、排序口径、失败隔离、结构自洽。
真机数据一律不假设 —— 本机可能 9 项全绿，也可能一堆 crit。
"""
from __future__ import annotations

import io
import json
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app.core import health  # noqa: E402


class WorstTest(unittest.TestCase):
    def test_picks_most_severe(self):
        self.assertEqual(health.worst(["ok", "warn", "info"]), "warn")
        self.assertEqual(health.worst(["info", "crit", "warn"]), "crit")
        self.assertEqual(health.worst(["ok"]), "ok")

    def test_empty_is_ok(self):
        self.assertEqual(health.worst([]), "ok")

    def test_unknown_level_is_ignored(self):
        # 拼错的等级不能把结论顶成严重，也不能让 worst 抛错
        self.assertEqual(health.worst(["bogus", "ok"]), "ok")


class SectionTest(unittest.TestCase):
    def test_level_derived_from_items(self):
        s = health.Section(key="x", items=[
            health.Item(severity="info"), health.Item(severity="warn")])
        self.assertEqual(s.level, "warn")
        self.assertEqual(s.warn, 1)
        self.assertEqual(s.crit, 0)

    def test_empty_section_is_ok(self):
        self.assertEqual(health.Section(key="x").level, "ok")

    def test_module_verdict_wins_over_items(self):
        # 模块自己有判定时不许被折叠过程改语义
        s = health.Section(key="x", lv="crit",
                           items=[health.Item(severity="info")])
        self.assertEqual(s.level, "crit")

    def test_error_is_warn_not_crit(self):
        # 检查跑失败 ≠ 机器坏了，但也不能算正常
        s = health.Section(key="x", error="boom")
        self.assertEqual(s.level, "warn")

    def test_error_beats_module_verdict(self):
        s = health.Section(key="x", lv="ok", error="boom")
        self.assertEqual(s.level, "warn")


class ReportTest(unittest.TestCase):
    def _rep(self, levels):
        return health.HealthReport(sections=[
            health.Section(key="s%d" % i, lv=lv) for i, lv in enumerate(levels)])

    def test_score_all_ok(self):
        self.assertEqual(self._rep(["ok"] * 9).score, 100)

    def test_score_is_per_section_average(self):
        # 一个板块再糟也只扣自己那一份，不是按问题条数线性扣
        rep = self._rep(["crit", "ok", "ok", "ok", "ok", "ok", "ok", "ok", "ok"])
        self.assertEqual(rep.score, int(round((60 + 100 * 8) / 9)))

    def test_score_all_crit_is_sixty(self):
        self.assertEqual(self._rep(["crit"] * 9).score, 60)

    def test_score_empty_report(self):
        self.assertEqual(health.HealthReport().score, 0)

    def test_headline_prefers_crit_over_earlier_warn(self):
        rep = health.HealthReport(sections=[
            health.Section(key="a", lv="warn", headline="小事"),
            health.Section(key="b", lv="crit", headline="大事")])
        self.assertEqual(rep.headline, "大事")

    def test_headline_all_ok(self):
        rep = self._rep(["ok"] * 3)
        self.assertTrue(rep.headline)

    def test_problems_sorted_and_filtered(self):
        rep = health.HealthReport(sections=[
            health.Section(key="a", lv="crit", items=[
                health.Item(severity="warn", title="w"),
                health.Item(severity="crit", title="c"),
                health.Item(severity="info", title="i")])])
        titles = [i.title for i in rep.problems()]
        self.assertEqual(titles, ["c", "w"])

    def test_problems_limit(self):
        rep = health.HealthReport(sections=[
            health.Section(key="a", items=[health.Item(severity="crit",
                                                       title=str(i))
                                           for i in range(5)])])
        self.assertEqual(len(rep.problems(limit=2)), 2)

    def test_section_lookup(self):
        rep = health.HealthReport(sections=[health.Section(key="smart")])
        self.assertIsNotNone(rep.section("smart"))
        self.assertIsNone(rep.section("nope"))

    def test_to_dict_is_json_serializable(self):
        rep = health.HealthReport(sections=[health.Section(
            key="a", title="A", counts={"n": 1},
            items=[health.Item(severity="crit", title="t",
                               advice=["x"])])])
        text = json.dumps(rep.to_dict(), ensure_ascii=False)
        back = json.loads(text)
        self.assertEqual(back["sections"][0]["items"][0]["advice"], ["x"])
        self.assertEqual(back["crit"], 1)


class ItemFoldTest(unittest.TestCase):
    """各模块结论类字段名不同，折叠必须都能吃。"""

    class _Other:
        name = "防火墙"
        level = "crit"
        detail = "三个配置文件都关了"
        where = "控制面板 → Windows Defender 防火墙"
        value = "关闭"

    class _Upgrade:
        name = "TPM"
        verdict = "warn"
        note = "需在 BIOS 开启"

    class _Plain:
        severity = "warn"
        title = "标题"
        detail = "说明"
        advice = ["单条建议"]

    def test_security_style_fields(self):
        it = health._item(self._Other(), "security")
        self.assertEqual((it.severity, it.title), ("crit", "防火墙"))
        self.assertEqual(it.advice, ["控制面板 → Windows Defender 防火墙"])

    def test_verdict_field_used_for_level(self):
        it = health._item(self._Upgrade(), "upgrade")
        self.assertEqual(it.severity, "warn")

    def test_advice_as_list(self):
        it = health._item(self._Plain(), "x")
        self.assertEqual(it.advice, ["单条建议"])

    def test_advice_str_becomes_list(self):
        obj = self._Plain()
        obj.advice = "一条建议"
        self.assertEqual(health._item(obj, "x").advice, ["一条建议"])

    def test_unknown_level_falls_back_to_info(self):
        obj = self._Plain()
        obj.severity = "严重"
        self.assertEqual(health._item(obj, "x").severity, "info")


class RunTest(unittest.TestCase):
    """用假探针测编排本身：顺序、失败隔离、取消、耗时。

    探针是 (key, title, 取数, 归一) 四元组 —— 只有取数那一半会真去碰系统。
    """

    def setUp(self):
        self._probes, self._keys, self._fold = (
            health.PROBES, health.KEYS, health.FOLD)
        self.ran = []

    def tearDown(self):
        health.PROBES, health.KEYS = self._probes, self._keys
        health.FOLD = self._fold

    def _pair(self, key, scan=None, sec=None):
        def _scan(_ctx):
            self.ran.append(key)
            return scan if scan is not None else key

        def _sec(raw, fast):
            if sec is not None:
                return sec(raw, fast)
            return health.Section(key=key, title="板块" + key)

        return (key, "板块" + key, _scan, _sec)

    def _install(self, pairs):
        health.PROBES = tuple(pairs)
        health.KEYS = tuple(p[0] for p in pairs)
        health.FOLD = {p[0]: p[3] for p in pairs}

    def test_default_keys_are_nine(self):
        # 与体检页的九个页签一一对应；加页签时这里要跟着改，别让报告漏项
        self.assertEqual(len(health.KEYS), 9)
        self.assertIn("dumpcfg", health.KEYS)

    def test_keys_order_follows_probes(self):
        self._install([self._pair("b"), self._pair("a")])
        rep = health.run(keys=["a", "b"])
        # 传入顺序不该决定报告顺序：报告的板块顺序必须和页签一致
        self.assertEqual([s.key for s in rep.sections], ["b", "a"])

    def test_unknown_key_raises(self):
        with self.assertRaises(ValueError):
            health.run(keys=["nope"])

    def test_scan_failure_does_not_abort_rest(self):
        def boom(_ctx):
            raise RuntimeError("模块炸了")

        self._install([("a", "板块a", boom, self._pair("a")[3]),
                       self._pair("b")])
        rep = health.run()
        self.assertEqual(len(rep.sections), 2)
        self.assertIn("模块炸了", rep.sections[0].error)
        self.assertEqual(rep.sections[1].error, "")
        self.assertEqual(rep.sections[0].level, "warn")

    def test_fold_failure_is_caught_too(self):
        # 归一那半也会炸（模块对象字段改名之类），同样不能拖垮整份报告
        def bad_sec(_raw, _fast):
            raise KeyError("headline")

        self._install([("a", "板块a", lambda _c: 1, bad_sec),
                       self._pair("b")])
        rep = health.run()
        self.assertIn("KeyError", rep.sections[0].error)
        self.assertEqual(rep.sections[1].error, "")

    def test_stop_flag_marks_remaining_without_running(self):
        self._install([self._pair("a"), self._pair("b")])
        rep = health.run(stop_flag=lambda: bool(self.ran))
        self.assertEqual(self.ran, ["a"])       # 第二项没跑
        self.assertIn("取消", rep.sections[1].error)

    def test_cost_is_measured(self):
        self._install([self._pair("a")])
        rep = health.run()
        self.assertGreaterEqual(rep.cost_ms, 0)
        self.assertGreaterEqual(rep.sections[0].cost_ms, 0)

    def test_tick_reports_progress(self):
        calls = []
        self._install([self._pair("a"), self._pair("b")])
        health.run(tick=lambda d, t, m: calls.append((d, t)))
        self.assertEqual(calls[0], (0, 2))
        self.assertEqual(calls[-1], (2, 2))

    def test_fast_flag_reaches_scan_and_sec(self):
        seen = {}

        def scan(ctx):
            seen["scan"] = ctx.fast
            return 1

        def sec(_raw, fast):
            seen["sec"] = fast
            return health.Section(key="a")

        self._install([("a", "板块a", scan, sec)])
        health.run(fast=True)
        self.assertEqual(seen, {"scan": True, "sec": True})


class FoldTest(unittest.TestCase):
    """`fold()` 是界面出报告的入口：九个 load 已经跑完，不能再扫一遍。"""

    def setUp(self):
        self._probes, self._fold = health.PROBES, health.FOLD
        self.scanned = []

    def tearDown(self):
        health.PROBES, health.FOLD = self._probes, self._fold

    def _install(self, keys):
        def _scan(_ctx):
            self.scanned.append(1)
            return "raw"

        table = [(k, "板块" + k, _scan,
                  (lambda raw, fast, k=k: health.Section(key=k, title="板块" + k)))
                 for k in keys]
        health.PROBES = tuple(table)
        health.FOLD = {p[0]: p[3] for p in table}

    def test_fold_never_scans(self):
        self._install(["a", "b"])
        health.fold({"a": object(), "b": object()})
        self.assertEqual(self.scanned, [])

    def test_fold_uses_given_raws(self):
        self._install(["a", "b"])
        rep = health.fold({"a": object(), "b": object()})
        self.assertEqual([s.key for s in rep.sections], ["a", "b"])
        self.assertEqual([s.error for s in rep.sections], ["", ""])

    def test_missing_key_is_reported_not_ignored(self):
        # 少跑一项就说「未运行」，不能悄悄当正常
        self._install(["a", "b"])
        rep = health.fold({"a": object()})
        self.assertEqual(rep.sections[1].error, "本次未运行")
        self.assertEqual(rep.sections[1].level, "warn")

    def test_none_raw_counts_as_missing(self):
        self._install(["a"])
        self.assertEqual(health.fold({"a": None}).sections[0].error, "本次未运行")

    def test_fold_counts_passthrough(self):
        self._install(["a"])
        self.assertEqual(health.fold({"a": 1}, cost_ms=1234).cost_ms, 1234)

    def test_fold_fast_flag_propagates(self):
        seen = {}
        health.PROBES = (("a", "板块a", lambda _c: 1,
                          lambda raw, fast: seen.update(fast=fast)
                          or health.Section(key="a")),)
        health.FOLD = {"a": health.PROBES[0][3]}
        health.fold({"a": 1}, fast=True)
        self.assertTrue(seen["fast"])

    def test_parts_dataclasses_exist(self):
        # 界面要按这两份原料把原始报告递进来，字段名就是约定
        dp = health.DevParts(["d"], ["v"], "stats")
        self.assertEqual((dp.devices, dp.drivers, dp.stats), (["d"], ["v"], "stats"))
        mp = health.DumpParts("snap", [1])
        self.assertEqual((mp.snap, mp.findings), ("snap", [1]))


class ReportExportTest(unittest.TestCase):
    """体检报告是「交付物」，不是调试输出：内容缺一块就等于白跑一趟。"""

    def setUp(self):
        import tempfile
        self.tmp = tempfile.mkdtemp(prefix="cls_rep_")
        self.rep = health.HealthReport(sections=[
            health.Section(key="security", title="安全基线", lv="crit",
                           headline="发现 1 项风险：防火墙",
                           counts={"评分": 46, "风险": 1},
                           items=[health.Item(severity="crit", title="防火墙",
                                              detail="三个配置文件都关了",
                                              advice=["Windows 安全中心 › 防火墙"])]),
            health.Section(key="smart", title="磁盘 SMART", lv="ok",
                           headline="1 块盘健康", counts={"磁盘": 1}),
        ], cost_ms=5200)

    def test_html_has_score_headline_and_advice(self):
        from app.core import report
        path = os.path.join(self.tmp, "h.html")
        report.export_health_html(path, self.rep)
        html = io.open(path, encoding="utf-8").read()
        self.assertIn("健康评分", html)
        self.assertIn(str(self.rep.score), html)
        self.assertIn("发现 1 项风险：防火墙", html)
        self.assertIn("Windows 安全中心", html)      # 建议动作必须落到报告里
        self.assertIn("安全基线", html)
        self.assertIn("5.2 秒", html)                # 耗时不显示等于没说

    def test_html_shows_section_error(self):
        from app.core import report
        rep = health.HealthReport(sections=[
            health.Section(key="smart", title="磁盘 SMART",
                           error="RuntimeError: boom")])
        path = os.path.join(self.tmp, "e.html")
        report.export_health_html(path, rep)
        html = io.open(path, encoding="utf-8").read()
        self.assertIn("本项未完成", html)
        self.assertIn("boom", html)

    def test_xlsx_has_three_sheets(self):
        from openpyxl import load_workbook

        from app.core import report
        path = os.path.join(self.tmp, "h.xlsx")
        report.export_health_xlsx(path, self.rep)
        wb = load_workbook(path)
        self.assertEqual(wb.sheetnames, ["体检结论", "问题清单", "指标"])
        ws = wb["问题清单"]
        self.assertEqual(ws.max_row, 2)               # 表头 + 1 条问题
        self.assertEqual(ws.cell(row=2, column=1).value, "严重")
        self.assertEqual(wb["体检结论"].max_row, 3)    # 表头 + 2 个板块

    def test_empty_report_still_writes(self):
        # 一项都没跑完也要能出文件，不能抛异常让用户以为是导出的锅
        from app.core import report
        path = os.path.join(self.tmp, "empty.html")
        report.export_health_html(path, health.HealthReport())
        self.assertTrue(os.path.getsize(path) > 0)


class CliSayTest(unittest.TestCase):
    """打包版的 `--scan` 挂在 GBK 控制台上，`›`(U+203A) 编不出来会让整份
    结果白跑（实测曾因此崩溃）。控制台只是顺手看的，落盘才是正本。"""

    def setUp(self):
        import main
        self.entry = main
        self._stdout = sys.stdout

    def tearDown(self):
        sys.stdout = self._stdout

    def test_gbk_console_does_not_raise(self):
        buf = io.BytesIO()
        sys.stdout = io.TextIOWrapper(buf, encoding="gbk", errors="strict")
        try:
            self.entry._say("设备管理器 › 高级 → 环境变量")
        finally:
            sys.stdout.flush()
        self.assertTrue(buf.getvalue())

    def test_no_console_is_noop(self):
        sys.stdout = None
        self.entry._say("随便写点什么")      # windowed 构建就是这个场景

    def test_ascii_passes_through(self):
        buf = io.BytesIO()
        sys.stdout = io.TextIOWrapper(buf, encoding="utf-8", errors="strict")
        self.entry._say("plain text")
        sys.stdout.flush()
        self.assertIn("plain text", buf.getvalue().decode("utf-8"))


class StructureTest(unittest.TestCase):
    def test_probe_keys_unique(self):
        keys = [k for k, _t, _s, _f in health.PROBES]
        self.assertEqual(len(keys), len(set(keys)))

    def test_every_probe_callable(self):
        for key, title, scan, sec in health.PROBES:
            self.assertTrue(key and title, key)
            self.assertTrue(callable(scan), key)
            self.assertTrue(callable(sec), key)

    def test_fold_and_titles_cover_keys(self):
        # 三张表都由 PROBES 派生，漏一个就会让 fold 静默丢板块
        self.assertEqual(set(health.FOLD), set(health.KEYS))
        self.assertEqual(set(health.TITLES), set(health.KEYS))

    def test_level_cn_covers_every_level(self):
        for lv in health.LEVELS:
            self.assertIn(lv, health.LEVEL_CN)

    def test_deduct_covers_every_level(self):
        for lv in health.LEVELS:
            self.assertIn(lv, health.DEDUCT)


if __name__ == "__main__":
    unittest.main(verbosity=2)
