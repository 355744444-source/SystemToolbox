# -*- coding: utf-8 -*-
"""崩溃转储体检页签：转储类型 × 页面文件容量核对。

代码体与拆分前逐字节一致（`tools/split_health.py` 按 AST 行区间搬的）；
状态全在 `self` 上 —— 这里只换文件，不改结构。
"""
from __future__ import annotations

import os

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (QFrame, QHBoxLayout, QLabel, QPlainTextEdit, QSplitter,
                             QStackedWidget, QTabWidget, QVBoxLayout, QWidget)

from ...core import dumpcfg as dcfg
from ..tables import Col, RowModel, make_table, run_bg
from ..widgets import EmptyState, FindingCard, KpiCard, ScrollColumn, ghost_button, mono_font
from .common import RIGHT


class DumpCfgTabMixin:
    def _build_dumpcfg(self) -> QWidget:
        w = QWidget()
        l = QVBoxLayout(w)
        l.setContentsMargins(0, 8, 0, 0)
        l.setSpacing(7)

        row = QHBoxLayout()
        row.setSpacing(8)
        self.dc_headline = QLabel("尚未检查")
        self.dc_headline.setObjectName("H3")
        self.dc_headline.setWordWrap(True)
        row.addWidget(self.dc_headline, 1)
        self.btn_dc = ghost_button("重新体检", "refresh")
        self.btn_dc.clicked.connect(self.load_dumpcfg)
        row.addWidget(self.btn_dc)
        l.addLayout(row)

        hint = QLabel("读转储配置、页面文件容量与 System 通道的蓝屏记录，核对下次蓝屏能不能留下 dump。"
                      "只读，不改设置。")
        hint.setProperty("role", "faint")
        hint.setWordWrap(True)
        l.addWidget(hint)

        kpi = QHBoxLayout()
        kpi.setSpacing(8)
        self.dc_type = KpiCard("转储类型", "-", "", "dim")
        self.dc_pf = KpiCard("页面文件", "-", "", "dim")
        self.dc_need = KpiCard("容量校验", "-", "", "dim")
        self.dc_file = KpiCard("转储文件", "-", "", "dim")
        self.dc_crash = KpiCard("蓝屏记录", "-", "", "dim")
        for k in (self.dc_type, self.dc_pf, self.dc_need, self.dc_file, self.dc_crash):
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
        self.dc_cards = ScrollColumn(9)
        ll.addWidget(self.dc_cards, 1)
        split.addWidget(left)

        self.dc_tabs = QTabWidget()
        self.dc_tabs.setMinimumWidth(380)
        self.dc_dump_model = RowModel([
            Col("文件名", 180, lambda r: r.name, tip=lambda r: r.path),
            Col("大小", 78, lambda r: r.size_text, align=RIGHT, mono=True),
            Col("类型", 78, lambda r: r.kind_text, color="dim",
                color_fn=lambda r: "warn" if r.kind == "complete" else ""),
            Col("修改时间", 130, lambda r: r.time_text, color="faint"),
        ], parent=self)
        self.dc_dump_stack = QStackedWidget()
        self.dc_dump_stack.addWidget(make_table(self.dc_dump_model, row_height=24))
        self.dc_dump_stack.addWidget(EmptyState(
            "crash", "还没有转储文件",
            "这台机器自日志滚动以来没有产生过 dump。若「崩溃记录」里有蓝屏条目，"
            "说明当时写入失败 —— 看左侧的容量校验结论。"))
        self.dc_tabs.addTab(self.dc_dump_stack, "转储文件")

        self.dc_crash_model = RowModel([
            Col("时间", 116, lambda r: r.time_short, mono=True),
            Col("来源", 128, lambda r: r.provider_short, color="dim",
                tip=lambda r: r.provider),
            Col("类型", 142, lambda r: r.kind_text,
                color_fn=lambda r: "crit" if r.kind == "bugcheck" else "faint"),
            Col("代码", 76, lambda r: r.code_text, mono=True,
                color_fn=lambda r: "crit" if r.code else "dim"),
            Col("说明", 0, lambda r: r.explain, color="dim",
                stretch=True, tip=lambda r: r.text or r.explain),
        ], parent=self)
        self.dc_crash_stack = QStackedWidget()
        self.dc_crash_stack.addWidget(make_table(self.dc_crash_model, row_height=24))
        self.dc_crash_stack.addWidget(EmptyState(
            "info", "System 日志里没有 41 / 1001 记录",
            "既没有蓝屏（BugCheck），也没有意外关机记录。"
            "如果系统日志被清理过或日志上限太小，这里会是空的。"))
        self.dc_tabs.addTab(self.dc_crash_stack, "崩溃记录")

        self.dc_raw = QPlainTextEdit()
        self.dc_raw.setReadOnly(True)
        self.dc_raw.setFrameShape(QFrame.NoFrame)
        self.dc_raw.setProperty("role", "tech")
        self.dc_raw.setFont(mono_font(8))
        self.dc_tabs.addTab(self.dc_raw, "原始配置")
        split.addWidget(self.dc_tabs)
        split.setStretchFactor(0, 5)
        split.setStretchFactor(1, 5)
        self.dc_split = split
        # 左栏是结论卡片（文字多，需要宽度），右栏是表格。
        # 只给 setStretchFactor 没用：ScrollColumn 的 sizeHint 很小，
        # splitter 会按它把左栏压到 250 px，卡片被挤成一条竖线。
        split.setSizes([560, 660])
        l.addWidget(split, 1)
        return w

    def load_dumpcfg(self) -> None:
        if self._busy["dc"]:
            return
        self._busy["dc"] = True
        self.status.emit("正在核对崩溃转储配置与页面文件容量…")

        def job(tick, stop):
            tick(0, 2, "读取 CrashControl 与页面文件")
            snap = dcfg.read(with_events=False)
            if stop[0]:
                return snap
            tick(1, 2, "读取 System 通道的蓝屏记录")
            snap.crashes = dcfg.scan_crashes()
            return snap

        def done(snap):
            self._busy["dc"] = False
            self._section_done("dumpcfg")
            self.dc_snap = snap
            self.dc_findings = dcfg.evaluate(snap)
            self._fill_dumpcfg()
            self.status.emit("崩溃转储检查完成：" + self.dc_headline.text())

        run_bg(self, job, done, None, self._fail)

    def _fill_dumpcfg(self) -> None:
        s = self.dc_snap
        if s is None:
            return
        findings = self.dc_findings
        level, text = dcfg.verdict(findings)
        self.dc_headline.setText(text)

        # 卡片：crit/warn 排前面，同级保持 evaluate 给出的顺序
        self.dc_cards.clear()
        order = {"crit": 0, "warn": 1, "info": 2, "ok": 3}
        for f in sorted(findings, key=lambda x: order.get(x.severity, 9)):
            self.dc_cards.add(FindingCard(f))

        need, need_note = s.need_pagefile()
        pf = s.pagefile_bytes
        if s.disabled:
            self.dc_type.set_value("已关闭", "不会生成 dump", "crit")
        else:
            # 小型转储落到 Minidump 目录，其他类型落到单个 .dmp 文件
            if s.enabled in (3, 4):
                where = "写入 " + os.path.basename(s.minidump_path or "Minidump")
            else:
                where = "写入 " + os.path.basename(s.dump_file_path or "-")
            self.dc_type.set_value(s.type_text, where, "ok")
        self.dc_pf.set_value(dcfg.human(pf) if pf else "无",
                             "%s 上 %d 个" % (s.system_drive,
                                              len(s.pagefiles)),
                             "crit" if pf == 0 else "")
        if s.disabled or need == 0:
            self.dc_need.set_value("不适用", "转储已关闭", "dim")
        elif pf >= need:
            self.dc_need.set_value("够用", "需 ≥ " + dcfg.human(need), "ok")
        else:
            self.dc_need.set_value("不够", "需 ≥ " + dcfg.human(need), "crit")
        n = len(s.dumps)
        self.dc_file.set_value("%d 个" % n, s.latest_dump.time_text
                               if s.latest_dump else "尚无转储",
                               "ok" if n else "dim")
        nb, np_ = len(s.bugchecks), len(s.power_losses)
        self.dc_crash.set_value("%d 次" % nb,
                                "另有 %d 次意外关机" % np_ if np_ else "无蓝屏记录",
                                "crit" if nb and not n else ("dim" if not nb else ""))
        self.kpi_dc.set_value(
            {"crit": "需处理", "warn": "有风险", "ok": "正常"}.get(level, "-"),
            s.type_text, level if level != "dim" else "")

        self.dc_dump_model.set_rows(list(s.dumps))
        self.dc_crash_model.set_rows(list(s.crashes))
        self.dc_dump_stack.setCurrentIndex(0 if s.dumps else 1)
        self.dc_crash_stack.setCurrentIndex(0 if s.crashes else 1)
        self.dc_tabs.setTabText(0, "转储文件 (%d)" % n)
        self.dc_tabs.setTabText(1, "崩溃记录 (%d)" % len(s.crashes))

        lines = ["[CrashControl]", ""]
        for k, v in (("CrashDumpEnabled", s.enabled),
                     ("FilterPages", s.filter_pages),
                     ("DumpFile", s.dump_file),
                     ("MinidumpDir", s.minidump_dir),
                     ("AutoReboot", s.auto_reboot),
                     ("Overwrite", s.overwrite),
                     ("LogEvent", s.log_event),
                     ("MinidumpsCount", s.minidumps_count),
                     ("AlwaysKeepMemoryDump", s.always_keep),
                     ("DumpFilters", "、".join(str(x) for x in s.dump_filters))):
            lines.append("  %-22s %s" % (k, v))
        lines += ["", "[内存与页面文件]", ""]
        lines.append("  %-22s %s" % ("物理内存", dcfg.human(s.ram)))
        lines.append("  %-22s %s" % ("提交上限", dcfg.human(s.commit_limit)))
        lines.append("  %-22s %s" % ("内核占用", dcfg.human(s.kernel_total)))
        lines.append("  %-22s %s" % ("页面文件需求", dcfg.human(need)))
        lines.append("  %-22s %s" % ("需求口径", need_note))
        for p, sz in s.pagefiles:
            lines.append("  %-22s %s %s" % ("页面文件", p, dcfg.human(sz)))
        lines.append("  %-22s %s 剩 %s" % ("系统盘", s.system_drive,
                                           dcfg.human(s.free_system)))
        lines += ["", "[扫描]", ""]
        lines.append("  %-22s %s" % ("时间", s.scanned_at.strftime(
            "%Y-%m-%d %H:%M:%S") if s.scanned_at else "-"))
        lines.append("  %-22s %d 个" % ("活动内核报告", len(s.live_reports)))
        for p in s.live_reports[:6]:
            lines.append("    " + p)
        self.dc_raw.setPlainText("\n".join(lines))
