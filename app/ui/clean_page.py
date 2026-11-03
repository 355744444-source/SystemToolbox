# -*- coding: utf-8 -*-
"""清理优化工作台：垃圾清理 / 大文件 / 大目录 / 磁盘优化。"""
from __future__ import annotations

from datetime import datetime

from PyQt5.QtCore import Qt, QThread, pyqtSignal
from PyQt5.QtWidgets import (QApplication, QCheckBox, QFrame, QHBoxLayout,
                             QLabel, QPlainTextEdit, QProgressBar,
                             QPushButton, QScrollArea, QSplitter, QTabWidget,
                             QVBoxLayout, QWidget)

from .. import theme
from ..core import cleaner, disk
from .space_page import DirTreeTab, LargeFileTab, fmt_time
from .tables import Col, RowModel, make_table, run_bg, stop_all
from . import notice
from .widgets import Bar, Dot, ghost_button, primary_button


def _admin_suffix(rule) -> str:
    return " ·需管理员" if rule.need_admin else ""


def _prog2(tick):
    """把三参数 tick 包装成两参数进度回调（delete_files 的签名）。"""
    return lambda done, total: tick(done, total, "正在处理")


# ------------------------------------------------------------------ 垃圾清理
class GarbageTab(QWidget):
    status = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._busy = False
        self._results: list[cleaner.ScanResult] = []
        self._build()

    def _build(self) -> None:
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 10, 0, 0)
        lay.setSpacing(9)

        top = QHBoxLayout()
        top.setSpacing(8)
        title = QLabel("系统与应用缓存清理")
        title.setProperty("role", "H2")
        top.addWidget(title)
        top.addStretch(1)
        self.btn_scan = primary_button("开始扫描", "search")
        self.btn_scan.clicked.connect(self.scan)
        top.addWidget(self.btn_scan)
        lay.addLayout(top)

        hint = QLabel("扫描全程只读。清理时文件默认送入回收站，可随时还原；"
                      "正在被程序占用的文件会自动跳过。")
        hint.setProperty("role", "faint")
        lay.addWidget(hint)

        split = QSplitter(Qt.Vertical)
        split.setChildrenCollapsible(False)
        self.split = split

        self.model = RowModel([
            Col("", 34),
            Col("清理项", 210, lambda r: r.rule.name),
            Col("可释放", 100, lambda r: cleaner.human_size(r.total_size), color="warn"),
            Col("文件数", 80, lambda r: f"{r.count:,}", color="dim", mono=True),
            Col("风险", 54, lambda r: r.rule.risk, color="dim"),
            Col("说明", 0, lambda r: (r.rule.desc or "") + _admin_suffix(r.rule),
                color="faint", stretch=True),
        ], checkable=True, parent=self)
        self.model.checkChanged.connect(self._sync_footer)
        self.table = make_table(self.model, row_height=30)
        self.table.selectionModel().currentRowChanged.connect(self._show_detail)
        split.addWidget(self.table)

        detail_wrap = QWidget()
        dl = QVBoxLayout(detail_wrap)
        dl.setContentsMargins(0, 6, 0, 0)
        dl.setSpacing(5)
        self.detail_cap = QLabel("选中上方某一项，这里显示将被删除的具体文件")
        self.detail_cap.setProperty("role", "faint")
        dl.addWidget(self.detail_cap)
        self.detail = RowModel([
            Col("文件路径", 0, lambda r: r[0], stretch=True),
            Col("大小", 96, lambda r: cleaner.human_size(r[1]), color="dim"),
            Col("修改时间", 140, lambda r: fmt_time(r[2]), color="faint", mono=True),
        ], parent=self)
        self.detail_table = make_table(self.detail, row_height=22)
        self.detail_table.setMinimumHeight(168)
        dl.addWidget(self.detail_table, 1)
        split.addWidget(detail_wrap)
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
        self.chk_recycle = QCheckBox("删除到回收站（可还原）")
        self.chk_recycle.setChecked(True)
        foot.addWidget(self.chk_recycle)
        foot.addStretch(1)
        self.sel_label = QLabel("尚未扫描")
        self.sel_label.setProperty("role", "dim")
        foot.addWidget(self.sel_label)
        self.btn_clean = QPushButton("清理选中项")
        self.btn_clean.setObjectName("DangerBtn")
        self.btn_clean.setCursor(Qt.PointingHandCursor)
        self.btn_clean.setEnabled(False)
        self.btn_clean.clicked.connect(self._clean)
        foot.addWidget(self.btn_clean)
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
            self.split.setSizes([int(h * 0.56), int(h * 0.44)])
            self._sized = True

    # -------- 扫描
    def scan(self) -> None:
        if self._busy:
            return
        self._busy = True
        self.btn_scan.setEnabled(False)
        self.prog.setRange(0, len(cleaner.RULES))
        self.prog.setValue(0)
        self.prog.show()
        self.status.emit("正在扫描垃圾文件…")

        def job(tick, stop):
            return cleaner.scan_rules(cleaner.RULES, stop, progress=tick)

        run_bg(self, job, self._on_done, self._on_tick, self._on_fail)

    def _on_tick(self, i, n, name) -> None:
        self.prog.setRange(0, max(1, n))
        self.prog.setValue(i)
        self.status.emit(f"正在扫描：{name}" if name else "扫描收尾…")

    def _on_fail(self, msg: str) -> None:
        self._busy = False
        self.btn_scan.setEnabled(True)
        self.prog.hide()
        self.status.emit("扫描失败：" + msg)

    def _on_done(self, results) -> None:
        self._busy = False
        self.btn_scan.setEnabled(True)
        self.prog.hide()
        self._results = list(results)
        self.model.set_rows(self._results)
        # 默认勾选"安全且确有内容"的项。按**行对象**勾，不按行号 ——
        # 表格可能停在上次的排序状态上，行号对不上源列表
        picked = [r for r in self._results if r.rule.default_on and r.count > 0]
        self.model.check_rows(picked)
        total = sum(r.total_size for r in picked)
        self.status.emit(f"扫描完成：可清理 {cleaner.human_size(total)}")

    # -------- 交互
    def _sync_footer(self) -> None:
        rows = self.model.checked_rows()
        total = sum(r.total_size for r in rows)
        count = sum(r.count for r in rows)
        self.sel_label.setText(
            f"已选 {len(rows)} 项 · {count:,} 个文件 · {cleaner.human_size(total)}"
            if rows else "未选中任何清理项")
        self.btn_clean.setEnabled(bool(rows) and not self._busy)

    def _show_detail(self, cur, _prev=None) -> None:
        res = self.model.row_at(cur.row()) if cur.isValid() else None
        if res is None:
            self.detail.set_rows([])
            return
        self.detail.set_rows(res.files)
        cap = f"{res.rule.name} · 共 {res.count:,} 个文件 / {cleaner.human_size(res.total_size)}"
        if res.rule.note:
            cap += f" · {res.rule.note}"
        if res.detail_capped:
            cap += f"（明细仅显示最大的 {len(res.files):,} 个）"
        if not res.ok:
            cap = f"{res.rule.name} · 该目录在当前系统上不存在"
        self.detail_cap.setText(cap)

    # -------- 清理
    def _clean(self) -> None:
        if self._busy:
            return
        picked = [r for r in self.model.checked_rows() if r.count > 0]
        if not picked:
            return
        total = sum(r.total_size for r in picked)
        count = sum(r.count for r in picked)
        names = "、".join(r.rule.name for r in picked[:4])
        if len(picked) > 4:
            names += " 等 %d 项" % len(picked)
        use_recycle = self.chk_recycle.isChecked()
        mode = ("移入回收站，可还原" if use_recycle else "永久删除，不可恢复")
        notice.ask(
            self, "确认清理",
            "即将清理 %s 个文件，合计 %s：%s。处理方式：%s。"
            "被程序占用的文件会自动跳过，不影响系统运行。"
            % (format(count, ","), cleaner.human_size(total), names, mode),
            ok_text="开始清理", danger=not use_recycle,
            on_yes=lambda: self._do_clean(picked, use_recycle))

    def _do_clean(self, picked, use_recycle: bool) -> None:
        self._busy = True
        self.btn_clean.setEnabled(False)
        self.btn_scan.setEnabled(False)
        self.prog.setRange(0, 0)
        self.prog.show()
        self.status.emit("正在清理…")

        def job(tick, stop):
            tick2 = _prog2(tick)
            all_files: list[tuple[str, int]] = []
            for i, res in enumerate(picked):
                tick(i, len(picked), res.rule.name)
                all_files.extend(cleaner.collect_files(res.rule, stop))
                if stop[0]:
                    break
            rep = cleaner.delete_files(all_files, use_recycle=use_recycle,
                                       stop_flag=stop, progress=tick2)
            cleaner.prune_empty_dirs(picked)
            return rep

        run_bg(self, job, self._after_clean, self._on_tick, self._on_fail)

    def _after_clean(self, rep) -> None:
        self._busy = False
        self.btn_scan.setEnabled(True)
        self.prog.hide()
        msg = ("已处理 %d 个文件，释放 %s"
               % (rep.deleted, cleaner.human_size(rep.freed)))
        if rep.to_recycle and rep.deleted:
            msg += "，文件已移入回收站可还原"
        if rep.skipped:
            msg += "；跳过 %d 个（被占用或无权限）" % rep.skipped
        if rep.failed:
            msg += "；失败 %d 个" % rep.failed
            if rep.problems:
                msg += "（%s）" % "；".join(rep.problems[:3])
        notice.note(self, "ok" if not rep.failed else "warn", msg)
        self.status.emit(f"清理完成：释放 {cleaner.human_size(rep.freed)}")
        self.scan()

    def shutdown(self) -> None:
        stop_all(self)


