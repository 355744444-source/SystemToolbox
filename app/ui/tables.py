# -*- coding: utf-8 -*-
"""表格与后台任务的通用件。

六个功能页都是同一套结构：后台扫描 → 表格展示 → 勾选 → 执行，
所以把「带勾选的通用表格模型」和「可停止的线程封装」抽出来复用。
"""
from __future__ import annotations

import csv
import io
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Callable

from PyQt5.QtCore import QAbstractTableModel, QModelIndex, Qt, QThread, pyqtSignal
from PyQt5.QtGui import QColor
from PyQt5.QtWidgets import (QAbstractItemView, QHBoxLayout, QHeaderView,
                             QLabel, QLineEdit, QTableView, QVBoxLayout, QWidget)

from .. import theme
from .widgets import ui_font


@dataclass
class Col:
    title: str
    width: int = 120
    get: Callable = None                  # fn(row) -> str
    mono: bool = False
    color: str = ""                       # 主题 token 名，空 = 主文字色
    align: int = int(Qt.AlignLeft | Qt.AlignVCenter)
    tip: Callable = None                  # fn(row) -> str，悬浮提示
    stretch: bool = False
    color_fn: Callable = None             # fn(row) -> 主题 token 名，按行着色
    sort: Callable = None                 # fn(row) -> 可比较值，不给就按显示文字猜


