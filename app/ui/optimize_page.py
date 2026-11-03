# -*- coding: utf-8 -*-
"""系统优化工作台：系统信息 / 启动项 / 服务 / 响应微调。

四个页签的共同原则：**先看后动、动了能退**
- 系统信息：纯只读，回答「这台机器什么底子」
- 启动项：只切启用状态（StartupApproved / 移出启动文件夹 / 任务禁用），不删本体
- 服务：白名单式给建议，核心服务不给操作按钮
- 微调：每项独立开关，改前记原值，可单项或一键还原

**Win10 / Win11 双分支**：顶部 OsBar 明确告诉用户当前按哪个分支呈现；
微调清单里给另一代系统准备的项会灰显且不可勾选（check_filter），
避免出现「点了没反应」的开关。
"""
from __future__ import annotations

import os
import time
from datetime import datetime

from PyQt5.QtCore import Qt, QUrl, pyqtSignal
from PyQt5.QtGui import QDesktopServices
from PyQt5.QtWidgets import (QHBoxLayout, QLabel, QPlainTextEdit,
                             QProgressBar, QPushButton, QSplitter, QTabWidget,
                             QVBoxLayout, QWidget)

from .. import theme
from ..core import changes as chg
from ..core import cleaner, services as svc_mod, startup, sysinfo, winver
from ..core import tweaks as tw
from .tables import Col, RowModel, make_table, run_bg, stop_all
from . import notice
from .widgets import (Dot, KpiCard, chip, ghost_button, primary_button)

RIGHT = int(Qt.AlignRight | Qt.AlignVCenter)
CENTER = int(Qt.AlignHCenter | Qt.AlignVCenter)


def _open_path(path: str) -> None:
    if not path:
        return
    if os.path.isfile(path):
        path = os.path.dirname(path)
    QDesktopServices.openUrl(QUrl.fromLocalFile(path))


def fmt_time(ts: float) -> str:
    if not ts:
        return "-"
    try:
        return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")
    except (OSError, ValueError, OverflowError):
        return "-"


class ChipBar(QWidget):
    """一排互斥的筛选胶囊。"""

    changed = pyqtSignal(str)

    def __init__(self, options, current: str = "", parent=None):
        super().__init__(parent)
        self.keys = [k for _t, k in options]
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        self.buttons = {}
        for text, key in options:
            b = chip(text)
            b.setChecked(key == current)
            b.clicked.connect(lambda _c=False, k=key: self._pick(k))
            lay.addWidget(b)
            self.buttons[key] = b
        lay.addStretch(1)
        self.current = current or (self.keys[0] if self.keys else "")

    def _pick(self, key: str) -> None:
        self.current = key
        for k, b in self.buttons.items():
            b.setChecked(k == key)
        self.changed.emit(key)

    def set_current(self, key: str) -> None:
        self._pick(key)


def _admin_bar(lay, obj) -> None:
    """顶部那条「当前权限」提示 + 提权按钮。"""
    row = QHBoxLayout()
    row.setSpacing(8)
    admin = cleaner.is_admin()
    t = theme.current()
    row.addWidget(Dot(t["ok"] if admin else t["warn"], 8))
    label = QLabel("管理员权限 · 可执行系统级改动" if admin
                   else "普通权限 · 部分项需要管理员")
    label.setProperty("role", "faint")
    row.addWidget(label)
    row.addStretch(1)
    if not admin:
        btn = ghost_button("以管理员身份重启")
        btn.clicked.connect(lambda: (cleaner.relaunch_as_admin()
                                     and os._exit(0)))
        row.addWidget(btn)
    lay.addLayout(row)


def _progress(lay) -> QProgressBar:
    p = QProgressBar()
    p.setTextVisible(False)
    p.setFixedHeight(5)
    p.hide()
    lay.addWidget(p)
    return p


