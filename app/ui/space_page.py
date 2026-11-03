# -*- coding: utf-8 -*-
"""空间占用：大文件检索 + 目录占用分析。"""
from __future__ import annotations

import os
from datetime import datetime

from PyQt5.QtCore import Qt, QUrl, pyqtSignal
from PyQt5.QtGui import QDesktopServices
from PyQt5.QtWidgets import (QComboBox, QFileDialog, QHBoxLayout, QLabel,
                             QProgressBar, QPushButton,
                             QVBoxLayout, QWidget)

from ..core import cleaner, disk, space
from .tables import Col, RowModel, make_table, run_bg, stop_all
from . import notice
from .widgets import ghost_button, primary_button


def fmt_time(ts: float) -> str:
    if not ts:
        return "-"
    try:
        return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")
    except (OSError, OverflowError, ValueError):
        return "-"


def _default_root() -> str:
    for guess in (os.path.expandvars("%SystemDrive%\\"), "C:\\"):
        if os.path.isdir(guess):
            return guess
    return os.path.abspath(os.sep)


class PathPicker(QWidget):
    """磁盘 / 目录选择器，供两个空间分析页复用。

    默认下拉列出本机所有卷（带卷标与剩余空间），点一下就能换盘；
    同时也允许直接手输路径，或用「浏览」挑一个具体目录。
    """

    changed = pyqtSignal(str)

    def __init__(self, value: str = "", parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        self.box = QComboBox()
        self.box.setEditable(True)
        self.box.setInsertPolicy(QComboBox.NoInsert)
        self.box.setMaxVisibleItems(12)
        self.box.lineEdit().setPlaceholderText("选择磁盘，或直接输入目录路径")
        self.box.setToolTip("下拉选择磁盘；也可以直接输入路径，或点「浏览」选目录")
        lay.addWidget(self.box, 1)
        self.btn = ghost_button("浏览")
        self.btn.setMinimumWidth(62)
        self.btn.clicked.connect(self._browse)
        lay.addWidget(self.btn)

        # 只有用户真的动手改了文本才回传，避免程序设置路径时触发重扫
        self.box.lineEdit().textEdited.connect(lambda _t: self.changed.emit(self.path()))
        self.box.activated[int].connect(self._on_pick)

        self.reload_volumes()
        self.set_path(value or _default_root())

    # -------- 卷列表
    def reload_volumes(self) -> None:
        """重新枚举本机卷。U 盘热插拔后切换页面即可看到新的盘符。"""
        keep = self.path()
        self.box.blockSignals(True)
        self.box.clear()
        for v in disk.list_volumes():
            if not v.total:                       # 空光驱 / 未格式化卷没有意义
                continue
            tag = v.label or ("本地磁盘" if v.is_fixed else v.media_text)
            # 与「磁盘优化」页口径一致：USB 移动盘即使被当成固定盘也标出来
            if v.label and (not v.is_fixed or v.bus == "USB"):
                tag = f"{v.label} · {v.media_text}"
            text = (f"{v.letter}　{tag}　·　剩余 {cleaner.human_size(v.free)}"
                    f" / {cleaner.human_size(v.total)}")
            self.box.addItem(text, v.letter + "\\")
        self.box.blockSignals(False)
        if keep:
            self.set_path(keep)

    def _on_pick(self, i: int) -> None:
        root = self.box.itemData(i)
        if root:
            self.changed.emit(root)

    # -------- 取值
    def path(self) -> str:
        txt = self.box.currentText().strip()
        if not txt:
            return ""
        idx = self.box.findText(txt)          # 选中的是卷：文本带卷标，靠文本反查盘符
        if idx >= 0:
            data = self.box.itemData(idx)
            if data:
                return data
        return txt

    def set_path(self, p: str) -> None:
        p = (p or "").strip()
        idx = self.box.findData(p) if p else -1
        if idx >= 0:
            self.box.setCurrentIndex(idx)      # 盘符根目录：选中该项，显示卷标与容量
        else:
            self.box.setEditText(p)

    def _browse(self) -> None:
        start = self.path()
        if not start or not os.path.isdir(start):
            start = _default_root()
        d = QFileDialog.getExistingDirectory(self, "选择目录", start)
        if d:
            p = os.path.normpath(d)
            self.set_path(p)
            self.changed.emit(p)

    def showEvent(self, e):
        super().showEvent(e)
        self.reload_volumes()


# ------------------------------------------------------------------ 大文件
class LargeFileTab(QWidget):
    status = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._busy = False
        self._build()

    def _build(self) -> None:
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 10, 0, 0)
        lay.setSpacing(9)

        top = QHBoxLayout()
        top.setSpacing(8)
        self.picker = PathPicker(_default_root())
        top.addWidget(self.picker, 1)
        big = QLabel("大于")
        big.setProperty("role", "dim")
        top.addWidget(big)
        self.size_box = QComboBox()
        for label, mb in (("100 MB", 100), ("500 MB", 500), ("1 GB", 1024),
                          ("5 GB", 5120)):
            self.size_box.addItem(label, mb)
        self.size_box.setCurrentIndex(1)
        self.size_box.setFixedWidth(96)
        top.addWidget(self.size_box)
        self.btn_scan = primary_button("开始扫描", "search")
        self.btn_scan.clicked.connect(self.scan)
        top.addWidget(self.btn_scan)
        lay.addLayout(top)

        hint = QLabel("只读扫描，不会删除任何文件。勾选后删除默认送入回收站，可从回收站还原。")
        hint.setProperty("role", "faint")
        lay.addWidget(hint)

        self.model = RowModel([
            Col("", 34),
            Col("文件", 300, lambda r: os.path.basename(r[0]), tip=lambda r: r[0]),
            Col("大小", 96, lambda r: cleaner.human_size(r[1]), color="warn"),
            Col("修改时间", 140, lambda r: fmt_time(r[2]), color="dim", mono=True),
            Col("所在目录", 0, lambda r: os.path.dirname(r[0]), color="faint",
                stretch=True),
        ], checkable=True, parent=self)
        self.model.checkChanged.connect(self._sync_footer)
        self.table = make_table(self.model)
        self.table.doubleClicked.connect(self._reveal)
        lay.addWidget(self.table, 1)

        foot = QHBoxLayout()
        foot.setSpacing(8)
        self.btn_all = ghost_button("全选")
        self.btn_all.clicked.connect(lambda: self.model.check_all(True))
        self.btn_none = ghost_button("取消全选")
        self.btn_none.clicked.connect(lambda: self.model.check_all(False))
        foot.addWidget(self.btn_all)
        foot.addWidget(self.btn_none)
        self.btn_open = ghost_button("打开所在位置")
        self.btn_open.clicked.connect(self._reveal)
        foot.addWidget(self.btn_open)
        foot.addStretch(1)
        self.sel_label = QLabel("未扫描")
        self.sel_label.setProperty("role", "dim")
        foot.addWidget(self.sel_label)
        self.btn_del = QPushButton("删除选中（回收站）")
        self.btn_del.setObjectName("DangerBtn")
        self.btn_del.setCursor(Qt.PointingHandCursor)
        self.btn_del.clicked.connect(self._delete)
        self.btn_del.setEnabled(False)
        foot.addWidget(self.btn_del)
        lay.addLayout(foot)

        self.prog = QProgressBar()
        self.prog.setTextVisible(False)
        self.prog.setFixedHeight(5)
        self.prog.hide()
        lay.addWidget(self.prog)

    # -------- 扫描
    def scan(self) -> None:
        if self._busy:
            return
        root = self.picker.path()
        if not root or not os.path.isdir(root):
            notice.note(self, "warn", "请选择一个存在的目录。")
            return
        min_size = int(self.size_box.currentData()) * 1024 * 1024
        self._busy = True
        self.btn_scan.setEnabled(False)
        self.prog.setRange(0, 0)
        self.prog.show()
        self.status.emit(f"正在扫描 {root} …")

        def job(tick, stop):
            return space.find_large_files(
                root, min_size, limit=300,
                stop_flag=stop,
                progress=lambda n, cur: tick(n, 0, os.path.basename(cur) or cur))

        run_bg(self, job, self._on_done, self._on_tick, self._on_fail)

    def _on_tick(self, a, b, msg) -> None:
        self.status.emit(f"已遍历 {a:,} 个条目 · {msg}")

    def _on_fail(self, msg: str) -> None:
        self._busy = False
        self.btn_scan.setEnabled(True)
        self.prog.hide()
        self.status.emit("扫描失败：" + msg)

    def _on_done(self, rows) -> None:
        self._busy = False
        self.btn_scan.setEnabled(True)
        self.prog.hide()
        self.model.set_rows(rows)
        total = sum(r[1] for r in rows)
        self.sel_label.setText(
            f"共 {len(rows)} 个文件 · {cleaner.human_size(total)}" if rows
            else "没有找到超过阈值的文件")
        self.status.emit(f"扫描完成：{len(rows)} 个大文件，合计 {cleaner.human_size(total)}")

    # -------- 操作
    def _sync_footer(self) -> None:
        rows = self.model.checked_rows()
        self.btn_del.setEnabled(bool(rows) and not self._busy)
        if rows:
            self.sel_label.setText(
                f"已选 {len(rows)} 个 · {cleaner.human_size(sum(r[1] for r in rows))}")

    def _reveal(self) -> None:
        idx = self.table.currentIndex()
        row = self.model.row_at(idx.row()) if idx.isValid() else None
        if row is None:
            rows = self.model.checked_rows()
            row = rows[0] if rows else None
        if row is None:
            return
        path = row[0]
        if os.path.exists(path):
            QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.dirname(path)))
        else:
            notice.note(self, "warn", "该文件已被移动或删除，重新扫描一下列表。")

    def _delete(self) -> None:
        rows = self.model.checked_rows()
        if not rows:
            return
        total = sum(r[1] for r in rows)
        paths = [(r[0], r[1]) for r in rows]
        notice.ask(
            self, "确认删除",
            "将把 %d 个文件（合计 %s）移入回收站。回收站清空后无法恢复，"
            "请确认这些文件不再需要。"
            % (len(rows), cleaner.human_size(total)),
            ok_text="移入回收站", danger=True,
            on_yes=lambda: self._do_delete(paths))

    def _do_delete(self, paths) -> None:
        self.status.emit("正在移入回收站…")
        run_bg(self, lambda tick, stop: cleaner.delete_files(
            paths, use_recycle=True, stop_flag=stop),
            self._after_delete, None, self._on_fail)

    def _after_delete(self, rep) -> None:
        notice.note(
            self, "ok",
            "已移入回收站 %d 个文件，释放 %s%s"
            % (rep.deleted, cleaner.human_size(rep.freed),
               "；跳过 %d 个（被占用或无权限）" % rep.skipped if rep.skipped else ""))
        self.scan()