class RowModel(QAbstractTableModel):
    """带可选勾选的只读表格模型，支持点表头排序 + 关键字筛选。

    三层数据，别混：

    - `_all`   **源顺序**，`set_rows()` 灌进来的原始列表，排序筛选都不动它
    - `_order` 显示顺序，存 `_all` 的下标，排序 / 筛选只改这个
    - `_rows`  按 `_order` 摊平出来的行对象，视图看到的第 i 行就是 `_rows[i]`

    勾选状态**按行对象身份记**（`id(row)`）而不是行号 —— 按行号记的话，
    点一下表头排序，勾选就整体错位：用户勾了 3 项，删的却是另外 3 项。
    """

    checkChanged = pyqtSignal()

    def __init__(self, cols: list[Col], checkable: bool = False, parent=None,
                 check_filter: Callable = None):
        super().__init__(parent)
        self.cols = cols
        self.checkable = checkable
        # check_filter(row) -> bool：返回 False 的行不给勾选框（如「本系统不适用」）
        self.check_filter = check_filter
        self._all: list = []
        self._rows: list = []
        self._order: list[int] = []
        self._checked: set[int] = set()      # 存 id(row)
        self._sort_col = -1
        self._sort_desc = False
        self._filter = ""

    # -------- Qt 接口
    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.cols)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if orientation != Qt.Horizontal:
            return None
        if not (0 <= section < len(self.cols)):
            return None
        if role == Qt.DisplayRole:
            return self.cols[section].title
        if role == Qt.TextAlignmentRole:
            # 表头跟着列数据的对齐走：金额/大小这类右对齐的列，标题也右对齐，
            # 否则标题贴左、数字贴右，整列看着是「斜」的
            return self.cols[section].align
        return None

    def _can_check(self, row) -> bool:
        if not self.check_filter:
            return True
        try:
            return bool(self.check_filter(row))
        except Exception:       # noqa: BLE001
            return True

    def flags(self, index):
        base = Qt.ItemIsEnabled | Qt.ItemIsSelectable
        if (self.checkable and index.column() == 0
                and index.row() < len(self._rows)
                and self._can_check(self._rows[index.row()])):
            return base | Qt.ItemIsUserCheckable
        return base

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        row = self._rows[index.row()]
        col = self.cols[index.column()]
        t = theme.current()
        if role == Qt.CheckStateRole and self.checkable and index.column() == 0:
            return Qt.Checked if id(row) in self._checked else Qt.Unchecked
        if role == Qt.DisplayRole:
            try:
                return str(col.get(row)) if col.get else ""
            except Exception:       # noqa: BLE001
                return ""
        if role == Qt.TextAlignmentRole:
            return col.align
        if role == Qt.ForegroundRole:
            if col.color_fn:
                try:
                    return QColor(t.get(col.color_fn(row), t["text"]))
                except Exception:   # noqa: BLE001
                    return QColor(t["text"])
            return QColor(t.get(col.color, t["text"]) if col.color else t["text"])
        if role == Qt.FontRole and col.mono:
            return ui_font(8)
        if role == Qt.ToolTipRole:
            if col.tip:
                try:
                    return col.tip(row)
                except Exception:   # noqa: BLE001
                    return ""
            return None
        return None

    def setData(self, index, value, role=Qt.EditRole):
        if role == Qt.CheckStateRole and self.checkable:
            row = self._rows[index.row()] if index.row() < len(self._rows) else None
            if row is None:
                return False
            if int(value) == Qt.Checked:
                self._checked.add(id(row))
            else:
                self._checked.discard(id(row))
            self.dataChanged.emit(index, index, [Qt.CheckStateRole])
            self.checkChanged.emit()
            return True
        return False

    # -------- 排序与筛选
    def sort_by(self, col: int, desc: bool = False) -> None:
        """按第 col 列排；`col < 0` 表示取消排序、回到源顺序。"""
        self._sort_col = col if 0 <= col < len(self.cols) else -1
        self._sort_desc = bool(desc) and self._sort_col >= 0
        self._rebuild()

    def sort_state(self) -> tuple[int, bool]:
        return self._sort_col, self._sort_desc

    def sort(self, column, order=Qt.AscendingOrder) -> None:
        """`QTableView.setSortingEnabled(True)` 点表头时 Qt 调的入口。

        勾选框那一列（checkable 模型的第 0 列）不参与排序：
        点它直接还原成源顺序，而不是按「有没有勾」排。
        """
        if column is None or column < 0 or column >= len(self.cols):
            self.clear_sort()
            return
        if self.checkable and column == 0:
            self.clear_sort()
            return
        self._sort_col = int(column)
        self._sort_desc = order == Qt.DescendingOrder
        self._rebuild()

    def clear_sort(self) -> None:
        if self._sort_col == -1 and not self._sort_desc:
            return
        self._sort_col, self._sort_desc = -1, False
        self._rebuild()

    def set_filter(self, text: str) -> int:
        """按关键字筛（任一可见列命中即可），返回筛完剩几行。

        只改显示顺序，不动 `_all` —— 勾选是按行对象身份记的，
        所以「勾上 → 输入关键字 → 清空关键字」勾选还在。
        """
        text = (text or "").strip()
        if text == self._filter:
            return len(self._rows)
        self._filter = text
        self._rebuild()
        return len(self._rows)

    def filter_text(self) -> str:
        return self._filter

    def total_count(self) -> int:
        """不受筛选影响的源行数。"""
        return len(self._all)

    def _compute(self) -> list:
        """算出该显示哪些行、按什么顺序（返回 `_all` 的下标列表）。"""
        idx = list(range(len(self._all)))
        if self._filter:
            needle = self._filter.casefold()
            idx = [i for i in idx if needle in self._hay(self._all[i])]
        if self._sort_col >= 0:
            col = self.cols[self._sort_col]
            key = col.sort or (lambda row, _c=col: sort_key(_c.get(row)
                                                          if _c.get else ""))
            idx.sort(key=lambda i: key(self._all[i]), reverse=self._sort_desc)
        return idx

    def _rebuild(self, reset: bool = False) -> None:
        """重算显示顺序。

        **行数不变时绝不能 `beginResetModel`**：排序是从表头点击进来的，
        表头此刻还在自己的事件处理里，整表 reset 会让它的 section 数据失效 ——
        实测直接访问违例、进程无声消失（354 行的表必崩，50 行的表看不出来）。
        改用 `layoutAboutToBeChanged` / `layoutChanged`（Qt 自己的排序模型
        就是这条路），顺带把选中行跟着行走，比 reset 体验也好。
        """
        idx = self._compute()
        same_len = len(idx) == len(self._rows) and bool(self._order)
        if reset or not same_len:
            self.beginResetModel()
            self._order = idx
            self._rows = [self._all[i] for i in idx]
            self.endResetModel()
            return
        old = list(self._order)
        persist = self.persistentIndexList()
        self.layoutAboutToBeChanged.emit()
        pos = {src: i for i, src in enumerate(idx)}
        moved = []
        for i in persist:
            src = old[i.row()] if 0 <= i.row() < len(old) else -1
            moved.append(self.index(pos[src], i.column())
                         if src in pos else QModelIndex())
        self._order = idx
        self._rows = [self._all[i] for i in idx]
        self.changePersistentIndexList(persist, moved)
        self.layoutChanged.emit()

    def _hay(self, row) -> str:
        """一行的全部可见文字，拼成小写串给筛选做子串匹配。"""
        parts = []
        for c in self.cols:
            if not c.get:
                continue
            try:
                parts.append(str(c.get(row)))
            except Exception:       # noqa: BLE001
                continue
        return " ".join(parts).casefold()

    # -------- 业务
    def set_rows(self, rows) -> None:
        """换一批数据。

        排序与筛选项都是**用户意图**，换数据时保留（重扫一遍表格还按原来的
        排法看，才符合预期）；勾选则清空 —— 旧行对象已经不在了。

        数据换了就老实走整表 reset：行数可能一样但内容全变了，
        留着旧的选中行只会让人看错。
        """
        self._all = list(rows)
        self._checked.clear()
        self._rebuild(reset=True)
        self.checkChanged.emit()

    def rows(self) -> list:
        return self._rows

    def row_at(self, i: int):
        return self._rows[i] if 0 <= i < len(self._rows) else None

    def checked_rows(self) -> list:
        """全部勾选的行，按源顺序。

        注意：**不受筛选影响** —— 被筛掉的勾选行照样返回。
        这个函数的结果直接喂给「删除 / 禁用 / 清理」，少返回一行就等于
        悄悄漏做一项，用户看着「已选 20 项」却只处理了看得见的 3 项。
        """
        return [r for r in self._all if id(r) in self._checked]

    def checked_count(self) -> int:
        return len(self.checked_rows())

    def check_all(self, on: bool) -> None:
        """全选 / 全不选。全选只作用于**当前显示的行**（筛选后全选 = 选筛出来的）。"""
        if on:
            picked = {id(r) for r in self._rows if self._can_check(r)}
            self._checked |= picked
        else:
            self._checked.clear()
        self._emit_check_changed()

    def check_rows(self, rows, on: bool = True) -> None:
        """按行对象勾选 —— 排序筛选之后行号会变，用对象才可靠。"""
        for r in rows or ():
            if on:
                if self._can_check(r):
                    self._checked.add(id(r))
            else:
                self._checked.discard(id(r))
        self._emit_check_changed()

    def check_indices(self, indices, on: bool = True) -> None:
        """按**当前显示位置**勾选。行的排序会变，新代码请用 `check_rows`。"""
        self.check_rows([self._rows[i] for i in indices
                         if 0 <= i < len(self._rows)], on)

    def _emit_check_changed(self) -> None:
        if self._rows:
            self.dataChanged.emit(self.index(0, 0),
                                  self.index(len(self._rows) - 1, 0),
                                  [Qt.CheckStateRole])
        self.checkChanged.emit()


