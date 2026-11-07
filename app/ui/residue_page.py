# -*- coding: utf-8 -*-
"""残留治理工作台：注册表冗余 / 卸载残留。"""
from __future__ import annotations

import os
from datetime import datetime

from PyQt5.QtCore import Qt, QUrl, pyqtSignal
from PyQt5.QtGui import QDesktopServices
from PyQt5.QtWidgets import (QApplication, QCheckBox, QFileDialog, QHBoxLayout,
                             QLabel, QLineEdit, QPlainTextEdit,
                             QProgressBar, QPushButton, QSplitter,
                             QStackedWidget, QTabWidget, QVBoxLayout, QWidget)

from ..core import cleaner, locks, registry_clean as reg, residue
from .space_page import fmt_time
from .tables import Col, RowModel, TablePane, make_table, run_bg, stop_all
from . import notice
from .widgets import (PAGE_MARGINS, EmptyState, KpiCard, ghost_button,
                      primary_button)


def _risk_token(risk: str) -> str:
    return {"低": "ok", "中": "warn", "高": "crit"}.get(risk, "dim")


def _conf_token(conf: int) -> str:
    if conf >= 82:
        return "crit"
    if conf >= 68:
        return "warn"
    return "dim"


def backup_dir() -> str:
    base = os.path.expandvars(r"%LOCALAPPDATA%\SystemToolbox\backup")
    try:
        os.makedirs(base, exist_ok=True)
        return base
    except OSError:
        return os.path.expandvars("%TEMP%")


