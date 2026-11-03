# -*- coding: utf-8 -*-
"""v2.0 表格能力：点表头排序 + 关键字筛选（`app/ui/tables.py`）。

这一层全是纯逻辑（`RowModel`），不需要真窗口 —— 但 `QAbstractTableModel`
要一个 `QApplication` 才肯发信号，所以照样离屏起一个。

重点锁四件容易悄悄坏的事：

1. 数字按数值排，不按字符串（"10" 不能排在 "9" 前面）
2. 勾选按**行对象**记，排序后不会错位（错位就等于删错文件）
3. 三态循环点回「原顺序」
4. 筛选只改显示，`checked_rows()` 照样返回被筛掉的勾选行
"""
from __future__ import annotations

import os
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from PyQt5.QtCore import Qt  # noqa: E402
from PyQt5.QtWidgets import QApplication  # noqa: E402

from app import theme  # noqa: E402
from app.ui.tables import Col, RowModel, make_table, sort_key  # noqa: E402

_APP = None


def setUpModule():
    global _APP
    _APP = QApplication.instance() or QApplication(sys.argv[:1])
    theme.apply_theme(_APP, "dark")


class Item:
    """假行对象。故意用同一个类，避免不同类之间比较。"""

    def __init__(self, name, size=0, ver="", note=""):
        self.name = name
        self.size = size
        self.ver = ver
        self.note = note

    def __repr__(self) -> str:
        return "<%s %s>" % (self.name, self.size)


def _model(checkable=False):
    return RowModel([
        Col("名称", 160, lambda r: r.name),
        Col("大小", 80, lambda r: "%d MB" % r.size, align=int(Qt.AlignRight)),
        Col("版本", 100, lambda r: r.ver or "—"),
        Col("备注", 0, lambda r: r.note, stretch=True),
    ], checkable=checkable)


class SortKeyTest(unittest.TestCase):

    def test_numbers_compare_by_value(self):
        self.assertLess(sort_key("9"), sort_key("10"))
        self.assertLess(sort_key("1,234"), sort_key("2,000"))

    def test_bytes_units_normalized(self):
        # 412 MB < 12.3 GB，按字符串比会得出相反结论
        self.assertLess(sort_key("412 MB"), sort_key("12.3 GB"))
        self.assertLess(sort_key("900 KB"), sort_key("1 MB"))

    def test_version_compares_by_number(self):
        self.assertLess(sort_key("10.0.9"), sort_key("10.0.26100.9731"))
        self.assertLess(sort_key("1.0.2"), sort_key("1.0.10"))

    def test_empty_sorts_last(self):
        keys = [sort_key("—"), sort_key(""), sort_key("-"), sort_key("abc"),
                sort_key("5")]
        self.assertEqual(max(keys), sort_key(""))
        self.assertGreater(sort_key("—"), sort_key("abc"))

    def test_all_keys_same_arity(self):
        """形状不一致会拿 float 和 tuple 比大小，直接 TypeError。"""
        samples = ["9", "1.0.2", "abc", "", "—", "12.3 GB", "26%", None]
        widths = {len(sort_key(s)) for s in samples}
        self.assertEqual(len(widths), 1)
        # 混着排一遍，不抛异常就说明可比较
        sorted(samples, key=sort_key)

    def test_case_insensitive_text(self):
        self.assertEqual(sort_key("Apple"), sort_key("apple"))

    def test_decorated_numbers(self):
        """IO 告警的「×138」、估算值的「≈ 3.2 GB」都得按数值排。"""
        vals = ["×138", "×146", "×2", "×2", "×9"]
        self.assertEqual(sorted(vals, key=sort_key),
                         ["×2", "×2", "×9", "×138", "×146"])
        self.assertLess(sort_key("900 MB"), sort_key("≈ 3.2 GB"))