# 排序键：数字要按数值大小排，别按字符串（"10" < "9"）。
# 表里大量单元格是「12.3 GB」「412 MB」「26%」这种带单位的，纯 float 解析不了，
# 所以拆成「数字 + 单位」再换算成同一个量纲；认不出来的当文字排。
_NUM_RE = re.compile(r"^([+-]?[\d,]*\.?\d+)\s*([^\d\s]{0,3})$")
# 显示串上常带的装饰（"×138"、"≈ 3.2 GB"），不去掉的话会被当成文字排
_DECOR = "×≈~＞<>「」 "
_VER_RE = re.compile(r"^\d+(\.\d+){2,}$")
_UNITS = {
    "b": 1, "kb": 1024, "mb": 1024 ** 2, "gb": 1024 ** 3, "tb": 1024 ** 4,
    "字节": 1, "kb/s": 1024, "mb/s": 1024 ** 2,
    "ms": 1e-3, "s": 1.0, "秒": 1.0, "分钟": 60.0,
    "%": 1.0, "个": 1.0, "项": 1.0, "条": 1.0,
}


def sort_key(text) -> tuple:
    """把单元格文字折成可比较的元组。

    返回 `(rank, num, ver, text)` 四元组，**每一项都必须是这个形状**，
    否则排序时会拿 float 和 tuple 比大小直接抛 TypeError：

    - `rank=0` 数字 / 带单位（按 `num` 比）
    - `rank=1` 版本号（按 `ver` 元组比，`10.0.26100.9731` 才不会排到 `10.0.9` 前面）
    - `rank=2` 普通文字（按 `text` 比）
    - `rank=3` 空值（空串、`—`、`-`）排最后
    """
    s = str(text if text is not None else "").strip()
    if not s or s in ("—", "-", "--", "N/A", "n/a", "未知"):
        return (3, 0.0, (), "")
    s = s.lstrip(_DECOR)
    if _VER_RE.match(s):
        return (1, 0.0, tuple(int(x) for x in s.split(".")), "")
    m = _NUM_RE.match(s)
    if m:
        try:
            num = float(m.group(1).replace(",", ""))
        except ValueError:
            return (2, 0.0, (), s.casefold())
        unit = m.group(2).casefold()
        if unit in _UNITS:
            return (0, num * _UNITS[unit], (), "")
        return (0, num, (), "")
    return (2, 0.0, (), s.casefold())


