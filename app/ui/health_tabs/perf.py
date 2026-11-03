# -*- coding: utf-8 -*-
"""性能采样页签：CPU/内存/磁盘/网络曲线 + 进程 TOP。

代码体与拆分前逐字节一致（`tools/split_health.py` 按 AST 行区间搬的）；
状态全在 `self` 上 —— 这里只换文件，不改结构。
"""
from __future__ import annotations

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (QHBoxLayout, QLabel, QSplitter, QVBoxLayout, QWidget)

from ..tables import Col, RowModel, make_table
from ..widgets import ghost_button
from ..optimize_page import ChipBar
from ..line_chart import LineChart
from .common import RIGHT, _cpu_color, _open


class PerfTabMixin:
    def _build_perf(self) -> QWidget:
        w = QWidget()
        outer = QVBoxLayout(w)
        outer.setContentsMargins(0, 8, 0, 0)
        outer.setSpacing(0)

        split = QSplitter(Qt.Vertical)
        split.setChildrenCollapsible(False)

        # ---- 上半：实时数字 + 两张曲线
        upper = QWidget()
        l = QVBoxLayout(upper)
        l.setContentsMargins(0, 0, 0, 0)
        l.setSpacing(6)

        row = QHBoxLayout()
        row.setSpacing(10)
        self.live_labels = {}
        for key, text in (("cpu", "CPU"), ("mem", "内存"), ("disk", "磁盘"),
                          ("net", "网络")):
            box = QVBoxLayout()
            box.setSpacing(0)
            cap = QLabel(text)
            cap.setProperty("role", "faint")
            val = QLabel("—")
            val.setObjectName("H2")
            box.addWidget(cap)
            box.addWidget(val)
            row.addLayout(box)
            self.live_labels[key] = val
        row.addStretch(1)
        self.btn_pause = ghost_button("暂停采样")
        self.btn_pause.clicked.connect(self._toggle_pause)
        row.addWidget(self.btn_pause)
        self.btn_reset = ghost_button("清空曲线", "refresh")
        self.btn_reset.clicked.connect(self._reset_chart)
        row.addWidget(self.btn_reset)
        l.addLayout(row)

        self.chart = LineChart([("cpu", "CPU", "accent"),
                                ("mem", "内存", "warn"),
                                ("disk", "磁盘", "crit")], 100, unit="%")
        l.addWidget(self.chart, 3)
        self.chart_net = LineChart([("net_in", "下行", "ok"),
                                    ("net_out", "上行", "accent")],
                                   100, auto=True, unit=" KB/s")
        l.addWidget(self.chart_net, 2)
        self.perf_note = QLabel("等待采样…")
        self.perf_note.setProperty("role", "faint")
        self.perf_note.setWordWrap(True)
        l.addWidget(self.perf_note)
        split.addWidget(upper)

        # ---- 下半：进程占用 TOP
        lower = QWidget()
        ll = QVBoxLayout(lower)
        ll.setContentsMargins(0, 8, 0, 0)
        ll.setSpacing(4)
        head = QHBoxLayout()
        head.setSpacing(8)
        cap = QLabel("进程占用 TOP")
        cap.setObjectName("H3")
        head.addWidget(cap)
        self.proc_chips = ChipBar([("CPU", "cpu"), ("内存", "mem"),
                                   ("句柄", "handles")], "cpu")
        self.proc_chips.changed.connect(lambda _k: self._fill_procs())
        head.addWidget(self.proc_chips)
        self.proc_count = QLabel("采集中（CPU 需要两个采样点）")
        self.proc_count.setProperty("role", "faint")
        head.addWidget(self.proc_count)
        head.addStretch(1)
        ll.addLayout(head)

        self.proc_model = RowModel([
            Col("进程", 226, lambda p: p.name, tip=lambda p: p.path or p.name),
            Col("PID", 66, lambda p: str(p.pid), mono=True, align=RIGHT),
            Col("CPU", 64, lambda p: f"{p.cpu:.2f}%", align=RIGHT,
                color_fn=_cpu_color),
            Col("内存", 78, lambda p: f"{p.mem_mb:.0f} MB", align=RIGHT),
            Col("私有", 78, lambda p: f"{p.private_mb:.0f} MB", align=RIGHT,
                color="dim"),
            Col("句柄", 58, lambda p: str(p.handles), mono=True, align=RIGHT,
                color="dim"),
            Col("线程", 54, lambda p: str(p.threads), mono=True, align=RIGHT,
                color="dim"),
            Col("启动", 0, lambda p: p.started_text, mono=True, color="faint",
                stretch=True, tip=lambda p: p.path),
        ], parent=self)
        self.proc_table = make_table(self.proc_model, row_height=24)
        self.proc_table.doubleClicked.connect(self._open_proc)
        ll.addWidget(self.proc_table, 1)
        split.addWidget(lower)
        # 上半区（曲线）权重更高：进程表是明细，曲线是趋势，别让明细把趋势挤没
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 2)
        split.setSizes([460, 300])

        outer.addWidget(split, 1)
        return w

    def _open_proc(self, index) -> None:
        if not index.isValid():
            return
        p = self.proc_model.row_at(index.row())
        if p and p.path:
            _open(p.path)

    def _fill_procs(self) -> None:
        rep = self._prep
        if rep is None:
            return
        key = self.proc_chips.current
        rows = rep.sorted_by(key, 40)
        self.proc_model.set_rows(rows)
        note = (f"TOP {len(rows)} / {rep.total} 个进程 · {rep.ncpu} 核 · "
                f"全机口径（与任务管理器一致，满一核约 {100.0 / max(1, rep.ncpu):.1f}%）"
                f" · {rep.elapsed * 1000:.0f} ms")
        if rep.denied:
            note += f" · {rep.denied} 个进程权限不足"
        if not rep.warm:
            note = "正在采第二个采样点，CPU 列马上就有数…"
        self.proc_count.setText(note)
