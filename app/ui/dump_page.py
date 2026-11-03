# -*- coding: utf-8 -*-
"""蓝屏 / 崩溃转储分析工作台。"""
from __future__ import annotations

import glob
import os
from datetime import datetime

from PyQt5.QtCore import (QAbstractTableModel, QModelIndex, Qt, QThread,
                          QUrl, pyqtSignal)
from PyQt5.QtGui import QColor, QDesktopServices
from PyQt5.QtWidgets import (QAbstractItemView, QFileDialog, QFrame, QHBoxLayout,
                             QLabel, QPlainTextEdit,
                             QSplitter, QStackedWidget, QTabWidget, QTableView,
                             QVBoxLayout, QWidget)

from .. import theme
from ..core import analysis as ana
from ..core import bugchecks, debugger, knowledge as kb, minidump, report
from . import notice
from .tables import Col, RowModel, attach_table_menu, make_table, run_bg
from .widgets import (Badge, EmptyState, FindingCard, KpiCard, ScrollColumn,
                      DropZone, ghost_button, mono_font, plain_button,
                      primary_button, ui_font)


class CdbWorker(QThread):
    """后台跑 cdb !analyze -v —— 首次下载符号可能要几分钟，不能卡住界面。"""

    done = pyqtSignal(object)

    def __init__(self, path: str, timeout: int = 300, parent=None):
        super().__init__(parent)
        self.path = path
        self.timeout = timeout

    def run(self) -> None:
        try:
            res = debugger.run(self.path, timeout=self.timeout)
        except Exception as exc:  # noqa: BLE001
            res = debugger.CdbResult(ok=False, error=str(exc))
        self.done.emit(res)


class ModuleModel(QAbstractTableModel):
    HEADERS = ["基址", "模块", "版本", "大小", "完整路径"]
    WIDTHS = [170, 190, 120, 90, -1]

    def __init__(self, parent=None):
        super().__init__(parent)
        self._mods = []

    def set_modules(self, mods) -> None:
        self.beginResetModel()
        self._mods = list(mods)
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._mods)

    def columnCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else 5

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role == Qt.DisplayRole and orientation == Qt.Horizontal:
            return self.HEADERS[section]
        return None

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        m = self._mods[index.row()]
        col = index.column()
        t = theme.current()
        if role == Qt.DisplayRole:
            return [m.base_text, m.short_name, m.version or "-", m.size_text, m.name][col]
        if role == Qt.ForegroundRole:
            if col in (0, 2, 3):
                return QColor(t["dim"])
            if col == 3:
                return QColor(t["dim"])
            return QColor(t["text"] if col == 1 else t["faint"])
        if role == Qt.FontRole and col in (0, 2, 3):
            return ui_font(8)
        if role == Qt.ToolTipRole:
            return m.name
        return None