def make_table(model: RowModel, row_height: int = 26) -> QTableView:
    """统一表格外观：隔行底色、整行选择、隐藏竖表头、末列拉伸。

    点表头排序在这里统一挂上（三态：升序 → 降序 → 原顺序）。
    三十九张表原来点表头全都没反应，用户以为漏了功能。
    """
    tv = QTableView()
    tv.setModel(model)
    tv.setAlternatingRowColors(True)
    tv.setSelectionBehavior(QAbstractItemView.SelectRows)
    # 原来只能选一行。现场最常见的动作是「挑几行贴进工单」，
    # 单选逼着用户一行行抄，所以放开成 Ctrl / Shift 多选。
    tv.setSelectionMode(QAbstractItemView.ExtendedSelection)
    tv.setShowGrid(False)
    tv.setWordWrap(False)
    tv.verticalHeader().setVisible(False)
    tv.verticalHeader().setDefaultSectionSize(row_height)
    hh = tv.horizontalHeader()
    hh.setHighlightSections(False)
    hh.setStretchLastSection(True)
    hh.setSectionsMovable(False)
    for i, c in enumerate(model.cols):
        if c.width > 0 and not c.stretch:
            tv.setColumnWidth(i, c.width)
        elif c.stretch:
            hh.setSectionResizeMode(i, QHeaderView.Stretch)

    # 排序交给 Qt 自己驱动：`setSortingEnabled(True)` 之后点表头由 QHeaderView
    # 改指示器、再回调 `model.sort(column, order)`。**不自己连 sectionClicked**：
    # 在表头的事件处理里动手（改指示器 / 重置模型）会让 header 的 section 数据
    # 失效，354 行的表实测必崩、50 行的表看不出来 —— 这个坑踩过一次。
    #
    # 先把指示器设成 -1 再开排序，否则 Qt 会立刻按第 0 列排一遍。
    hh.setSortIndicatorShown(True)
    hh.setSortIndicator(-1, Qt.AscendingOrder)
    if model.checkable and model.cols:
        # 勾选框那列没数据可排，别让用户点了没反应还以为坏了
        hh.setSectionsClickable(True)
    tv.setSortingEnabled(True)
    attach_table_menu(tv, model)      # 每张表自带复制 / 导出 CSV
    return tv