# ------------------------------------------------------------------ 注册表
class RegistryTab(QWidget):
    status = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._busy = False
        self._issues: list[reg.RegIssue] = []
        self._build()

    def _build(self) -> None:
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 10, 0, 0)
        lay.setSpacing(9)

        top = QHBoxLayout()
        top.setSpacing(8)
        title = QLabel("注册表冗余引用")
        title.setProperty("role", "H2")
        top.addWidget(title)
        self.chk_com = QCheckBox("包含 COM 组件深扫（较慢，默认不勾选）")
        self.chk_com.setChecked(False)
        top.addWidget(self.chk_com)
        top.addStretch(1)
        self.btn_scan = primary_button("开始检测", "search")
        self.btn_scan.clicked.connect(self.scan)
        top.addWidget(self.btn_scan)
        lay.addLayout(top)

        hint = QLabel("只报告「登记路径确实不存在」的孤立引用，不猜、不批量扫全表；"
                      "不触碰 SYSTEM、Services、安全策略等关键分支。修复前会自动导出 .reg 备份。")
        hint.setProperty("role", "faint")
        hint.setWordWrap(True)
        lay.addWidget(hint)

        split = QSplitter(Qt.Vertical)
        split.setChildrenCollapsible(False)
        self.split = split

        self.model = RowModel([
            Col("", 34),
            Col("类别", 110, lambda r: r.category),
            Col("对象", 190, lambda r: r.label, tip=lambda r: r.label),
            Col("风险", 52, lambda r: r.risk, color_fn=lambda r: _risk_token(r.risk)),
            Col("注册表位置", 0, lambda r: r.full_path, color="faint", stretch=True),
        ], checkable=True, parent=self)
        self.model.checkChanged.connect(self._sync_footer)
        self.pane = TablePane(self.model, 26, "筛选类别 / 对象 / 注册表路径…")
        self.table = self.pane.view
        self.table.selectionModel().currentRowChanged.connect(self._show_detail)
        split.addWidget(self.pane)

        self.detail = QPlainTextEdit()
        self.detail.setProperty("role", "tech")
        self.detail.setReadOnly(True)
        self.detail.setLineWrapMode(QPlainTextEdit.WidgetWidth)
        self.detail.setPlaceholderText("选中一行，这里显示判定依据")
        self.detail.setMinimumHeight(140)
        split.addWidget(self.detail)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 2)
        lay.addWidget(split, 1)

        foot = QHBoxLayout()
        foot.setSpacing(8)
        self.btn_low = ghost_button("全选低风险项")
        self.btn_low.clicked.connect(self._check_low)
        foot.addWidget(self.btn_low)
        self.btn_none = ghost_button("取消全选")
        self.btn_none.clicked.connect(lambda: self.model.check_all(False))
        foot.addWidget(self.btn_none)
        self.btn_backup = ghost_button("打开备份目录")
        self.btn_backup.clicked.connect(self._open_backup)
        foot.addWidget(self.btn_backup)
        foot.addStretch(1)
        self.sel_label = QLabel("尚未检测")
        self.sel_label.setProperty("role", "dim")
        foot.addWidget(self.sel_label)
        self.btn_fix = QPushButton("备份并修复选中项")
        self.btn_fix.setObjectName("DangerBtn")
        self.btn_fix.setCursor(Qt.PointingHandCursor)
        self.btn_fix.setEnabled(False)
        self.btn_fix.clicked.connect(self._fix)
        foot.addWidget(self.btn_fix)
        lay.addLayout(foot)

        self.prog = QProgressBar()
        self.prog.setTextVisible(False)
        self.prog.setFixedHeight(5)
        self.prog.hide()
        lay.addWidget(self.prog)

    def showEvent(self, e):
        super().showEvent(e)
        if not getattr(self, "_sized", False) and self.split.height() > 200:
            h = self.split.height()
            self.split.setSizes([int(h * 0.62), int(h * 0.38)])
            self._sized = True

    # -------- 扫描
    def scan(self) -> None:
        if self._busy:
            return
        self._busy = True
        self.btn_scan.setEnabled(False)
        self.prog.setRange(0, 5)
        self.prog.setValue(0)
        self.prog.show()
        self.status.emit("正在检测注册表孤立引用…")
        include_com = self.chk_com.isChecked()

        def job(tick, stop):
            return reg.scan_all(stop, progress=tick, include_com=include_com)

        run_bg(self, job, self._on_done, self._on_tick, self._on_fail)

    def _on_tick(self, i, n, name) -> None:
        self.prog.setRange(0, max(1, n))
        self.prog.setValue(i)
        if name:
            self.status.emit(f"正在检查：{name}")

    def _on_fail(self, msg: str) -> None:
        self._busy = False
        self.btn_scan.setEnabled(True)
        self.prog.hide()
        self.status.emit("检测失败：" + msg)

    def _on_done(self, issues) -> None:
        self._busy = False
        self.btn_scan.setEnabled(True)
        self.prog.hide()
        self._issues = list(issues)
        self._issues.sort(key=lambda x: ({"低": 0, "中": 1}.get(x.risk, 2), x.category))
        self.model.set_rows(self._issues)
        low = [i for i, x in enumerate(self._issues) if x.risk == "低"]
        self.model.check_indices(low)
        n_low = len(low)
        self.status.emit(f"检测完成：{len(self._issues)} 处孤立引用，其中低风险 {n_low} 处")
        if not self._issues:
            notice.note(self, "ok",
                        "检测完成：没有发现可确认的孤立引用 —— 卸载记录、启动项、"
                        "右键菜单、应用路径都指向有效文件。")

    def _sync_footer(self) -> None:
        rows = self.model.checked_rows()
        self.sel_label.setText(f"已选 {len(rows)} 处" if rows else "未选中任何项目")
        self.btn_fix.setEnabled(bool(rows) and not self._busy)

    def _check_low(self) -> None:
        self.model.check_rows([x for x in self._issues if x.risk == "低"])

    def _show_detail(self, cur, _prev=None) -> None:
        it = self.model.row_at(cur.row()) if cur.isValid() else None
        if it is None:
            self.detail.clear()
            return
        self.detail.setPlainText(
            f"类别：{it.category}\n"
            f"对象：{it.label}\n"
            f"风险：{it.risk}\n"
            f"注册表位置：{it.full_path}\n"
            f"删除方式：{'删除整个子键' if it.delete_key else '仅删除值 ' + it.value_name}\n\n"
            f"判定依据：\n{it.reason}\n\n"
            f"缺失目标：\n{it.target or '-'}")

    # -------- 修复
    def _fix(self) -> None:
        if self._busy:
            return
        picked = self.model.checked_rows()
        if not picked:
            return
        names = "、".join("[%s] %s" % (x.category, x.label) for x in picked[:4])
        if len(picked) > 4:
            names += " 等 %d 项" % len(picked)
        notice.ask(
            self, "确认修复",
            "即将删除 %d 处孤立注册表引用：%s。删除前会自动导出 .reg 备份，"
            "出问题双击备份即可还原。" % (len(picked), names),
            ok_text="删除并备份", danger=True,
            on_yes=lambda: self._do_fix(picked))

    def _do_fix(self, picked) -> None:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        out = os.path.join(backup_dir(), f"registry_{stamp}.reg")
        self._busy = True
        self.btn_fix.setEnabled(False)
        self.prog.setRange(0, 0)
        self.prog.show()
        self.status.emit("正在导出备份…")

        def job(tick, stop):
            good, msg = reg.export_backup(picked, out)
            if not good:
                return (False, msg, 0, 0, [])
            ok_n, fail_n, problems = reg.delete_issues(
                picked, progress=lambda i, n: tick(i, n, "正在修复"))
            return (True, out, ok_n, fail_n, problems)

        run_bg(self, job, self._after_fix, self._on_tick, self._on_fail)

    def _after_fix(self, payload) -> None:
        self._busy = False
        self.btn_scan.setEnabled(True)
        self.prog.hide()
        good, info, ok_n, fail_n, problems = payload
        if not good:
            notice.note(
                self, "error",
                "已中止：导出备份失败（%s）。为避免无法回滚，本次未做任何删除。" % info)
            self.status.emit("备份失败，已中止修复")
            self._sync_footer()
            return
        msg = "已删除 %d 处孤立引用，备份在 %s" % (ok_n, info)
        if fail_n:
            msg += "；失败 %d 处（多为需要管理员的系统级项目）：%s" % (
                fail_n, "；".join(problems[:3]))
        notice.note(
            self, "ok" if not fail_n else "warn", msg,
            action=("打开备份目录", self._open_backup))
        self.status.emit(f"注册表修复完成：{ok_n} 处成功，{fail_n} 处失败")
        self.scan()

    def _open_backup(self) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(backup_dir()))

    def shutdown(self) -> None:
        stop_all(self)