# ------------------------------------------------------------------ 磁盘优化
class VolumeCard(QFrame):
    clicked = pyqtSignal(object)

    def __init__(self, vol: disk.Volume, parent=None):
        super().__init__(parent)
        self.vol = vol
        self._selected = False
        self.setObjectName("VolumeCard")
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedHeight(74)
        t = theme.current()
        self.setStyleSheet(
            f"QFrame#VolumeCard{{background:{t['surface']};"
            f"border:1px solid {t['border']}; border-radius:10px;}}")

        h = QHBoxLayout(self)
        h.setContentsMargins(4, 8, 14, 8)
        h.setSpacing(12)
        self.bar_left = QFrame()
        self.bar_left.setFixedWidth(3)
        self.bar_left.setStyleSheet("background:transparent; border:none; border-radius:1px;")
        h.addWidget(self.bar_left)

        letter = QLabel(vol.letter)
        letter.setStyleSheet("font-size:20px; font-weight:700; background:transparent;")
        letter.setFixedWidth(42)
        h.addWidget(letter)

        mid = QVBoxLayout()
        mid.setSpacing(3)
        name = QLabel(vol.label or "本地磁盘")
        name.setStyleSheet("font-size:12px; font-weight:600; background:transparent;")
        mid.addWidget(name)
        sub = QLabel(f"{vol.media_text} · {vol.fs or '未知文件系统'}"
                     + (f" · {vol.bus}" if vol.bus else ""))
        sub.setStyleSheet(f"color:{t['dim']}; font-size:11px; background:transparent;")
        mid.addWidget(sub)
        h.addLayout(mid, 1)

        right = QVBoxLayout()
        right.setSpacing(5)
        stat = QLabel(f"已用 {cleaner.human_size(vol.used)} / {cleaner.human_size(vol.total)}"
                      f" · 可用 {cleaner.human_size(vol.free)}")
        stat.setStyleSheet(f"color:{t['dim']}; font-size:11px; background:transparent;")
        stat.setAlignment(Qt.AlignRight)
        right.addWidget(stat)
        self.bar = Bar(vol.used_ratio, 8)
        right.addWidget(self.bar)
        pct = QLabel(f"占用 {vol.used_ratio * 100:.0f}%")
        pct.setStyleSheet(f"color:{t['faint']}; font-size:11px; background:transparent;")
        pct.setAlignment(Qt.AlignRight)
        right.addWidget(pct)
        h.addLayout(right, 1)

    def set_selected(self, on: bool) -> None:
        self._selected = on
        t = theme.current()
        color = t["accent"] if on else t["border"]
        self.setStyleSheet(
            f"QFrame#VolumeCard{{background:{t['surface2'] if on else t['surface']};"
            f"border:1px solid {color}; border-radius:10px;}}")
        self.bar_left.setStyleSheet(
            f"background:{t['accent'] if on else 'transparent'};"
            f"border:none; border-radius:1px;")

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.clicked.emit(self.vol)
        super().mousePressEvent(e)


