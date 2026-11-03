# -*- coding: utf-8 -*-
"""核心逻辑回归测试（stdlib unittest，不依赖 pytest）。

运行：python -m unittest discover -s tests -v
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app.core import analysis as ana  # noqa: E402
from app.core import evtx, knowledge as kb, minidump, report  # noqa: E402

FIXTURES = os.path.join(ROOT, "tests", "fixtures")


def _ev(eid, level=4, when=None, provider="Test", data=None, msg=""):
    return evtx.Event(time=when or datetime(2026, 8, 27, 12, 0, 0),
                      channel="System", provider=provider, event_id=eid,
                      level=level, task=0, opcode=0, record_id=1,
                      computer="PC", user="", process_id=0, thread_id=0,
                      data=data or {}, message=msg, raw_xml="")


class TestMinidump(unittest.TestCase):
    def test_user_mode_av(self):
        path = os.path.join(FIXTURES, "user_appcrash_av.dmp")
        rep = minidump.parse(path)
        self.assertEqual(rep.kind, "minidump")
        self.assertEqual(rep.exception_code, 0xC0000005)
        self.assertEqual(rep.exception_address, 0x7FF6A0173C58)
        self.assertEqual(rep.faulting_module.short_name, "VI.UI.exe")
        self.assertEqual(len(rep.modules), 4)
        self.assertEqual(rep.thread_count, 3)
        self.assertEqual(rep.process_id, 4321)
        self.assertEqual(rep.system.arch, 9)
        self.assertIn("19045", rep.system.version_text)
        self.assertTrue(any("exception of interest" in c for c in rep.comments))
        self.assertIn("0xC0000005", rep.key_summary)

    def test_user_mode_heap(self):
        rep = minidump.parse(os.path.join(FIXTURES, "user_appcrash_heap.dmp"))
        self.assertEqual(rep.exception_code, 0xC0000374)
        self.assertEqual(rep.faulting_module.short_name, "ntdll.dll")

    def test_kernel_whea(self):
        rep = minidump.parse(os.path.join(FIXTURES, "kernel_whea_124.dmp"))
        self.assertEqual(rep.kind, "kernel")
        self.assertEqual(rep.bugcheck.code, 0x124)
        self.assertEqual(len([p for p in rep.bugcheck.params if p]), 3)
        self.assertEqual(rep.system.cpus, 16)
        # 真机转储的 MinorVersion 装的是 build 号，不能当成 10.26100 展示
        self.assertEqual(rep.system.build, 26100)
        self.assertEqual(rep.system.version_text, "10.0.26100")
        self.assertEqual(rep.system.os_short, "Win11 24H2")

    def test_nt_version_from_build(self):
        self.assertEqual(minidump.nt_version_from_build(26100), (10, 0))
        self.assertEqual(minidump.nt_version_from_build(19045), (10, 0))
        self.assertEqual(minidump.nt_version_from_build(9600), (6, 3))
        self.assertEqual(minidump.nt_version_from_build(7601), (6, 1))

    def test_kernel_x86(self):
        rep = minidump.parse(os.path.join(FIXTURES, "kernel_x86_7e.dmp"))
        self.assertEqual(rep.bugcheck.code, 0x7E)
        self.assertEqual(rep.bugcheck.params[0], 0xC0000005)
        self.assertEqual(rep.system.arch, 0)

    def test_kernel_dpc(self):
        rep = minidump.parse(os.path.join(FIXTURES, "kernel_dpc_watchdog_133.dmp"))
        self.assertEqual(rep.bugcheck.code, 0x133)
        dana = ana.analyze_dump(rep)
        self.assertTrue(any("DPC" in f.title for f in dana.findings))
        self.assertEqual(rep.bugcheck.name_cn, "DPC 看门狗超时")

    def test_sniff_and_reject(self):
        self.assertEqual(minidump.sniff(os.path.join(FIXTURES, "kernel_whea_124.dmp")),
                         "kernel")
        tmp = os.path.join(tempfile.gettempdir(), "cls_bad.dmp")
        with open(tmp, "wb") as f:
            f.write(b"NOTADUMP" * 8)
        self.assertEqual(minidump.sniff(tmp), "")
        with self.assertRaises(ValueError):
            minidump.parse(tmp)


class TestKnowledge(unittest.TestCase):
    def test_bugcheck_lookup(self):
        self.assertEqual(kb.bugcheck_info(0x124).name, "WHEA_UNCORRECTABLE_ERROR")
        self.assertEqual(kb.bugcheck_info(0x000000D1).name_cn,
                         "驱动在高 IRQL 访问非法地址")
        # 带位标志的写法
        self.assertEqual(kb.bugcheck_info(0x1000007E).code, 0x7E)
        self.assertIsNone(kb.bugcheck_info(0))
        self.assertIsNone(kb.bugcheck_info(0xABCDFFFF))

    def test_bugcheck_table_quality(self):
        self.assertGreaterEqual(len(kb.BUGCHECKS), 45)
        for code, info in kb.BUGCHECKS.items():
            self.assertTrue(info.name, hex(code))
            self.assertTrue(info.name_cn, hex(code))
            self.assertTrue(info.advice, hex(code))

    def test_exception_lookup(self):
        self.assertEqual(kb.exception_info(0xC0000005).name, "ACCESS_VIOLATION")
        self.assertEqual(kb.exception_info(0xE06D7363).name, "MSVC_CPP_EXCEPTION")
        self.assertIsNone(kb.exception_info(0x12345678))

    def test_event_meta(self):
        self.assertEqual(kb.event_meta(41).topic, "掉电")
        self.assertEqual(kb.event_meta(1001).severity, "临界")
        self.assertIsNone(kb.event_meta(999999))


SAMPLE_XML = """<Event xmlns="http://schemas.microsoft.com/win/2004/08/events/event">
<System><Provider Name="Microsoft-Windows-Kernel-Power" Guid="{331c3b3a}"/>
<EventID>41</EventID><Version>6</Version><Level>1</Level><Task>63</Task>
<Opcode>0</Opcode><Keywords>0x8000400000000002</Keywords>
<TimeCreated SystemTime="2026-08-27T11:49:17.1234567Z"/>
<EventRecordID>98765</EventRecordID><Correlation/><Execution ProcessID="4" ThreadID="8"/>
<Channel>System</Channel><Computer>PC-AOI-01</Computer>
<Security UserID="S-1-5-18"/></System>
<EventData><Data Name="BugcheckCode">292</Data>
<Data Name="BugcheckParameter1">0x0</Data></EventData></Event>"""


class TestEvtxParse(unittest.TestCase):
    def test_parse_event_xml(self):
        ev = evtx.parse_event_xml(SAMPLE_XML)
        self.assertIsNotNone(ev)
        self.assertEqual(ev.event_id, 41)
        self.assertEqual(ev.level, 1)
        self.assertEqual(ev.level_name, "严重")
        self.assertEqual(ev.provider, "Microsoft-Windows-Kernel-Power")
        self.assertEqual(ev.computer, "PC-AOI-01")
        self.assertEqual(ev.record_id, 98765)
        self.assertEqual(ev.process_id, 4)
        self.assertEqual(ev.data["BugcheckCode"], "292")
        # XML 里是 UTC，解析后按本地时区呈现（与事件查看器一致），
        # 所以这里用"UTC→本地"换算后的值比对，保证换台机器也能过。
        expect = (datetime(2026, 8, 27, 11, 49, 17, tzinfo=timezone.utc)
                  .astimezone().replace(tzinfo=None))
        self.assertEqual(ev.time.replace(microsecond=0), expect)

    def test_parse_bad_xml(self):
        self.assertIsNone(evtx.parse_event_xml("<Event><broken>"))

    def test_build_xpath(self):
        self.assertEqual(evtx.build_xpath(), "*")
        start = datetime(2026, 8, 1, 0, 0)
        xp = evtx.build_xpath(start=start, max_level=3)
        self.assertIn("Level<=3", xp)
        self.assertIn("2026-08-01T00:00:00.000Z", xp)

    def test_level_color(self):
        from app import theme
        self.assertEqual(theme.level_color(1, theme.DARK), theme.DARK["crit"])
        self.assertEqual(theme.level_color(4, theme.DARK), theme.DARK["info"])


class TestAnalysisRules(unittest.TestCase):
    def test_bugcheck_with_power41(self):
        t = datetime(2026, 8, 27, 11, 49, 17)
        events = [
            _ev(1001, 4, t, "Microsoft-Windows-WER-SystemErrorReporting",
                {"param1": "292", "param2": "0x0", "param3": "0xfffff80012345678"}),
            _ev(41, 1, t + timedelta(seconds=40), "Microsoft-Windows-Kernel-Power",
                {"BugcheckCode": "292"}),
        ]
        res = ana.analyze(events)
        self.assertEqual(len(res.crash_times), 1)
        titles = [f.title for f in res.findings]
        self.assertTrue(any("蓝屏 0x00000124" in t for t in titles))
        top = res.findings[0]
        self.assertEqual(top.severity, "临界")
        self.assertIn("WHEA", top.title)
        # 41 与蓝屏同时发生，不应再单独算掉电
        self.assertFalse(any(f.topic == "掉电" and f.severity == "高" for f in res.findings))
        self.assertTrue(any("不单独计为掉电" in n for n in res.notes))
        self.assertLess(res.health, 90)

    def test_power41_alone_is_outage(self):
        t = datetime(2026, 8, 27, 9, 0, 0)
        events = [_ev(41, 1, t, "Microsoft-Windows-Kernel-Power", {"BugcheckCode": "0"}),
                  _ev(6008, 2, t + timedelta(seconds=30), "EventLog")]
        res = ana.analyze(events)
        self.assertTrue(any(f.topic == "掉电" and f.severity == "高" for f in res.findings))

    def test_disk_errors(self):
        t = datetime(2026, 8, 27, 9, 0, 0)
        events = [_ev(7, 2, t + timedelta(minutes=i), "disk", {"param1": "\\Device\\Harddisk0"})
                  for i in range(12)]
        res = ana.analyze(events)
        disk = [f for f in res.findings if f.topic == "磁盘"]
        self.assertTrue(disk)
        self.assertEqual(disk[0].severity, "高")
        self.assertEqual(disk[0].count, 12)

    def test_app_crash_module(self):
        t = datetime(2026, 8, 27, 9, 0, 0)
        events = [_ev(1000, 2, t, "Application Error",
                      {"param1": "VI.UI.exe", "param3": "halcon.dll",
                       "param5": "0xc0000005"})]
        res = ana.analyze(events)
        app = [f for f in res.findings if f.topic == "应用"]
        self.assertTrue(app)
        self.assertIn("halcon.dll", app[0].detail)

    def test_clean_log(self):
        events = [_ev(6005, 4, datetime(2026, 8, 27, 8, 0, 0), "EventLog")]
        res = ana.analyze(events)
        self.assertEqual(res.health, 100)
        self.assertIn("日志干净", res.headline)

    def test_health_never_zero(self):
        t = datetime(2026, 8, 27, 9, 0, 0)
        events = []
        for code in (0x124, 0x133, 0x1E):
            events.append(_ev(1001, 4, t, "WER", {"param1": str(code)}))
        for eid in (7, 51, 153, 129, 55, 18, 17, 7031, 7000, 1000, 1102):
            events.append(_ev(eid, 2, t, "p", {}))
        res = ana.analyze(events)
        self.assertGreaterEqual(res.health, 5)
        self.assertLessEqual(res.health, 40)


CDB_SAMPLE = """
Microsoft (R) Windows Debugger Version 10.0.19041.1 AMD64
Loading Dump File [C:////Windows////Minidump////082726-12345-01.dmp]
Kernel Bitmap Dump File: Kernel address space is available

*******************************************************************************
*                                                                             *
*                        Bugcheck Analysis                                    *
*                                                                             *
*******************************************************************************

DPC_WATCHDOG_VIOLATION (133)
The DPC watchdog detected a prolonged run time at an IRQL of DISPATCH_LEVEL
Arguments:
Arg1: 0000000000000001, The system cumulatively spent an extended period
Arg2: 0000000000001e00, The watchdog period
Arg3: fffff8004a2c3b18
Arg4: 0000000000000000

Debugging Details:
------------------

KEY_VALUES_STRING: 1

PROCESS_NAME:  System
BUGCHECK_CODE:  133
BUGCHECK_P1: 1
BUGCHECK_P2: 1e00
IMAGE_NAME:  storahci.sys
MODULE_NAME: storahci
FAULTING_MODULE: fffff8004a200000 storahci
FAILURE_BUCKET_ID:  0x133_ISR_storahci!AhciHwInterrupt

STACK_TEXT:
fffff800`4a2c3a10 fffff800`4a211234 ffffaf00`11223344 fffff800`4a2c3a60 : storahci!AhciHwInterrupt+0x1a4
fffff800`4a2c3a90 fffff800`12345678 ffffaf00`11223344 fffff800`4a2c3ae0 : nt!KiInterruptDispatch+0x1d4
fffff800`4a2c3b20 fffff800`87654321 00000000`00000000 00000000`00000000 : nt!KeAccumulateTicks+0x1c6


SYMBOL_NAME:  storahci!AhciHwInterrupt+1a4

FOLLOWUP_NAME:  MachineOwner
Probably caused by : storahci.sys ( storahci!AhciHwInterrupt )
"""


class TestDebuggerParse(unittest.TestCase):
    def test_parse_analyze_output(self):
        from app.core import debugger
        keys = debugger._parse_keys(CDB_SAMPLE)
        self.assertEqual(keys.get("BUGCHECK_CODE"), "133")
        self.assertEqual(keys.get("MODULE_NAME"), "storahci")
        self.assertEqual(keys.get("IMAGE_NAME"), "storahci.sys")
        self.assertEqual(keys.get("PROCESS_NAME"), "System")
        self.assertIn("0x133", keys.get("FAILURE_BUCKET_ID", ""))
        self.assertIn("storahci", debugger._parse_blamed(CDB_SAMPLE))
        stack = debugger._parse_stack(CDB_SAMPLE)
        self.assertTrue(stack)
        self.assertIn("storahci!AhciHwInterrupt", stack[0])
        self.assertTrue(any("nt!KiInterruptDispatch" in s for s in stack))

    def test_no_cdb_graceful(self):
        from app.core import debugger
        res = debugger.run(os.path.join(FIXTURES, "no_such_file.dmp"), timeout=5)
        self.assertFalse(res.ok)
        self.assertTrue(res.error)


class TestReport(unittest.TestCase):
    def setUp(self):
        t = datetime(2026, 8, 27, 11, 49, 17)
        self.events = [
            _ev(1001, 4, t, "WER", {"param1": "292"}),
            _ev(7, 2, t, "disk", {"param1": "\\Device\\Harddisk0"}),
            _ev(1000, 2, t, "Application Error", {"param1": "VI.UI.exe"}),
        ]
        self.res = ana.analyze(self.events)

    def test_log_html(self):
        with tempfile.TemporaryDirectory() as d:
            p = report.export_log_html(os.path.join(d, "r.html"), self.events, self.res,
                                       source="3a.evtx")
            self.assertTrue(os.path.getsize(p) > 3000)
            with open(p, encoding="utf-8") as fh:
                text = fh.read()
            self.assertIn("事件日志分析报告", text)
            self.assertIn("诊断结论与建议", text)
            self.assertIn("江苏振远智能科技", text)

    def test_log_xlsx(self):
        with tempfile.TemporaryDirectory() as d:
            p = report.export_log_xlsx(os.path.join(d, "r.xlsx"), self.events, self.res)
            self.assertTrue(os.path.getsize(p) > 4000)
            from openpyxl import load_workbook
            wb = load_workbook(p)
            self.assertIn("诊断结论", wb.sheetnames)
            self.assertIn("事件明细", wb.sheetnames)
            self.assertEqual(wb["事件明细"].max_row, len(self.events) + 1)

    def test_dump_reports(self):
        rep = minidump.parse(os.path.join(FIXTURES, "kernel_whea_124.dmp"))
        dana = ana.analyze_dump(rep)
        with tempfile.TemporaryDirectory() as d:
            h = report.export_dump_html(os.path.join(d, "d.html"), rep, dana)
            x = report.export_dump_xlsx(os.path.join(d, "d.xlsx"), rep, dana)
            self.assertGreater(os.path.getsize(h), 2000)
            self.assertGreater(os.path.getsize(x), 4000)


class TestRealEvtxIfPresent(unittest.TestCase):
    """有真实样本时顺带跑一遍端到端。"""

    def test_read_sample(self):
        sample = r"C:\Users\MSI\Desktop\3ａ.evtx"
        if not os.path.isfile(sample):
            self.skipTest("没有真实 evtx 样本")
        events, stats = evtx.query_events(sample, True, "*", limit=3000)
        self.assertGreater(len(events), 100)
        self.assertTrue(all(e.event_id >= 0 for e in events))
        res = ana.analyze(events, stats)
        self.assertLessEqual(res.health, 100)


class TestXmlPretty(unittest.TestCase):
    """日志页的 XML 美化。

    回归：形参曾叫 xml，把顶上的 `xml` 模块盖成了本地字符串，
    `xml.dom.minidom` 必然 AttributeError，又被 except 吞掉 ——
    表现是「XML 从来没缩进过」，而且不报任何错。
    """

    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from app.ui.log_page import LogPage
        cls.fn = staticmethod(LogPage._pretty_xml)

    def test_indents_real_xml(self):
        out = self.fn("<Event><Data>1</Data></Event>")
        self.assertIn("\n", out, "单行 XML 没被拆行")
        self.assertIn("<Data>1</Data>", out)

    def test_empty_returns_empty(self):
        self.assertEqual(self.fn(""), "")

    def test_broken_xml_returned_as_is(self):
        self.assertEqual(self.fn("<Event><Data>"), "<Event><Data>")


if __name__ == "__main__":
    unittest.main(verbosity=2)