class TablePane(QWidget):
    """表格 + 顶部筛选框，给行数多的表用（设备 354 行、IO 告警 418 行）。

    不加这个包装器的表照样能点表头排序，只是没有筛选框。
    """

    def __init__(self, model: RowModel, row_height: int = 26,
                 placeholder: str = "输入关键字筛选…", parent=None):
        super().__init__(parent)
        self.model = model
        self.view = make_table(model, row_height)
        self.edit = QLineEdit()
        self.edit.setPlaceholderText(placeholder)
        self.edit.setClearButtonEnabled(True)
        self.edit.setMinimumWidth(200)
        self.edit.setMaximumWidth(280)
        self.hint = QLabel("")
        self.hint.setProperty("role", "faint")   # 复用主题里的 11px 小字
        self.edit.textChanged.connect(self._on_text)

        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(8)
        top.addWidget(self.edit, 0)
        top.addWidget(self.hint, 0)
        top.addStretch(1)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        lay.addLayout(top)
        lay.addWidget(self.view, 1)
        # 数据换了（set_rows）会走 reset，顺手把「共 N 行 / 筛出 M 行」刷新，
        # 页面不用记得每次填完表都来喊一声
        self.model.modelReset.connect(self._sync_hint)
        self._sync_hint()

    def _on_text(self, text: str) -> None:
        self.model.set_filter(text)
        self._sync_hint()

    def _sync_hint(self) -> None:
        total = self.model.total_count()
        shown = len(self.model.rows())
        if total == 0:
            self.hint.setText("")
        elif shown == total:
            self.hint.setText("共 %d 行" % total)
        else:
            self.hint.setText("筛出 %d / %d 行" % (shown, total))

    def refresh_hint(self) -> None:
        """数据换过之后调一下（set_rows 不会自动通知外面）。"""
        self._sync_hint()


class Worker(QThread):
    """后台跑一个 fn(tick, stop_flag)，可随时停止。

    约定 fn 的签名：fn(tick: Callable[[int,int,str], None], stop: list[bool]) -> object
    """

    tick = pyqtSignal(int, int, str)
    done = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, fn, parent=None):
        super().__init__(parent)
        self.fn = fn
        self.stop_flag = [False]

    def run(self) -> None:
        try:
            self.done.emit(self.fn(self.tick.emit, self.stop_flag))
        except Exception as exc:        # noqa: BLE001
            self.failed.emit(f"{type(exc).__name__}: {exc}")

    def stop(self) -> None:
        self.stop_flag[0] = True



def run_bg(parent, fn, on_done, on_tick=None, on_failed=None) -> Worker:
    """起一个后台任务并把信号接好；parent 负责持有引用防止被 GC。"""
    w = Worker(fn, parent)
    holders = parent.__dict__.setdefault("_workers", [])
    holders.append(w)

    def _cleanup():
        if w in holders:
            holders.remove(w)

    w.done.connect(lambda r: (_cleanup(), on_done(r)))
    if on_tick:
        w.tick.connect(on_tick)
    w.failed.connect(lambda m: (_cleanup(), on_failed(m) if on_failed else None))
    w.finished.connect(w.deleteLater)
    w.start()
    return w


# 一次操作里要连收多个页面的线程时（切主题 / 关窗），给一个**共享**的等待窗口，
# 否则每个页面各自等满预算：体检 1.2s + 清理 1.2s + …… 叠起来能到好几秒。
_WAIT_DEADLINE = [0.0]


def begin_wait_window(budget_ms: int = 1000) -> None:
    _WAIT_DEADLINE[0] = time.monotonic() + budget_ms / 1000.0


def end_wait_window() -> None:
    _WAIT_DEADLINE[0] = 0.0


