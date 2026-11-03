# -*- coding: utf-8 -*-
"""驱动体检页签：驱动包清单 + 逐个文件验签 + 已加载判定。

代码体与拆分前逐字节一致（`tools/split_health.py` 按 AST 行区间搬的）；
状态全在 `self` 上 —— 这里只换文件，不改结构。
"""
from __future__ import annotations

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (QHBoxLayout, QLabel, QSplitter, QStackedWidget,
                             QTabWidget, QVBoxLayout, QWidget)

from ...core import drivers as drv
from ..tables import Col, RowModel, TablePane, make_table, run_bg
from ..widgets import EmptyState, FindingCard, KpiCard, ScrollColumn, ghost_button
from ..optimize_page import ChipBar
from .common import RIGHT


class DriversTabMixin:
    def _build_drvtab(self) -> QWidget:
        w = QWidget()
        l = QVBoxLayout(w)
        l.setContentsMargins(0, 8, 0, 0)
        l.setSpacing(7)

        row = QHBoxLayout()
        row.setSpacing(8)
        self.drv_headline = QLabel("尚未检查")
        self.drv_headline.setObjectName("H3")
        self.drv_headline.setWordWrap(True)
        row.addWidget(self.drv_headline, 1)
        self.btn_drv = ghost_button("重新体检", "refresh")
        self.btn_drv.clicked.connect(self.load_drivers)
        row.addWidget(self.btn_drv)
        l.addLayout(row)

        hint = QLabel("回答三件事：**这台机器额外装过哪些驱动**（谁签的、哪天从哪个目录装的）、"
                      "**驱动文件签名状态**（未签名 / 证书链有问题的会被点名）、"
                      "**哪些正在内核里跑**。")
        hint.setProperty("role", "faint")
        hint.setWordWrap(True)
        l.addWidget(hint)

        kpi = QHBoxLayout()
        kpi.setSpacing(8)
        self.drv_kpi_pkg = KpiCard("第三方驱动包", "-", "", "dim")
        self.drv_kpi_files = KpiCard("驱动文件", "-", "", "dim")
        self.drv_kpi_bad = KpiCard("签名问题", "-", "", "dim")
        self.drv_kpi_mod = KpiCard("已加载模块", "-", "", "dim")
        for k in (self.drv_kpi_pkg, self.drv_kpi_files,
                  self.drv_kpi_bad, self.drv_kpi_mod):
            kpi.addWidget(k, 1)
        l.addLayout(kpi)

        split = QSplitter(Qt.Horizontal)
        split.setChildrenCollapsible(False)
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 2, 0, 0)
        ll.setSpacing(6)
        cap = QLabel("诊断结论")
        cap.setProperty("role", "H2")
        ll.addWidget(cap)
        self.drv_cards = ScrollColumn(9)
        ll.addWidget(self.drv_cards, 1)
        split.addWidget(left)

        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(4)
        self.drv_tabs = QTabWidget()
        self.drv_tabs.setMinimumWidth(380)

        # --- 第三方驱动包：Windows 给后装驱动的发布名就是 oemNN.inf
        self.drv_pkg_model = RowModel([
            Col("发布名", 74, lambda r: r.inf, mono=True,
                tip=lambda r: r.key),
            Col("原始 INF", 168, lambda r: r.original_inf or r.inf, mono=True,
                tip=lambda r: r.original_inf or r.inf),
            Col("签名者", 122, lambda r: r.signer_text,
                color_fn=lambda r: "" if r.signer else "faint",
                tip=lambda r: r.signer_full),
            Col("导入", 82, lambda r: r.installed or "—", mono=True,
                color="dim"),
            Col("大小", 62, lambda r: r.size_text, align=RIGHT, color="faint"),
            Col("来源", 0, lambda r: r.source_text, stretch=True, color="faint",
                tip=lambda r: r.source),
        ], parent=self)
        self.drv_pkg_stack = QStackedWidget()
        self.drv_pkg_pane = TablePane(self.drv_pkg_model, 24,
                                      "筛选驱动包 / 提供者 / INF…")
        self.drv_pkg_stack.addWidget(self.drv_pkg_pane)
        self.drv_pkg_stack.addWidget(EmptyState(
            "check", "没有第三方驱动包",
            "除了 Windows 自带的那 271 个包，本机没有再装过厂商驱动。"
            "（装硬件驱动、外设工具、虚拟机、远控软件都会在这里出现。）"))
        self.drv_tabs.addTab(self.drv_pkg_stack, "第三方驱动包")

        # --- 驱动文件：默认只看有问题的，避免 540 行糊脸
        chip_row = QHBoxLayout()
        chip_row.setSpacing(6)
        self.drv_chips = ChipBar([("有问题", "bad"), ("已加载", "live"),
                                  ("全部", "all")], "bad")
        self.drv_chips.changed.connect(lambda _k: self._fill_drv_files())
        chip_row.addWidget(self.drv_chips, 1)
        self.drv_count = QLabel("")
        self.drv_count.setProperty("role", "faint")
        chip_row.addWidget(self.drv_count)
        chip_row.addSpacing(4)

        files = QWidget()
        fl = QVBoxLayout(files)
        fl.setContentsMargins(0, 4, 0, 0)
        fl.setSpacing(4)
        fl.addLayout(chip_row)
        # 末列拉满：文件路径最长；服务名放中间
        self.drv_file_model = RowModel([
            Col("状态", 84, lambda r: r.level_text,
                color_fn=lambda r: r.level,
                tip=lambda r: (drv.sg.describe(r.info) if r.info else "未扫描")),
            Col("运行", 44, lambda r: "是" if r.loaded else "—",
                color_fn=lambda r: "warn" if r.loaded else "faint"),
            Col("起点", 52, lambda r: r.start_text, color="dim",
                tip=lambda r: "服务启动类型：" + r.start_text),
            Col("服务", 122, lambda r: "、".join(r.services[:2]) or "—",
                mono=True, color="faint",
                tip=lambda r: "、".join(r.services) or "没有服务引用这个文件"),
            Col("文件", 0, lambda r: r.name, stretch=True, mono=True,
                tip=lambda r: r.path),
        ], parent=self)
        self.drv_file_stack = QStackedWidget()
        self.drv_file_stack.addWidget(make_table(self.drv_file_model, row_height=24))
        self.drv_file_stack.addWidget(EmptyState(
            "check", "这一档里没有文件",
            "切到「全部」看看。签名、加载状态都正常的驱动没必要逐个盯。"))
        fl.addWidget(self.drv_file_stack, 1)
        self.drv_tabs.addTab(files, "驱动文件")

        rl.addWidget(self.drv_tabs, 1)
        split.addWidget(right)
        split.setSizes([520, 700])
        l.addWidget(split, 1)

        note = QLabel("只读：不装驱动、不卸载、不改服务启动类型。"
                      "签名校验用的是 WinVerifyTrust + 系统目录（catalog），"
                      "两次启动之间不查证书吊销 —— 所以「签名有效」不代表撤销名单里没有它。"
                      "扫描结果按「文件大小 + 修改时间」缓存，文件没变就不会重扫。")
        note.setProperty("role", "faint")
        note.setWordWrap(True)
        l.addWidget(note)
        return w

    def load_drivers(self) -> None:
        if self._busy["drv"]:
            return
        self._busy["drv"] = True
        self.btn_drv.setEnabled(False)
        self.status.emit("正在读驱动包数据库并校验驱动签名…")

        def job(tick, stop):
            return drv.audit(stop_flag=stop, progress=tick)

        def on_tick(_done, _total, text):
            # audit 自己把进度写进 text（「校验驱动签名 120/540」），直接透传
            if text:
                self.drv_headline.setText(text)

        def done(rep):
            self._busy["drv"] = False
            self._section_done("drivers")
            self.btn_drv.setEnabled(True)
            self.drv_rep = rep
            self.drv_findings = drv.build_findings(rep)
            self._fill_drv()
            n_bad = len(rep.unsigned) + len(rep.missing_files)
            self.tabs.setTabText(9, "驱动体检" + (" (%d)" % n_bad if n_bad else ""))
            self.status.emit("驱动体检完成：%s" % drv.verdict(rep)[1])

        def failed(msg):
            self._busy["drv"] = False
            self.btn_drv.setEnabled(True)
            self.drv_headline.setText("驱动体检失败：%s" % msg)
            self.status.emit("驱动体检失败：%s" % msg)

        run_bg(self, job, done, on_tick, failed)

    def _fill_drv(self) -> None:
        rep = self.drv_rep
        if rep is None:
            return
        level, text = drv.verdict(rep)
        tail = "　签名扫描 %.1f 秒%s" % (
            rep.elapsed_ms / 1000.0,
            "（%d 个命中缓存）" % rep.cache_hits if rep.cache_hits else "")
        if rep.signature_cut:
            tail += "·已停止，部分文件未校验"
        self.drv_headline.setText(text + tail)

        self.drv_cards.clear()
        for f in self.drv_findings:
            self.drv_cards.add(FindingCard(f))

        self.drv_kpi_pkg.set_value(
            "%d" % len(rep.third_packages),
            "个 · 内置 %d 个" % rep.builtin_count,
            "info" if rep.third_packages else "ok")
        self.drv_kpi_files.set_value(
            "%d" % len(rep.files), "个驱动文件",
            "dim" if not rep.files else "")
        bad = len(rep.unsigned) + len(rep.suspicious_sign)
        self.drv_kpi_bad.set_value(
            "%d" % bad if bad else "无",
            ("个 · %d 个在运行" % len(rep.unsigned_loaded))
            if rep.unsigned_loaded else ("个待确认" if bad else "签名都正常"),
            "crit" if rep.unsigned_loaded else ("warn" if bad else "ok"))
        self.drv_kpi_mod.set_value("%d" % rep.modules, "个内核模块在跑", "")

        # 第三方包表
        pkgs = rep.third_packages
        self.drv_pkg_model.set_rows(pkgs)
        self.drv_pkg_stack.setCurrentIndex(0 if pkgs else 1)
        self._fill_drv_files()

    def _fill_drv_files(self) -> None:
        rep = self.drv_rep
        if rep is None:
            return
        rows = list(rep.files)
        key = self.drv_chips.current
        if key == "bad":
            rows = [r for r in rows if r.problem]
        elif key == "live":
            rows = [r for r in rows if r.loaded]
        # 有问题的排前面，其次在跑的
        rows.sort(key=lambda r: (not r.problem, not r.loaded, r.name.lower()))
        self._drv_rows = rows
        self.drv_file_model.set_rows(rows)
        self.drv_file_stack.setCurrentIndex(0 if rows else 1)
        n_problem = sum(1 for r in rep.files if r.problem)
        self.drv_count.setText("%d / %d" % (len(rows), len(rep.files)))
        self.drv_tabs.setTabText(
            1, "驱动文件" + (" (%d)" % n_problem if n_problem else ""))