class OsBar(QWidget):
    """顶部那条「当前系统 + 按哪个分支裁剪」的提示条。

    Win10 和 Win11 的微调项并不通用（Win11 没有时间线，Win10 没有小组件），
    所以第一眼就该让用户看到「这台机器按哪一代走」，而不是让他去猜某个开关
    为什么点了没反应。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 2)
        lay.setSpacing(7)
        self.dot = Dot(theme.current()["accent"], 7)
        lay.addWidget(self.dot)
        self.label = QLabel("")
        self.label.setProperty("role", "dim")
        lay.addWidget(self.label)
        lay.addStretch(1)

    def refresh(self) -> None:
        v = winver.current(force=True)
        st = tw.stats(v)
        other = "Win10" if v.is_11 else "Win11"
        fam = winver.FAMILY_CN.get(v.family, v.family)
        self.label.setText(
            f"{v.label}  ·  {fam} 分支：微调 {st['usable']} 项适配本机，"
            f"另 {st['other']} 项为 {other} 专属（表中灰显、不可勾选）")


# ================================================================== 系统信息
class InfoTab(QWidget):
    status = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._busy = False
        self.snap = None
        self._build()

    def _build(self) -> None:
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 10, 0, 0)
        lay.setSpacing(9)

        top = QHBoxLayout()
        top.setSpacing(8)
        title = QLabel("系统信息")
        title.setObjectName("H2")
        top.addWidget(title)
        self.boot_label = QLabel("")
        self.boot_label.setProperty("role", "faint")
        top.addWidget(self.boot_label)
        top.addStretch(1)
        self.btn_refresh = ghost_button("刷新", "refresh")
        self.btn_refresh.clicked.connect(self.refresh)
        top.addWidget(self.btn_refresh)
        lay.addLayout(top)

        kpis = QHBoxLayout()
        kpis.setSpacing(10)
        self.kpi_mem = KpiCard("内存占用", "-", "读取中")
        self.kpi_disk = KpiCard("系统盘剩余", "-", "-")
        self.kpi_up = KpiCard("开机时长", "-", "-")
        self.kpi_proc = KpiCard("进程数", "-", "当前用户会话")
        for k in (self.kpi_mem, self.kpi_disk, self.kpi_up, self.kpi_proc):
            kpis.addWidget(k, 1)
        lay.addLayout(kpis)

        split = QSplitter(Qt.Vertical)
        split.setChildrenCollapsible(False)
        self.split = split

        upper = QSplitter(Qt.Horizontal)
        upper.setChildrenCollapsible(False)

        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.setSpacing(6)
        t1 = QLabel("系统")
        t1.setObjectName("H3")
        ll.addWidget(t1)
        self.sys_model = RowModel([
            Col("项目", 84, lambda r: r.label),
            Col("值", 196, lambda r: r.value, tip=lambda r: r.value),
            Col("备注", 0, lambda r: r.hint, color="dim", stretch=True),
        ], parent=self)
        self.sys_table = make_table(self.sys_model, row_height=24)
        ll.addWidget(self.sys_table, 1)
        upper.addWidget(left)

        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(6)
        t2 = QLabel("硬件与存储")
        t2.setObjectName("H3")
        rl.addWidget(t2)
        self.hw_model = RowModel([
            Col("项目", 84, lambda r: r.label),
            Col("型号 / 状态", 232, lambda r: r.value, tip=lambda r: r.value),
            Col("详细信息", 0, lambda r: r.hint, color="dim", stretch=True),
        ], parent=self)
        self.hw_table = make_table(self.hw_model, row_height=24)
        rl.addWidget(self.hw_table, 1)
        upper.addWidget(right)
        upper.setSizes([460, 520])
        split.addWidget(upper)

        lower = QWidget()
        dl = QVBoxLayout(lower)
        dl.setContentsMargins(0, 0, 0, 0)
        dl.setSpacing(6)
        row = QHBoxLayout()
        row.setSpacing(8)
        t3 = QLabel("内存占用 TOP")
        t3.setObjectName("H3")
        row.addWidget(t3)
        self.sec_label = QLabel("")
        self.sec_label.setProperty("role", "faint")
        row.addWidget(self.sec_label)
        row.addStretch(1)
        dl.addLayout(row)
        self.proc_model = RowModel([
            Col("进程", 190, lambda r: r.name, tip=lambda r: r.name),
            Col("PID", 76, lambda r: str(r.pid), mono=True),
            Col("内存", 92, lambda r: cleaner.human_size(r.mem), align=RIGHT),
            Col("会话", 64, lambda r: r.session, color="faint"),
        ], parent=self)
        self.proc_table = make_table(self.proc_model, row_height=23)
        dl.addWidget(self.proc_table, 1)
        split.addWidget(lower)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 2)
        lay.addWidget(split, 1)

    def showEvent(self, e):
        super().showEvent(e)
        if not getattr(self, "_sized", False) and self.split.height() > 240:
            h = self.split.height()
            self.split.setSizes([int(h * 0.58), int(h * 0.42)])
            self._sized = True
        if self.snap is None and not self._busy:
            self.refresh()

    def refresh(self) -> None:
        if self._busy:
            return
        self._busy = True
        self.btn_refresh.setEnabled(False)
        self.status.emit("正在读取系统信息…")

        def job(tick, stop):
            return sysinfo.collect(stop)

        run_bg(self, job, self._on_done, None, self._on_fail)

    def _on_fail(self, msg: str) -> None:
        self._busy = False
        self.btn_refresh.setEnabled(True)
        self.status.emit(f"读取系统信息失败：{msg}")

    def _on_done(self, snap) -> None:
        self._busy = False
        self.btn_refresh.setEnabled(True)
        if snap is None:
            return
        self.snap = snap
        sys_rows = list(snap.os_rows)
        if snap.power:
            sys_rows.append(sysinfo.Info("电源计划", snap.power))
        sys_rows.extend(snap.sec_rows)
        self.sys_model.set_rows(sys_rows)
        self.hw_model.set_rows(snap.hw_rows)
        self.proc_model.set_rows(snap.procs)

        self.kpi_mem.set_value(
            f"{snap.mem_percent}%" if snap.mem_total else "-",
            f"已用 {cleaner.human_size(snap.mem_used)} / {cleaner.human_size(snap.mem_total)}"
            if snap.mem_total else "读取失败",
            "warn" if snap.mem_percent >= 85 else "")
        fixed = [v for v in snap.volumes if v.letter.upper().startswith("C") and v.total]
        if fixed:
            v = fixed[0]
            self.kpi_disk.set_value(
                cleaner.human_size(v.free), f"C: 共 {cleaner.human_size(v.total)}",
                "crit" if v.used_ratio >= 0.92 else ("warn" if v.used_ratio >= 0.85 else ""))
        else:
            self.kpi_disk.set_value("-", "未找到 C 盘")
        self.kpi_up.set_value(sysinfo.human_duration(snap.uptime),
                              f"开机于 {fmt_time(snap.boot_at)}")
        self.kpi_proc.set_value(str(snap.proc_total), "当前用户会话")

        self.sec_label.setText(f"· 共 {snap.proc_total} 个进程，按工作集排序")
        self.boot_label.setText("读取于 " + time.strftime("%H:%M:%S"))
        self.status.emit(f"系统信息已刷新 · {snap.host} · {snap.proc_total} 个进程")


# ================================================================== 启动项
class StartupTab(QWidget):
    status = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._busy = False
        self.items: list[startup.StartupItem] = []
        self._build()

    def _build(self) -> None:
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 10, 0, 0)
        lay.setSpacing(9)

        top = QHBoxLayout()
        top.setSpacing(8)
        title = QLabel("开机启动项")
        title.setObjectName("H2")
        top.addWidget(title)
        self.count_label = QLabel("")
        self.count_label.setProperty("role", "faint")
        top.addWidget(self.count_label)
        top.addStretch(1)
        self.btn_scan = primary_button("扫描启动项", "search")
        self.btn_scan.clicked.connect(self.scan)
        top.addWidget(self.btn_scan)
        lay.addLayout(top)

        hint = QLabel("覆盖注册表 Run（含 32 位视图）、启动文件夹、登录时触发的计划任务。"
                      "「禁用」只切状态、不删除本体：注册表项写入系统同款 StartupApproved，"
                      "启动文件夹项移到备份区，计划任务用系统命令禁用 —— 都能一键恢复。")
        hint.setProperty("role", "faint")
        hint.setWordWrap(True)
        lay.addWidget(hint)

        self.chips = ChipBar([("第三方", "third"), ("全部", "all"),
                              ("已禁用", "off"), ("程序缺失", "missing")], "all")
        self.chips.changed.connect(lambda _k: self._fill())
        lay.addWidget(self.chips)

        self.model = RowModel([
            Col("", 34),
            Col("名称", 202, lambda r: r.name, tip=lambda r: r.name),
            Col("类型", 130, lambda r: r.kind, color="dim"),
            Col("状态", 58, lambda r: r.state_text,
                color_fn=lambda r: "ok" if r.enabled else "faint"),
            Col("发布者", 176, lambda r: r.publisher or "—",
                color="dim", tip=lambda r: r.publisher),
            Col("备注", 170, lambda r: r.note, color="warn"),
            Col("命令 / 路径", 0, lambda r: r.command, color="faint",
                stretch=True, tip=lambda r: r.target or r.command),
        ], checkable=True, parent=self)
        self.model.checkChanged.connect(self._sync_footer)
        self.table = make_table(self.model, row_height=25)
        self.table.doubleClicked.connect(self._open_selected)
        lay.addWidget(self.table, 1)

        foot = QHBoxLayout()
        foot.setSpacing(8)
        self.btn_all = ghost_button("全选可见项")
        self.btn_all.clicked.connect(lambda: self.model.check_all(True))
        foot.addWidget(self.btn_all)
        self.btn_none = ghost_button("取消全选")
        self.btn_none.clicked.connect(lambda: self.model.check_all(False))
        foot.addWidget(self.btn_none)
        self.btn_open = ghost_button("打开所在位置")
        self.btn_open.clicked.connect(self._open_selected)
        foot.addWidget(self.btn_open)
        self.btn_backup = ghost_button("打开备份目录")
        self.btn_backup.clicked.connect(
            lambda: _open_path(startup.backup_root()))
        foot.addWidget(self.btn_backup)
        foot.addStretch(1)
        self.sel_label = QLabel("尚未扫描")
        self.sel_label.setProperty("role", "dim")
        foot.addWidget(self.sel_label)
        self.btn_enable = ghost_button("启用选中")
        self.btn_enable.setEnabled(False)
        self.btn_enable.clicked.connect(lambda: self._toggle(True))
        foot.addWidget(self.btn_enable)
        self.btn_disable = QPushButton("禁用选中")
        self.btn_disable.setObjectName("DangerBtn")
        self.btn_disable.setCursor(Qt.PointingHandCursor)
        self.btn_disable.setEnabled(False)
        self.btn_disable.clicked.connect(lambda: self._toggle(False))
        foot.addWidget(self.btn_disable)
        lay.addLayout(foot)

        self.prog = _progress(lay)

    def showEvent(self, e):
        super().showEvent(e)
        if not self.items and not self._busy:
            self.scan()

    def scan(self) -> None:
        if self._busy:
            return
        self._busy = True
        self.btn_scan.setEnabled(False)
        self.prog.setRange(0, 3)
        self.prog.setValue(0)
        self.prog.show()
        self.status.emit("正在扫描启动项…")

        def job(tick, stop):
            return startup.scan_all(stop, progress=tick)

        run_bg(self, job, self._on_done, self._on_tick, self._on_fail)

    def _on_tick(self, i, n, name) -> None:
        self.prog.setRange(0, max(1, n))
        self.prog.setValue(i)
        if name:
            self.status.emit(f"正在读取：{name}")

    def _on_fail(self, msg: str) -> None:
        self._busy = False
        self.btn_scan.setEnabled(True)
        self.prog.hide()
        self.status.emit(f"扫描启动项失败：{msg}")

    def _on_done(self, items) -> None:
        self._busy = False
        self.btn_scan.setEnabled(True)
        self.prog.hide()
        self.items = list(items or [])
        self._fill()
        third = sum(1 for i in self.items if not i.is_ms)
        off = sum(1 for i in self.items if not i.enabled)
        miss = sum(1 for i in self.items if not i.exists)
        self.count_label.setText(
            f"共 {len(self.items)} 项 · 第三方 {third} · 已禁用 {off} · 程序缺失 {miss}")
        self.status.emit(f"启动项扫描完成：{len(self.items)} 项")

    def _visible(self) -> list[startup.StartupItem]:
        key = self.chips.current
        if key == "third":
            return [i for i in self.items if not i.is_ms]
        if key == "off":
            return [i for i in self.items if not i.enabled]
        if key == "missing":
            return [i for i in self.items if not i.exists]
        return list(self.items)

    def _fill(self) -> None:
        rows = self._visible()
        self.model.set_rows(rows)
        self._sync_footer()

    def _sync_footer(self) -> None:
        n = self.model.checked_count()
        self.sel_label.setText(f"已选 {n} 项" if n else "尚未选择")
        self.btn_disable.setEnabled(n > 0)
        self.btn_enable.setEnabled(n > 0)

    def _open_selected(self) -> None:
        idx = self.table.currentIndex()
        if not idx.isValid():
            return
        item = self.model.row_at(idx.row())
        if item and (item.target or item.file_path):
            _open_path(item.target or item.file_path)

    def _toggle(self, on: bool) -> None:
        rows = self.model.checked_rows()
        if not rows:
            return
        verb = "启用" if on else "禁用"
        notice.ask(
            self, "确认%s" % verb,
            "将对 %d 个启动项执行「%s」。该操作不删除程序本体，"
            "可随时在右侧再次%s。" % (len(rows), verb,
                                     "禁用" if on else "启用"),
            ok_text="%s这 %d 项" % (verb, len(rows)),
            on_yes=lambda: self._do_toggle(rows, on, verb))

    def _do_toggle(self, rows, on: bool, verb: str) -> None:
        self.status.emit(f"正在{verb} {len(rows)} 个启动项…")
        self.btn_disable.setEnabled(False)
        self.btn_enable.setEnabled(False)

        def job(tick, stop):
            return startup.set_many(rows, on, progress=tick)

        def done(res):
            ok, errs = res
            self.status.emit(f"已{verb} {ok} 项" + (f"，{len(errs)} 项失败" if errs else ""))
            if errs:
                notice.note(self, "warn",
                            "%s完成：成功 %d 项，失败 %d 项（%s）"
                            % (verb, ok, len(errs), "；".join(errs[:4])))
            self.scan()

        run_bg(self, job, done, self._on_tick, self._on_fail)


# ================================================================== 服务
class ServicesTab(QWidget):
    status = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._busy = False
        self.items: list[svc_mod.Service] = []
        self._build()

    def _build(self) -> None:
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 10, 0, 0)
        lay.setSpacing(9)

        top = QHBoxLayout()
        top.setSpacing(8)
        title = QLabel("服务调优")
        title.setObjectName("H2")
        top.addWidget(title)
        self.count_label = QLabel("")
        self.count_label.setProperty("role", "faint")
        top.addWidget(self.count_label)
        top.addStretch(1)
        self.btn_scan = primary_button("扫描服务", "search")
        self.btn_scan.clicked.connect(self.scan)
        top.addWidget(self.btn_scan)
        lay.addLayout(top)

        hint = QLabel("只对「有明确收益」的服务给出建议；核心服务（RPC、事件日志、电源、"
                      "安全中心等）不提供操作按钮。禁用只改启动类型，首次改动前会记录原始值，"
                      "可随时「恢复原状」。判定按当前系统版本裁剪：某一代系统独有的服务"
                      "（如 Win11 的安卓子系统）不会在另一代上被标成「建议调优」。")
        hint.setProperty("role", "faint")
        hint.setWordWrap(True)
        lay.addWidget(hint)

        row = QHBoxLayout()
        row.setSpacing(8)
        self.chips = ChipBar([("建议调优", "tune"), ("第三方", "third"),
                              ("已禁用", "disabled"), ("全部", "all")], "tune")
        self.chips.changed.connect(lambda _k: self._fill())
        row.addWidget(self.chips, 1)
        lay.addLayout(row)
        _admin_bar(lay, self)

        split = QSplitter(Qt.Vertical)
        split.setChildrenCollapsible(False)
        self.split = split

        self.model = RowModel([
            Col("", 34),
            Col("服务", 164, lambda r: r.name, tip=lambda r: r.path, mono=True),
            Col("显示名", 238, lambda r: r.display, tip=lambda r: r.display),
            Col("启动", 56, lambda r: r.start_text,
                color_fn=lambda r: "crit" if r.start == 4 else ""),
            Col("状态", 64, lambda r: r.state,
                color_fn=lambda r: "ok" if r.running else "faint"),
            Col("判定", 76, lambda r: r.verdict,
                color_fn=lambda r: {"可调优": "accent", "第三方": "warn",
                                    "核心服务": "crit"}.get(r.verdict, "dim")),
            Col("建议", 0, lambda r: r.advice, color="dim", stretch=True),
        ], checkable=True, parent=self)
        self.model.checkChanged.connect(self._sync_footer)
        self.table = make_table(self.model, row_height=24)
        self.table.selectionModel().currentRowChanged.connect(self._show_detail)
        split.addWidget(self.table)

        self.detail = QPlainTextEdit()
        self.detail.setReadOnly(True)
        self.detail.setLineWrapMode(QPlainTextEdit.WidgetWidth)
        self.detail.setPlaceholderText("选中一行，这里显示服务的说明与判定依据")
        self.detail.setMinimumHeight(96)
        split.addWidget(self.detail)
        split.setStretchFactor(0, 4)
        split.setStretchFactor(1, 1)
        lay.addWidget(split, 1)

        foot = QHBoxLayout()
        foot.setSpacing(8)
        self.btn_pick = ghost_button("全选建议调优项")
        self.btn_pick.clicked.connect(self._check_tunable)
        foot.addWidget(self.btn_pick)
        self.btn_none = ghost_button("取消全选")
        self.btn_none.clicked.connect(lambda: self.model.check_all(False))
        foot.addWidget(self.btn_none)
        foot.addStretch(1)
        self.sel_label = QLabel("尚未扫描")
        self.sel_label.setProperty("role", "dim")
        foot.addWidget(self.sel_label)
        self.btn_restore = ghost_button("恢复原状")
        self.btn_restore.setEnabled(False)
        self.btn_restore.clicked.connect(self._restore)
        foot.addWidget(self.btn_restore)
        self.btn_disable = QPushButton("禁用选中服务")
        self.btn_disable.setObjectName("DangerBtn")
        self.btn_disable.setCursor(Qt.PointingHandCursor)
        self.btn_disable.setEnabled(False)
        self.btn_disable.clicked.connect(self._disable)
        foot.addWidget(self.btn_disable)
        lay.addLayout(foot)

        self.prog = _progress(lay)

    def showEvent(self, e):
        super().showEvent(e)
        if self.split.height() > 240 and not getattr(self, "_sized", False):
            h = self.split.height()
            self.split.setSizes([int(h * 0.76), int(h * 0.24)])
            self._sized = True
        if not self.items and not self._busy:
            self.scan()

    def scan(self) -> None:
        if self._busy:
            return
        self._busy = True
        self.btn_scan.setEnabled(False)
        self.prog.setRange(0, 1)
        self.prog.setValue(0)
        self.prog.show()
        self.status.emit("正在读取服务列表…")

        def job(tick, stop):
            return svc_mod.scan(stop, progress=tick)

        run_bg(self, job, self._on_done, None, self._on_fail)

    def _on_fail(self, msg: str) -> None:
        self._busy = False
        self.btn_scan.setEnabled(True)
        self.prog.hide()
        self.status.emit(f"读取服务失败：{msg}")

    def _on_done(self, items) -> None:
        self._busy = False
        self.btn_scan.setEnabled(True)
        self.prog.hide()
        self.items = list(items or [])
        tune = sum(1 for s in self.items if s.verdict == "可调优")
        third = sum(1 for s in self.items if s.verdict == "第三方")
        self.count_label.setText(
            f"{winver.current().short} · 共 {len(self.items)} 个服务 · "
            f"建议调优 {tune} · 第三方 {third}")
        self._fill()
        self.status.emit(f"服务扫描完成：{len(self.items)} 个")

    def _visible(self) -> list[svc_mod.Service]:
        key = self.chips.current
        if key == "tune":
            return [s for s in self.items if s.verdict == "可调优"]
        if key == "third":
            return [s for s in self.items if s.verdict == "第三方"]
        if key == "disabled":
            return [s for s in self.items if s.start == 4]
        return [s for s in self.items if s.verdict != "核心服务"]

    def _fill(self) -> None:
        self.model.set_rows(self._visible())
        self._sync_footer()

    def _sync_footer(self) -> None:
        n = self.model.checked_count()
        self.sel_label.setText(f"已选 {n} 项" if n else "尚未选择")
        self.btn_disable.setEnabled(n > 0)
        self.btn_restore.setEnabled(n > 0)

    def _check_tunable(self) -> None:
        self.model.check_rows([s for s in self.model.rows()
                              if s.verdict == "可调优"])

    def _show_detail(self, cur, _prev=None) -> None:
        if not cur.isValid():
            return
        s = self.model.row_at(cur.row())
        if not s:
            return
        lines = [
            f"{s.display}   ({s.name})",
            f"启动类型：{s.start_text}    状态：{s.state}",
            f"判定：{s.verdict}    建议：{s.advice}",
            "",
            f"依据：{s.reason}",
        ]
        if s.desc:
            lines += ["", s.desc]
        if s.path:
            lines += ["", f"程序路径：{s.path}"]
        self.detail.setPlainText("\n".join(lines))

    def _disable(self) -> None:
        rows = [s for s in self.model.checked_rows() if s.safe_disable]
        if not rows:
            return
        if not cleaner.is_admin():
            notice.note(self, "warn",
                        "禁用服务需要管理员权限，请点上方「以管理员身份重启」后再操作。")
            return
        names = "、".join(s.display for s in rows[:4])
        if len(rows) > 4:
            names += " 等 %d 个" % len(rows)
        notice.ask(
            self, "确认禁用",
            "将把 %d 个服务的启动类型改为「禁用」并尝试停止：%s。"
            "原始启动类型已记录，可用「恢复原状」还原。" % (len(rows), names),
            ok_text="禁用这 %d 个" % len(rows), danger=True,
            on_yes=lambda: self._run_change(rows, True))

    def _restore(self) -> None:
        rows = self.model.checked_rows()
        if not rows:
            return
        if not cleaner.is_admin():
            notice.note(self, "warn", "恢复服务启动类型需要管理员权限。")
            return
        self._run_change(rows, False)

    def _run_change(self, rows, disable: bool) -> None:
        verb = "禁用" if disable else "恢复"
        self.status.emit(f"正在{verb} {len(rows)} 个服务…")
        self.btn_disable.setEnabled(False)
        self.btn_restore.setEnabled(False)
        self.prog.setRange(0, len(rows))
        self.prog.setValue(0)
        self.prog.show()

        def job(tick, stop):
            return svc_mod.batch_apply(rows, disable, progress=tick)

        def on_tick(i, n, name):
            self.prog.setRange(0, max(1, n))
            self.prog.setValue(i)
            if name:
                self.status.emit(f"正在{verb}：{name}")

        def done(res):
            ok, errs = res
            self.prog.hide()
            self.status.emit(f"已{verb} {ok} 个服务" + (f"，{len(errs)} 个失败" if errs else ""))
            if errs:
                notice.note(self, "warn",
                            "%s完成：成功 %d 个，失败 %d 个（%s）"
                            % (verb, ok, len(errs), "；".join(errs[:4])))
            self.scan()

        run_bg(self, job, done, on_tick, self._on_fail)


# ================================================================== 微调
class TweaksTab(QWidget):
    status = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._build()

    def _build(self) -> None:
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 10, 0, 0)
        lay.setSpacing(9)

        top = QHBoxLayout()
        top.setSpacing(8)
        title = QLabel("响应速度微调")
        title.setObjectName("H2")
        top.addWidget(title)
        self.count_label = QLabel("")
        self.count_label.setProperty("role", "faint")
        top.addWidget(self.count_label)
        top.addStretch(1)
        self.btn_refresh = ghost_button("刷新状态", "refresh")
        self.btn_refresh.clicked.connect(self.refresh)
        top.addWidget(self.btn_refresh)
        lay.addLayout(top)

        hint = QLabel("每项只改一个注册表值（或一个电源计划），改之前把原值记进 "
                      "tweaks.json。双击某一行可直接切换该项；需要重启的项在说明里写明。"
                      "标「不适用」的是为另一代 Windows 准备的项，本机点了不会有反应，"
                      "因此不给勾选框。")
        hint.setProperty("role", "faint")
        hint.setWordWrap(True)
        lay.addWidget(hint)

        self.chips = ChipBar([("本机可用", "usable"), ("全部", "all"),
                              ("已优化", "on"), ("未优化", "off")], "usable")
        self.chips.changed.connect(lambda _k: self._fill())
        lay.addWidget(self.chips)

        self.model = RowModel([
            Col("", 34),
            Col("项目", 186, lambda r: r.name,
                color_fn=lambda r: "" if r.applies_to() else "faint",
                tip=lambda r: r.desc),
            Col("适用", 74, lambda r: r.os_text,
                color_fn=lambda r: "" if r.applies_to() else "faint"),
            Col("当前", 78, lambda r: r.state(),
                color_fn=lambda r: {"已优化": "ok", "不适用": "faint"}.get(
                    r.state(), "dim")),
            Col("现值", 88, lambda r: r.current_text(), color="faint"),
            Col("风险", 50, lambda r: r.risk,
                color_fn=lambda r: {"低": "ok", "中": "warn"}.get(r.risk, "dim")),
            Col("说明", 0, lambda r: r.why_not() or r.desc, color="dim",
                stretch=True, tip=lambda r: r.desc),
        ], checkable=True, parent=self,
            check_filter=lambda r: r.applies_to())
        self.model.checkChanged.connect(self._sync_footer)
        self.table = make_table(self.model, row_height=26)
        self.table.doubleClicked.connect(self._toggle_one)
        lay.addWidget(self.table, 1)

        foot = QHBoxLayout()
        foot.setSpacing(8)
        self.btn_all = ghost_button("全选")
        self.btn_all.clicked.connect(lambda: self.model.check_all(True))
        foot.addWidget(self.btn_all)
        self.btn_none = ghost_button("取消全选")
        self.btn_none.clicked.connect(lambda: self.model.check_all(False))
        foot.addWidget(self.btn_none)
        self.btn_cfg = ghost_button("打开备份目录")
        self.btn_cfg.clicked.connect(lambda: _open_path(os.path.dirname(
            os.path.expandvars(tw.CONFIG))))
        foot.addWidget(self.btn_cfg)
        foot.addStretch(1)
        self.sel_label = QLabel("")
        self.sel_label.setProperty("role", "dim")
        foot.addWidget(self.sel_label)
        self.btn_revert = ghost_button("一键还原全部")
        self.btn_revert.clicked.connect(self._revert_all)
        foot.addWidget(self.btn_revert)
        self.btn_apply = primary_button("优化选中项")
        self.btn_apply.clicked.connect(self._apply)
        foot.addWidget(self.btn_apply)
        lay.addLayout(foot)

        self.prog = _progress(lay)

    def showEvent(self, e):
        super().showEvent(e)
        if not self.model.rows():
            self.refresh()

    def refresh(self) -> None:
        st = tw.stats()
        self.count_label.setText(
            f"共 {st['total']} 项 · 本机可用 {st['usable']}"
            f"（已优化 {st['applied']}）· {st['other']} 项属另一代系统")
        self._fill()

    def _visible(self) -> list:
        key = getattr(self, "chips", None).current if hasattr(self, "chips") else "usable"
        if key == "all":
            return list(tw.TWEAKS)
        if key == "on":
            return [t for t in tw.TWEAKS if t.state() == "已优化"]
        if key == "off":
            return [t for t in tw.TWEAKS
                    if t.applies_to() and t.state() == "未优化"]
        return tw.usable()

    def _fill(self) -> None:
        self.model.set_rows(self._visible())
        self._sync_footer()

    def _sync_footer(self) -> None:
        n = self.model.checked_count()
        self.sel_label.setText(f"已选 {n} 项" if n else "")
        self.btn_apply.setEnabled(n > 0)
        self.btn_revert.setEnabled(bool(tw.load_backup()))

    def _toggle_one(self, index) -> None:
        if not index.isValid():
            return
        t = self.model.row_at(index.row())
        if not t:
            return
        if not t.applies_to():
            notice.note(self, "info", "「%s」在当前系统不适用：%s"
                        % (t.name, t.why_not()))
            return
        do_revert = t.state() == "已优化"
        ok, msg = (tw.revert(t) if do_revert else tw.apply(t))
        self.status.emit(("已还原 " if do_revert else "已优化 ") + t.name
                         + (f"（{msg}）" if msg else ""))
        if not ok:
            notice.note(self, "error", "「%s」操作失败：%s" % (t.name, msg))
        self.refresh()

    def _apply(self) -> None:
        rows = [t for t in self.model.checked_rows() if t.applies_to()]
        if not rows:
            return
        need_admin = [t for t in rows if t.admin]
        if need_admin and not cleaner.is_admin():
            notice.note(
                self, "warn",
                "以下项需要管理员权限，请点上方「以管理员身份重启」后再操作：%s"
                % "、".join(t.name for t in need_admin[:6])
                + (" 等 %d 项" % len(need_admin) if len(need_admin) > 6 else ""))
            return
        reboot = [t for t in rows if t.reboot]
        tip = ""
        if reboot:
            tip = "其中 %s 需要重启后生效。" % "、".join(t.name for t in reboot[:3])
        notice.ask(
            self, "确认优化",
            "将应用 %d 项微调，原值已备份可随时还原。%s" % (len(rows), tip),
            ok_text="应用这 %d 项" % len(rows),
            on_yes=lambda: self._do_apply(rows))

    def _do_apply(self, rows) -> None:
        self.btn_apply.setEnabled(False)
        self.prog.setRange(0, len(rows))
        self.prog.show()

        def job(tick, stop):
            return tw.apply_many(rows, progress=tick)

        def on_tick(i, n, name):
            self.prog.setRange(0, max(1, n))
            self.prog.setValue(i)
            if name:
                self.status.emit(f"正在应用：{name}")

        def done(res):
            ok, errs = res
            self.prog.hide()
            self.status.emit(f"已应用 {ok} 项" + (f"，{len(errs)} 项失败" if errs else ""))
            if errs:
                notice.note(self, "warn", "%d 项没写成：%s"
                            % (len(errs), "；".join(errs[:4])))
            self.refresh()

        run_bg(self, job, done, on_tick, None)

    def _revert_all(self) -> None:
        if not tw.load_backup():
            notice.note(self, "info", "没有需要还原的项：还没有记录任何改动。")
            return
        notice.ask(
            self, "确认还原",
            "把所有改动过的项恢复为原始值。只还原本工具改过的项，"
            "不会覆盖你手动改的设置。",
            ok_text="全部还原",
            on_yes=self._do_revert_all)

    def _do_revert_all(self) -> None:
        ok, errs = tw.revert_all()
        self.status.emit(f"已还原 {ok} 项" + (f"，{len(errs)} 项失败" if errs else ""))
        if errs:
            notice.note(self, "warn", "%d 项没还原成：%s"
                        % (len(errs), "；".join(errs[:4])))
        self.refresh()


# ================================================================== 变更记录
class ChangesTab(QWidget):
    """把三处备份折成一条时间线，并提供一键全部还原。

    这一页存在的理由：前四个页签各自都能还原，但用户记不住「刚才到底动过什么」。
    换一台机器、隔一天再回来，只有这一页能回答「现在还被改成什么样了」。
    """

    status = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._busy = False
        self.rows: list[chg.Change] = []
        self.summary = chg.Summary()
        self._build()

    def _build(self) -> None:
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 10, 0, 0)
        lay.setSpacing(9)

        top = QHBoxLayout()
        top.setSpacing(8)
        title = QLabel("变更记录")
        title.setObjectName("H2")
        top.addWidget(title)
        self.count_label = QLabel("")
        self.count_label.setProperty("role", "faint")
        top.addWidget(self.count_label)
        top.addStretch(1)
        self.btn_cfg = ghost_button("打开备份目录")
        self.btn_cfg.clicked.connect(lambda: _open_path(os.path.join(
            startup.backup_root())))
        top.addWidget(self.btn_cfg)
        self.btn_refresh = ghost_button("刷新", "refresh")
        self.btn_refresh.clicked.connect(self.refresh)
        top.addWidget(self.btn_refresh)
        lay.addLayout(top)

        hint = QLabel("本工具所有改动都留了原始记录：启动项记在 startup_actions.jsonl，"
                      "服务与微调分别记在 services.json / tweaks.json。"
                      "「一键全部还原」只退本工具改过的目标，你手动改的不会被覆盖；"
                      "已经在原状态的会跳过。")
        hint.setProperty("role", "faint")
        hint.setWordWrap(True)
        lay.addWidget(hint)

        self.chips = ChipBar([("全部", "all"), ("生效中", "active"),
                              ("已还原", "done")], "all")
        self.chips.changed.connect(lambda _k: self._fill())
        lay.addWidget(self.chips)

        self.model = RowModel([
            # 时间列要放得下完整 "2026-10-22 09:04:31"，窄了会截成 2026-10-22 09:0…
            Col("时间", 152, lambda r: r.when or "—", color="faint"),
            Col("模块", 72, lambda r: r.module,
                color_fn=lambda r: {"启动项": "accent", "服务": "warn"}.get(r.module, "dim")),
            Col("动作", 104, lambda r: r.action,
                color_fn=lambda r: "ok" if r.active else "faint"),
            Col("目标", 216, lambda r: r.target, tip=lambda r: r.target),
            Col("原始 → 当前", 0, lambda r: r.detail, color="dim",
                stretch=True, tip=lambda r: r.detail),
            # 状态用强调色而不是警告色：生效中只表示「还没退回去」，不是异常
            Col("状态", 78, lambda r: r.state_text,
                color_fn=lambda r: "accent" if r.active else "faint"),
        ], parent=self)
        self.table = make_table(self.model, row_height=25)
        lay.addWidget(self.table, 1)

        foot = QHBoxLayout()
        foot.setSpacing(8)
        self.sel_label = QLabel("")
        self.sel_label.setProperty("role", "dim")
        foot.addWidget(self.sel_label)
        foot.addStretch(1)
        self.btn_revert = primary_button("一键全部还原")
        self.btn_revert.clicked.connect(self._revert_all)
        foot.addWidget(self.btn_revert)
        lay.addLayout(foot)

        self.prog = _progress(lay)

    def showEvent(self, e):
        super().showEvent(e)
        if not self._busy:
            self.refresh()

    def refresh(self) -> None:
        if self._busy:
            return
        self._busy = True
        self.btn_refresh.setEnabled(False)
        self.status.emit("正在汇总变更记录…")

        def job(tick, stop):
            return chg.collect(include_restored=True)

        def done(res):
            self._busy = False
            self.btn_refresh.setEnabled(True)
            rows, summary = res
            self.rows = rows
            self.summary = summary
            self._fill()

        run_bg(self, job, done, None, self._on_fail)

    def _on_fail(self, msg: str) -> None:
        self._busy = False
        self.btn_refresh.setEnabled(True)
        self.status.emit(f"读取变更记录失败：{msg}")

    def _visible(self) -> list:
        key = self.chips.current
        if key == "active":
            return [c for c in self.rows if c.active]
        if key == "done":
            return [c for c in self.rows if not c.active]
        return self.rows

    def _fill(self) -> None:
        s = self.summary
        if s.total:
            self.count_label.setText(
                f"仍生效 {s.active_total} 项（启动项 {s.active_startup} · "
                f"服务 {s.active_service} · 微调 {s.active_tweak}）· "
                f"已还原 {s.restored} 项")
        else:
            self.count_label.setText("还没有任何改动记录")
        self.model.set_rows(self._visible())
        self.btn_revert.setEnabled(s.pending)
        self.sel_label.setText(
            "没有需要还原的项" if not s.pending
            else f"共 {s.active_total} 项仍处于改动状态")
        self.status.emit(f"变更记录：{s.total} 条，仍生效 {s.active_total} 项")

    def _revert_all(self) -> None:
        s = self.summary
        if not s.pending:
            notice.note(self, "info", "当前没有仍处于改动状态的目标，无需还原。")
            return
        # 原来这里套了两层确认：先问「权限不足还继续吗」，再问「确认全部还原」。
        # 同一件事问两遍是弹窗时代最烦的地方，合并成一条，权限情况写进正文。
        extra = ""
        if (s.active_service or s.active_tweak) and not cleaner.is_admin():
            extra = "当前不是管理员，服务与微调可能写不回去（会记在失败里）。"
        notice.ask(
            self, "确认全部还原",
            "将把 %d 项改动退回原始状态：启动项 %d 项（重新启用）、"
            "服务 %d 项（写回原始启动类型）、微调 %d 项（写回或删除原值）。"
            "%s只处理本工具改过的目标，不会覆盖你手动改的设置。"
            % (s.active_total, s.active_startup, s.active_service,
               s.active_tweak, extra),
            ok_text="全部还原",
            on_yes=lambda: self._do_revert(s))

    def _do_revert(self, s) -> None:
        self.btn_revert.setEnabled(False)
        self.prog.setRange(0, max(1, s.active_total))
        self.prog.setValue(0)
        self.prog.show()

        def job(tick, stop):
            return chg.revert_all(progress=tick)

        def on_tick(i, n, name):
            self.prog.setRange(0, max(1, n))
            self.prog.setValue(i)
            if name:
                self.status.emit(f"正在还原：{name}")

        def done(res):
            ok, errs = res
            self.prog.hide()
            self.status.emit(f"已还原 {ok} 项" + (f"，{len(errs)} 项失败" if errs else ""))
            if errs:
                notice.note(self, "warn", "还原完成：成功 %d 项，失败 %d 项（%s）"
                            % (ok, len(errs), "；".join(errs[:4])))
            self.refresh()

        run_bg(self, job, done, on_tick, self._on_fail)


# ================================================================== 容器
class OptimizePage(QWidget):
    status = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 10, 16, 10)
        lay.setSpacing(0)

        # 第一眼就交代「这台机器按哪一代 Windows 呈现」
        self.os_bar = OsBar()
        lay.addWidget(self.os_bar)

        self.tabs = QTabWidget()
        self.tab_info = InfoTab()
        self.tab_startup = StartupTab()
        self.tab_services = ServicesTab()
        self.tab_tweaks = TweaksTab()
        self.tab_changes = ChangesTab()
        for w, name in ((self.tab_info, "系统信息"), (self.tab_startup, "启动项"),
                        (self.tab_services, "服务"), (self.tab_tweaks, "响应微调"),
                        (self.tab_changes, "变更记录")):
            self.tabs.addTab(w, name)
            w.status.connect(self.status)
        lay.addWidget(self.tabs, 1)

    def showEvent(self, e):
        super().showEvent(e)
        self.os_bar.refresh()

    def shutdown(self) -> None:
        for w in (self.tab_info, self.tab_startup, self.tab_services,
                  self.tab_tweaks, self.tab_changes):
            stop_all(w)