def stop_all(parent, budget_ms: int = 1200) -> None:
    """停掉 parent 名下的全部后台任务。

    关键是**总预算**而不是逐个等待：以前每个线程 wait(1500)，
    体检页同时挂 6 个扫描时切主题 / 关窗要卡 5 秒以上。
    现在先统一置停止位，再共用一个总预算，最早停的线程把时间省给后面的。
    外层如果开了 `begin_wait_window()`，就以那个共享窗口为准。
    """
    workers = list(parent.__dict__.get("_workers", []))
    if not workers:
        return
    for w in workers:
        try:
            w.stop()
        except Exception:       # noqa: BLE001
            pass
    deadline = time.monotonic() + budget_ms / 1000.0
    if _WAIT_DEADLINE[0] > 0:
        deadline = min(deadline, _WAIT_DEADLINE[0])
    for w in workers:
        left = deadline - time.monotonic()
        if left <= 0:
            break
        try:
            w.wait(int(left * 1000) + 1)
        except Exception:       # noqa: BLE001
            pass


# ---------------------------------------------------------------- 复制与导出
# 八个工作台一共三十来张表，用户现场最常做的是「把这几行贴到邮件/工单里」。
# 原来只能一行行手抄，所以这里统一给每张表装一个右键菜单：
# 复制选中 / 复制全部 / 导出 CSV。make_table 里自动挂，页面不用改。

def _visible_cols(model: "RowModel") -> list:
    """勾选框那一列不是数据，复制和导出都要跳过。"""
    cols = model.cols
    if model.checkable and cols:
        return list(cols[1:])
    return list(cols)


def _cell(model: "RowModel", col, row) -> str:
    if not col.get:
        return ""
    try:
        return str(col.get(row)).replace("\t", " ").replace("\n", " ")
    except Exception:       # noqa: BLE001
        return ""


def table_tsv(model: "RowModel", rows=None) -> str:
    """整张表（或指定行）转成 TSV。粘到 Excel / WPS 里会自动分列。"""
    rows = model.rows() if rows is None else list(rows)
    cols = _visible_cols(model)
    out = ["\t".join(c.title for c in cols)]
    for r in rows:
        out.append("\t".join(_cell(model, c, r) for c in cols))
    return "\n".join(out)


def view_tsv(view: QTableView, rows=None) -> str:
    """从表格视图读 TSV。

    直接问 Qt 要单元格文字，所以对 `RowModel` 之外的模型
    （日志页的 EventModel、转储页的 ModuleModel）一样管用。
    """
    m = view.model()
    if m is None:
        return ""
    ncol = m.columnCount()
    nrow = m.rowCount()
    rows = list(range(nrow)) if rows is None else list(rows)
    start = 0
    # 勾选框那列的表头是空的，不是数据
    if ncol and not str(m.headerData(0, Qt.Horizontal, Qt.DisplayRole) or "").strip():
        start = 1
    head = [str(m.headerData(c, Qt.Horizontal, Qt.DisplayRole) or "")
            for c in range(start, ncol)]
    out = ["\t".join(head)]
    for r in rows:
        if not (0 <= r < nrow):
            continue
        cells = []
        for c in range(start, ncol):
            v = m.data(m.index(r, c), Qt.DisplayRole)
            cells.append(str(v).replace("\t", " ").replace("\n", " ")
                         if v is not None else "")
        out.append("\t".join(cells))
    return "\n".join(out)


# 复制 / 导出只保留「读视图」这一版：视图对 RowModel 之外的模型同样管用。
# 曾有一份按 RowModel 直读的同名同签名实现，被下面这版静默盖掉，
# 成了永远跑不到的死代码 —— 别再往这里加第二个同名函数。
def copy_table(view: QTableView, rows=None) -> int:
    """复制到剪贴板，返回复制的行数。"""
    from PyQt5.QtWidgets import QApplication

    if rows is None:
        rows = list(range(view.model().rowCount())) if view.model() else []
    rows = list(rows)
    if not rows:
        return 0
    QApplication.clipboard().setText(view_tsv(view, rows))
    return len(rows)


