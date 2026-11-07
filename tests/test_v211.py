# -*- coding: utf-8 -*-
"""v2.1.1 单测：这一版修的都是「看起来没问题、其实是错的」那一类。

分两块：

1. **内核层的假正常 / 泄漏 / 边界** —— 事件 XML 里一个空白字段、32 位计数器回绕
   一圈、PATH 有一层读不到。
2. **界面的横向一致性** —— 9 个页面的边距、标题字号、结论条。这类问题没法用
   「看起来对不对」来验收，只能把量出来的数字钉住。
"""
from __future__ import annotations

import os
import sys
import unittest

from PyQt5.QtWidgets import QApplication, QLabel

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app import theme                                              # noqa: E402
from app.core import analysis, evtx, pathenv, perf                 # noqa: E402
from app.core.evtx import ReadStats                                # noqa: E402
from app.ui.widgets import PAGE_MARGINS, VerdictBar                # noqa: E402


# ====================================================================== 内核
class Delta32Test(unittest.TestCase):
    """`GetIfTable` 的字节计数是 32 位，千兆链路上约 34 秒转一圈。"""

    def test_normal_increase(self):
        self.assertEqual(perf._delta32(1500, 1000), 500.0)

    def test_no_change(self):
        self.assertEqual(perf._delta32(1000, 1000), 0.0)

    def test_wrap_around_is_counted_not_zeroed(self):
        # 从 2^32-100 涨到 50：真实增量是 150 字节，不是 0，也不是负
        self.assertEqual(perf._delta32(50, (1 << 32) - 100), 150.0)

    def test_nic_went_down_is_zero_not_a_spike(self):
        # 某块网卡从「已连接」变成「未连接」，总量会变小 —— 这不是回绕，
        # 不能补一个 4 GB 的假尖峰上去
        self.assertEqual(perf._delta32(10 ** 9, 3 * 10 ** 9), 0.0)


class DirtyXmlTest(unittest.TestCase):
    """现场拷回来的 evtx 里什么脏数据都有，一条不该让整次读取崩掉。"""

    XML = (
        '<Event xmlns="http://schemas.microsoft.com/win/2004/08/events/event">'
        "<System>"
        "<Provider Name=\"Microsoft-Windows-Kernel-Power\"/>"
        "<EventID>41</EventID>"
        "<Level>2</Level>"
        "<Task>   </Task>"            # 只有空白：`or 0` 挡不住 int('  ')
        "<Opcode>abc</Opcode>"        # 非数字
        "<EventRecordID></EventRecordID>"
        "<TimeCreated SystemTime=\"2026-11-07T10:00:00.0000000Z\"/>"
        "<Computer>PC-01</Computer>"
        "</System>"
        "<EventData><Data Name=\"BugcheckCode\">0</Data></EventData>"
        "</Event>"
    )

    def test_blank_and_garbage_fields_become_zero(self):
        ev = evtx.parse_event_xml(self.XML, "System")
        self.assertIsNotNone(ev)
        self.assertEqual(ev.event_id, 41)
        self.assertEqual(ev.task, 0)
        self.assertEqual(ev.opcode, 0)
        self.assertEqual(ev.record_id, 0)

    def test_good_values_survive(self):
        xml = self.XML.replace("<Task>   </Task>", "<Task>7</Task>").replace(
            "<EventRecordID></EventRecordID>", "<EventRecordID>12345</EventRecordID>")
        ev = evtx.parse_event_xml(xml, "System")
        self.assertEqual(ev.task, 7)
        self.assertEqual(ev.record_id, 12345)


class IncompleteHeadlineTest(unittest.TestCase):
    """「日志干净」这句话只有读全了才能说。"""

    def test_truncated_read_is_not_called_clean(self):
        stats = ReadStats(source="System")
        stats.truncated = True
        res = analysis.analyze([], stats=stats)
        res.total = 500                     # 假装读到了 500 条但被截断
        res.findings = []
        line = analysis._headline(res)
        self.assertIn("只读到", line)
        self.assertNotIn("日志干净", line)

    def test_read_error_is_not_called_clean(self):
        stats = ReadStats(source="System")
        stats.error = "读取事件失败（错误码 5）"
        res = analysis.analyze([], stats=stats)
        res.total = 12
        res.findings = []
        self.assertIn("只读到", analysis._headline(res))

    def test_complete_clean_read_still_says_clean(self):
        res = analysis.analyze([], stats=ReadStats(source="System"))
        res.total = 12
        res.findings = []
        self.assertIn("日志干净", analysis._headline(res))


