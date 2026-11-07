# -*- coding: utf-8 -*-
"""日志分析工作台。"""
from __future__ import annotations

import os
import threading
import xml.dom.minidom
from datetime import datetime, timedelta
from html import escape

from PyQt5.QtCore import (QAbstractTableModel, QModelIndex, QObject, Qt, QThread,
                          QTimer, pyqtSignal, pyqtSlot)
from PyQt5.QtGui import QColor
from PyQt5.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox,
                             QFileDialog, QFrame, QHBoxLayout, QLabel,
                             QLineEdit, QPlainTextEdit, QProgressBar,
                             QSpinBox, QSplitter, QTabWidget, QTableView,
                             QTextBrowser, QVBoxLayout, QWidget)

from .. import theme
from ..core import analysis as ana
from ..core import evtx, knowledge as kb
from ..core import report
from . import notice
from .tables import attach_table_menu
from .widgets import (PAGE_MARGINS, EmptyState, FindingCard, KpiCard,
                      ScrollColumn, bare_button, chip, ghost_button,
                      mono_font, plain_button, primary_button, ui_font)

TIME_RANGES = [
    ("最近 1 小时", timedelta(hours=1)),
    ("最近 6 小时", timedelta(hours=6)),
    ("最近 24 小时", timedelta(hours=24)),
    ("最近 7 天", timedelta(days=7)),
    ("最近 30 天", timedelta(days=30)),
    ("全部时间", None),
]

LEVEL_ORDER = [(1, "严重"), (2, "错误"), (3, "警告"), (4, "信息"), (5, "详细")]


# ------------------------------------------------------------------ 表格模型
class EventModel(QAbstractTableModel):
    HEADERS = ["时间", "级别", "来源", "ID", "含义", "摘要"]
    WIDTHS = [146, 60, 200, 56, 156, -1]
    # 内部行结构：(时间, level 数值, 级别名, 来源, ID, 含义, 摘要)
    COLMAP = {0: 0, 1: 2, 2: 3, 3: 4, 4: 5, 5: 6}

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rows: list[tuple] = []
        self._events: list[evtx.Event] = []

    def set_events(self, events: list[evtx.Event]) -> None:
        self.beginResetModel()
        self._events = events
        rows = []
        for e in events:
            meta = kb.event_meta(e.event_id)
            prov = e.provider or e.channel
            if prov.startswith("Microsoft-Windows-"):
                prov = prov[len("Microsoft-Windows-"):]
            rows.append((
                e.time_text,
                e.level,
                e.level_name,
                prov,
                str(e.event_id),
                meta.name_cn if meta else "",
                e.summary,
            ))
        self._rows = rows
        self.endResetModel()

    def event_at(self, row: int):
        if 0 <= row < len(self._events):
            return self._events[row]
        return None

    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.HEADERS)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role != Qt.DisplayRole:
            return None
        if orientation == Qt.Horizontal:
            return self.HEADERS[section]
        return None

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        row = self._rows[index.row()]
        col = index.column()
        level = row[1]
        t = theme.current()
        if role == Qt.DisplayRole:
            return row[self.COLMAP[col]]
        if role == Qt.ForegroundRole:
            if col == 1:
                return QColor(theme.level_color(level, t))
            if col == 0:
                return QColor(t["faint"])
            if col in (3,):
                return QColor(t["dim"])
            if col == 5:
                return QColor(t["text"])
            return QColor(t["dim"] if col in (2, 4) else t["text"])
        if role == Qt.FontRole:
            # 全表统一微软雅黑；级别列仅用加粗区分轻重
            if col in (0, 3):
                return ui_font(8)
            if col == 1:
                return ui_font(8, level <= 2)
        if role == Qt.TextAlignmentRole:
            if col in (1, 3):
                return int(Qt.AlignCenter)
            return int(Qt.AlignVCenter | Qt.AlignLeft)
        if role == Qt.ToolTipRole:
            ev = self._events[index.row()]
            tip = (ev.provider or ev.channel) + f" / {ev.event_id}\n" + ev.summary
            if ev.data_text and ev.data_text not in tip:
                tip += "\n" + ev.data_text
            return tip[:1200]
        return None

    def sort(self, column: int, order=Qt.AscendingOrder) -> None:
        if not self._rows:
            return
        pairs = list(zip(self._rows, self._events))
        if column == 1:
            key = lambda p: p[0][1]
        elif column == 3:
            key = lambda p: int(p[0][4])
        else:
            key = lambda p: p[0][self.COLMAP[column]]
        pairs.sort(key=key, reverse=(order == Qt.DescendingOrder))
        self.beginResetModel()
        self._rows = [p[0] for p in pairs]
        self._events = [p[1] for p in pairs]
        self.endResetModel()


