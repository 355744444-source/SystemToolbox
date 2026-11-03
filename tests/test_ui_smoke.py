# -*- coding: utf-8 -*-
"""界面冒烟单测：离屏建八个工作台，锁住「能建起来 + 关键控件在」这条底线。

以前 `tests/ui_smoke.py` 只是截图脚本，文件名不符合 `test_*.py`，
`unittest discover` 根本不收集 —— 590 项单测里 UI 零覆盖。这里补上。

注意：**不 show 窗口**。页面上的 `showEvent` 会自己拉后台任务
（清理页读物理磁盘、测速页探测盘符），测试里一律不触发。
"""
from __future__ import annotations

import os
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from PyQt5.QtWidgets import QApplication, QSplitter, QWidget  # noqa: E402

from app import theme  # noqa: E402
from app.ui.main_window import TABS, MainWindow  # noqa: E402

_APP = None


def setUpModule():
    global _APP
    _APP = QApplication.instance() or QApplication(sys.argv[:1])
    theme.apply_theme(_APP, "dark")


class LazyBuildTest(unittest.TestCase):
    """单独一个窗口：`page(i)` 会顺手把页面建出来，混在别处测不准。"""

    def test_only_first_page_built_at_start(self):
        win = MainWindow()
        built = [i for i, p in enumerate(win._pages) if p is not None]
        self.assertEqual(built, [0])
        self.assertIsNotNone(win.page(0))


class BuildTest(unittest.TestCase):
    """一个窗口建一次，八页全建完共用。"""

    @classmethod
    def setUpClass(cls):
        cls.win = MainWindow()

    @classmethod
    def tearDownClass(cls):
        cls.win = None

    def test_rail_has_eight_buttons(self):
        self.assertEqual(len(TABS), 8)
        self.assertEqual(len(self.win.rail_buttons), 8)

    def test_window_title_is_set(self):
        # 标题里没有版本号（版本在「关于」里），这里只锁「有标题、不是空窗」
        self.assertIn("系统万能工具箱", self.win.windowTitle())

    def test_all_eight_pages_build(self):
        for i in range(len(TABS)):
            page = self.win.page(i)
            self.assertIsInstance(page, QWidget, "第 %d 页没建起来" % i)

    def test_dump_page_structure(self):
        p = self.win.page(1)
        self.assertEqual(p.tabs.count(), 4)
        self.assertTrue(hasattr(p, "bc_model"))
        self.assertEqual(p.btn_bc.text(), "读取本机蓝屏代码")
        # 没扫过本机蓝屏码时 KPI 区应当藏着，别占一片空白
        self.assertTrue(p.kpi_box.isHidden())

    def test_health_page_has_ten_tabs(self):
        p = self.win.page(6)
        self.assertEqual(p.tabs.count(), 10)

    def test_health_verdict_bar_starts_hidden(self):
        # 九项没跑完就不该有条形结论 —— 半成品结论比没有结论更误导
        p = self.win.page(6)
        self.assertIsNone(p.health_rep)
        self.assertTrue(p.verdict.isHidden())
        self.assertEqual(p.btn_report.text(), "导出报告")

    def test_residue_page_has_three_tabs(self):
        p = self.win.page(3)
        self.assertEqual(p.tabs.count(), 3)

    def test_clean_page_has_splitters(self):
        # 分栏宽度只能显式给（ScrollColumn 的 sizeHint 小，会被压到 250px），
        # 所以每个上下分栏的页签都应该真的存在 QSplitter
        splits = self.win.page(2).findChildren(QSplitter)
        self.assertTrue(splits, "清理页没有任何分栏")


class SingleSkinTest(unittest.TestCase):
    """v2.0.0 只剩深色一套皮肤 —— 切主题那套整窗重建连同入口一起删了。

    这里锁的是「别再长回来」：导航栏不许有主题按钮、窗口不许有切换方法、
    传 "light" 也只给深色。真要加第二套皮肤，得先改掉这几条。
    """

    @classmethod
    def setUpClass(cls):
        cls.win = MainWindow()

    @classmethod
    def tearDownClass(cls):
        cls.win = None

    def test_only_dark_tokens(self):
        self.assertEqual(list(theme.THEMES), ["dark"])
        self.assertEqual(theme.current()["name"], "dark")

    def test_rail_has_no_theme_button(self):
        texts = [b.text() for b in self.win.rail_buttons]
        self.assertNotIn("浅色", texts)
        self.assertNotIn("深色", texts)
        self.assertEqual(len(self.win.rail_buttons), 8, "导航按钮数不该变")

    def test_window_has_no_switch_api(self):
        for name in ("btn_theme", "toggle_theme", "theme_name", "apply_theme"):
            self.assertFalse(hasattr(self.win, name), name)

    def test_light_request_falls_back_to_dark(self):
        theme.apply_theme(_APP, "light")
        self.assertEqual(theme.current()["name"], "dark")
        theme.apply_theme(_APP, "dark")


if __name__ == "__main__":
    unittest.main(verbosity=2)