class PathUnreadableTest(unittest.TestCase):
    """读不到 PATH 与「PATH 没问题」是两件事。"""

    def setUp(self):
        self._orig = pathenv._read

    def tearDown(self):
        pathenv._read = self._orig

    def _audit(self, machine, user):
        def fake(scope):
            return machine if scope == "machine" else user
        pathenv._read = fake
        return pathenv.audit()

    def test_unreadable_scope_kills_the_ok_finding(self):
        rep = self._audit(("", -1), (r"C:\Windows;C:\Windows\System32", 2))
        topics = [f.topic for f in rep.findings]
        self.assertIn("path-unreadable", topics)
        self.assertNotIn("path-ok", topics)
        self.assertIn("读不到", rep.findings[0].title)

    def test_both_unreadable_still_reports(self):
        rep = self._audit(("", -1), ("", -1))
        self.assertEqual(sum(1 for f in rep.findings if f.topic == "path-unreadable"), 2)


# ====================================================================== 界面
_APP = None


def setUpModule():
    global _APP
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    _APP = QApplication.instance() or QApplication(sys.argv[:1])
    theme.apply_theme(_APP, "dark")


class VerdictBarTest(unittest.TestCase):
    def test_hidden_by_default(self):
        # 结论条在有结论之前必须不可见：空着的一条会被读成「没问题」
        self.assertFalse(VerdictBar().isVisible())

    def test_level_color_is_transparent(self):
        # 内联上色不带 background:transparent 时，QLabel 会在卡片上自绘一层底色
        bar = VerdictBar()
        bar.set_level("很慢", "crit")
        self.assertIn("background:transparent", bar.level.styleSheet())
        self.assertIn(theme.current()["crit"], bar.level.styleSheet())

    def test_level_accepts_hex(self):
        bar = VerdictBar()
        bar.set_level("12", "#abcdef")
        self.assertIn("#abcdef", bar.level.styleSheet())

    def test_clear_color_keeps_transparent(self):
        bar = VerdictBar()
        bar.set_level("很慢", "crit")
        bar.set_level("—")
        self.assertIn("background:transparent", bar.level.styleSheet())
        self.assertNotIn("color:", bar.level.styleSheet())

    def test_sub_hidden_when_empty(self):
        bar = VerdictBar()
        bar.set_text("一句话")
        self.assertFalse(bar.sub.isVisible())


class PageConsistencyTest(unittest.TestCase):
    """9 个工作台的页边距必须是同一个数，否则切页时内容会整体平移。"""

    @classmethod
    def setUpClass(cls):
        from app.ui.main_window import TABS, MainWindow
        cls.tabs = TABS
        cls.win = MainWindow()

    @classmethod
    def tearDownClass(cls):
        cls.win = None

    def test_every_page_uses_the_same_margins(self):
        wrong = []
        for i in range(len(self.tabs)):
            page = self.win.page(i)
            lay = page.layout()
            got = lay.getContentsMargins() if lay is not None else (0, 0, 0, 0)
            if got != PAGE_MARGINS:
                wrong.append((self.tabs[i][0], got))
        self.assertEqual(wrong, [], "这些页面的边距与 PAGE_MARGINS 不一致：%s" % wrong)

    def test_role_h2_resolves_to_title_size(self):
        # QSS 只有 `QLabel#H2` 时，用 setProperty 写的标题会回落成正文 12px。
        # 两种写法都必须给到 13px 加粗。
        made = []
        for how in ("objectName", "property"):
            lbl = QLabel("标题")
            if how == "objectName":
                lbl.setObjectName("H2")
            else:
                lbl.setProperty("role", "H2")
            lbl.ensurePolished()
            made.append((how, lbl.font().pixelSize(), lbl.font().bold()))
        for how, px, bold in made:
            self.assertEqual(px, 13, "%s 写的 H2 字号是 %s" % (how, px))
            self.assertTrue(bold, "%s 写的 H2 没有加粗" % how)

    def test_no_markdown_asterisks_in_labels(self):
        # QLabel 不解析 Markdown：写 **强调** 的话，用户看到的就是两个星号
        bad = []
        for i in range(len(self.tabs)):
            for lbl in self.win.page(i).findChildren(QLabel):
                if "**" in (lbl.text() or ""):
                    bad.append((self.tabs[i][0], lbl.text()[:30]))
        self.assertEqual(bad, [], "这些标签里带着 Markdown 星号：%s" % bad)


if __name__ == "__main__":
    unittest.main()
