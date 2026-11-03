# -*- coding: utf-8 -*-
"""硬件错误页签：WHEA CPER 归类 + 蓝屏/内存诊断/IO 汇总。

代码体与拆分前逐字节一致（`tools/split_health.py` 按 AST 行区间搬的）；
状态全在 `self` 上 —— 这里只换文件，不改结构。
"""
from __future__ import annotations

from datetime import datetime

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (QHBoxLayout, QLabel, QSplitter, QVBoxLayout, QWidget)

from ...core import hwevent
from ..tables import Col, RowModel, TablePane, make_table, run_bg
from ..widgets import ghost_button
from .common import RIGHT, _level_color


class HwTabMixin:
    def _build_hw(self) -> QWidget:
        w = QWidget()
        l = QVBoxLayout(w)
        l.setContentsMargins(0, 8, 0, 0)
        l.setSpacing(6)
        row = QHBoxLayout()
        row.setSpacing(8)
        self.hw_headline = QLabel("尚未检查")
        self.hw_headline.setObjectName("H3")
        self.hw_headline.setWordWrap(True)
        row.addWidget(self.hw_headline, 1)
        self.btn_hw = ghost_button("重新检查", "refresh")
        self.btn_hw.clicked.connect(self.load_hw)
        row.addWidget(self.btn_hw)
        l.addLayout(row)
        hint = QLabel("只统计 WHEA 记录里的 CPER 错误记录，并按严重级别与部件归类；"
                      "「错误源通知」（每次开机刷几百条）不计入。"
                      "已纠正错误单次不致命，密度高才说明硬件在劣化。")
        hint.setProperty("role", "faint")
        hint.setWordWrap(True)
        l.addWidget(hint)

        split = QSplitter(Qt.Vertical)
        self.hw_model = RowModel([
            Col("级别", 68, lambda r: r.sev, color_fn=_level_color,
                tip=lambda r: r.sev),
            Col("部件", 176, lambda r: r.part, tip=lambda r: r.part),
            Col("次数", 58, lambda r: str(r.count), align=RIGHT),
            Col("最近7天", 72, lambda r: str(r.recent7), align=RIGHT,
                color_fn=lambda r: "warn" if r.recent7 else ""),
            Col("首次", 132, lambda r: r.first.strftime("%Y-%m-%d %H:%M")
                if r.first else "—"),
            Col("最近", 132, lambda r: r.last.strftime("%Y-%m-%d %H:%M")
                if r.last else "—"),
            Col("处置建议", 0, lambda r: r.hint or "—", color="dim",
                stretch=True, tip=lambda r: r.hint),
        ], parent=self)
        self.hw_pane = TablePane(self.hw_model, 28, "筛选部件 / 级别…")
        split.addWidget(self.hw_pane)

        box = QWidget()
        bl = QVBoxLayout(box)
        bl.setContentsMargins(0, 6, 0, 0)
        bl.setSpacing(3)
        cap = QLabel("蓝屏 / 内存诊断 / 磁盘 IO（与上面的硬件错误时间对照）")
        cap.setProperty("role", "faint")
        bl.addWidget(cap)
        self.extra_model = RowModel([
            Col("类别", 96, lambda r: r[0], color_fn=lambda r: r[3]),
            Col("时间", 152, lambda r: r[1].strftime("%Y-%m-%d %H:%M:%S")
                if r[1] else "—"),
            Col("内容", 0, lambda r: r[2], color="dim", stretch=True,
                tip=lambda r: r[2]),
        ], parent=self)
        bl.addWidget(make_table(self.extra_model, row_height=25))
        split.addWidget(box)
        split.setSizes([250, 280])
        l.addWidget(split, 1)
        return w

    def load_hw(self) -> None:
        if self._busy["hw"]:
            return
        self._busy["hw"] = True
        self.status.emit("正在聚合 WHEA 硬件错误记录…")

        def job(tick, stop):
            return hwevent.scan(
                stop_flag=lambda: bool(stop[0]),
                progress=lambda d, t, m: tick(d, t, m))

        def done(rep):
            self._busy["hw"] = False
            self._section_done("hw")
            self.hw_rep = rep
            self._fill_hw()
            if rep.fatal:
                val, sub, color = f"{rep.fatal} 条致命", "必须定位部件", "crit"
            elif rep.corrected:
                val = f"{rep.corrected} 条已纠正"
                sub = ("最近 7 天仍有" if any(r.recent7 for r in rep.rows)
                       else "近期无新增")
                color = ("warn" if any(r.level == "warn" for r in rep.rows)
                         else "info")
            elif rep.io_total:
                val, sub, color = "无 WHEA", f"磁盘 IO 告警 {rep.io_total} 条", "info"
            else:
                val, sub, color = "未发现", "无硬件错误记录", "ok"
            self.kpi_hw.set_value(val, sub, color)
            self.status.emit("硬件错误检查完成：" + rep.headline)

        run_bg(self, job, done, None, self._fail)

    def _fill_hw(self) -> None:
        rep = self.hw_rep
        if rep is None:
            return
        self.hw_model.set_rows(list(rep.rows))
        info = "　（错误源通知 %s 条已排除）" % sum(rep.info_ids.values()) \
            if rep.info_ids else ""
        self.hw_headline.setText("%s%s" % (rep.headline, info))
        rows: list[tuple] = []
        for when, code, msg in rep.bugchecks:
            rows.append(("蓝屏", when, f"BugCheck {code} · {msg}", "crit"))
        if rep.memdiag:
            for when, eid, txt in rep.memdiag:
                rows.append(("内存诊断", when, txt, "warn"))
        elif rep.memdiag_note:
            rows.append(("内存诊断", None, rep.memdiag_note, "faint"))
        # 磁盘 IO 按事件类型汇总，不逐条铺 —— 一次复位会连带刷四五条重复
        if rep.io_counts:
            parts = "、".join(f"ID {k}×{v}" for k, v in
                             sorted(rep.io_counts.items(), key=lambda x: -x[1]))
            last = rep.io_rows[0][0] if rep.io_rows else None
            lv = ("crit" if rep.io_counts.get(7) or rep.io_counts.get(51)
                  else "warn")
            rows.append(("磁盘 IO", last,
                         f"共 {rep.io_total} 条：{parts}"
                         f"　（明细见「磁盘 SMART」页）", lv))
        # 时间倒序；没有时间的（如「从未运行过内存诊断」）排最后
        rows.sort(key=lambda x: x[1] or datetime.min, reverse=True)
        self.extra_model.set_rows(rows)