class SortTest(unittest.TestCase):

    def setUp(self):
        self.m = _model()
        self.rows = [Item("c", 300, "10.0.9"), Item("a", 100, "10.0.10"),
                     Item("b", 200, "10.0.2")]
        self.m.set_rows(self.rows)

    def test_initial_state_is_source_order(self):
        self.assertEqual(self.m.sort_state(), (-1, False))
        self.assertEqual([r.name for r in self.m.rows()], ["c", "a", "b"])

    def test_clear_sort_restores_source_order(self):
        self.m.sort_by(1, desc=True)
        self.m.clear_sort()
        self.assertEqual(self.m.sort_state(), (-1, False))
        self.assertEqual([r.name for r in self.m.rows()], ["c", "a", "b"])

    def test_qt_sort_entry(self):
        """Qt 的 `setSortingEnabled(True)` 点表头就是调 `model.sort()`。"""
        self.m.sort(1, Qt.AscendingOrder)
        self.assertEqual([r.size for r in self.m.rows()], [100, 200, 300])
        self.m.sort(1, Qt.DescendingOrder)
        self.assertEqual([r.size for r in self.m.rows()], [300, 200, 100])
        self.m.sort(-1, Qt.AscendingOrder)
        self.assertEqual([r.name for r in self.m.rows()], ["c", "a", "b"])

    def test_asc_and_desc(self):
        self.m.sort_by(1)
        self.assertEqual([r.size for r in self.m.rows()], [100, 200, 300])
        self.m.sort_by(1, desc=True)
        self.assertEqual([r.size for r in self.m.rows()], [300, 200, 100])

    def test_switch_column(self):
        self.m.sort_by(1, desc=True)
        self.m.sort(2, Qt.AscendingOrder)        # 换一列
        self.assertEqual(self.m.sort_state(), (2, False))

    def test_out_of_range_col_is_noop(self):
        self.m.sort_by(99)
        self.assertEqual(self.m.sort_state()[0], -1)
        self.assertEqual(len(self.m.rows()), 3)

    def test_custom_sort_key(self):
        m = RowModel([Col("名称", 100, lambda r: r.name,
                          sort=lambda r: -len(r.name))])
        m.set_rows([Item("aa"), Item("b"), Item("cccc")])
        m.sort_by(0)
        self.assertEqual([r.name for r in m.rows()], ["cccc", "aa", "b"])

    def test_empty_rows(self):
        m = _model()
        m.set_rows([])
        m.sort(0, Qt.AscendingOrder)
        self.assertEqual(m.rows(), [])


class CheckIdentityTest(unittest.TestCase):
    """勾选必须跟着行对象走 —— 排序后按行号记会点到别的行。"""

    def setUp(self):
        self.m = _model(checkable=True)
        self.rows = [Item("a", 100), Item("b", 200), Item("c", 300)]
        self.m.set_rows(self.rows)

    def _check(self, row):
        i = self.m.rows().index(row)
        idx = self.m.index(i, 0)
        self.assertTrue(self.m.setData(idx, Qt.Checked, Qt.CheckStateRole))

    def test_check_survives_sort(self):
        self._check(self.rows[0])
        self.m.sort_by(1, desc=True)              # 顺序反转
        self.assertEqual(self.m.rows()[0], self.rows[2])
        self.assertEqual(self.m.checked_rows(), [self.rows[0]])

    def test_uncheck_survives_sort(self):
        self._check(self.rows[1])
        self.m.sort_by(1, desc=True)
        i = self.m.rows().index(self.rows[1])
        self.m.setData(self.m.index(i, 0), Qt.Unchecked, Qt.CheckStateRole)
        self.assertEqual(self.m.checked_rows(), [])

    def test_check_rows_by_object(self):
        self.m.check_rows([self.rows[2]], True)
        self.assertEqual(self.m.checked_rows(), [self.rows[2]])
        self.m.check_rows([self.rows[2]], False)
        self.assertEqual(self.m.checked_count(), 0)

    def test_checked_rows_ignores_filter(self):
        """筛选后仍返回被筛掉的勾选行。

        少返回一行就等于「已选 2 项」却只删了 1 项，用户根本看不出来。
        """
        self._check(self.rows[0])
        self._check(self.rows[2])
        # 用只在名字里出现的字母：MB 会 casefold 成 mb，搜 "b" 会命中所有行
        self.m.set_filter("c")                    # 只剩 c
        self.assertEqual([r.name for r in self.m.rows()], ["c"])
        self.assertEqual(len(self.m.checked_rows()), 2)

    def test_check_all_only_visible(self):
        self.m.set_filter("a")
        self.m.check_all(True)
        self.assertEqual([r.name for r in self.m.checked_rows()], ["a"])

    def test_check_all_false_clears_hidden(self):
        self.m.check_all(True)
        self.m.set_filter("a")
        self.m.check_all(False)
        self.assertEqual(self.m.checked_count(), 0)

    def test_set_rows_clears_checks(self):
        self._check(self.rows[0])
        self.m.set_rows([Item("x"), Item("y")])
        self.assertEqual(self.m.checked_count(), 0)


