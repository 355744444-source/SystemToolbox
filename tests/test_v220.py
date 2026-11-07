# -*- coding: utf-8 -*-
"""v2.2.0：全应用按钮统一成蓝实心后，钉住「谁该蓝、谁不该蓝」。

这一版把四档按钮（primary / 默认 / ghost / bare）并成一档，最容易出的回归
不是「不好看」，而是**悄悄多出一个第五档**：某个页面为了强调又写了内联
`setStyleSheet("background:…")`，或者新控件忘了设 role。像素级断言比读代码可靠，
所以这里全部用 `QWidget.grab()` 取按钮真实填充色来判断。

判断口径（`_fill()` 只取按钮内边距区域，刻意避开文字与图标的抗锯齿像素）：

| 按钮 | 期望填充 |
|---|---|
| 动作按钮（ghost / primary / 无 role） | `accent` #4C9AFF |
| 动作按钮禁用态 | `surface2`（不是蓝、也不是红） |
| `#DangerBtn` | 红调（r 明显大于 b），不是蓝 |
| `#DangerBtn` 禁用态 | `surface2`（id 选择器比 `:disabled` 更具体，必须显式写） |
| `[role="chip"]` 未选中 | 透明（＝页面底色） |
| `[role="chip"]` 选中 | `accent_soft`（深蓝底 + 蓝字，不是亮蓝实心） |
| `[role="bare"]` | 透明（输入框里的 ×） |
"""
from __future__ import annotations

import os
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "windows")   # offscreen 没有字体引擎

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from PyQt5.QtCore import Qt                                              # noqa: E402
from PyQt5.QtGui import QColor                                          # noqa: E402
from PyQt5.QtWidgets import QApplication, QPushButton                     # noqa: E402

from app import theme                                                   # noqa: E402
from app.ui import widgets                                              # noqa: E402

_APP = None


def setUpModule():
    global _APP
    _APP = QApplication.instance() or QApplication(sys.argv)
    theme.apply_theme(_APP, "dark")