# ------------------------------------------------------------------ 卸载残留
class LeftoverTab(QWidget):
    status = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._busy = False
        self._items: list[residue.Leftover] = []
        self._build()

    def _build(self) -> None:
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 10, 0, 0)
        lay.setSpacing(9)

        top = QHBoxLayout()
        top.setSpacing(8)
        title = QLabel("卸载残留")
        title.setProperty("role", "H2")
        top.addWidget(title)
        top.addStretch(1)
        self.btn_scan = primary_button("深度扫描", "search")
        self.btn_scan.clicked.connect(self.scan)
        top.addWidget(self.btn_scan)
        lay.addLayout(top)

        hint = QLabel("比对「已安装软件清单」与文件系统实际目录，给出置信度与判定理由。"
                      "这是启发式判断：<b>默认不勾选</b>，请逐条确认后再删。删除走回收站。")
        hint.setProperty("role", "faint")
        hint.setWordWrap(True)
        lay.addWidget(hint)

        split = QSplitter(Qt.Vertical)
        split.setChildrenCollapsible(False)
        self.split = split

        self.model = RowModel([
            Col("", 34),
            Col("目录 / 名称", 200, lambda r: r.name, tip=lambda r: r.path),
            Col("置信度", 70, lambda r: f"{r.confidence}%",
                color_fn=lambda r: _conf_token(r.confidence)),
            Col("占用", 92, lambda r: cleaner.human_size(r.size), color="warn"),
            Col("文件数", 76, lambda r: f"{r.files:,}", color="dim", mono=True),
            Col("最后修改", 130, lambda r: fmt_time(r.mtime), color="dim", mono=True),
            Col("完整路径", 0, lambda r: r.path, color="faint", stretch=True),
        ], checkable=True, parent=self)
        self.model.checkChanged.connect(self._sync_footer)
        self.table = make_table(self.model, row_height=27)
        self.table.selectionModel().currentRowChanged.connect(self._show_detail)
        self.table.doubleClicked.connect(self._open_selected)
        split.addWidget(self.table)

        self.detail = QPlainTextEdit()
        self.detail.setReadOnly(True)
        self.detail.setLineWrapMode(QPlainTextEdit.WidgetWidth)
        self.detail.setPlaceholderText("选中一行，这里显示置信度构成与判定理由")
        self.detail.setMinimumHeight(140)
        split.addWidget(self.detail)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 2)
        lay.addWidget(split, 1)

        foot = QHBoxLayout()
        foot.setSpacing(8)
        self.btn_all = ghost_button("全选")
        self.btn_all.clicked.connect(lambda: self.model.check_all(True))
        foot.addWidget(self.btn_all)
        self.btn_none = ghost_button("取消全选")
        self.btn_none.clicked.connect(lambda: self.model.check_all(False))
        foot.addWidget(self.btn_none)
        self.btn_open = ghost_button("打开目录")
        self.btn_open.clicked.connect(self._open_selected)
        foot.addWidget(self.btn_open)
        foot.addStretch(1)
        self.sel_label = QLabel("尚未扫描")
        self.sel_label.setProperty("role", "dim")
        foot.addWidget(self.sel_label)
        self.btn_del = QPushButton("删除选中（回收站）")
        self.btn_del.setObjectName("DangerBtn")
        self.btn_del.setCursor(Qt.PointingHandCursor)
        self.btn_del.setEnabled(False)
        self.btn_del.clicked.connect(self._delete)
        foot.addWidget(self.btn_del)
        lay.addLayout(foot)

        self.prog = QProgressBar()
        self.prog.setTextVisible(False)
        self.prog.setFixedHeight(5)
        self.prog.hide()
        lay.addWidget(self.prog)

    def showEvent(self, e):
        super().showEvent(e)
        if not getattr(self, "_sized", False) and self.split.height() > 200:
            h = self.split.height()
            self.split.setSizes([int(h * 0.62), int(h * 0.38)])
            self._sized = True

    # -------- 扫描
    def scan(self) -> None:
        if self._busy:
            return
        self._busy = True
        self.btn_scan.setEnabled(False)
        self.prog.setRange(0, 0)
        self.prog.show()
        self.status.emit("正在建立已安装软件索引…")

        def job(tick, stop):
            return residue.scan_leftovers(stop_flag=stop, progress=tick)

        run_bg(self, job, self._on_done, self._on_tick, self._on_fail)

    def _on_tick(self, i, n, name) -> None:
        if n:
            self.prog.setRange(0, n)
            self.prog.setValue(i)
        self.status.emit(f"正在分析：{name}" if name else "正在分析…")

    def _on_fail(self, msg: str) -> None:
        self._busy = False
        self.btn_scan.setEnabled(True)
        self.prog.hide()
        self.status.emit("扫描失败：" + msg)

    def _on_done(self, payload) -> None:
        items, checked = payload
        self._busy = False
        self.btn_scan.setEnabled(True)
        self.prog.hide()
        self._items = list(items)
        self.model.set_rows(self._items)
        total = sum(x.size for x in self._items)
        self.status.emit(f"扫描完成：检查 {checked} 个目录，疑似残留 "
                         f"{len(self._items)} 项 / {cleaner.human_size(total)}")
        if not self._items:
            notice.note(self, "ok",
                        "扫描完成：检查了 %d 个目录，没有发现高置信度的卸载残留。"
                        % checked)

    def _sync_footer(self) -> None:
        rows = self.model.checked_rows()
        if rows:
            self.sel_label.setText(
                f"已选 {len(rows)} 项 · {cleaner.human_size(sum(r.size for r in rows))}")
        else:
            self.sel_label.setText(f"{len(self._items)} 项待确认")
        self.btn_del.setEnabled(bool(rows) and not self._busy)

    def _show_detail(self, cur, _prev=None) -> None:
        it = self.model.row_at(cur.row()) if cur.isValid() else None
        if it is None:
            self.detail.clear()
            return
        reasons = "\n".join(f"  · {r}" for r in it.reasons)
        self.detail.setPlainText(
            f"路径：{it.path}\n"
            f"类别：{it.category}\n"
            f"置信度：{it.confidence}%（{it.level}）\n"
            f"占用：{cleaner.human_size(it.size)} · {it.files:,} 个文件\n"
            f"最后修改：{fmt_time(it.mtime)}\n\n"
            f"判定理由：\n{reasons}\n\n"
            "提醒：置信度只是启发式参考。删除前请确认该目录不属于仍在使用的绿色软件。")

    def _open_selected(self) -> None:
        idx = self.table.currentIndex()
        it = self.model.row_at(idx.row()) if idx.isValid() else None
        if it is None:
            rows = self.model.checked_rows()
            it = rows[0] if rows else None
        if it and os.path.isdir(it.path):
            QDesktopServices.openUrl(QUrl.fromLocalFile(it.path))

    def _delete(self) -> None:
        rows = self.model.checked_rows()
        if not rows:
            return
        total = sum(r.size for r in rows)
        names = "、".join("%s（置信度 %d%%）" % (r.name, r.confidence)
                          for r in rows[:4])
        if len(rows) > 4:
            names += " 等 %d 项" % len(rows)
        notice.ask(
            self, "确认删除",
            "将把 %d 个目录整体移入回收站（合计 %s）：%s。"
            "删除后可从回收站还原，请确认这些目录确实不再需要。"
            % (len(rows), cleaner.human_size(total), names),
            ok_text="移入回收站", danger=True,
            on_yes=lambda: self._do_delete(rows))

    def _do_delete(self, rows) -> None:
        def job(tick, stop):
            rep = cleaner.CleanReport(to_recycle=True)
            for i, it in enumerate(rows):
                if stop[0]:
                    break
                tick(i, len(rows), it.name)
                files = []
                for dirpath, _dirs, names in os.walk(it.path):
                    for n in names:
                        p = os.path.join(dirpath, n)
                        try:
                            files.append((p, os.path.getsize(p)))
                        except OSError:
                            continue
                sub = cleaner.delete_files(files, use_recycle=True, stop_flag=stop)
                rep.deleted += sub.deleted
                rep.freed += sub.freed
                rep.skipped += sub.skipped
                rep.failed += sub.failed
                rep.problems.extend(sub.problems)
                cleaner._prune_empty_dirs([it.path])
            return rep

        self._busy = True
        self.btn_del.setEnabled(False)
        self.prog.setRange(0, len(rows))
        self.prog.show()
        run_bg(self, job, self._after_delete, self._on_tick, self._on_fail)

    def _after_delete(self, rep) -> None:
        self._busy = False
        self.prog.hide()
        msg = "已移入回收站 %d 个文件，释放 %s" % (
            rep.deleted, cleaner.human_size(rep.freed))
        if rep.skipped:
            msg += "；跳过 %d 个（被占用或无权限）" % rep.skipped
        notice.note(self, "ok", msg)
        self.status.emit(f"残留清理完成：释放 {cleaner.human_size(rep.freed)}")
        self.scan()

    def shutdown(self) -> None:
        stop_all(self)