class FilterTest(unittest.TestCase):

    def setUp(self):
        self.m = _model()
        self.m.set_rows([Item("Realtek 网卡", 10, "1.0.1", "onboard"),
                         Item("Intel 网卡", 20, "1.0.2", ""),
                         Item("NVIDIA 显卡", 30, "2.0", "pcie")])

    def test_match_any_column(self):
        self.assertEqual(self.m.set_filter("网卡"), 2)
        self.assertEqual(self.m.set_filter("pcie"), 1)
        self.assertEqual(self.m.set_filter("1.0.2"), 1)

    def test_case_insensitive(self):
        self.assertEqual(self.m.set_filter("realtek"), 1)

    def test_clear_filter(self):
        self.m.set_filter("网卡")
        self.assertEqual(self.m.set_filter(""), 3)

    def test_total_count_unaffected(self):
        self.m.set_filter("网卡")
        self.assertEqual(self.m.total_count(), 3)
        self.assertEqual(len(self.m.rows()), 2)

    def test_no_match(self):
        self.assertEqual(self.m.set_filter("不存在的词"), 0)
        self.assertEqual(self.m.rows(), [])

    def test_filter_and_sort_together(self):
        self.m.set_filter("网卡")
        self.m.sort_by(1, desc=True)
        self.assertEqual([r.size for r in self.m.rows()], [20, 10])

    def test_view_state_survives_set_rows(self):
        """重扫一遍数据，用户挑的排序与筛选不该被清掉。"""
        self.m.set_filter("网卡")
        self.m.sort_by(1, desc=True)
        self.m.set_rows([Item("Realtek 网卡", 99), Item("Intel 网卡", 1),
                         Item("别的", 50)])
        self.assertEqual(self.m.filter_text(), "网卡")
        self.assertEqual(self.m.sort_state(), (1, True))
        self.assertEqual([r.size for r in self.m.rows()], [99, 1])

    def test_hay_handles_raising_getter(self):
        m = RowModel([Col("坏列", 10, lambda r: 1 / 0),
                      Col("好列", 10, lambda r: r.name)])
        m.set_rows([Item("abc")])
        self.assertEqual(m.set_filter("abc"), 1)


class HeaderHookTest(unittest.TestCase):
    """三十九张表原来点表头全都没反应 —— 这条锁住 make_table 的挂载。

    走 `sortByColumn()` 而不是自己 emit sectionClicked：Qt 的 QTableView
    点表头最终就是调它（经由 header 的 sortIndicatorChanged）。自己连
    sectionClicked 在表头事件里动模型，354 行的表会访问违例直接崩掉。
    """

    def test_header_click_sorts(self):
        m = _model()
        rows = [Item("c", 300), Item("a", 100), Item("b", 200)]
        m.set_rows(rows)
        tv = make_table(m, row_height=22)
        self.assertTrue(tv.isSortingEnabled())
        tv.sortByColumn(1, Qt.AscendingOrder)
        self.assertEqual([r.size for r in m.rows()], [100, 200, 300])
        self.assertTrue(tv.horizontalHeader().isSortIndicatorShown())
        tv.sortByColumn(1, Qt.DescendingOrder)
        self.assertEqual(tv.horizontalHeader().sortIndicatorOrder(),
                         Qt.DescendingOrder)
        self.assertEqual([r.size for r in m.rows()], [300, 200, 100])

    def test_no_auto_sort_at_build(self):
        """建表格时不能自作主张按第 0 列排一遍。"""
        m = _model()
        m.set_rows([Item("c", 300), Item("a", 100), Item("b", 200)])
        tv = make_table(m, row_height=22)
        self.assertEqual(m.sort_state(), (-1, False))
        self.assertEqual([r.name for r in m.rows()], ["c", "a", "b"])
        self.assertEqual(tv.horizontalHeader().sortIndicatorSection(), -1)

    def test_checkbox_column_not_sortable(self):
        m = _model(checkable=True)
        m.set_rows([Item("b", 2), Item("a", 1)])
        tv = make_table(m, row_height=22)
        tv.sortByColumn(0, Qt.AscendingOrder)
        self.assertEqual(m.sort_state(), (-1, False))
        self.assertEqual([r.name for r in m.rows()], ["b", "a"])

    def test_sort_survives_many_rows(self):
        """354 行是现场真实体量，小表测不出来的问题都在这个量级。"""
        m = _model()
        m.set_rows([Item("r%03d" % i, 354 - i) for i in range(354)])
        tv = make_table(m, row_height=22)
        tv.sortByColumn(1, Qt.AscendingOrder)
        self.assertEqual(m.rows()[0].size, 1)
        self.assertEqual(m.rows()[-1].size, 354)


if __name__ == "__main__":
    unittest.main()
