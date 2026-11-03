# -*- coding: utf-8 -*-
"""磁盘 SMART 页签：NVMe 寿命/温度/读写量 + 磁盘 IO 告警。

代码体与拆分前逐字节一致（`tools/split_health.py` 按 AST 行区间搬的）；
状态全在 `self` 上 —— 这里只换文件，不改结构。
"""
from __future__ import annotations

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (QHBoxLayout, QLabel, QSplitter, QVBoxLayout, QWidget)

from ...core import smart as smartm
from ..tables import Col, RowModel, TablePane, make_table, run_bg
from ..widgets import ghost_button
from .common import RIGHT, _level_color


class DiskTabMixin:
    def _build_disk(self) -> QWidget:
        w = QWidget()
        l = QVBoxLayout(w)
        l.setContentsMargins(0, 8, 0, 0)
        l.setSpacing(6)
        row = QHBoxLayout()
        row.setSpacing(8)
        self.disk_headline = QLabel("尚未检查")
        self.disk_headline.setObjectName("H3")
        self.disk_headline.setWordWrap(True)
        row.addWidget(self.disk_headline, 1)
        self.btn_disk = ghost_button("重新检查", "refresh")
        self.btn_disk.clicked.connect(self.load_smart)
        row.addWidget(self.btn_disk)
        l.addLayout(row)
        hint = QLabel("数据来自 Storport 存储健康事件里的 NVMe 健康日志原页"
                      "（寿命、可用备用、累计读写、通电时长、介质错误）。"
                      "健康日志是**开机约 11 分钟时**写的一条快照，不是此刻温度；"
                      "寿命与写入量这类慢变量不受影响。")
        hint.setProperty("role", "faint")
        hint.setWordWrap(True)
        l.addWidget(hint)

        split = QSplitter(Qt.Vertical)
        self.disk_model = RowModel([
            Col("磁盘", 236, lambda d: d.title, tip=lambda d: d.title),
            Col("总线", 62, lambda d: d.bus),
            Col("容量", 76, lambda d: f"{d.size_gb:.0f} GB" if d.size_gb else "—",
                align=RIGHT),
            Col("剩余寿命", 86, lambda d: d.life_text, align=RIGHT,
                color_fn=_level_color, tip=lambda d: d.life_tip),
            Col("温度", 62, lambda d: d.temp_text, align=RIGHT),
            Col("累计写入", 92, lambda d: d.write_text, align=RIGHT),
            Col("通电", 90,
                lambda d: f"{d.log.hours:,} 小时" if d.log else "—", align=RIGHT),
            Col("状态", 190, lambda d: d.status, color_fn=_level_color,
                tip=lambda d: d.hint or d.status),
            Col("处置建议", 0, lambda d: d.hint or "—", color="dim",
                stretch=True, tip=lambda d: d.hint),
        ], parent=self)
        split.addWidget(make_table(self.disk_model, row_height=28))

        box = QWidget()
        bl = QVBoxLayout(box)
        bl.setContentsMargins(0, 6, 0, 0)
        bl.setSpacing(3)
        cap = QLabel("磁盘 IO 异常（来自系统日志，与上面的 SMART 指标互相对照；"
                     "同一秒的同类事件已合并计数）")
        cap.setProperty("role", "faint")
        bl.addWidget(cap)
        self.io_model = RowModel([
            Col("时间", 152, lambda r: r[0].strftime("%Y-%m-%d %H:%M:%S")
                if r[0] else "—"),
            Col("事件", 72, lambda r: f"ID {r[1]}", align=RIGHT),
            Col("现象", 176, lambda r: r[2], color_fn=lambda r: r[4],
                tip=lambda r: r[2]),
            Col("次数", 54, lambda r: f"×{r[6]}" if r[6] > 1 else "",
                align=RIGHT, color="warn"),
            Col("设备/参数", 190, lambda r: r[3] or "—", color="faint",
                tip=lambda r: r[3]),
            Col("处置建议", 0, lambda r: r[5], color="dim", stretch=True,
                tip=lambda r: r[5]),
        ], parent=self)
        self.io_pane = TablePane(self.io_model, 25, "筛选事件 ID / 设备 / 现象…")
        bl.addWidget(self.io_pane)
        split.addWidget(box)
        split.setSizes([240, 290])
        l.addWidget(split, 1)
        return w

    def load_smart(self) -> None:
        if self._busy["smart"]:
            return
        self._busy["smart"] = True
        self.status.emit("正在读取磁盘 SMART 与存储健康事件…")

        def job(tick, stop):
            return smartm.scan(
                stop_flag=lambda: bool(stop[0]),
                progress=lambda d, t, m: tick(d, t, m))

        def done(rep):
            self._busy["smart"] = False
            self._section_done("smart")
            self.disk_rep = rep
            self._fill_disk()
            n_crit = len(rep.risks)
            n_warn = len(rep.warns)
            io_lv = rep.io_level
            # SMART 正常但 IO 狂刷也是问题，KPI 不能只看盘本身
            if n_crit:
                val, color = f"{n_crit} 块需处理", "crit"
            elif n_warn:
                val, color = f"{n_warn} 块需关注", "warn"
            elif io_lv in ("crit", "warn"):
                val, color = f"{rep.io_total} 条 IO 告警", io_lv
            elif not rep.disks:
                val, color = "无数据", "info"
            else:
                val, color = f"{len(rep.disks)} 块健康", \
                    ("info" if io_lv == "info" else "ok")
            sub = (f"{len(rep.disks)} 块盘 · IO 告警 {rep.io_total} 条"
                   if rep.disks else "未读到健康事件")
            self.kpi_disk.set_value(val, sub, color)
            self.status.emit("磁盘检查完成：" + rep.headline)

        run_bg(self, job, done, None, self._fail)

    def _fill_disk(self) -> None:
        rep = self.disk_rep
        if rep is None:
            return
        self.disk_model.set_rows(list(rep.disks))
        extra = ""
        if rep.disks:
            d0 = rep.disks[0]
            extra = "　记录于 %s（开机后快照）" % d0.when_text
        self.disk_headline.setText("%s%s" % (rep.headline, extra))
        self.io_model.set_rows(list(rep.io_rows))