# ------------------------------------------------------------------ 文件占用
class LockTab(QWidget):
    """谁占着这个文件：Restart Manager 直接给 PID 与程序名。"""

    status = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._busy = False
        self._worker = None
        self._rep: locks.LockReport | None = None
        self._rows: list[locks.LockFile] = []
        self.setAcceptDrops(True)
        self._build()

    def _build(self) -> None:
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 10, 0, 0)
        lay.setSpacing(8)

        top = QHBoxLayout()
        top.setSpacing(8)
        title = QLabel("文件占用排查")
        title.setProperty("role", "H2")
        top.addWidget(title)
        top.addStretch(1)
        self.btn_file = ghost_button("选择文件…", "folder")
        self.btn_file.clicked.connect(self._pick_file)
        top.addWidget(self.btn_file)
        self.btn_dir = ghost_button("选择文件夹…", "folder")
        self.btn_dir.clicked.connect(self._pick_dir)
        top.addWidget(self.btn_dir)
        self.btn_go = primary_button("查占用", "search")
        self.btn_go.clicked.connect(self.scan)
        top.addWidget(self.btn_go)
        self.btn_stop = ghost_button("停止")
        self.btn_stop.setEnabled(False)
        self.btn_stop.clicked.connect(self._stop)
        top.addWidget(self.btn_stop)
        lay.addLayout(top)

        pick = QHBoxLayout()
        pick.setSpacing(8)
        self.path_edit = QLineEdit()
        self.path_edit.setPlaceholderText(
            "把文件或文件夹拖到这里，或点上面的按钮选择；也可以直接粘贴完整路径后回车")
        self.path_edit.setClearButtonEnabled(True)
        self.path_edit.returnPressed.connect(self.scan)
        pick.addWidget(self.path_edit, 1)
        self.only_locked = QCheckBox("只看占用的")
        self.only_locked.setMinimumWidth(104)
        self.only_locked.setChecked(True)
        self.only_locked.stateChanged.connect(lambda _: self._fill_files())
        pick.addWidget(self.only_locked)
        lay.addLayout(pick)

        hint = QLabel("回答「这个文件删不掉、这个程序装不上，到底是谁占着」。"
                      "查的是 Restart Manager —— 安装程序让你「关闭以下程序」用的就是它。"
                      "内核态独占的文件（页面文件、注册表 hive）查不到进程，会单独标注。")
        hint.setProperty("role", "faint")
        hint.setWordWrap(True)
        lay.addWidget(hint)

        kpi = QHBoxLayout()
        kpi.setSpacing(8)
        self.kpi_files = KpiCard("查询文件", "-", "尚未运行")
        self.kpi_locked = KpiCard("被占用", "-", "")
        self.kpi_proc = KpiCard("占用进程", "-", "")
        self.kpi_cost = KpiCard("耗时", "-", "")
        for k in (self.kpi_files, self.kpi_locked, self.kpi_proc, self.kpi_cost):
            kpi.addWidget(k, 1)
        lay.addLayout(kpi)

        self.headline = QLabel("选一个文件或文件夹开始")
        self.headline.setObjectName("H3")
        self.headline.setWordWrap(True)
        lay.addWidget(self.headline)

        split = QSplitter(Qt.Vertical)
        split.setChildrenCollapsible(False)

        # 末列必须是要拉满的那一列（make_table 用 setStretchLastSection）。
        # 早期把「说明」放最后，结果路径列被挤成 40 个字符的省略号。
        self.file_model = RowModel([
            Col("状态", 88, lambda r: r.verdict,
                color_fn=lambda r: r.verdict_level, tip=lambda r: r.detail),
            Col("占用", 62, lambda r: ("%d 个" % len(r.processes)
                                       if r.processes else "—"), mono=True,
                color="dim"),
            Col("文件", 0, lambda r: r.name, stretch=True, mono=True,
                tip=lambda r: r.path + ("\n" + r.detail if r.detail else "")),
        ], parent=self)
        self.file_stack = QStackedWidget()
        self.file_table = make_table(self.file_model, row_height=24)
        self.file_table.selectionModel().currentRowChanged.connect(
            lambda cur, _prev: self._show_procs(self._row_at(cur.row())))
        self.file_stack.addWidget(self.file_table)
        self.file_stack.addWidget(EmptyState(
            "search", "还没有查询结果",
            "选一个删不掉的文件夹，或粘贴那个文件的完整路径。"
            "结果会直接告诉你占着它的是哪个程序、PID 是多少。"))
        split.addWidget(self.file_stack)

        self.proc_model = RowModel([
            Col("PID", 66, lambda r: str(r.pid), mono=True),
            Col("程序", 150, lambda r: r.label, tip=lambda r: r.exe),
            Col("类型", 84, lambda r: r.type_text, color="dim"),
            Col("状态", 92, lambda r: r.status_text, color="dim"),
            Col("可重启", 62, lambda r: "是" if r.restartable else "否",
                color="faint"),
            Col("可执行文件", 0, lambda r: r.exe, stretch=True, color="faint",
                tip=lambda r: r.exe),
        ], parent=self)
        self.proc_stack = QStackedWidget()
        self.proc_table = make_table(self.proc_model, row_height=24)
        self.proc_stack.addWidget(self.proc_table)
        self.proc_stack.addWidget(EmptyState(
            "info", "选中上面一行，这里显示占用它的进程",
            "如果文件确实没人占用，说明问题不在「被锁」—— "
            "可能是权限不足（需要管理员）或路径本身有问题。"))
        split.addWidget(self.proc_stack)
        split.setSizes([300, 220])
        lay.addWidget(split, 1)

        foot = QHBoxLayout()
        foot.setSpacing(8)
        self.btn_open = ghost_button("打开所在位置", "folder")
        self.btn_open.clicked.connect(self._open_location)
        foot.addWidget(self.btn_open)
        self.btn_copy = ghost_button("复制结论")
        self.btn_copy.clicked.connect(self._copy_result)
        foot.addWidget(self.btn_copy)
        foot.addStretch(1)
        self.note = QLabel("Restart Manager 只报告「替换/删除该文件需要重启的进程」，"
                           "看不到驱动内部持有的句柄。")
        self.note.setProperty("role", "faint")
        self.note.setWordWrap(True)
        foot.addWidget(self.note, 2)
        lay.addLayout(foot)

    # ---- 选择
    def _pick_file(self) -> None:
        start = self.path_edit.text().strip() or os.path.expandvars("%USERPROFILE%")
        path, _ = QFileDialog.getOpenFileName(self, "选择要排查的文件", start,
                                              "所有文件 (*.*)")
        if path:
            self.path_edit.setText(os.path.normpath(path))
            self.scan()

    def _pick_dir(self) -> None:
        start = self.path_edit.text().strip() or os.path.expandvars("%USERPROFILE%")
        path = QFileDialog.getExistingDirectory(self, "选择要排查的文件夹", start)
        if path:
            self.path_edit.setText(os.path.normpath(path))
            self.scan()

    def _open_location(self) -> None:
        target = self.path_edit.text().strip()
        if not target:
            return
        if os.path.isdir(target):
            QDesktopServices.openUrl(QUrl.fromLocalFile(target))
        elif os.path.isfile(target):
            QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.dirname(target)))

    def _copy_result(self) -> None:
        rep = self._rep
        if rep is None:
            return
        lines = ["文件占用排查：" + rep.target, rep.headline, ""]
        for f in rep.files:
            if not (f.locked or f.rc or f.kernel_held):
                continue
            lines.append("%s  [%s]  %s" % (f.name, f.verdict, f.path))
            if f.detail:
                lines.append("    " + f.detail)
            for p in f.processes:
                lines.append("    pid=%d %s（%s）%s" % (p.pid, p.label,
                                                       p.type_text, p.exe))
        QApplication.clipboard().setText("\n".join(lines))
        self.status.emit("结论已复制到剪贴板")

    # ---- 拖放
    def dragEnterEvent(self, e) -> None:
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e) -> None:
        urls = e.mimeData().urls()
        if not urls:
            return
        path = urls[0].toLocalFile()
        if path:
            self.path_edit.setText(os.path.normpath(path))
            self.scan()

    # ---- 查询
    def scan(self) -> None:
        if self._busy:
            return
        target = self.path_edit.text().strip().strip('"')
        if not target:
            self.status.emit("先选一个文件或文件夹")
            return
        self._busy = True
        self.btn_go.setEnabled(False)
        self.btn_stop.setEnabled(True)
        self.headline.setText("正在查询「%s」…" % os.path.basename(target) or target)
        self.status.emit("正在向 Restart Manager 查询占用…")

        def job(tick, stop):
            # 目录里几乎每个文件都被占着时，二分退化成 O(n)，一个会话 200ms
            # 起步 —— 所以 query 自己带时间预算，同时把进度和取消位传进去。
            return locks.query(target, stop_flag=stop, progress=tick)

        def tick(done, total, text):
            self.headline.setText("正在%s：%d / %d 个文件" % (text, done, total))

        def done(rep):
            self._busy = False
            self._worker = None
            self.btn_go.setEnabled(True)
            self.btn_stop.setEnabled(False)
            self._rep = rep
            self._fill(rep)
            self.status.emit("查询完成：" + rep.headline)

        def failed(msg):
            self._busy = False
            self._worker = None
            self.btn_go.setEnabled(True)
            self.btn_stop.setEnabled(False)
            self.headline.setText("查询失败：%s" % msg)
            self.status.emit("查询失败：%s" % msg)

        self._worker = run_bg(self, job, done, tick, failed)

    def _stop(self) -> None:
        if self._worker is not None:
            self._worker.stop_flag[0] = True
            self.btn_stop.setEnabled(False)
            self.headline.setText("正在停止（等当前这一批查完）…")
            self.status.emit("已请求停止")

    def _fill(self, rep: locks.LockReport) -> None:
        self.headline.setText(rep.headline + (
            "　（%s）" % "；".join(rep.notes) if rep.notes else ""))
        if rep.timeout_count:
            self.kpi_files.set_value("%d" % rep.total,
                                     "个文件 · %d 个没查完" % rep.timeout_count,
                                     "warn")
        else:
            self.kpi_files.set_value("%d" % rep.total, "个文件已查",
                                     "dim" if not rep.total else "")
        self.kpi_locked.set_value("%d" % rep.locked_count,
                                  "个文件被占用" if rep.locked_count else "没有占用",
                                  "warn" if rep.locked_count else "ok")
        uniq = len({p.pid for f in rep.files for p in f.processes})
        self.kpi_proc.set_value("%d" % uniq, "个不同进程",
                                "warn" if uniq else "dim")
        self.kpi_cost.set_value("%.0f ms" % rep.elapsed_ms, "查询耗时",
                                "ok" if rep.elapsed_ms < 500 else "info")
        self._fill_files()
        # 默认把第一行有内容的选中，省一次点击
        prefer = None
        for i, f in enumerate(self._rows):
            if f.locked or f.rc:
                prefer = i
                break
        if prefer is None and self._rows:
            prefer = 0
        if prefer is not None:
            self.file_table.selectRow(prefer)
            self._show_procs(self._rows[prefer])
        else:
            self._show_procs(None)

    def _fill_files(self) -> None:
        rep = self._rep
        if rep is None:
            return
        rows = rep.files
        if self.only_locked.isChecked():
            # 「未查完」也要留着：这几行既没有占用也没有结论，藏起来会让人
            # 以为整批都干净了。
            rows = [f for f in rows
                    if f.locked or f.rc or f.kernel_held or f.timeout
                    or not f.exists]
        self._rows = rows
        self.file_model.set_rows(rows)
        self.file_stack.setCurrentIndex(0 if rows else 1)
        if not rows and not rep.total:
            w0 = self.file_stack.widget(1)
            if isinstance(w0, EmptyState):
                w0.set_text("没有可查的文件",
                            rep.notes[0] if rep.notes else
                            "这个路径不存在，或者是个空目录。")
        elif not rows and rep.total:
            # 全部干净时不要留一片空白，直说结论
            self.file_stack.setCurrentIndex(1)
            w = self.file_stack.widget(1)
            if isinstance(w, EmptyState):
                w.set_text("这批文件都没有被占用",
                           "共查了 %d 个文件，没有进程持有它们。"
                           "如果仍然删不掉，方向是权限（需要管理员）或磁盘只读/坏道。"
                           % rep.total)

    def _row_at(self, row: int):
        if 0 <= row < len(self._rows):
            return self._rows[row]
        return None

    def _show_procs(self, lf) -> None:
        if lf is None:
            self.proc_model.set_rows([])
            self.proc_stack.setCurrentIndex(1)
            return
        procs = list(lf.processes)
        self.proc_model.set_rows(procs)
        self.proc_stack.setCurrentIndex(0 if procs else 1)
        if not procs:
            w = self.proc_stack.widget(1)
            if isinstance(w, EmptyState):
                if lf.kernel_held:
                    w.set_text("系统独占，查不到进程",
                               "%s。这类文件不需要也无法「关闭占用」，"
                               "要释放只能改系统设置或重启。" % lf.kernel_held)
                elif lf.rc:
                    w.set_text("这个路径查不了", lf.error)
                elif not lf.exists:
                    w.set_text("路径不存在", "确认一下是不是已经被删了，或路径拼错。")
                else:
                    w.set_text("选中上面一行，这里显示占用它的进程",
                               "如果文件确实没人占用，说明问题不在「被锁」—— "
                               "可能是权限不足（需要管理员）或路径本身有问题。")

    def shutdown(self) -> None:
        if self._worker is not None:
            self._worker.stop_flag[0] = True
            self._worker = None
        stop_all(self)


# ------------------------------------------------------------------ 容器
class ResiduePage(QWidget):
    status = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(*PAGE_MARGINS)
        lay.setSpacing(8)
        tabs = QTabWidget()
        self.tab_reg = RegistryTab()
        self.tab_left = LeftoverTab()
        self.tab_lock = LockTab()
        tabs.addTab(self.tab_reg, "注册表冗余")
        tabs.addTab(self.tab_left, "卸载残留")
        tabs.addTab(self.tab_lock, "文件占用")
        for w in (self.tab_reg, self.tab_left, self.tab_lock):
            w.status.connect(self.status)
        lay.addWidget(tabs, 1)
        self.tabs = tabs

    def shutdown(self) -> None:
        for w in (self.tab_reg, self.tab_left, self.tab_lock):
            w.shutdown()
