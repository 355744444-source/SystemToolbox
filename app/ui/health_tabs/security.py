# -*- coding: utf-8 -*-
"""安全基线页签：Defender / 防火墙 / UAC / SMBv1 / RDP 等只读检查。

代码体与拆分前逐字节一致（`tools/split_health.py` 按 AST 行区间搬的）；
状态全在 `self` 上 —— 这里只换文件，不改结构。
"""
from __future__ import annotations

from PyQt5.QtWidgets import (QHBoxLayout, QLabel, QVBoxLayout, QWidget)

from ...core import security
from ..tables import Col, RowModel, make_table, run_bg
from ..widgets import ghost_button
from .common import _level_color


class SecurityTabMixin:
    def _build_sec(self) -> QWidget:
        w = QWidget()
        l = QVBoxLayout(w)
        l.setContentsMargins(0, 8, 0, 0)
        l.setSpacing(6)
        row = QHBoxLayout()
        row.setSpacing(8)
        self.sec_headline = QLabel("尚未检查")
        self.sec_headline.setObjectName("H3")
        self.sec_headline.setWordWrap(True)
        row.addWidget(self.sec_headline, 1)
        self.btn_sec = ghost_button("重新检查", "refresh")
        self.btn_sec.clicked.connect(self.load_security)
        row.addWidget(self.btn_sec)
        l.addLayout(row)
        hint = QLabel("只报现状、不改设置：这些项都会影响业务连通性（防火墙、UAC、SMB），"
                      "自动「优化」出了问题没人能复盘。每行给出该去哪儿改。")
        hint.setProperty("role", "faint")
        hint.setWordWrap(True)
        l.addWidget(hint)
        self.sec_model = RowModel([
            Col("检查项", 132, lambda r: r.name),
            Col("现状", 236, lambda r: r.value, tip=lambda r: r.value),
            Col("判定", 68, lambda r: r.level_text, color_fn=_level_color),
            Col("说明", 0, lambda r: r.detail, color="dim", stretch=True,
                tip=lambda r: r.detail),
            Col("该去哪儿改", 236, lambda r: r.where or "—", color="faint",
                tip=lambda r: r.where),
        ], parent=self)
        l.addWidget(make_table(self.sec_model, row_height=27), 1)
        return w

    def load_security(self) -> None:
        if self._busy["sec"]:
            return
        self._busy["sec"] = True
        self.status.emit("正在检查安全基线…")

        def job(tick, stop):
            tick(0, 2, "读取 Defender / 防火墙 / UAC")
            rep = security.scan()
            tick(1, 2, "整理结论")
            return rep

        def done(rep):
            self._busy["sec"] = False
            self._section_done("security")
            self.sec_rep = rep
            self.sec_model.set_rows(rep.rows)
            self.sec_headline.setText(
                f"{rep.headline}　（基线分 {rep.score}/100，"
                f"{rep.elapsed:.1f} 秒）")
            self.kpi_sec.set_value(
                f"{rep.score}" if rep.score >= 80 else f"{rep.score} 分",
                ("%d 项风险 · %d 项注意" % (len(rep.risks), len(rep.warns)))
                if (rep.risks or rep.warns) else "常见基线项正常",
                "ok" if not rep.risks else
                ("crit" if len(rep.risks) > 1 else "warn"))
            self.status.emit("安全基线完成：" + rep.headline)

        run_bg(self, job, done, None, self._fail)
