# -*- coding: utf-8 -*-
"""设备与驱动页签：设备清单 + 驱动清单 + 四档筛选。

代码体与拆分前逐字节一致（`tools/split_health.py` 按 AST 行区间搬的）；
状态全在 `self` 上 —— 这里只换文件，不改结构。
"""
from __future__ import annotations

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (QHBoxLayout, QLabel, QSplitter, QVBoxLayout, QWidget)

from ...core import devices as dv
from ..tables import Col, RowModel, TablePane, run_bg
from ..optimize_page import ChipBar
from .common import RIGHT, _level_color


class DevTabMixin:
    def _build_dev(self) -> QWidget:
        w = QWidget()
        l = QVBoxLayout(w)
        l.setContentsMargins(0, 8, 0, 0)
        l.setSpacing(6)

        row = QHBoxLayout()
        row.setSpacing(8)
        self.dev_chips = ChipBar([("全部", "all"), ("异常", "bad"),
                                  ("已禁用", "off"), ("已移除", "gone")], "all")
        self.dev_chips.changed.connect(lambda _k: self._fill_dev())
        row.addWidget(self.dev_chips, 1)
        self.dev_count = QLabel("")
        self.dev_count.setProperty("role", "faint")
        row.addWidget(self.dev_count)
        l.addLayout(row)

        split = QSplitter(Qt.Vertical)
        split.setChildrenCollapsible(False)

        upper = QWidget()
        ul = QVBoxLayout(upper)
        ul.setContentsMargins(0, 0, 0, 0)
        ul.setSpacing(4)
        head = QLabel("设备清单")
        head.setObjectName("H3")
        ul.addWidget(head)
        self.dev_model = RowModel([
            Col("设备", 258, lambda r: r.name, tip=lambda r: r.instance),
            Col("类别", 118, lambda r: r.cls or "—", color="dim"),
            Col("状态", 118, lambda r: r.status, color_fn=_level_color),
            Col("厂商", 196, lambda r: r.vendor or "—", color="faint",
                tip=lambda r: r.vendor),
            Col("服务", 0, lambda r: r.driver or "—", color="faint",
                stretch=True, tip=lambda r: r.hint or r.instance),
        ], parent=self)
        self.dev_pane = TablePane(self.dev_model, 24, "筛选设备名 / 厂商 / 服务…")
        ul.addWidget(self.dev_pane, 1)
        split.addWidget(upper)

        lower = QWidget()
        ll = QVBoxLayout(lower)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.setSpacing(4)
        head2 = QLabel("驱动清单（同一 INF 的多个设备实例已合并）")
        head2.setObjectName("H3")
        ll.addWidget(head2)
        self.drv_model = RowModel([
            Col("驱动", 250, lambda r: r.desc, tip=lambda r: r.desc),
            Col("版本", 132, lambda r: r.version or "—", mono=True),
            Col("日期", 92, lambda r: r.date or "—", color="faint"),
            Col("提供者", 150, lambda r: r.provider or "—",
                color_fn=lambda r: "dim" if r.third_party else "faint"),
            Col("INF", 148, lambda r: r.inf or "—", color="faint", mono=True),
            Col("设备数", 64, lambda r: str(r.devices or 0), align=RIGHT),
            Col("实例", 70, lambda r: str(r.instances), align=RIGHT,
                color="faint"),
        ], parent=self)
        self.drv_pane = TablePane(self.drv_model, 23, "筛选驱动名 / 提供者 / INF…")
        ll.addWidget(self.drv_pane, 1)
        split.addWidget(lower)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 2)
        l.addWidget(split, 1)
        return w

    def _fill_dev(self) -> None:
        key = self.dev_chips.current
        if key == "bad":
            rows = [d for d in self._dev if d.present and d.level in ("crit", "warn")]
        elif key == "off":
            rows = [d for d in self._dev if d.problem == 22]
        elif key == "gone":
            rows = [d for d in self._dev if d.status == "已移除"]
        else:
            rows = list(self._dev)
        self.dev_model.set_rows(rows)
        st = self.dev_stats
        self.dev_count.setText(
            f"显示 {len(rows)} / {len(self._dev)} 台" + (
                f" · 异常 {st.problems} · 已禁用 {st.disabled} · "
                f"未连接 {st.absent} · 已移除 {st.removed}" if st else ""))

    def load_devices(self) -> None:
        if self._busy["dev"]:
            return
        self._busy["dev"] = True
        self.status.emit("正在枚举设备与驱动…")

        def job(tick, stop):
            items = dv.devices()
            tick(0, 3, "设备枚举完成，正在读驱动")
            drvs = dv.drivers()
            tick(1, 3, "驱动清单完成，正在关联")
            dv.link_devices(items, drvs)
            st = dv.stats(items, drvs)
            tick(2, 3, "关联完成")
            return (items, drvs, st)

        def done(res):
            self._busy["dev"] = False
            self._section_done("devices")
            items, drvs, st = res
            self._dev, self._drv, self.dev_stats = items, drvs, st
            self._fill_dev()
            self.drv_model.set_rows(drvs)
            if st.problems:
                self.kpi_dev.set_value(
                    f"{st.problems} 台异常",
                    f"共 {st.devices} 台设备 · {st.drivers} 个驱动", "crit")
            elif st.disabled:
                # 「已禁用」是被动过手，不是故障，别跟异常混成一个数字
                self.kpi_dev.set_value(
                    f"{st.disabled} 台已禁用",
                    f"共 {st.devices} 台设备 · {st.drivers} 个驱动", "warn")
            else:
                self.kpi_dev.set_value(
                    "全部正常",
                    f"共 {st.devices} 台设备 · {st.drivers} 个驱动", "ok")
            self.status.emit(f"设备体检完成：{st.devices} 台设备，"
                             f"{st.problems} 台异常，{st.disabled} 台已禁用")

        run_bg(self, job, done, lambda i, n, m: self.status.emit(
            f"设备体检：{m}" if m else "设备体检…"), self._fail)