def export_table_csv(view: QTableView, path: str, rows=None) -> int:
    """导出 CSV。用 utf-8-sig：不带 BOM 的话 Excel 打开中文全是乱码。"""
    m = view.model()
    if m is None:
        return 0
    ncol = m.columnCount()
    nrow = m.rowCount()
    rows = list(range(nrow)) if rows is None else list(rows)
    start = 0
    if ncol and not str(m.headerData(0, Qt.Horizontal, Qt.DisplayRole) or "").strip():
        start = 1
    with io.open(path, "w", encoding="utf-8-sig", newline="") as f:
        wr = csv.writer(f)
        wr.writerow([str(m.headerData(c, Qt.Horizontal, Qt.DisplayRole) or "")
                     for c in range(start, ncol)])
        for r in rows:
            if not (0 <= r < nrow):
                continue
            wr.writerow([
                "" if m.data(m.index(r, c), Qt.DisplayRole) is None
                else str(m.data(m.index(r, c), Qt.DisplayRole))
                for c in range(start, ncol)])
    return len(rows)


def attach_table_menu(view: QTableView, model=None, title: str = "") -> None:
    """给表格装右键菜单：复制选中 / 复制全部 / 导出 CSV。

    重复调用直接返回（主题重建时表格是新的，不会重复挂）。
    """
    if getattr(view, "_menu_on", False):
        return
    view._menu_on = True
    view.setContextMenuPolicy(Qt.CustomContextMenu)

    def _pop(pos):
        from PyQt5.QtWidgets import QFileDialog, QMenu

        from . import notice

        m = view.model()
        if m is None:
            return
        picked = _picked_rows(view)
        n_all = m.rowCount()
        menu = QMenu(view)
        act_sel = menu.addAction("复制选中 %d 行" % len(picked) if picked
                                 else "复制选中行")
        act_sel.setEnabled(bool(picked))
        act_all = menu.addAction("复制全部 %d 行" % n_all)
        act_all.setEnabled(n_all > 0)
        menu.addSeparator()
        act_reset = menu.addAction("恢复原顺序")
        # 排歪了要能退回去：表头点两下是升降序，第三下退回原顺序这个手势
        # 交给我们自己连 sectionClicked 会在表头事件里动模型 —— 会崩，别加。
        act_reset.setEnabled(bool(getattr(m, "sort_state", None)
                                 and m.sort_state()[0] >= 0))
        act_csv = menu.addAction("导出 CSV…")
        act_csv.setEnabled(n_all > 0)
        chosen = menu.exec_(view.viewport().mapToGlobal(pos))
        if chosen is act_reset:
            m.clear_sort()
            hh = view.horizontalHeader()
            hh.setSortIndicator(-1, Qt.AscendingOrder)
            notice.note(view, "ok", "已恢复源顺序")
        elif chosen is act_sel:
            notice.note(view, "ok", "已复制 %d 行到剪贴板" % copy_table(view, picked))
        elif chosen is act_all:
            notice.note(view, "ok",
                        "已复制全部 %d 行到剪贴板" % copy_table(view, range(n_all)))
        elif chosen is act_csv:
            name = title or "-".join(
                str(m.headerData(c, Qt.Horizontal, Qt.DisplayRole) or "")
                for c in (1, 2) if c < m.columnCount()).strip("-")
            base = "%s_%s.csv" % (name or "表格",
                                  datetime.now().strftime("%Y%m%d_%H%M"))
            path, _ = QFileDialog.getSaveFileName(
                view, "导出 CSV",
                os.path.join(os.path.expanduser("~"), "Desktop", base),
                "CSV (*.csv)")
            if path:
                try:
                    n = export_table_csv(view, path)
                    notice.note(view, "ok", "已导出 %d 行到 %s"
                                % (n, os.path.basename(path)))
                except Exception as exc:        # noqa: BLE001
                    notice.note(view, "error", "导出失败：%s" % exc)

    view.customContextMenuRequested.connect(_pop)


def _picked_rows(view: QTableView) -> list:
    """当前选中的行号。没选中时返回空表（调用方据此禁用菜单项）。"""
    sel = view.selectionModel()
    if sel is None:
        return []
    return sorted({i.row() for i in sel.selectedRows()})
