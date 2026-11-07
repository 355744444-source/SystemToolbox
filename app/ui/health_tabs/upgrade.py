# -*- coding: utf-8 -*-
"""Win11 升级体检页签：逐项判定 + 阻塞项说明。

代码体与拆分前逐字节一致（`tools/split_health.py` 按 AST 行区间搬的）；
状态全在 `self` 上 —— 这里只换文件，不改结构。
"""
from __future__ import annotations

from PyQt5.QtWidgets import (QHBoxLayout, QLabel, QVBoxLayout, QWidget)

from ...core import upgrade as up
from ..tables import Col, RowModel, make_table, run_bg
from ..widgets import ghost_button
from .common import _level_color


class UpgradeTabMixin:
    def _build_up(self) -> QWidget:
        w = QWidget()
        l = QVBoxLayout(w)
        l.setContentsMargins(0, 8, 0, 0)
        l.setSpacing(6)
        row = QHBoxLayout()
        row.setSpacing(8)
        self.up_headline = QLabel("尚未运行")
        self.up_headline.setObjectName("H3")
        self.up_headline.setWordWrap(True)
        row.addWidget(self.up_headline, 1)
        self.btn_up = ghost_button("重新体检", "refresh")
        self.btn_up.clicked.connect(self.load_upgrade)
        row.addWidget(self.btn_up)
        l.addLayout(row)
        hint = QLabel("只判断硬件与固件是否满足微软公布的最低要求；账号、区域、"
                      "激活状态不在判断范围内。CPU 代际按型号字符串估算，"
                      "至强 / 赛扬 / 奔腾等另有官方名单的型号会标「需确认」。")
        hint.setProperty("role", "faint")
        hint.setWordWrap(True)
        l.addWidget(hint)
        self.up_model = RowModel([
            Col("项目", 104, lambda r: r.name),
            Col("本机现状", 338, lambda r: r.current, tip=lambda r: r.current),
            Col("官方要求", 210, lambda r: r.required, color="dim"),
            Col("判定", 76, lambda r: r.verdict_text, color_fn=_level_color),
            Col("说明", 0, lambda r: r.note, color="dim", stretch=True,
                tip=lambda r: r.note),
        ], parent=self)
        l.addWidget(make_table(self.up_model, row_height=27), 1)
        return w

    def load_upgrade(self) -> None:
        if self._busy["up"]:
            return
        self._busy["up"] = True
        self.status.emit("正在进行 Win11 升级体检…")

        def job(tick, stop):
            tick(0, 2, "检查处理器 / 内存 / 磁盘")
            rep = up.check()
            tick(1, 2, "检查固件 / TPM / 安全启动")
            return rep

        def done(rep):
            self._busy["up"] = False
            self._section_done("upgrade")
            self.up_rep = rep
            self.up_model.set_rows(rep.items)
            self.up_headline.setText(rep.headline)
            bad = len(rep.blockers)
            self.kpi_up.set_value(
                "可升级" if rep.ready else f"不满足 {bad} 项",
                rep.cpu_gen or rep.cpu or "-",
                "ok" if rep.ready and not rep.cautions else
                ("warn" if rep.ready else "crit"))
            self.status.emit("升级体检完成：" + rep.headline)

        run_bg(self, job, done, None, self._fail)