# ------------------------------------------------------------------ 大目录
class DirTreeTab(QWidget):
    status = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._busy = False
        self._root_node = None
        self._history: list[str] = []
        self._build()

    def _build(self) -> None:
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 10, 0, 0)
        lay.setSpacing(9)

        top = QHBoxLayout()
        top.setSpacing(8)
        self.picker = PathPicker(_default_root())
        top.addWidget(self.picker, 1)
        self.btn_up = ghost_button("上级目录")
        self.btn_up.clicked.connect(self._go_up)
        self.btn_up.setEnabled(False)
        top.addWidget(self.btn_up)
        self.btn_scan = primary_button("分析占用", "search")
        self.btn_scan.clicked.connect(self.scan)
        top.addWidget(self.btn_scan)
        lay.addLayout(top)

        self.crumb = QLabel("选择磁盘后点「分析占用」，双击某一行可继续下钻")
        self.crumb.setProperty("role", "faint")
        lay.addWidget(self.crumb)

        self.model = RowModel([
            Col("目录", 300, lambda r: r.name, tip=lambda r: r.path),
            Col("占用", 100, lambda r: cleaner.human_size(r.size), color="warn"),
            Col("占当前层", 90, lambda r: f"{self._pct(r)}", color="accent"),
            Col("文件数", 90, lambda r: f"{r.files:,}", color="dim", mono=True),
            Col("最后修改", 140, lambda r: fmt_time(r.mtime), color="dim", mono=True),
        ], parent=self)
        self.table = make_table(self.model, row_height=28)
        self.table.doubleClicked.connect(self._drill)
        lay.addWidget(self.table, 1)

        foot = QHBoxLayout()
        foot.setSpacing(8)
        self.btn_open = ghost_button("打开该目录")
        self.btn_open.clicked.connect(self._open_current)
        foot.addWidget(self.btn_open)
        self.btn_reveal = ghost_button("打开选中项")
        self.btn_reveal.clicked.connect(self._open_selected)
        foot.addWidget(self.btn_reveal)
        foot.addStretch(1)
        self.sum_label = QLabel("")
        self.sum_label.setProperty("role", "dim")
        foot.addWidget(self.sum_label)
        lay.addLayout(foot)

        self.prog = QProgressBar()
        self.prog.setTextVisible(False)
        self.prog.setFixedHeight(5)
        self.prog.hide()
        lay.addWidget(self.prog)

    def _pct(self, node) -> str:
        total = self._root_node.size if self._root_node else 0
        if not total:
            return "-"
        return f"{node.size / total * 100:.1f}%"

    # -------- 扫描
    def scan(self, path: str = "") -> None:
        if self._busy:
            return
        target = path or self.picker.path()
        if not target or not os.path.isdir(target):
            notice.note(self, "warn", "请选择一个存在的目录。")
            return
        if not path and self.picker.path() != target:
            self.picker.set_path(target)
        self._busy = True
        self.btn_scan.setEnabled(False)
        self.prog.setRange(0, 0)
        self.prog.show()
        self.crumb.setText(f"正在统计 {target} 下各子目录的大小…")

        def job(tick, stop):
            node = space.analyze_dir(
                target, stop_flag=stop,
                progress=lambda i, n, name: tick(i, n, name))
            return (target, node)

        run_bg(self, job, self._on_done, self._on_tick, self._on_fail)

    def _on_tick(self, i, n, name) -> None:
        if n:
            self.prog.setRange(0, n)
            self.prog.setValue(i)
        self.status.emit(f"正在统计 {name}（{i + 1}/{n}）" if n else "正在统计…")

    def _on_fail(self, msg: str) -> None:
        self._busy = False
        self.btn_scan.setEnabled(True)
        self.prog.hide()
        self.status.emit("分析失败：" + msg)

    def _on_done(self, payload) -> None:
        target, node = payload
        self._busy = False
        self.btn_scan.setEnabled(True)
        self.prog.hide()
        self._root_node = node
        self.model.set_rows(node.children)
        self.crumb.setText(f"{target} · 合计 {cleaner.human_size(node.size)} · "
                           f"{node.files:,} 个文件 · 下方为一级子目录占用")
        self.sum_label.setText(f"共 {len(node.children)} 个子目录")
        self.btn_up.setEnabled(bool(self._history))
        self.status.emit(f"分析完成：{cleaner.human_size(node.size)}，"
                         f"{node.files:,} 个文件")

    def _drill(self) -> None:
        idx = self.table.currentIndex()
        node = self.model.row_at(idx.row()) if idx.isValid() else None
        if node is None or not os.path.isdir(node.path):
            return
        self._history.append(self.picker.path())
        self.btn_up.setEnabled(True)
        self.picker.set_path(node.path)
        self.scan(node.path)

    def _go_up(self) -> None:
        if not self._history:
            return
        prev = self._history.pop()
        self.btn_up.setEnabled(bool(self._history))
        self.picker.set_path(prev)
        self.scan(prev)

    def _open_current(self) -> None:
        p = self.picker.path()
        if os.path.isdir(p):
            QDesktopServices.openUrl(QUrl.fromLocalFile(p))

    def _open_selected(self) -> None:
        idx = self.table.currentIndex()
        node = self.model.row_at(idx.row()) if idx.isValid() else None
        if node and os.path.isdir(node.path):
            QDesktopServices.openUrl(QUrl.fromLocalFile(node.path))

    def shutdown(self) -> None:
        stop_all(self)