class DumpPage(QWidget):
    status = pyqtSignal(str)
    requestLogAt = pyqtSignal(object)      # datetime，联动到日志工作台

    def __init__(self, parent=None):
        super().__init__(parent)
        self.rep = None
        self.dana = None
        self.cdb_result = None
        self._cdb_busy = False
        self._cdb_worker = None
        self.cdb_card = None
        self._log_crashes: list[datetime] = []
        self.bc = None                  # bugchecks.LocalScan
        self._bc_busy = False
        self._bc_shaped = False         # 分栏只按这张表的需要调整过一次
        self._build()

    # ---------------- UI
    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 14, 18, 14)
        root.setSpacing(12)

        self.drop = DropZone("把崩溃转储拖到这里",
                             "支持 .dmp / .mdmp：用户模式 MDMP 与内核转储（含 MEMORY.DMP）",
                             (".dmp", ".mdmp", ".hdmp"))
        self.drop.fileDropped.connect(self.load_file)
        self.drop.clicked.connect(self._pick_file)
        self.drop.setMinimumHeight(124)
        root.addWidget(self.drop)

        extra = QHBoxLayout()
        extra.setSpacing(8)
        self.btn_bc = primary_button("读取本机蓝屏代码", "crash")
        self.btn_bc.setToolTip("自动扫本机的 Minidump、MEMORY.DMP、LiveKernelReports，"
                               "并读取事件日志，列出这台机器历史上的全部蓝屏码")
        self.btn_bc.clicked.connect(self.scan_local)
        extra.addWidget(self.btn_bc)
        self.btn_latest = ghost_button("分析最新转储", "play")
        self.btn_latest.clicked.connect(self._open_latest)
        extra.addWidget(self.btn_latest)
        self.btn_dir = ghost_button("打开转储目录")
        self.btn_dir.clicked.connect(self._open_dir)
        extra.addWidget(self.btn_dir)
        extra.addStretch(1)
        self.cdb_hint = QLabel("")
        self.cdb_hint.setProperty("role", "faint")
        extra.addWidget(self.cdb_hint)
        self.extra_row = extra
        root.addLayout(extra)

        # 文件信息条（加载后显示）
        self.filebar = QFrame()
        self.filebar.setProperty("role", "card")
        bar = QHBoxLayout(self.filebar)
        bar.setContentsMargins(14, 10, 14, 10)
        bar.setSpacing(10)
        self.fb_name = QLabel("-")
        self.fb_name.setStyleSheet("font-size:13px; font-weight:600;")
        bar.addWidget(self.fb_name)
        self.fb_badge = Badge("内核转储", "临界")
        bar.addWidget(self.fb_badge)
        bar.addStretch(1)
        self.btn_export_html = plain_button("HTML 报告", "export")
        self.btn_export_html.clicked.connect(self._export_html)
        bar.addWidget(self.btn_export_html)
        self.btn_export_xlsx = plain_button("Excel", "export")
        self.btn_export_xlsx.clicked.connect(self._export_xlsx)
        bar.addWidget(self.btn_export_xlsx)
        self.btn_reload = ghost_button("换一个文件", "folder")
        self.btn_reload.clicked.connect(self._pick_file)
        bar.addWidget(self.btn_reload)
        self.filebar.hide()
        root.addWidget(self.filebar)

        kpi = QHBoxLayout()
        kpi.setSpacing(10)
        self.k_type = KpiCard("转储类型", "-", "", "info")
        self.k_code = KpiCard("蓝屏码", "-", "", "crit")
        self.k_mod = KpiCard("故障模块", "-", "", "error")
        self.k_os = KpiCard("操作系统", "-", "", "accent")
        self.k_arch = KpiCard("架构 / 处理器", "-", "", "dim")
        self.k_size = KpiCard("文件大小", "-", "", "warn")
        for k in (self.k_type, self.k_code, self.k_mod, self.k_os, self.k_arch, self.k_size):
            kpi.addWidget(k, 1)
        self.kpi_row = kpi
        # 没有转储文件时这 6 张卡全是 "-"，白占 80px，先藏起来。
        # 包一层 QWidget 是因为 QHBoxLayout 本身不能 hide。
        self.kpi_box = QWidget()
        kpi.setContentsMargins(0, 0, 0, 0)
        self.kpi_box.setLayout(kpi)
        self.kpi_box.hide()
        root.addWidget(self.kpi_box)

        split = QSplitter(Qt.Horizontal)
        split.setChildrenCollapsible(False)
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.setSpacing(8)
        cap = QLabel("诊断结论")
        cap.setProperty("role", "H2")
        ll.addWidget(cap)
        self.findings = ScrollColumn(10)
        ll.addWidget(self.findings, 1)
        split.addWidget(left)

        self.tabs = QTabWidget()
        self.tabs.setMinimumWidth(400)
        self.mod_model = ModuleModel(self)
        self.mod_table = QTableView()
        self.mod_table.setModel(self.mod_model)
        self.mod_table.setAlternatingRowColors(True)
        self.mod_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.mod_table.setShowGrid(False)
        self.mod_table.setSortingEnabled(False)
        self.mod_table.verticalHeader().setVisible(False)
        self.mod_table.verticalHeader().setDefaultSectionSize(26)
        self.mod_table.horizontalHeader().setStretchLastSection(True)
        self.mod_table.horizontalHeader().setHighlightSections(False)
        attach_table_menu(self.mod_table, self.mod_model, "崩溃模块清单")
        for i, w in enumerate(ModuleModel.WIDTHS):
            if w > 0:
                self.mod_table.setColumnWidth(i, w)
        self.mod_stack = QStackedWidget()
        self.mod_stack.addWidget(self.mod_table)
        self.mod_empty = EmptyState(
            "folder", "该转储不含模块列表",
            "内核转储按物理页组织，模块清单需要用 cdb 解析；"
            "用户模式转储（MDMP）会在这里列出全部已加载模块与版本。")
        self.mod_stack.addWidget(self.mod_empty)
        self.tabs.addTab(self.mod_stack, "模块清单")

        self.raw_fields = QPlainTextEdit()
        self.raw_fields.setReadOnly(True)
        self.raw_fields.setFrameShape(QFrame.NoFrame)
        self.raw_fields.setProperty("role", "tech")
        self.raw_fields.setFont(mono_font(9))
        self.tabs.addTab(self.raw_fields, "原始字段")

        cdb_box = QWidget()
        cl = QVBoxLayout(cdb_box)
        cl.setContentsMargins(0, 8, 0, 0)
        cl.setSpacing(8)
        row = QHBoxLayout()
        row.setSpacing(8)
        self.cdb_state = QLabel("检查中…")
        self.cdb_state.setProperty("role", "faint")
        row.addWidget(self.cdb_state, 1)
        self.btn_cdb = primary_button("用 cdb 取符号堆栈")
        self.btn_cdb.clicked.connect(self._run_cdb)
        row.addWidget(self.btn_cdb)
        cl.addLayout(row)
        self.cdb_out = QPlainTextEdit()
        self.cdb_out.setReadOnly(True)
        self.cdb_out.setFrameShape(QFrame.NoFrame)
        self.cdb_out.setProperty("role", "tech")
        self.cdb_out.setFont(mono_font(8))
        self.cdb_out.setPlainText(
            "未运行调试器。\n\n"
            "本工具的本地解析已给出蓝屏码 / 异常码 / 故障模块；\n"
            "若需要函数级调用栈，安装 Windows SDK 的 Debugging Tools（或 WinDbg）后，\n"
            "点上方按钮即可自动调用 cdb !analyze -v，并自动下载微软符号。")
        cl.addWidget(self.cdb_out, 1)
        self.tabs.addTab(cdb_box, "调试器堆栈")

        self.bc_tab = self.tabs.addTab(self._build_bc_tab(), "本机蓝屏记录")

        split.addWidget(self.tabs)
        split.setStretchFactor(0, 1)
        split.setStretchFactor(1, 0)
        split.setSizes([720, 560])
        self.split = split
        root.addWidget(split, 1)

        self.empty_findings = EmptyState(
            "crash", "还没有分析任何转储",
            "蓝屏后系统会把转储写到 C:\\Windows\\Minidump，"
            "把文件拖进来就能看到蓝屏码、故障模块与排查方向。")
        self.findings.add(self.empty_findings)

        self._refresh_cdb_hint()

    def _refresh_cdb_hint(self) -> None:
        path = debugger.find_cdb()
        if path:
            self.cdb_hint.setText("已检测到调试器：cdb.exe")
            self.cdb_state.setText(f"调试器就绪：{path}")
            self.btn_cdb.setEnabled(bool(self.rep))
        else:
            self.cdb_hint.setText("未检测到 cdb（可选，不影响本地解析）")
            self.cdb_state.setText("未检测到 cdb.exe —— 本地解析不依赖它，"
                                   "如需函数级堆栈请安装 Windows SDK → Debugging Tools")
            self.btn_cdb.setEnabled(False)

    # ---------------- 本机蓝屏记录
    def _build_bc_tab(self) -> QWidget:
        """本机蓝屏记录：不依赖用户先选文件，直接扫全机。"""
        box = QWidget()
        lay = QVBoxLayout(box)
        lay.setContentsMargins(0, 10, 0, 0)
        lay.setSpacing(8)

        self.bc_head = QLabel("还没有读取本机蓝屏记录")
        self.bc_head.setObjectName("H3")
        self.bc_head.setWordWrap(True)
        lay.addWidget(self.bc_head)

        hint = QLabel(
            "三处取证、互相比对：转储文件头（最准 —— 2 GB 的转储也只读前 96 字节）、"
            "System 日志的 BugCheck 1001、以及 Kernel-Power 41（掉电 / 硬关机）。"
            "双击某一行可直接分析它对应的转储。")
        hint.setProperty("role", "faint")
        hint.setWordWrap(True)
        lay.addWidget(hint)

        # 末列必须是要拉满的那一列（make_table 用 setStretchLastSection）。
        # 「转储大小」不进列 —— 6 列在 780px 里会把「说明」挤成省略号，
        # 大小放进「来源」的 tooltip（实况报告动辄 2 GB，看大小并不改变判断）。
        self.bc_model = RowModel([
            Col("时间", 102, lambda r: r.time_short, mono=True, color="dim",
                tip=lambda r: r.time_text),
            Col("蓝屏码", 90, lambda r: r.code_text, mono=True,
                color_fn=lambda r: r.level, tip=lambda r: r.cn_text),
            Col("名称", 320, lambda r: r.name_text, tip=lambda r: r.path_tip),
            Col("来源", 96, lambda r: r.origin_text, color="dim",
                tip=lambda r: r.origin_tip + (
                    "\n文件 %s" % r.dump_text if r.dump_path else "")),
            Col("说明", 0, lambda r: r.note_text, color="dim", stretch=True),
        ], parent=self)
        self.bc_stack = QStackedWidget()
        self.bc_table = make_table(self.bc_model, row_height=26)
        self.bc_table.doubleClicked.connect(lambda _i: self._analyze_selected())
        self.bc_table.selectionModel().selectionChanged.connect(
            lambda *_a: self._sync_bc_buttons())
        self.bc_stack.addWidget(self.bc_table)
        self.bc_empty = EmptyState(
            "crash", "还没有读取本机蓝屏记录",
            "点上面的「读取本机蓝屏代码」，工具会自己去找这台机器蓝屏过的证据，"
            "不需要你手动挑文件。")
        self.bc_stack.addWidget(self.bc_empty)
        self.bc_stack.setCurrentIndex(1)      # 先显示空态，别给一张空表
        lay.addWidget(self.bc_stack, 1)

        row = QHBoxLayout()
        row.setSpacing(8)
        self.btn_bc_analyze = primary_button("分析选中的转储", "play")
        self.btn_bc_analyze.setEnabled(False)
        self.btn_bc_analyze.clicked.connect(self._analyze_selected)
        row.addWidget(self.btn_bc_analyze)
        self.btn_bc_folder = ghost_button("在文件夹中显示", "folder")
        self.btn_bc_folder.setEnabled(False)
        self.btn_bc_folder.clicked.connect(self._reveal_selected)
        row.addWidget(self.btn_bc_folder)
        self.btn_bc_rescan = ghost_button("重新读取", "refresh")
        self.btn_bc_rescan.clicked.connect(self.scan_local)
        row.addWidget(self.btn_bc_rescan)
        row.addStretch(1)
        self.bc_foot = QLabel("")
        self.bc_foot.setProperty("role", "faint")
        row.addWidget(self.bc_foot)
        lay.addLayout(row)
        return box

    def scan_local(self) -> None:
        """扫本机蓝屏记录。会读事件日志与转储头，所以放后台跑。"""
        if self._bc_busy:
            return
        self._bc_busy = True
        self.btn_bc.setEnabled(False)
        self.btn_bc.setText("正在扫描…")
        self.bc_head.setText("正在枚举 Minidump / MEMORY.DMP / LiveKernelReports，"
                             "并读取 System 事件日志…")
        self.tabs.setCurrentIndex(self.bc_tab)
        # 这张表在默认 560px 右栏里放不下「说明」列 —— 首次打开时主动拉宽一次。
        # 只在第一次做：之后用户自己拖过分栏，再点「重新读取」不该把宽度抢回去。
        if not self._bc_shaped:
            self.split.setSizes([600, 780])
            self._bc_shaped = True
        self.status.emit("正在读取本机蓝屏记录…")

        def job(_tick, _stop):
            return bugchecks.collect(with_events=True)

        # 不用自己存引用：run_bg 已经把它挂在 self._workers 上防 GC
        run_bg(self, job, self._on_bc_done, on_failed=self._on_bc_failed)

    def _on_bc_failed(self, msg: str) -> None:
        self._bc_busy = False
        self.btn_bc.setEnabled(True)
        self.btn_bc.setText("读取本机蓝屏代码")
        self.bc_head.setText("读取失败：%s" % msg)
        notice.note(self, "error", "读取本机蓝屏记录失败：%s" % msg)

    def _on_bc_done(self, s) -> None:
        self._bc_busy = False
        self.btn_bc.setEnabled(True)
        self.btn_bc.setText("重新读取本机蓝屏代码")
        self.bc = s
        self._fill_bc(s)
        kind = "ok" if s.with_code else ("warn" if s.records else "info")
        notice.note(self, kind, s.short_line)
        # 事件日志读不到时必须说出来：否则用户看到「只有 2 条」会以为本机只蓝屏过 2 次
        if s.event_error:
            notice.note(self, "warn", s.event_error
                        + "（本次结果只包含转储文件里的记录）")
        self.status.emit(s.headline)
        # 还没分析过转储时，左侧「诊断结论」也换成扫描结论 —— 否则右边有 3 条记录、
        # 左边还写着「还没有分析任何转储」，自相矛盾。
        if self.rep is None:
            self._show_bc_findings(s)

    def restore_scan(self, s) -> None:
        """切主题会整窗重建页面 —— 用这个把扫描结果放回去，不重扫、不弹提示。"""
        self.bc = s
        self.btn_bc.setText("重新读取本机蓝屏代码")
        self._fill_bc(s)
        if self.rep is None:
            self._show_bc_findings(s)

    def _fill_bc(self, s) -> None:
        self.bc_model.set_rows(s.records)
        self.bc_head.setText(s.headline)
        if s.records:
            self.bc_stack.setCurrentIndex(0)
            self.tabs.setTabText(self.bc_tab, "本机蓝屏记录 (%d)" % len(s.records))
        else:
            self.bc_stack.setCurrentIndex(1)
            self.bc_empty.set_text(
                "本机没有找到蓝屏或异常关机记录",
                "\n".join(s.problems) if s.problems else
                "转储目录、事件日志都没找到相关记录。")
            self.tabs.setTabText(self.bc_tab, "本机蓝屏记录")
        stamp = s.scanned_at.strftime("%H:%M:%S") if s.scanned_at else "—"
        self.bc_foot.setText("扫描于 %s · 耗时 %d ms · 有蓝屏码 %d 条%s"
                             % (stamp, s.cost_ms, len(s.with_code),
                                " · 事件日志不可用" if s.event_error else ""))
        self._sync_bc_buttons()

    def _show_bc_findings(self, s) -> None:
        self._clear_findings()
        self.cross_card = None
        t = theme.current()
        head = QFrame()
        head.setProperty("role", "card")
        lay = QVBoxLayout(head)
        lay.setContentsMargins(14, 12, 14, 13)
        lay.setSpacing(6)
        cap = QLabel("本机蓝屏记录")
        cap.setStyleSheet(f"color:{t['accent']}; font-size:11px; font-weight:600;")
        lay.addWidget(cap)
        title = QLabel("共 %d 条记录，其中 %d 条有蓝屏码"
                       % (len(s.records), len(s.with_code)))
        title.setWordWrap(True)
        title.setStyleSheet("font-size:15px; font-weight:600;")
        lay.addWidget(title)
        last = s.latest_with_code or s.latest
        sub_text = "最近一次：" + (last.time_text if last else "—")
        if last and last.has_code:
            sub_text += " · %s %s" % (last.code_text, last.name_text)
        sub = QLabel(sub_text)
        sub.setProperty("role", "faint")
        sub.setWordWrap(True)
        lay.addWidget(sub)
        self.findings.add(head)
        for f in bugchecks.findings(s):
            self.findings.add(FindingCard(f))

    def _selected_bc(self):
        idx = self.bc_table.currentIndex()
        if idx.isValid():
            return self.bc_model.row_at(idx.row())
        rows = self.bc_table.selectionModel().selectedRows()
        return self.bc_model.row_at(rows[0].row()) if rows else None

    def _sync_bc_buttons(self) -> None:
        c = self._selected_bc()
        has_dump = bool(c and c.dump_path)
        self.btn_bc_analyze.setEnabled(bool(has_dump))
        self.btn_bc_folder.setEnabled(bool(has_dump))

    def _analyze_selected(self) -> None:
        c = self._selected_bc()
        if not c:
            return
        if not c.dump_path:
            notice.note(self, "info",
                        "这条只有事件日志，没有转储文件 —— 拿不到出错模块与调用栈。"
                        "转储可能被清理工具删掉了，也可能蓝屏时系统没开转储。")
            return
        self.load_file(c.dump_path)

    def _reveal_selected(self) -> None:
        c = self._selected_bc()
        if not c or not c.dump_path:
            return
        d = os.path.dirname(c.dump_path)
        if os.path.isdir(d):
            QDesktopServices.openUrl(QUrl.fromLocalFile(d))

    # ---------------- 数据
    def _pick_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "选择崩溃转储文件", self._dump_dir(), "转储文件 (*.dmp *.mdmp *.hdmp);;所有文件 (*.*)")
        if path:
            self.load_file(path)

    @staticmethod
    def _dump_dir() -> str:
        d = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "Minidump")
        return d if os.path.isdir(d) else os.path.expanduser("~")

    def _open_dir(self) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(self._dump_dir()))

    def _open_latest(self) -> None:
        d = self._dump_dir()
        files = sorted(glob.glob(os.path.join(d, "*.dmp")), key=os.path.getmtime, reverse=True)
        if not files:
            notice.note(
                self, "info",
                "%s 下没有 .dmp 文件。如果系统刚蓝屏过，可能未开启转储记录："
                "系统属性 → 高级 → 启动和故障恢复 → 写入调试信息。" % d)
            return
        self.load_file(files[0])

    def load_file(self, path: str) -> None:
        try:
            rep = minidump.parse(path)
        except Exception as exc:  # noqa: BLE001
            self.status.emit(f"解析失败：{exc}")
            notice.note(self, "error",
                        "无法解析 %s：%s（仅支持 Windows 崩溃转储 MDMP / PAGEDUMP）"
                        % (os.path.basename(path), exc))
            return
        self.rep = rep
        self.dana = ana.analyze_dump(rep)
        self.cdb_result = None
        self._clear_findings()
        self._fill_kpis(rep)
        self._fill_findings(rep, self.dana)
        self._fill_details(rep)
        self.drop.hide()
        self.filebar.show()
        self.kpi_box.show()
        self.fb_name.setText(rep.name)
        self.fb_badge.set_severity("临界" if rep.kind == "kernel" else "高")
        self.fb_badge.setText("内核转储" if rep.kind == "kernel" else "用户模式转储")
        self.btn_cdb.setEnabled(bool(debugger.find_cdb()))
        self.cdb_out.setPlainText(
            "内核转储的模块与堆栈需要调试器解析。点上方按钮运行 cdb !analyze -v。"
            if rep.kind == "kernel" else
            "用户模式转储已由本地解析给出异常码与故障模块；\n"
            "如需函数级调用栈，可同样调用 cdb（需安装调试工具）。")
        self.tabs.setTabText(2, "调试器堆栈")
        self.cdb_card = None
        self.status.emit(f"{rep.name}：{rep.key_summary}")
        self._maybe_cross_link(rep)
        # 有 cdb 就自动跑一遍（内核小转储几秒钟；GB 级 MEMORY.DMP 交给用户手动点）
        if debugger.find_cdb() and rep.kind == "kernel" and rep.file_size <= 128 * 1024 * 1024:
            self._run_cdb()

    def _clear_findings(self) -> None:
        self.findings.clear()

    def _fill_kpis(self, rep) -> None:
        is_kernel = rep.kind == "kernel"
        self.k_type.set_value("内核" if is_kernel else "用户模式",
                              "MEMORY.DMP / Minidump" if is_kernel else "MDMP 迷你转储")
        if is_kernel:
            self.k_code.set_label("蓝屏码")
            self.k_code.set_value(rep.bugcheck.code_text,
                                  (rep.bugcheck.name_cn or "")[:26], "crit")
            self.k_mod.set_value("-", "需 cdb 解析", "dim")
        else:
            self.k_code.set_label("异常码")
            info = kb.exception_info(rep.exception_code)
            self.k_code.set_value(rep.exception_text,
                                  (info.name_cn if info else "未知异常")[:26], "crit")
            mod = rep.faulting_module
            self.k_mod.set_value(mod.short_name if mod else "-",
                                 mod.version if mod else "地址不在已知模块内", "error")
        self.k_os.set_value(rep.system.os_short[:18],
                            rep.system.version_text if rep.system.build
                            else "build 未知（内核转储不记录）")
        arch = rep.system.arch_name.split(" (")[0]
        self.k_arch.set_value(arch, f"{rep.system.cpus or '?'} 逻辑处理器")
        self.k_size.set_value(rep.size_text, rep.created_text)

    def _fill_findings(self, rep, dana) -> None:
        head = QFrame()
        head.setProperty("role", "card")
        lay = QVBoxLayout(head)
        lay.setContentsMargins(14, 12, 14, 13)
        lay.setSpacing(6)
        t = theme.current()
        cap = QLabel("总体结论")
        cap.setStyleSheet(f"color:{t['accent']}; font-size:11px; font-weight:600;")
        lay.addWidget(cap)
        title = QLabel(dana.headline or rep.key_summary)
        title.setWordWrap(True)
        title.setStyleSheet("font-size:15px; font-weight:600;")
        lay.addWidget(title)
        sub = QLabel(f"{rep.kind_text} · {rep.size_text} · {rep.created_text}")
        sub.setProperty("role", "faint")
        sub.setWordWrap(True)
        lay.addWidget(sub)
        self.findings.add(head)

        for f in dana.findings:
            self.findings.add(FindingCard(f))

        self.cross_card = None
        if self._log_crashes:
            near = [c for c in self._log_crashes
                    if rep.created and abs((c - rep.created).total_seconds()) <= 900]
            if near:
                card = QFrame()
                card.setProperty("role", "findingCard")
                cl = QHBoxLayout(card)
                cl.setContentsMargins(14, 12, 14, 12)
                cl.setSpacing(10)
                txt = QLabel(f"事件日志中同一时段有 {len(near)} 次蓝屏记录，"
                             f"点击跳到日志工作台看前后上下文 →")
                txt.setWordWrap(True)
                txt.setStyleSheet(f"color:{t['accent']};")
                cl.addWidget(txt, 1)
                card.setCursor(Qt.PointingHandCursor)
                target = near[0]
                card.mouseReleaseEvent = lambda _e, d=target: self.requestLogAt.emit(d)
                self.cross_card = card
                self.findings.add(card)

    def _fill_details(self, rep) -> None:
        self.mod_model.set_modules(sorted(rep.modules, key=lambda m: m.base))
        self.mod_stack.setCurrentIndex(0 if rep.modules else 1)
        lines = []
        for section, payload in rep.raw_fields.items():
            lines.append(f"── {section} ──")
            if isinstance(payload, dict):
                w = max((len(str(k)) for k in payload), default=8)
                for k, v in payload.items():
                    lines.append(f"  {str(k).ljust(w)}  {v}")
            else:
                lines.append(f"  {payload}")
            lines.append("")
        if rep.comments:
            lines.append("── 转储内嵌注释 ──")
            for c in rep.comments:
                lines.append("  " + c.replace("\n", "\n  "))
            lines.append("")
        if rep.warnings:
            lines.append("── 警告 ──")
            for w in rep.warnings:
                lines.append("  · " + w)
        self.raw_fields.setPlainText("\n".join(lines))
        self.tabs.setTabText(0, f"模块清单 ({len(rep.modules)})")

    def set_log_context(self, crash_times: list) -> None:
        """由主窗口注入日志工作台的蓝屏时间点，用于跨工作台联动。"""
        self._log_crashes = list(crash_times or [])
        if self.rep is not None and self.dana is not None:
            self._refresh_cross_card()

    def _refresh_cross_card(self) -> None:
        if getattr(self, "cross_card", None) is not None:
            self.cross_card.setParent(None)
            self.cross_card.deleteLater()
            self.cross_card = None
        if not self._log_crashes or self.rep is None:
            return
        near = [c for c in self._log_crashes
                if self.rep.created and abs((c - self.rep.created).total_seconds()) <= 900]
        if not near:
            return
        t = theme.current()
        card = QFrame()
        card.setProperty("role", "findingCard")
        cl = QHBoxLayout(card)
        cl.setContentsMargins(14, 12, 14, 12)
        txt = QLabel(f"事件日志中同一时段有 {len(near)} 次蓝屏记录，"
                     f"点击跳到日志工作台看前后上下文 →")
        txt.setWordWrap(True)
        txt.setStyleSheet(f"color:{t['accent']};")
        cl.addWidget(txt, 1)
        card.setCursor(Qt.PointingHandCursor)
        target = near[0]
        card.mouseReleaseEvent = lambda _e, d=target: self.requestLogAt.emit(d)
        self.cross_card = card
        self.findings.add(card)

    def _maybe_cross_link(self, rep) -> None:
        self._refresh_cross_card()

    # ---------------- cdb
    def _run_cdb(self) -> None:
        if not self.rep or self._cdb_busy:
            return
        if not debugger.find_cdb():
            self.status.emit("未检测到 cdb.exe，无法取符号堆栈")
            return
        self._cdb_busy = True
        self.btn_cdb.setEnabled(False)
        self.btn_cdb.setText("解析中…")
        self.cdb_out.setPlainText("正在调用调试器并下载符号，首次可能需要 1-3 分钟…\n"
                                  "（后台运行，界面不会卡住，可以先看其他页）")
        self.status.emit("正在运行 cdb !analyze -v …")
        self._cdb_worker = CdbWorker(self.rep.path, parent=self)
        self._cdb_worker.done.connect(self._on_cdb_done)
        self._cdb_worker.start()

    def _on_cdb_done(self, res) -> None:
        self._cdb_busy = False
        self.btn_cdb.setText("重新取符号堆栈")
        self.btn_cdb.setEnabled(bool(self.rep))
        self.cdb_result = res
        if not res.ok:
            self.cdb_out.setPlainText(res.error + "\n\n" + (res.text or "")[:4000])
            self.status.emit(res.error)
            return
        header = []
        order = ["PROCESS_NAME", "BUGCHECK_CODE", "EXCEPTION_CODE", "MODULE_NAME",
                 "IMAGE_NAME", "IMAGE_VERSION", "FAILURE_BUCKET_ID", "PRIMARY_PROBLEM_CLASS",
                 "OS_VERSION"]
        for k in order:
            if k in res.keys:
                header.append(f"{k:22}{res.keys[k]}")
        if res.blamed:
            header.append(f"{'Probably caused by':22}{res.blamed}")
        text = "── !analyze 结论 ──\n" + "\n".join(header)
        if res.stack:
            text += "\n\n── 调用栈 ──\n" + "\n".join(res.stack)
        text += "\n\n── 原始输出 ──\n" + res.text
        self.cdb_out.setPlainText(text)
        self.status.emit("cdb 分析完成：" + res.summary)
        self._merge_cdb(res)

    def _merge_cdb(self, res) -> None:
        """把调试器结论并入 KPI、诊断卡与标签页标题。"""
        k = res.keys or {}
        mod = k.get("MODULE_NAME", "")
        img = k.get("IMAGE_NAME", "")
        ver = k.get("IMAGE_VERSION", "")
        if mod:
            sub = " ".join(x for x in (img, ver) if x)
            self.k_mod.set_value(mod, sub or "cdb 定位", "error")
        finding = ana.cdb_finding(res)
        if finding is None:
            return
        self.dana.findings.insert(0, finding)
        card = FindingCard(finding)
        # 索引 0 是「总体结论」卡，调试器结论紧跟其后
        self.findings._lay.insertWidget(1, card)
        self.cdb_card = card
        self.tabs.setTabText(2, "调试器堆栈 ✓")
        self.tabs.setCurrentIndex(2)

    # ---------------- 导出
    def _export_html(self) -> None:
        if not self.rep:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "保存 HTML 报告",
            os.path.join(os.path.expanduser("~"), "Desktop",
                         f"崩溃分析_{self.rep.name}_{datetime.now():%Y%m%d_%H%M}.html"),
            "网页 (*.html)")
        if not path:
            return
        try:
            report.export_dump_html(path, self.rep, self.dana,
                                    self.cdb_result.text if self.cdb_result else "",
                                    cdb=self.cdb_result)
            self.status.emit(f"报告已保存：{path}")
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))
        except Exception as exc:  # noqa: BLE001
            notice.note(self, "error", "导出失败：%s" % exc)

    def _export_xlsx(self) -> None:
        if not self.rep:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "保存 Excel",
            os.path.join(os.path.expanduser("~"), "Desktop",
                         f"崩溃分析_{self.rep.name}_{datetime.now():%Y%m%d_%H%M}.xlsx"),
            "Excel (*.xlsx)")
        if not path:
            return
        try:
            report.export_dump_xlsx(path, self.rep, self.dana, cdb=self.cdb_result)
            self.status.emit(f"已保存：{path}")
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))
        except Exception as exc:  # noqa: BLE001
            notice.note(self, "error", "导出失败：%s" % exc)