def _fill(btn: QPushButton) -> QColor:
    """按钮的填充色：取左侧内边距里的一个像素（避开文字、图标、边框）。"""
    btn.adjustSize()
    img = btn.grab().toImage()
    y = max(1, img.height() // 2)
    for x in (img.width() - 3, 3):          # 右内边距优先（文字靠左起排）
        c = img.pixelColor(x, y)
        if c.alpha() > 0:
            return c
    return img.pixelColor(1, y)


def _near(a: QColor, b: QColor, tol: int = 8) -> bool:
    return (abs(a.red() - b.red()) <= tol and abs(a.green() - b.green()) <= tol
            and abs(a.blue() - b.blue()) <= tol)


class ActionButtonTest(unittest.TestCase):
    """三种工厂出来的动作按钮必须是同一种蓝。"""

    def setUp(self):
        self.t = theme.current()

    def test_all_action_buttons_same_blue(self):
        accent = QColor(self.t["accent"])
        for make in (widgets.ghost_button, widgets.plain_button,
                     widgets.primary_button):
            btn = make("扫描")
            self.assertTrue(_near(_fill(btn), accent),
                            "%s 的填充不是 accent：%s"
                            % (make.__name__, _fill(btn).name()))

    def test_same_height_and_weight(self):
        hs, ws = set(), set()
        for make in (widgets.ghost_button, widgets.plain_button,
                     widgets.primary_button):
            btn = make("扫描")
            btn.adjustSize()
            hs.add(btn.sizeHint().height())
            ws.add(btn.font().weight())
        self.assertEqual(len(hs), 1, "三种动作按钮高度不一致：%s" % hs)
        self.assertEqual(len(ws), 1, "字重不一致：%s" % ws)

    def test_disabled_action_button_is_grey(self):
        btn = widgets.ghost_button("扫描")
        btn.setEnabled(False)
        self.assertTrue(_near(_fill(btn), QColor(self.t["surface2"])),
                        "禁用态应当回落成 surface2，实测 %s" % _fill(btn).name())

    def test_icon_is_white_on_blue(self):
        """蓝底上的图标必须是白的 —— 原来 ghost 用正文色 #E6EDF3，压在蓝上发灰。"""
        for make in (widgets.ghost_button, widgets.plain_button,
                     widgets.primary_button):
            btn = make("导出", "export")
            self.assertFalse(btn.icon().isNull(), make.__name__ + " 没挂上图标")
            img = btn.icon().pixmap(16, 16).toImage()
            top = max((img.pixelColor(x, y).lightness()
                       for x in range(img.width()) for y in range(img.height())
                       if img.pixelColor(x, y).alpha() > 200), default=-1)
            self.assertGreater(top, 200, "%s 的图标不是白色" % make.__name__)


class SpecialButtonTest(unittest.TestCase):
    """三个「不该跟着变蓝」的例外。"""

    def setUp(self):
        self.t = theme.current()

    def test_danger_is_reddish_not_blue(self):
        btn = QPushButton("删除到回收站")
        btn.setObjectName("DangerBtn")
        btn.adjustSize()
        c = _fill(btn)
        self.assertGreater(c.red(), c.blue(), "危险按钮不是红调：%s" % c.name())
        self.assertFalse(_near(c, QColor(self.t["accent"])), "危险按钮变成了蓝")

    def test_disabled_danger_does_not_look_clickable(self):
        """id 选择器比 `QPushButton:disabled` 更具体 —— 不显式写就会红底红字假装能点。"""
        btn = QPushButton("删除到回收站")
        btn.setObjectName("DangerBtn")
        btn.setEnabled(False)
        btn.adjustSize()
        self.assertTrue(_near(_fill(btn), QColor(self.t["surface2"])),
                        "禁用的危险按钮仍是 %s" % _fill(btn).name())

    def test_chip_unchecked_is_transparent(self):
        c = widgets.chip("严重")
        c.adjustSize()
        self.assertFalse(_near(_fill(c), QColor(self.t["accent"])),
                         "未选中的胶囊不该是蓝实心")

    def test_chip_checked_uses_soft_not_accent(self):
        c = widgets.chip("严重")
        c.setChecked(True)
        c.adjustSize()
        self.assertTrue(_near(_fill(c), QColor(self.t["accent_soft"])),
                        "选中的胶囊应是 accent_soft，实测 %s" % _fill(c).name())

    def test_bare_button_is_transparent(self):
        b = widgets.bare_button("×")
        b.adjustSize()
        self.assertFalse(_near(_fill(b), QColor(self.t["accent"])),
                         "行内 × 不该是蓝实心")


class QssSourceTest(unittest.TestCase):
    """样式表本身：不许留着「第五档」的老选择器与死令牌。"""

    def setUp(self):
        self.css = theme.qss(theme.current())

    def test_no_role_specific_button_blocks(self):
        for gone in ('QPushButton[role="primary"]', 'QPushButton[role="ghost"]'):
            self.assertNotIn(gone, self.css, "%s 应当已合并进 base" % gone)

    def test_grey_button_tokens_removed(self):
        for tok in ("btn_alt", "btn_ghost"):
            self.assertNotIn(tok, self.css, "%s 已无使用者，应当删掉" % tok)

    def test_base_button_is_accent(self):
        import re
        m = re.search(r"QPushButton \{(.*?)\}", self.css, re.S)
        self.assertIsNotNone(m)
        body = m.group(1)
        self.assertIn("background: %s" % theme.current()["accent"], body)
        self.assertIn("color: #FFFFFF", body)
        self.assertIn("font-weight: 600", body)


class PagesUniformTest(unittest.TestCase):
    """九个工作台里不许出现「不是蓝、也不是例外」的按钮。"""

    @classmethod
    def setUpClass(cls):
        from app.ui.main_window import TABS, MainWindow
        cls.win = MainWindow()
        cls.win.resize(1180, 800)
        cls.win.setAttribute(Qt.WA_DontShowOnScreen, True)
        cls.win.show()
        cls.tabs = TABS

    @classmethod
    def tearDownClass(cls):
        cls.win.close()

    def test_every_page_button_is_accent_or_exception(self):
        t = theme.current()
        accent, surface2 = QColor(t["accent"]), QColor(t["surface2"])
        bad = []
        for i, (name, _key, _hint) in enumerate(self.tabs):
            page = self.win.page(i)
            page.ensurePolished()
            for btn in page.findChildren(QPushButton):
                role = btn.property("role")
                if role in ("chip", "bare") or btn.objectName() == "DangerBtn":
                    continue
                if not btn.isEnabled():
                    ok = _near(_fill(btn), surface2)
                else:
                    ok = _near(_fill(btn), accent)
                if not ok:
                    bad.append((name, btn.text()[:10], _fill(btn).name(),
                                btn.styleSheet()[:40]))
        self.assertEqual(bad, [], "这些按钮不是蓝实心：%s" % bad)

    def test_no_inline_button_background(self):
        """内联 background 会盖掉统一规则，是「第五档」最可能的来源。"""
        bad = []
        for i, (name, _key, _hint) in enumerate(self.tabs):
            for btn in self.win.page(i).findChildren(QPushButton):
                if "background" in btn.styleSheet():
                    bad.append((name, btn.text()[:12]))
        self.assertEqual(bad, [], "按钮上写了内联背景：%s" % bad)


class SpaceTruncationTest(unittest.TestCase):
    """目录统计 / 大文件检索撞到上限时，必须能被上层看出来。

    这一族问题和 v2.1.1 的「日志只读到一半却说干净」是同一类：
    只读取证工具把「跑到一半」当成「就这么多」，用户据此判断「这个目录不大」。
    """

    def setUp(self):
        import tempfile
        self._td = tempfile.TemporaryDirectory()
        self.root = self._td.name
        os.makedirs(os.path.join(self.root, "a"), exist_ok=True)
        for i in range(5):
            with open(os.path.join(self.root, "a", "f%d.bin" % i), "wb") as f:
                f.write(b"x" * 4096)
        self.real = 5 * 4096

    def tearDown(self):
        self._td.cleanup()

    def test_dir_size_flags_cap(self):
        from app.core import space
        size, files, _newest, failed, cut = space._dir_size(
            os.path.join(self.root, "a"), counter=[0], max_files=2)
        self.assertTrue(cut, "撞到 max_files 却没标记截断")
        self.assertLess(size, self.real, "截断后不应当成完整大小")
        self.assertEqual(files, 3, "第 3 个文件就该停（counter > 2）")
        self.assertFalse(failed, "截断不是读失败，两者要分开")

    def test_dir_size_flags_stop_flag(self):
        from app.core import space
        found = space._dir_size(os.path.join(self.root, "a"), stop_flag=[1])
        self.assertTrue(found[4], "用户按停止后算出的数字只是下限")

    def test_dir_size_complete_no_flag(self):
        from app.core import space
        size, files, _n, failed, cut = space._dir_size(os.path.join(self.root, "a"))
        self.assertFalse(cut)
        self.assertFalse(failed)
        self.assertEqual(size, self.real)
        self.assertEqual(files, 5)

    def test_analyze_dir_propagates(self):
        from app.core import space
        node = space.analyze_dir(self.root)
        self.assertFalse(node.truncated, "完整统计不该标截断")
        self.assertEqual(node.files, 5)
        cut = space.analyze_dir(self.root, counter=[10 ** 9])
        self.assertTrue(cut.truncated, "上限用尽时根节点必须标截断")

    def test_dirnode_default_not_truncated(self):
        from app.core import space
        self.assertFalse(space.DirNode(path="C:\\x", name="x").truncated)

    def test_find_large_files_reports_matched_vs_shown(self):
        from app.core import space
        st = {}
        rows = space.find_large_files(self.root, min_size=1024, limit=2, stats=st)
        self.assertEqual(len(rows), 2, "limit 应当生效")
        self.assertEqual(st["matched"], 5)
        self.assertEqual(st["shown"], 2)
        self.assertTrue(st["truncated"], "被 limit 截断要能看出来")
        both = space.find_large_files(self.root, min_size=1024, limit=99, stats=st)
        self.assertEqual(len(both), 5)
        self.assertFalse(st["truncated"])


if __name__ == "__main__":
    unittest.main()