class ActionWorker(QThread):
    line = pyqtSignal(str)
    done = pyqtSignal(int)

    def __init__(self, action: disk.Action, parent=None):
        super().__init__(parent)
        self.action = action
        self.stop_flag = [False]

    def run(self) -> None:
        try:
            code, _lines = disk.run_action(self.action, on_line=self.line.emit,
                                           stop_flag=self.stop_flag)
        except Exception as exc:        # noqa: BLE001
            self.line.emit(f"执行异常：{exc}")
            code = -1
        self.done.emit(code)


class DiskTab(QWidget):
    status = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.volumes: list[disk.Volume] = []
        self.cards: list[VolumeCard] = []
        self.current: disk.Volume | None = None
        self._worker: ActionWorker | None = None
        # 物理磁盘健康要走 PowerShell，冷启动要 1~3 秒；等用户真正切到这个页签再查，
        # 免得一开程序就有个长任务在跑（关窗时容易踩到线程未收敛）
        self._phy_loaded = False
        self._build()
        self.refresh()

    def _build(self) -> None:
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 10, 0, 0)
        lay.setSpacing(9)

        top = QHBoxLayout()
        top.setSpacing(8)
        title = QLabel("磁盘优化")
        title.setProperty("role", "H2")
        top.addWidget(title)
        self.admin_dot = Dot(theme.current()["ok"], 8)
        top.addWidget(self.admin_dot)
        self.admin_label = QLabel("")
        self.admin_label.setProperty("role", "faint")
        top.addWidget(self.admin_label)
        top.addStretch(1)
        self.btn_elevate = ghost_button("以管理员身份重启")
        self.btn_elevate.clicked.connect(self._elevate)
        top.addWidget(self.btn_elevate)
        self.btn_refresh = ghost_button("刷新", "refresh")
        self.btn_refresh.clicked.connect(self.refresh)
        top.addWidget(self.btn_refresh)
        lay.addLayout(top)

        split = QSplitter(Qt.Vertical)
        split.setChildrenCollapsible(False)
        self.split = split

        upper = QWidget()
        ul = QVBoxLayout(upper)
        ul.setContentsMargins(0, 0, 0, 0)
        ul.setSpacing(8)

        self.vol_host = QWidget()
        self.vol_lay = QVBoxLayout(self.vol_host)
        self.vol_lay.setContentsMargins(0, 0, 0, 0)
        self.vol_lay.setSpacing(7)
        self.vol_lay.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setWidget(self.vol_host)
        ul.addWidget(scroll, 1)

        self.phy_label = QLabel("")
        self.phy_label.setProperty("role", "faint")
        self.phy_label.setWordWrap(True)
        ul.addWidget(self.phy_label)

        act = QHBoxLayout()
        act.setSpacing(8)
        self.btn_optimize = primary_button("按介质自动优化")
        self.btn_optimize.clicked.connect(lambda: self._run("optimize"))
        act.addWidget(self.btn_optimize)
        self.btn_trim = ghost_button("TRIM 全盘回收")
        self.btn_trim.clicked.connect(lambda: self._run("retrim"))
        act.addWidget(self.btn_trim)
        self.btn_analyze = ghost_button("分析碎片")
        self.btn_analyze.clicked.connect(lambda: self._run("analyze"))
        act.addWidget(self.btn_analyze)
        self.btn_chkdsk = ghost_button("扫描磁盘错误")
        self.btn_chkdsk.clicked.connect(lambda: self._run("chkdsk"))
        act.addWidget(self.btn_chkdsk)
        act.addStretch(1)
        self.btn_stop = QPushButton("停止")
        self.btn_stop.setObjectName("DangerBtn")
        self.btn_stop.setEnabled(False)
        self.btn_stop.clicked.connect(self._stop)
        act.addWidget(self.btn_stop)
        ul.addLayout(act)

        self.prog = QProgressBar()
        self.prog.setTextVisible(False)
        self.prog.setFixedHeight(5)
        ul.addWidget(self.prog)
        split.addWidget(upper)

        log_wrap = QWidget()
        ll = QVBoxLayout(log_wrap)
        ll.setContentsMargins(0, 6, 0, 0)
        ll.setSpacing(5)
        cap = QLabel("执行日志")
        cap.setProperty("role", "faint")
        ll.addWidget(cap)
        self.log = QPlainTextEdit()
        self.log.setProperty("role", "tech")
        self.log.setReadOnly(True)
        self.log.setPlaceholderText("选择一个卷后执行优化动作，系统 defrag / chkdsk 的输出会实时显示在这里")
        ll.addWidget(self.log, 1)
        split.addWidget(log_wrap)
        split.setSizes([344, 200])
        lay.addWidget(split, 1)

        self._sync_admin()

    def showEvent(self, ev) -> None:
        super().showEvent(ev)
        # 分栏只能在真正显示出来之后按实际高度分一次；之后不再抢用户的拖动。
        # 注意：这里必须和「首次显示再查物理磁盘」写在同一个 showEvent 里 ——
        # 同一个类里定义两个 showEvent，后一个会静默盖掉前一个（PyQt 不报错）。
        if not getattr(self, "_sized", False) and self.split.height() > 200:
            h = self.split.height()
            self.split.setSizes([int(h * 0.64), int(h * 0.36)])
            self._sized = True
        if not self._phy_loaded:
            self._phy_loaded = True
            self._load_physical()
    # -------- 数据
    def refresh(self) -> None:
        while self.vol_lay.count() > 1:
            item = self.vol_lay.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
        self.cards.clear()
        try:
            self.volumes = [v for v in disk.list_volumes() if v.total > 0]
        except Exception:       # noqa: BLE001
            self.volumes = []
        for v in self.volumes:
            card = VolumeCard(v)
            card.clicked.connect(self._select)
            self.vol_lay.insertWidget(self.vol_lay.count() - 1, card)
            self.cards.append(card)
        keep = next((v for v in self.volumes if v.letter == "C:"), None)
        self._select(self.current or keep or (self.volumes[0] if self.volumes else None))
        # 首次 refresh 只列表卷；物理磁盘健康留到页签首次显示时再查
        if self._phy_loaded:
            self._load_physical()
        self.status.emit(f"已检测到 {len(self.volumes)} 个磁盘卷")

    def _load_physical(self) -> None:
        self.phy_label.setText("正在读取物理磁盘健康状态…")

        def job(tick, stop):
            if stop[0]:
                return []
            return disk.physical_disks()

        run_bg(self, job, self._on_physical, None, self._on_phy_fail)

    def _on_phy_fail(self, msg: str) -> None:
        self.phy_label.setText("物理磁盘健康状态：未获取（PowerShell 不可用）")

    def _on_physical(self, disks) -> None:
        if not disks:
            self.phy_label.setText("物理磁盘健康状态：未获取（需要 Windows 8 以上或管理员）")
            return
        t = theme.current()
        parts = []
        for d in disks:
            color = {"Healthy": t["ok"], "Warning": t["warn"],
                     "Unhealthy": t["crit"]}.get(d.health, t["dim"])
            parts.append(
                f"<span style='color:{t['text']}'>{d.model}</span> "
                f"<span style='color:{t['faint']}'>{d.bus} · {d.media} · "
                f"{cleaner.human_size(d.size)}</span> "
                f"<span style='color:{color}'>健康：{d.health}</span>")
        self.phy_label.setText("　|　".join(parts))

    def _select(self, vol) -> None:
        if vol is None:
            return
        self.current = vol
        for card in self.cards:
            card.set_selected(card.vol is vol)
        self._sync_buttons()

    def _sync_buttons(self) -> None:
        v = self.current
        if v is None:
            for b in (self.btn_optimize, self.btn_trim, self.btn_analyze, self.btn_chkdsk):
                b.setEnabled(False)
            return
        admin = cleaner.is_admin()
        busy = self._worker is not None
        fixed = v.is_fixed
        self.btn_optimize.setEnabled(fixed and admin and not busy)
        self.btn_trim.setEnabled(fixed and admin and not busy and v.is_ssd is not False)
        self.btn_analyze.setEnabled(fixed and not busy)
        self.btn_chkdsk.setEnabled(fixed and admin and not busy)
        tip = "" if admin else "需要管理员权限"
        self.btn_optimize.setToolTip(tip)
        self.btn_chkdsk.setToolTip(tip)
        self.btn_trim.setToolTip(
            "机械硬盘不需要 TRIM" if v.is_ssd is False else tip)

    def _sync_admin(self) -> None:
        admin = cleaner.is_admin()
        self.admin_dot.set_color(theme.current()["ok"] if admin else theme.current()["warn"])
        self.admin_label.setText("管理员权限" if admin else "当前为普通用户，部分动作不可用")
        self.btn_elevate.setVisible(not admin)

    # -------- 动作
    def _run(self, kind: str) -> None:
        if self.current is None or self._worker is not None:
            return
        action = disk.build_action(self.current, kind)
        if action is None:
            notice.note(self, "warn", "该操作不适用于当前磁盘类型（%s）。"
                        % self.current.media_text)
            return
        if kind != "analyze":
            warn = ("该操作会移动磁盘上的数据以保证连续性，过程中请勿断电或强制关机。"
                    if kind == "optimize" else "")
            notice.ask(
                self, "确认执行 · %s" % action.name,
                "目标：%s（%s）。%s%s" % (self.current.letter,
                                         self.current.media_text,
                                         action.desc, warn),
                ok_text="开始执行", danger=(kind != "analyze"),
                on_yes=lambda: self._start_action(action))
            return
        self._start_action(action)

    def _start_action(self, action) -> None:
        self.log.clear()
        self.log.appendPlainText(f"[{datetime.now():%H:%M:%S}] {action.name} → {self.current.letter}")
        self.log.appendPlainText("$ " + " ".join(action.cmd))
        self.prog.setRange(0, 0)
        self._worker = ActionWorker(action, self)
        self._worker.line.connect(self._on_line)
        self._worker.done.connect(self._on_action_done)
        self._worker.start()
        self.btn_stop.setEnabled(True)
        self._sync_buttons()
        self.status.emit(f"正在执行：{action.name}")

    def _on_line(self, text: str) -> None:
        self.log.appendPlainText(text)
        pct = disk.parse_defrag_percent(text)
        if pct is not None:
            self.prog.setRange(0, 100)
            self.prog.setValue(pct)

    def _on_action_done(self, code: int) -> None:
        self._worker = None
        self.prog.setRange(0, 100)
        self.prog.setValue(100)
        self.btn_stop.setEnabled(False)
        self._sync_buttons()
        ok = code == 0
        self.log.appendPlainText(f"[完成] 退出码 {code}" + ("" if ok else "（非 0，可能权限不足或被中断）"))
        self.status.emit("磁盘优化完成" if ok else f"动作结束，退出码 {code}")
        if not ok:
            notice.note(
                self, "error",
                "动作未成功（系统命令退出码 %d）。常见原因：没有管理员权限、"
                "卷被占用，或该卷不支持此操作。详情见下方执行日志。" % code)

    def _stop(self) -> None:
        if self._worker is not None:
            self._worker.stop_flag[0] = True
            self.status.emit("正在停止…")

    def _elevate(self) -> None:
        if cleaner.relaunch_as_admin():
            QApplication.instance().quit()
        else:
            notice.note(self, "error",
                        "提权失败：没能以管理员身份重启，可能是 UAC 被取消。")

    def shutdown(self) -> None:
        if self._worker is not None:
            self._worker.stop_flag[0] = True
            self._worker.wait(2000)
            self._worker = None
        stop_all(self)


# ------------------------------------------------------------------ 容器
class CleanPage(QWidget):
    status = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._build()

    def _build(self) -> None:
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 12, 18, 12)
        lay.setSpacing(8)

        tabs = QTabWidget()
        self.tab_garbage = GarbageTab()
        self.tab_large = LargeFileTab()
        self.tab_dir = DirTreeTab()
        self.tab_disk = DiskTab()
        tabs.addTab(self.tab_garbage, "垃圾清理")
        tabs.addTab(self.tab_large, "大文件清理")
        tabs.addTab(self.tab_dir, "大目录分析")
        tabs.addTab(self.tab_disk, "磁盘优化")
        for w in (self.tab_garbage, self.tab_large, self.tab_dir, self.tab_disk):
            w.status.connect(self.status)
        lay.addWidget(tabs, 1)
        self.tabs = tabs

    def shutdown(self) -> None:
        for w in (self.tab_garbage, self.tab_large, self.tab_dir, self.tab_disk):
            if hasattr(w, "shutdown"):
                w.shutdown()
