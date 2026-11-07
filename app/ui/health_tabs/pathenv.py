# -*- coding: utf-8 -*-
"""环境变量页签：PATH 逐条核对失效 / 重复 / 变量未定义。

代码体与拆分前逐字节一致（`tools/split_health.py` 按 AST 行区间搬的）；
状态全在 `self` 上 —— 这里只换文件，不改结构。
"""
from __future__ import annotations

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (QFrame, QHBoxLayout, QLabel, QPlainTextEdit, QSplitter,
                             QStackedWidget, QTabWidget, QVBoxLayout, QWidget)

from ...core import pathenv as pe
from ..tables import Col, RowModel, make_table, run_bg
from ..widgets import EmptyState, FindingCard, KpiCard, ScrollColumn, ghost_button, mono_font


class PathEnvTabMixin:
    def _build_pathenv(self) -> QWidget:
        w = QWidget()
        l = QVBoxLayout(w)
        l.setContentsMargins(0, 8, 0, 0)
        l.setSpacing(7)

        row = QHBoxLayout()
        row.setSpacing(8)
        self.env_headline = QLabel("尚未运行")
        self.env_headline.setObjectName("H3")
        self.env_headline.setWordWrap(True)
        row.addWidget(self.env_headline, 1)
        self.btn_env = ghost_button("重新体检", "refresh")
        self.btn_env.clicked.connect(self.load_pathenv)
        row.addWidget(self.btn_env)
        l.addLayout(row)

        hint = QLabel("逐条展开系统与用户 PATH，检查目录是否还在、有没有重复、变量是否已失效。"
                      "只读，不改 PATH —— 建议里给出确切位置，由你自己决定删哪条。")
        hint.setProperty("role", "faint")
        hint.setWordWrap(True)
        l.addWidget(hint)

        kpi = QHBoxLayout()
        kpi.setSpacing(8)
        self.env_total = KpiCard("PATH 项数", "-", "", "dim")
        self.env_bad = KpiCard("失效项", "-", "", "dim")
        self.env_dup = KpiCard("重复项", "-", "", "dim")
        self.env_len = KpiCard("合并长度", "-", "", "dim")
        for k in (self.env_total, self.env_bad, self.env_dup, self.env_len):
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
        self.env_cards = ScrollColumn(9)
        ll.addWidget(self.env_cards, 1)
        split.addWidget(left)

        self.env_tabs = QTabWidget()
        self.env_tabs.setMinimumWidth(380)
        # 末列拉满：PATH 条目本身很长，宽度要让它先吃满，说明放悬浮提示
        self.env_model = RowModel([
            Col("状态", 92, lambda r: r.kind, color_fn=lambda r: r.level,
                tip=lambda r: r.note or r.raw),
            Col("来源", 46, lambda r: r.scope_text, color="dim"),
            Col("条目", 0, lambda r: r.show, stretch=True, mono=True,
                tip=lambda r: r.raw + ("\n" + r.note if r.note else "")),
        ], parent=self)
        self.env_stack = QStackedWidget()
        self.env_stack.addWidget(make_table(self.env_model, row_height=24))
        self.env_stack.addWidget(EmptyState(
            "check", "读不到 PATH",
            "系统与用户两级 PATH 都是空的。正常情况下不可能这样，"
            "除非注册表里的 Environment 键被改过。"))
        self.env_tabs.addTab(self.env_stack, "PATH 明细")

        self.env_raw = QPlainTextEdit()
        self.env_raw.setReadOnly(True)
        self.env_raw.setFrameShape(QFrame.NoFrame)
        self.env_raw.setProperty("role", "tech")
        self.env_raw.setFont(mono_font(8))
        self.env_tabs.addTab(self.env_raw, "原文与关键变量")
        split.addWidget(self.env_tabs)
        # 左边是结论卡片，右边是表格；ScrollColumn 的 sizeHint 很小，
        # 不显式给尺寸会被压成一条竖线（v1.13.0 踩过）。
        split.setSizes([520, 700])
        l.addWidget(split, 1)
        return w

    def load_pathenv(self) -> None:
        if self._busy["env"]:
            return
        self._busy["env"] = True
        self.status.emit("正在逐条核对 PATH 与环境变量…")

        def job(tick, stop):
            tick(0, 1, "展开系统与用户 PATH")
            return pe.audit()

        def done(rep):
            self._busy["env"] = False
            self._section_done("pathenv")
            self.env_rep = rep
            self.env_findings = list(rep.findings)
            self._fill_pathenv()
            self.status.emit("环境变量检查完成：" + rep.headline)

        run_bg(self, job, done, None, self._fail)

    def _fill_pathenv(self) -> None:
        rep = self.env_rep
        if rep is None:
            return
        level, text = pe.verdict(self.env_findings)
        self.env_headline.setText("%s　%s" % (text, rep.headline))

        self.env_cards.clear()
        order = {"crit": 0, "warn": 1, "info": 2, "ok": 3}
        for f in sorted(self.env_findings, key=lambda x: order.get(x.severity, 9)):
            self.env_cards.add(FindingCard(f))

        bad = rep.missing + rep.broken_other
        self.env_total.set_value("%d 项" % rep.total,
                                 "系统 %d · 用户 %d" % (rep.machine_count,
                                                        rep.user_count))
        self.env_bad.set_value("%d 项" % bad if bad else "无",
                               "指向不存在的目录" if rep.missing else "全部有效",
                               "warn" if bad else "ok")
        self.env_dup.set_value("%d 项" % rep.dups if rep.dups else "无",
                               "同级 %d · 跨级 %d" % (rep.dups_same_scope,
                                                      rep.dups - rep.dups_same_scope)
                               if rep.dups else "没有重复",
                               "info" if rep.dups else "ok")
        merged = rep.env.get("PATH", "") or (rep.machine_raw + ";" + rep.user_raw)
        self.env_len.set_value("%d" % len(merged), "字符 · 上限 32767",
                               "info" if len(merged) > 8000 else "")

        self.env_model.set_rows(list(rep.entries))
        self.env_stack.setCurrentIndex(0 if rep.entries else 1)
        # 页签上带失效数量，不点进来也能看见
        self.tabs.setTabText(8, "环境变量" + (" (%d)" % bad if bad else ""))

        kind_cn = {1: "REG_SZ（不展开变量）", 2: "REG_EXPAND_SZ（会展开变量）",
                   0: "不存在", -1: "读取失败"}
        lines = ["[系统 PATH]  " + kind_cn.get(rep.machine_kind, "未知类型")]
        lines += ["  " + rep.machine_raw, ""]
        lines += ["[用户 PATH]  " + kind_cn.get(rep.user_kind, "未知类型")]
        lines += ["  " + rep.user_raw, ""]
        lines += ["[关键变量]", ""]
        for name in pe.KEY_VARS:
            val = rep.env.get(name, "")
            lines.append("  %-16s %s" % (name, val if val else "（空）"))
        lines += ["", "[合并后的有效 PATH 长度]", ""]
        lines.append("  %d 字符（上限 32767）" % len(merged))
        lines += ["", "[判定口径]", ""]
        lines.append("  失效：展开后 os.path.isdir/isfile 为假")
        lines.append("  重复：展开后去尾斜杠、转小写相同；有效 PATH 系统在前")
        lines.append("  未展开：%%NAME%% 里的 NAME 在当前环境里不存在")
        self.env_raw.setPlainText("\n".join(lines))