# ------------------------------------------------------------------ 读取线程
class ReadWorker(QObject):
    progressed = pyqtSignal(int)
    finished = pyqtSignal(object, object)
    failed = pyqtSignal(str)

    def __init__(self, path: str, is_file: bool, xpath: str, limit: int):
        super().__init__()
        self.path = path
        self.is_file = is_file
        self.xpath = xpath
        self.limit = limit
        self.cancel_event = threading.Event()

    def cancel(self) -> None:
        self.cancel_event.set()

    @pyqtSlot()
    def run(self) -> None:
        import time
        t0 = time.time()
        try:
            events, stats = evtx.query_events(
                self.path, self.is_file, self.xpath, self.limit,
                progress=self.progressed.emit, cancel=self.cancel_event)
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc))
            return
        stats.elapsed = time.time() - t0
        self.finished.emit(events, stats)


# ------------------------------------------------------------------ 页面
class LogPage(QWidget):
    status = pyqtSignal(str)
    dataReady = pyqtSignal(object, object)   # events, analysis

    def __init__(self, parent=None):
        super().__init__(parent)
        self._events: list[evtx.Event] = []
        self._analysis: ana.LogAnalysis | None = None
        self._stats = None
        self._file_path = ""
        self._thread: QThread | None = None
        self._worker: ReadWorker | None = None
        self._current_event = None
        self._sort_col = 0
        self._sort_order = Qt.DescendingOrder
        self._build()

    # ---------------- UI
    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(*PAGE_MARGINS)
        root.setSpacing(12)

        # 页头：其余八个工作台都有「页名」这一行，只有日志和蓝屏没有 ——
        # 切到这两页时页头整行消失，看起来像没加载完。这里只补页名，
        # 一句话说明交给全局页头右侧的 hint（同一个意思不写两遍）。
        top = QHBoxLayout()
        top.setSpacing(8)
        title = QLabel("日志分析")
        title.setProperty("role", "H2")
        top.addWidget(title)
        top.addStretch(1)
        root.addLayout(top)

        root.addWidget(self._build_source_card())
        root.addLayout(self._build_kpi_row())

        split = QSplitter(Qt.Horizontal)
        split.setChildrenCollapsible(False)
        split.addWidget(self._build_table_side())
        split.addWidget(self._build_side_tabs())
        split.setStretchFactor(0, 1)
        split.setStretchFactor(1, 0)
        split.setSizes([900, 470])
        root.addWidget(split, 1)

        self.empty = EmptyState(
            "log", "还没有读取日志",
            "选择通道点「开始分析」读取本机实时日志，"
            "或把现场拷回来的 .evtx 文件直接拖进窗口。")
        self.table_holder.layout().addWidget(self.empty)
        self.empty.hide()

    def _build_source_card(self) -> QWidget:
        card = QFrame()
        card.setProperty("role", "card")
        lay = QVBoxLayout(card)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(9)

        row1 = QHBoxLayout()
        row1.setSpacing(8)
        lbl = QLabel("事件来源")
        lbl.setProperty("role", "dim")
        row1.addWidget(lbl)

        self.src_combo = QComboBox()
        self.src_combo.setMinimumWidth(258)
        for text, path in evtx.COMMON_CHANNELS:
            self.src_combo.addItem(text, path)
        self.src_combo.setCurrentIndex(0)
        row1.addWidget(self.src_combo)

        self.btn_open = plain_button("打开 .evtx 文件", "folder")
        self.btn_open.clicked.connect(self._pick_file)
        row1.addWidget(self.btn_open)

        self.file_chip = QLabel()
        self.file_chip.setProperty("role", "mono")
        self.file_chip.setStyleSheet(
            f"background:{theme.current()['accent_soft']}; color:{theme.current()['accent']};"
            "border-radius:10px; padding:3px 10px;")
        self.file_chip.hide()
        row1.addWidget(self.file_chip)

        self.btn_clear_file = bare_button("×")
        self.btn_clear_file.setFixedWidth(26)
        self.btn_clear_file.setToolTip("清除文件来源，回到实时通道")
        self.btn_clear_file.clicked.connect(self._clear_file)
        self.btn_clear_file.hide()
        row1.addWidget(self.btn_clear_file)

        row1.addSpacing(6)
        rng_lbl = QLabel("时间范围")
        rng_lbl.setProperty("role", "dim")
        row1.addWidget(rng_lbl)
        self.range_combo = QComboBox()
        for text, delta in TIME_RANGES:
            self.range_combo.addItem(text, delta)
        self.range_combo.setCurrentIndex(3)
        row1.addWidget(self.range_combo)

        cap_lbl = QLabel("上限")
        cap_lbl.setProperty("role", "dim")
        row1.addWidget(cap_lbl)
        self.limit_spin = QSpinBox()
        self.limit_spin.setRange(1000, 500000)
        self.limit_spin.setSingleStep(10000)
        self.limit_spin.setValue(80000)
        self.limit_spin.setFixedWidth(96)
        self.limit_spin.setToolTip("最多读取多少条事件；文件很大时可适当提高")
        row1.addWidget(self.limit_spin)

        row1.addStretch(1)
        self.btn_export_html = plain_button("HTML 报告", "export")
        self.btn_export_html.setToolTip("导出现场分析报告（单文件 HTML）")
        self.btn_export_html.clicked.connect(self._export_html)
        self.btn_export_html.setEnabled(False)
        row1.addWidget(self.btn_export_html)

        self.btn_export_xlsx = plain_button("Excel 明细", "export")
        self.btn_export_xlsx.clicked.connect(self._export_xlsx)
        self.btn_export_xlsx.setEnabled(False)
        row1.addWidget(self.btn_export_xlsx)

        self.btn_run = primary_button("开始分析", "play")
        self.btn_run.clicked.connect(self._start)
        row1.addWidget(self.btn_run)

        self.btn_cancel = ghost_button("取消", "stop")
        self.btn_cancel.clicked.connect(self._cancel)
        self.btn_cancel.hide()
        row1.addWidget(self.btn_cancel)
        lay.addLayout(row1)

        row2 = QHBoxLayout()
        row2.setSpacing(6)
        lv = QLabel("级别")
        lv.setProperty("role", "dim")
        row2.addWidget(lv)
        self.level_chips: dict[int, QWidget] = {}
        for level, name in LEVEL_ORDER:
            c = chip(name)
            c.setChecked(True)
            c.toggled.connect(lambda _v: self._apply_filter())
            self.level_chips[level] = c
            row2.addWidget(c)

        row2.addSpacing(10)
        self.only_key = QCheckBox("只看关键事件")
        self.only_key.setToolTip("仅显示知识库中标记为临界/高/中的事件")
        self.only_key.toggled.connect(lambda _v: self._apply_filter())
        row2.addWidget(self.only_key)

        row2.addSpacing(10)
        self.search = QLineEdit()
        self.search.setPlaceholderText("搜索来源 / 消息 / 数据（回车生效）")
        self.search.setClearButtonEnabled(True)
        self.search.setMinimumWidth(240)
        self.search.returnPressed.connect(self._apply_filter)
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(320)
        self._search_timer.timeout.connect(self._apply_filter)
        self.search.textChanged.connect(lambda _t: self._search_timer.start())
        row2.addWidget(self.search, 1)

        self.progress = QProgressBar()
        self.progress.setFixedWidth(150)
        self.progress.setTextVisible(False)
        self.progress.hide()
        row2.addWidget(self.progress)
        lay.addLayout(row2)
        return card

    def _build_kpi_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(10)
        self.kpi_total = KpiCard("事件总数", "-", "尚未运行", "info")
        self.kpi_crit = KpiCard("严重", "-", "", "crit")
        self.kpi_err = KpiCard("错误", "-", "", "error")
        self.kpi_warn = KpiCard("警告", "-", "", "warn")
        self.kpi_crash = KpiCard("蓝屏 / 崩溃", "-", "", "crit")
        self.kpi_health = KpiCard("健康评分", "-", "", "ok")
        for k in (self.kpi_total, self.kpi_crit, self.kpi_err,
                  self.kpi_warn, self.kpi_crash, self.kpi_health):
            row.addWidget(k, 1)
        return row

    def _build_table_side(self) -> QWidget:
        box = QWidget()
        lay = QVBoxLayout(box)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)

        bar = QHBoxLayout()
        bar.setSpacing(8)
        self.table_title = QLabel("事件明细")
        self.table_title.setProperty("role", "H2")
        bar.addWidget(self.table_title)
        self.count_label = QLabel("")
        self.count_label.setProperty("role", "faint")
        bar.addWidget(self.count_label)
        bar.addStretch(1)
        self.btn_reset_filter = ghost_button("清除筛选")
        self.btn_reset_filter.clicked.connect(self._reset_filter)
        bar.addWidget(self.btn_reset_filter)
        lay.addLayout(bar)

        self.table_holder = QWidget()
        holder_lay = QVBoxLayout(self.table_holder)
        holder_lay.setContentsMargins(0, 0, 0, 0)
        holder_lay.setSpacing(0)

        self.model = EventModel(self)
        self.table = QTableView()
        self.table.setModel(self.model)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setShowGrid(False)
        self.table.setSortingEnabled(True)
        self.table.setWordWrap(False)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(28)
        self.table.horizontalHeader().setHighlightSections(False)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.horizontalHeader().setSortIndicator(0, Qt.DescendingOrder)
        for i, w in enumerate(EventModel.WIDTHS):
            if w > 0:
                self.table.setColumnWidth(i, w)
        attach_table_menu(self.table, self.model, "事件明细")
        self.table.selectionModel().currentRowChanged.connect(self._on_row_changed)
        holder_lay.addWidget(self.table)
        lay.addWidget(self.table_holder, 1)
        return box

    def _build_side_tabs(self) -> QWidget:
        self.tabs = QTabWidget()
        self.tabs.setMinimumWidth(380)
        self.tabs.setMaximumWidth(620)

        self.findings_col = ScrollColumn(10)
        self.tabs.addTab(self.findings_col, "诊断结论")

        self.detail = QTextBrowser()
        self.detail.setOpenExternalLinks(False)
        self.detail.setFrameShape(QFrame.NoFrame)
        self.tabs.addTab(self.detail, "事件详情")

        self.raw_xml = QPlainTextEdit()
        self.raw_xml.setReadOnly(True)
        self.raw_xml.setFrameShape(QFrame.NoFrame)
        self.raw_xml.setProperty("role", "tech")
        self.raw_xml.setFont(mono_font(8))
        self.tabs.addTab(self.raw_xml, "原始 XML")
        return self.tabs

    # ---------------- 数据流
    def _pick_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "选择事件日志文件", evtx.default_log_dirs()[0] if evtx.default_log_dirs() else "",
            "事件日志 (*.evtx);;所有文件 (*.*)")
        if path:
            self.set_file(path)

    def set_file(self, path: str) -> None:
        self._file_path = path
        self.file_chip.setText(os.path.basename(path))
        self.file_chip.setToolTip(path)
        self.file_chip.show()
        self.btn_clear_file.show()
        self.src_combo.setEnabled(False)
        self.range_combo.setCurrentIndex(5)  # 文件默认全部时间
        self.status.emit(f"已选择日志文件：{path}")
        self._start()

    def _clear_file(self) -> None:
        self._file_path = ""
        self.file_chip.hide()
        self.btn_clear_file.hide()
        self.src_combo.setEnabled(True)

    def _xpath(self) -> str:
        delta = self.range_combo.currentData()
        start = end = None
        if delta is not None:
            end = datetime.now()
            start = end - delta
        return evtx.build_xpath(start=start, end=end)

    def _start(self) -> None:
        if self._thread is not None:
            self.status.emit("正在读取，请稍候…")
            return
        is_file = bool(self._file_path)
        target = self._file_path if is_file else self.src_combo.currentData()
        if not target:
            return
        self.btn_run.setEnabled(False)
        self.btn_run.setText("读取中…")
        self.btn_cancel.show()
        self.progress.show()
        self.progress.setRange(0, 0)
        self.status.emit(f"正在读取 {os.path.basename(target)} …")

        self._thread = QThread(self)
        self._worker = ReadWorker(target, is_file, self._xpath(), self.limit_spin.value())
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.progressed.connect(self._on_progress)
        self._worker.finished.connect(self._on_events)
        self._worker.failed.connect(self._on_failed)
        self._thread.start()

    def _cancel(self) -> None:
        if self._worker is not None:
            self._worker.cancel()
            self.status.emit("已请求取消…")

    def _teardown_thread(self) -> None:
        if self._thread is not None:
            self._thread.quit()
            self._thread.wait(3000)
            self._thread.deleteLater()
            self._thread = None
            self._worker = None
        self.btn_run.setEnabled(True)
        self.btn_run.setText("开始分析")
        self.btn_cancel.hide()
        self.progress.hide()

    def _on_progress(self, n: int) -> None:
        self.status.emit(f"已读取 {n:,} 条事件…")

    def _on_failed(self, msg: str) -> None:
        self._teardown_thread()
        self.status.emit("读取失败：" + msg)
        notice.note(self, "error", "读取失败：%s" % msg)

    def _on_events(self, events, stats) -> None:
        self._teardown_thread()
        self._events = events or []
        self._stats = stats
        result = ana.analyze(self._events, stats)
        self._analysis = result
        self._apply_filter()
        self._show_findings()
        self._show_kpis()
        self.empty.setVisible(not self._events)
        self.table.setVisible(bool(self._events))
        if not self._events and stats is not None and stats.error:
            self.status.emit(stats.error)
        elif stats is not None:
            self.status.emit(f"读取完成：{len(self._events):,} 条事件，"
                             f"用时 {stats.elapsed:.1f}s")
        else:
            self.status.emit("读取完成")
        self.btn_export_html.setEnabled(bool(self._events))
        self.btn_export_xlsx.setEnabled(bool(self._events))
        self.dataReady.emit(self._events, result)

    def _show_kpis(self) -> None:
        a = self._analysis
        if a is None:
            return
        lc = a.level_counts
        self.kpi_total.set_value(f"{a.total:,}", a.span_short if a.total else "无数据")
        self.kpi_crit.set_value(f"{lc.get(1, 0):,}", "严重级别事件")
        self.kpi_err.set_value(f"{lc.get(2, 0):,}", "错误级别事件")
        self.kpi_warn.set_value(f"{lc.get(3, 0):,}", "警告级别事件")
        crashes = len(a.crash_times) if a.crash_times else 0
        self.kpi_crash.set_value(f"{crashes}", "次 BugCheck 记录" if crashes else "无蓝屏记录")
        color = "ok" if a.health >= 85 else ("warn" if a.health >= 60 else "error")
        self.kpi_health.set_value(f"{a.health}", f"{len(a.findings)} 项诊断发现", color)
        # 三个分支都要有：原来缺 else，先扫出 40 分再扫出 95 分时，
        # 数字变绿、圆点还留着上一次的红色
        if a.health < 60:
            self.kpi_health.set_accent("error")
        elif a.health < 85:
            self.kpi_health.set_accent("warn")
        else:
            self.kpi_health.set_accent("ok")

    def _show_findings(self) -> None:
        self.findings_col.clear()
        a = self._analysis
        if a is None:
            return
        head = QFrame()
        head.setProperty("role", "card")
        lay = QVBoxLayout(head)
        lay.setContentsMargins(14, 12, 14, 13)
        lay.setSpacing(6)
        t = theme.current()
        cap = QLabel("总体结论")
        cap.setStyleSheet(f"color:{t['accent']}; font-size:11px; font-weight:600;")
        lay.addWidget(cap)
        title = QLabel(a.headline)
        title.setWordWrap(True)
        title.setStyleSheet("font-size:14px; font-weight:600;")
        lay.addWidget(title)
        sub = QLabel(f"共 {a.total:,} 条事件 · {a.span_text}")
        sub.setProperty("role", "faint")
        sub.setWordWrap(True)
        lay.addWidget(sub)
        if a.notes:
            note = QLabel("；".join(a.notes[:3]))
            note.setWordWrap(True)
            note.setStyleSheet(f"color:{t['dim']}; font-size:11px;")
            lay.addWidget(note)
        self.findings_col.add(head)

        if not a.findings:
            self.findings_col.add(EmptyState(
                "info", "没有发现异常模式",
                "该时间窗口内没有蓝屏、掉电、磁盘、硬件或服务崩溃类事件。"))
            return
        for f in a.findings:
            card = FindingCard(f)
            card.clicked.connect(self._locate_events)
            self.findings_col.add(card)

    def _locate_events(self, event_ids: list) -> None:
        if not event_ids:
            return
        ids = set(int(i) for i in event_ids)
        subset = [e for e in self._events if e.event_id in ids]
        if not subset:
            return
        self.model.set_events(subset)
        self.table.scrollToTop()
        self.count_label.setText(f"已定位 {len(subset):,} 条（ID " +
                                 ", ".join(str(i) for i in sorted(ids)[:6]) + "）")
        self.status.emit(f"已按事件 ID 定位证据：{sorted(ids)}")

    def _reset_filter(self) -> None:
        for c in self.level_chips.values():
            c.setChecked(True)
        self.only_key.setChecked(False)
        self.search.clear()
        self._apply_filter()

    def _apply_filter(self) -> None:
        events = self._events
        levels = {lv for lv, c in self.level_chips.items() if c.isChecked()}
        text = self.search.text().strip().lower()
        only_key = self.only_key.isChecked()
        out = []
        for e in events:
            if e.level not in levels:
                continue
            if only_key:
                meta = kb.event_meta(e.event_id)
                if not meta or meta.severity == "提示":
                    continue
            if text:
                meta = kb.event_meta(e.event_id)
                blob = " ".join((e.provider, e.channel, str(e.event_id),
                                 e.message, e.data_text,
                                 meta.name_cn if meta else "")).lower()
                if text not in blob:
                    continue
            out.append(e)
        self.model.set_events(out)
        shown = len(out)
        total = len(events)
        if shown != total:
            self.count_label.setText(f"显示 {shown:,} / {total:,} 条")
        else:
            self.count_label.setText(f"{total:,} 条")
        if self.table.isVisible() and shown:
            self.table.selectRow(0)

    # ---------------- 跨工作台联动 / 主题重建
    def focus_time(self, when: datetime, minutes: int = 10) -> None:
        """把表格聚焦到某个时间点前后，用于从转储跳回日志看上下文。"""
        if not self._events or when is None:
            return
        lo = when - timedelta(minutes=minutes)
        hi = when + timedelta(minutes=minutes)
        subset = [e for e in self._events if e.time and lo <= e.time <= hi]
        if not subset:
            subset = [e for e in self._events
                      if e.time and abs((e.time - when).total_seconds()) <= 3600]
        self.model.set_events(subset)
        if subset:
            self.table.selectRow(0)
        self.count_label.setText(f"聚焦 {lo:%m-%d %H:%M} ~ {hi:%H:%M}，{len(subset)} 条")
        self.status.emit(f"已聚焦 {when:%Y-%m-%d %H:%M} 前后 {minutes} 分钟的事件")

    def state(self) -> tuple:
        return (self._events, self._analysis, self._file_path, self._stats)

    def restore(self, state: tuple) -> None:
        events, result, file_path, stats = state
        self._events = events or []
        self._analysis = result
        self._stats = stats
        if file_path:
            self._file_path = file_path
            self.file_chip.setText(os.path.basename(file_path))
            self.file_chip.setToolTip(file_path)
            self.file_chip.show()
            self.btn_clear_file.show()
            self.src_combo.setEnabled(False)
        if self._analysis is not None:
            self._apply_filter()
            self._show_findings()
            self._show_kpis()
            self.empty.setVisible(not self._events)
            self.table.setVisible(bool(self._events))
            self.btn_export_html.setEnabled(bool(self._events))
            self.btn_export_xlsx.setEnabled(bool(self._events))

    def _on_row_changed(self, cur, _prev) -> None:
        if not cur.isValid():
            return
        ev = self.model.event_at(cur.row())
        if ev is None:
            return
        self._current_event = ev
        self.detail.setHtml(self._detail_html(ev))
        self.raw_xml.setPlainText(self._pretty_xml(ev.raw_xml))

    def _detail_html(self, e: evtx.Event) -> str:
        t = theme.current()
        meta = kb.event_meta(e.event_id)
        lc = theme.level_color(e.level, t)
        chips = []
        if meta:
            chips.append(
                f"<span style='color:{t['accent']};background:{t['accent_soft']};"
                f"padding:2px 8px;border-radius:9px;font-size:11px;'>"
                f"{escape(meta.name_cn)}</span>")
        rows = [
            ("时间", e.time_text), ("级别", f"{e.level_name} ({e.level})"),
            ("来源", e.provider), ("事件 ID", str(e.event_id)),
            ("通道", e.channel), ("记录 ID", str(e.record_id)),
            ("计算机", e.computer or "-"),
            ("用户", (e.user or "-").replace("S-1-5-18", "SYSTEM")),
            ("进程 / 线程", f"{e.process_id} / {e.thread_id}"),
        ]
        table = "".join(
            f"<tr><td style='color:{t['dim']};padding:3px 14px 3px 0;"
            f"white-space:nowrap;'>{k}</td>"
            f"<td style='color:{t['text']};padding:3px 0;'>{escape(str(v))}</td></tr>"
            for k, v in rows)
        data_rows = "".join(
            f"<tr><td style='color:{t['dim']};padding:2px 14px 2px 0;"
            f"vertical-align:top;white-space:nowrap;'>{escape(str(k))}</td>"
            f"<td style='color:{t['text']};padding:2px 0;"
            f"word-break:break-all;'>{escape(str(v))}</td></tr>"
            for k, v in e.data.items()) if e.data else ""

        why = ""
        if meta:
            why = f"<div style='margin-top:12px;color:{t['dim']};font-size:12px;'>" \
                  f"<b style='color:{t['text']};'>为什么值得注意</b><br>{escape(meta.why)}</div>"
            if meta.advice:
                items = "".join(
                    f"<li style='margin-bottom:3px;'>{escape(a)}</li>"
                    for a in meta.advice)
                why += (f"<div style='margin-top:10px;color:{t['dim']};font-size:12px;'>"
                        f"<b style='color:{t['text']};'>建议动作</b>"
                        f"<ul style='margin:5px 0 0 -22px;color:{t['text']};'>{items}</ul></div>")

        msg = escape(e.message or e.summary or "（无消息）").replace("\n", "<br>")
        return f"""
<div style="font-family:'Microsoft YaHei'; font-size:12px; color:{t['text']};">
  <div style="color:{lc};font-size:11px;letter-spacing:1px;">
      {e.level_name.upper()} · EVENT {e.event_id}</div>
  <div style="font-size:15px;font-weight:600;margin:4px 0 6px 0;">
      {escape(meta.name_cn if meta else (e.provider or '事件'))}</div>
  <div>{''.join(chips)}</div>
  <table cellspacing="0" cellpadding="0" style="margin-top:10px;">{table}</table>
  <div style="margin-top:14px;color:{t['dim']};font-size:11px;letter-spacing:1px;">消息</div>
  <div style="background:{t['surface2']};border-radius:8px;padding:10px 12px;
              margin-top:5px;color:{t['text']};line-height:160%;">{msg}</div>
  {'<div style="margin-top:14px;color:' + t['dim'] + ';font-size:11px;letter-spacing:1px;">事件数据</div>' +
   '<div style="background:' + t['surface2'] + ';border-radius:8px;padding:10px 12px;margin-top:5px;">' +
   '<table cellspacing="0" cellpadding="0">' + data_rows + '</table></div>' if data_rows else ''}
  {why}
</div>"""

    @staticmethod
    def _pretty_xml(text: str) -> str:
        # 形参千万别叫 xml：会把顶上的 xml 模块盖成本地字符串，
        # xml.dom.minidom 立刻 AttributeError，再被 except 吞掉 ——
        # 表现是「XML 从来没格式化过」，还不报错。
        if not text:
            return ""
        try:
            dom = xml.dom.minidom.parseString(text.encode("utf-8"))
            return dom.toprettyxml(indent="  ")
        except Exception:
            return text

    # ---------------- 导出
    def _export_html(self) -> None:
        if not self._analysis:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "保存 HTML 报告",
            os.path.join(os.path.expanduser("~"), "Desktop",
                         f"日志分析报告_{datetime.now():%Y%m%d_%H%M}.html"),
            "网页 (*.html)")
        if not path:
            return
        try:
            report.export_log_html(path, self._events, self._analysis,
                                   source=self._file_path or self.src_combo.currentText())
            self.status.emit(f"报告已保存：{path}")
            self._open_after_save(path)
        except Exception as exc:  # noqa: BLE001
            notice.note(self, "error", "导出失败：%s" % exc)

    def _export_xlsx(self) -> None:
        if not self._analysis:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "保存 Excel 明细",
            os.path.join(os.path.expanduser("~"), "Desktop",
                         f"事件明细_{datetime.now():%Y%m%d_%H%M}.xlsx"),
            "Excel (*.xlsx)")
        if not path:
            return
        try:
            report.export_log_xlsx(path, self._events, self._analysis,
                                   source=self._file_path or self.src_combo.currentText())
            self.status.emit(f"明细已保存：{path}")
            self._open_after_save(path)
        except Exception as exc:  # noqa: BLE001
            notice.note(self, "error", "导出失败：%s" % exc)

    def _open_after_save(self, path: str) -> None:
        from PyQt5.QtCore import QUrl
        from PyQt5.QtGui import QDesktopServices
        QDesktopServices.openUrl(QUrl.fromLocalFile(path))
