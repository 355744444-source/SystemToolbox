# -*- coding: utf-8 -*-
"""系统文件页签：DISM CheckHealth + CBS.log + 补丁级别 + WinSxS 体积。

代码体与拆分前逐字节一致（`tools/split_health.py` 按 AST 行区间搬的）；
状态全在 `self` 上 —— 这里只换文件，不改结构。
"""
from __future__ import annotations

from PyQt5.QtWidgets import (QHBoxLayout, QLabel, QVBoxLayout, QWidget)

from ...core import component
from ..tables import Col, RowModel, make_table, run_bg
from ..widgets import ghost_button
from .common import DISM_CN, _level_color


class ComponentTabMixin:
    def _build_file(self) -> QWidget:
        w = QWidget()
        l = QVBoxLayout(w)
        l.setContentsMargins(0, 8, 0, 0)
        l.setSpacing(6)
        row = QHBoxLayout()
        row.setSpacing(8)
        self.file_headline = QLabel("尚未运行")
        self.file_headline.setObjectName("H3")
        self.file_headline.setWordWrap(True)
        row.addWidget(self.file_headline, 1)
        self.btn_size = ghost_button("统计体积", "folder")
        self.btn_size.clicked.connect(self.measure_winsxs)
        row.addWidget(self.btn_size)
        self.btn_file = ghost_button("重新检查", "refresh")
        self.btn_file.clicked.connect(self.load_component)
        row.addWidget(self.btn_file)
        l.addLayout(row)
        hint = QLabel("组件存储健康走 DISM 只读检查（/CheckHealth，读缓存、秒级完成）；"
                      "CBS.log 只读末尾 8 MB，并把 0x800f0805 这类每次枚举包都会写的"
                      "常态噪音排除在外。发现可修复损坏时只给命令，本工具不动手。")
        hint.setProperty("role", "faint")
        hint.setWordWrap(True)
        l.addWidget(hint)
        self.file_model = RowModel([
            Col("检查项", 118, lambda r: r.name),
            Col("现状", 302, lambda r: r.value, tip=lambda r: r.value),
            Col("判定", 68, lambda r: r.level_text, color_fn=_level_color),
            Col("说明", 0, lambda r: r.detail, color="dim", stretch=True,
                tip=lambda r: r.detail),
            Col("该去哪儿改", 236, lambda r: r.where or "—", color="faint",
                tip=lambda r: r.where),
        ], parent=self)
        l.addWidget(make_table(self.file_model, row_height=27), 1)
        return w

    def load_component(self) -> None:
        if self._busy["file"]:
            return
        self._busy["file"] = True
        self.status.emit("正在检查组件存储与 CBS 日志…")

        def job(tick, stop):
            tick(0, 3, "运行 DISM 只读健康检查")
            rep = component.scan(with_dism=True)
            tick(2, 3, "整理结论")
            return rep

        def done(rep):
            self._busy["file"] = False
            self._section_done("component")
            self.comp_rep = rep
            self._fill_file()
            crit = sum(1 for r in rep.rows if r.level == "crit")
            warn = sum(1 for r in rep.rows if r.level == "warn")
            self.kpi_file.set_value(
                f"{crit} 项异常" if crit else
                (f"{warn} 项注意" if warn else "正常"),
                rep.ubr or rep.dism_text[:18] or "—",
                "crit" if crit else ("warn" if warn else "ok"))
            self.status.emit("系统文件检查完成：" + rep.headline)

        run_bg(self, job, done, None, self._fail)

    def _fill_file(self) -> None:
        rep = self.comp_rep
        if rep is None:
            return
        rows = list(rep.rows)
        if self._winsxs is not None:
            b, n, cut = self._winsxs
            for r in rows:
                if r.name == "组件存储体积":
                    r.value = "%.2f GB · %s 个文件%s" % (
                        b / 1073741824.0, f"{n:,}", "（未扫完）" if cut else "")
                    r.detail = ("已统计到 WinSxS 目录的实际占用；未扫完说明中途被中断，"
                                "结果只增不减，可再点一次补全" if cut else
                                "WinSxS 目录实际占用，已含系统硬链接的重复计数，"
                                "不等于可回收空间")
        self.file_model.set_rows(rows)
        self.file_headline.setText(
            "%s　（DISM：%s · 耗时 %.1f 秒）" % (
                rep.headline,
                DISM_CN.get(rep.dism_state, rep.dism_state or "未判定"),
                rep.elapsed))

    def measure_winsxs(self) -> None:
        """按需统计 WinSxS 体积：十几秒到一分钟，可中断。"""
        if self._busy["size"]:
            return
        self._busy["size"] = True
        self.btn_size.setEnabled(False)
        self.btn_size.setText("统计中…")
        self.status.emit("正在统计组件存储体积（切走页面会中断）…")

        def job(tick, stop):
            return component.measure_winsxs(
                stop_flag=lambda: bool(stop[0]),
                progress=lambda done, total, msg: tick(0, 0, msg))

        def done(res):
            self._busy["size"] = False
            self.btn_size.setEnabled(True)
            self.btn_size.setText("统计体积")
            self._winsxs = res
            b, n, cut = res
            self._fill_file()
            self.status.emit("组件存储体积：%.2f GB（%s 个文件）%s" % (
                b / 1073741824.0, f"{n:,}", "，未扫完" if cut else ""))

        run_bg(self, job, done, None, self._fail_size)

    def _fail_size(self, msg: str) -> None:
        self._busy["size"] = False
        self.btn_size.setEnabled(True)
        self.btn_size.setText("统计体积")
        self.status.emit(f"统计体积失败：{msg}")
