# -*- coding: utf-8 -*-
"""U 盘读写测速工作台。

这个页面存在的意义是回答三个具体问题：
1. 这块盘到底能跑多快（顺序读 / 顺序写，绕过系统缓存）；
2. 它是不是插在了 USB 2.0 口上（读写在 40 MB/s 以下且总线是 USB）；
3. 同一块盘这次比上次慢了多少（写入掉速往往是老化或坏块的前兆）。

不显示 4K 随机写 —— 实测 U 盘固件会把小写入收进内部缓存就返回，
三种刷盘口径都测出 5500 IOPS 这种物理上不可能的数，给了会误导。
"""
from __future__ import annotations

from PyQt5.QtCore import Qt, QTimer, pyqtSignal
from PyQt5.QtWidgets import (QComboBox, QHBoxLayout, QLabel, QSplitter,
                             QVBoxLayout, QWidget)

from ..core import usbperf
from .tables import Col, RowModel, make_table, run_bg, stop_all
from .widgets import (PAGE_MARGINS, Bar, EmptyState, KpiCard, ghost_button,
                      primary_button)


def _fmt_speed(v: float) -> str:
    return f"{v:,.0f} MB/s" if v >= 10 else f"{v:.1f} MB/s"


class UsbPage(QWidget):
    status = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._targets: list[usbperf.Target] = []
        self._worker = None
        self._busy = False
        self._last: usbperf.Report | None = None
        self._build()
        self._show_placeholder()
        # 枚举可测卷要逐个问介质类型（USB / 机械盘 / 网络盘），实测这一下
        # 能到 1 秒 —— 卡在构造里，用户看到的就是「点测速，界面僵住」。
        # 推到事件循环里：切页动画先出来，列表随后自己填上。
        # 定时器必须挂在本页上：QTimer.singleShot 没有宿主，
        # 页面被主题重建销毁后回调照样会触发（PyQt5 直接 abort）。
        self._boot = QTimer(self)
        self._boot.setSingleShot(True)
        self._boot.timeout.connect(self.reload)
        self._boot.start(0)

    # ------------------------------------------------------------------ 构建
    def _build(self) -> None:
        lay = QVBoxLayout(self)
        lay.setContentsMargins(*PAGE_MARGINS)
        lay.setSpacing(9)

        top = QHBoxLayout()
        top.setSpacing(8)
        title = QLabel("U 盘 / 移动盘读写测速")
        title.setProperty("role", "H2")
        top.addWidget(title)
        top.addSpacing(14)

        self.box = QComboBox()
        self.box.setMinimumWidth(320)
        self.box.setMaxVisibleItems(12)
        self.box.setToolTip("选择要测的盘。USB 盘排在最前面。")
        top.addWidget(self.box)

        self.cmb_size = QComboBox()
        for mb in usbperf.SIZES:
            label = f"{mb} MB" if mb < 1024 else "1 GB"
            self.cmb_size.addItem(label, mb)
        self.cmb_size.setCurrentIndex(1)          # 默认 256 MB
        self.cmb_size.setToolTip(
            "测试文件大小。实测 64 MB 时系统开销占比过高会低估速度，"
            "128 MB 是下限；盘慢就选小一点，盘快可以选大一点。")
        top.addWidget(self.cmb_size)

        self.btn_refresh = ghost_button("刷新", "refresh")
        self.btn_refresh.setToolTip("重新枚举盘符（插拔 U 盘后用）")
        self.btn_refresh.clicked.connect(self.reload)
        top.addWidget(self.btn_refresh)

        top.addStretch(1)
        self.btn_stop = ghost_button("停止")
        self.btn_stop.setEnabled(False)
        self.btn_stop.clicked.connect(self._stop)
        top.addWidget(self.btn_stop)
        self.btn_start = primary_button("开始测速", "speed")
        self.btn_start.clicked.connect(self.start)
        top.addWidget(self.btn_start)
        lay.addLayout(top)

        hint = QLabel("测试会在所选盘的根目录创建一个临时文件"
                      "（SystemToolbox_speedtest.bin），测完立即删除；"
                      "同名文件已存在时会直接放弃，不会覆盖任何已有文件。"
                      "全程绕过系统缓存，测的是盘本身的真实速度。")
        hint.setProperty("role", "faint")
        hint.setWordWrap(True)
        lay.addWidget(hint)

        kpi = QHBoxLayout()
        kpi.setSpacing(9)
        self.k_read = KpiCard("顺序读取", "-", "绕过系统缓存", "accent")
        self.k_write = KpiCard("顺序写入", "-", "带写穿透", "warn")
        self.k_ratio = KpiCard("写读比", "-", "低于 0.15 说明写入受限", "ok")
        self.k_bus = KpiCard("接口 / 介质", "-", "", "dim")
        for k in (self.k_read, self.k_write, self.k_ratio, self.k_bus):
            kpi.addWidget(k, 1)
        lay.addLayout(kpi)

        prog = QHBoxLayout()
        prog.setSpacing(8)
        self.bar = Bar(0.0, 6)
        prog.addWidget(self.bar, 1)
        lay.addLayout(prog)
        self.lbl_prog = QLabel("选一块盘，点「开始测速」")
        self.lbl_prog.setProperty("role", "faint")
        lay.addWidget(self.lbl_prog)

        split = QSplitter(Qt.Vertical)
        split.setChildrenCollapsible(False)
        self.split = split
        split.addWidget(self._build_result())
        split.addWidget(self._build_bottom())
        split.setSizes([250, 260])
        lay.addWidget(split, 1)

        self.foot = QLabel("测速只读取与写入自己创建的临时文件，"
                           "不改分区、不碰盘上已有数据。")
        self.foot.setProperty("role", "faint")
        lay.addWidget(self.foot)

    def _build_result(self) -> QWidget:
        wrap = QWidget()
        lay = QVBoxLayout(wrap)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        head = QLabel("本次结果")
        head.setProperty("role", "H3")
        lay.addWidget(head)
        self.model = RowModel([
            Col("项目", 130, lambda r: r[0]),
            Col("实测", 130, lambda r: r[1], mono=True,
                align=int(Qt.AlignRight | Qt.AlignVCenter)),
            Col("评级", 170, lambda r: r[2], color_fn=lambda r: r[4]),
            Col("说明", 0, lambda r: r[3], color="faint", stretch=True),
        ], parent=self)
        self.table = make_table(self.model, row_height=28)

        # 空态用 EmptyState，不再往表里塞一条假数据：那条「尚未运行」会参与
        # 点表头排序、能被 Ctrl+A 选中、还能被右键「导出 CSV」导进工单里 ——
        # 用户会拿到一行根本不是测量结果的记录。其余页面早就是这样做的。
        holder = QWidget()
        hl = QVBoxLayout(holder)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.setSpacing(0)
        hl.addWidget(self.table)
        self.empty = EmptyState(
            "usb", "还没有测速结果",
            "选一块盘，点右上角「开始测速」；128 MB 一般在十秒内完成。")
        hl.addWidget(self.empty)
        self.empty.hide()
        lay.addWidget(holder, 1)
        return wrap

    def _build_bottom(self) -> QWidget:
        wrap = QWidget()
        lay = QVBoxLayout(wrap)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        head = QLabel("结论与历史")
        head.setProperty("role", "H3")
        lay.addWidget(head)

        self.verdict = QLabel("还没有测试记录")
        self.verdict.setWordWrap(True)
        self.verdict.setTextFormat(Qt.RichText)
        lay.addWidget(self.verdict)

        self.hist_model = RowModel([
            Col("时间", 175, lambda r: r[0]),
            Col("顺序读", 120, lambda r: r[1], mono=True,
                align=int(Qt.AlignRight | Qt.AlignVCenter)),
            Col("顺序写", 120, lambda r: r[2], mono=True,
                align=int(Qt.AlignRight | Qt.AlignVCenter)),
            Col("较上次写入", 0, lambda r: r[3], stretch=True,
                color_fn=lambda r: ("crit" if r[3].startswith("-") and
                                    r[3] != "基本持平" else "dim")),
        ], parent=self)
        self.hist = make_table(self.hist_model, row_height=26)
        self.hist.setMinimumHeight(96)
        lay.addWidget(self.hist, 1)
        return wrap

    def _show_placeholder(self) -> None:
        """没测过时给空态，否则空白表格看起来像加载失败。"""
        self.model.set_rows([])
        self.table.hide()
        self.empty.show()

    # ------------------------------------------------------------------ 盘列表
    def reload(self) -> None:
        keep = self.box.currentData()
        self._targets = usbperf.scan()
        self.box.blockSignals(True)
        self.box.clear()
        for t in self._targets:
            self.box.addItem(f"{t.text} · {t.detail}", t.letter)
        if keep:
            i = self.box.findData(keep)
            if i >= 0:
                self.box.setCurrentIndex(i)
        self.box.blockSignals(False)
        usb = [t for t in self._targets if t.is_usb]
        if not self._targets:
            self.lbl_prog.setText("没有找到可测的盘")
            self.btn_start.setEnabled(False)
        else:
            self.btn_start.setEnabled(True)
            if not self._busy:
                self.lbl_prog.setText(
                    f"找到 {len(self._targets)} 个可测卷，其中 {len(usb)} 个是 USB"
                    if usb else "没插 USB 盘，也可以测内置盘")
        self._render_history()
        self.status.emit(f"已刷新盘列表：{len(self._targets)} 个卷")

    def _current(self) -> usbperf.Target | None:
        letter = self.box.currentData()
        for t in self._targets:
            if t.letter == letter:
                return t
        return None

    def _render_history(self) -> None:
        t = self._current()
        rows = usbperf.history_rows(t) if t else []
        self.hist_model.set_rows(rows)
        if not rows:
            self.verdict.setText(
                "<span style='color:%s'>这块盘还没有历史记录。"
                "同一块盘测两次以上就能看出速度有没有衰减。</span>"
                % _token("faint"))
            return
        if self._last is None or self._last.target.letter != (t.letter if t else ""):
            self.verdict.setText(
                "<span style='color:%s'>这块盘共 %d 次记录，"
                "最近一次：读 %s · 写 %s。点「开始测速」可以再测一次做对比。</span>"
                % (_token("faint"), len(rows), rows[-1][1], rows[-1][2]))

    # ------------------------------------------------------------------ 动作
    def start(self) -> None:
        if self._busy:
            return
        t = self._current()
        if t is None:
            self.status.emit("先选一块盘")
            return
        size_mb = int(self.cmb_size.currentData() or 256)
        free = usbperf.free_bytes(t.letter)
        need = size_mb * 2 * (1 << 20)
        if free and free < need:
            self.lbl_prog.setText(
                f"{t.letter} 剩余空间不足：需要约 {need / 2 ** 30:.1f} GB，"
                f"当前 {free / 2 ** 30:.1f} GB。换个小一点的测试文件，或换块盘。")
            self.status.emit("剩余空间不足，未开始")
            return

        self._busy = True
        self.btn_start.setEnabled(False)
        self.btn_stop.setEnabled(True)
        self.btn_refresh.setEnabled(False)
        self.box.setEnabled(False)
        self.cmb_size.setEnabled(False)
        self.bar.set_ratio(0.0, _token("accent"))
        self.lbl_prog.setText("准备中…")
        self.model.set_rows([])
        self.status.emit(f"正在测 {t.letter}（{size_mb} MB）…")

        def job(tick, stop_flag):
            return usbperf.run(
                t.letter, size_mb,
                on_progress=lambda p, msg: tick(int(p * 100), 100, msg),
                stop_flag=lambda: stop_flag[0])

        self._worker = run_bg(self, job, self._done, self._tick, self._failed)

    def _stop(self) -> None:
        if self._worker is not None:
            self._worker.stop()
            self.lbl_prog.setText("正在停止（当前这一块写完就停）…")
            self.status.emit("已请求停止测速")

    def _tick(self, done: int, total: int, msg: str) -> None:
        self.bar.set_ratio(done / total if total else 0.0, _token("accent"))
        self.lbl_prog.setText(msg)

    def _failed(self, msg: str) -> None:
        self._finish()
        self.lbl_prog.setText(f"测速失败：{msg}")
        self.status.emit("测速失败：" + msg)

    def _finish(self) -> None:
        self._busy = False
        self._worker = None
        self.btn_start.setEnabled(True)
        self.btn_stop.setEnabled(False)
        self.btn_refresh.setEnabled(True)
        self.box.setEnabled(True)
        self.cmb_size.setEnabled(True)

    def _done(self, rep) -> None:
        self._finish()
        self._last = rep
        s = rep.sample
        t = rep.target

        if rep.errors:
            self.bar.set_ratio(0.0, _token("crit"))
            self.lbl_prog.setText("测速未完成：" + rep.errors[0])
            self.verdict.setText(
                "<span style='color:%s'>%s</span>"
                % (_token("crit"), rep.errors[0]))
            self.status.emit("测速未完成")
            return

        self.bar.set_ratio(1.0, _token("ok"))
        self.lbl_prog.setText(
            f"{t.letter} 完成，用时 {s.seconds:.1f} 秒"
            + ("，临时文件已删除" if rep.cleaned else "，临时文件未能删除"))

        read_g, read_role = rep.read_grade
        write_g, write_role = rep.write_grade
        ratio_g, ratio_role = rep.ratio_grade
        rows = [
            ("顺序读取", _fmt_speed(s.read_mbs), read_g,
             "绕过系统缓存，最能代表这块盘的实际读取带宽", read_role),
            ("顺序写入", _fmt_speed(s.write_mbs), write_g,
             "写穿透到设备，U 盘普遍远低于读取", write_role),
            ("4K 随机读", f"{s.rand_read_iops:,.0f} IOPS", "仅供参考",
             "小文件访问能力；设备自带读缓存会让数值偏高", "faint"),
            ("写读比", f"{s.ratio:.2f}" if s.ratio else "-",
             ratio_g, "写入速度 ÷ 读取速度；U 盘普遍 0.1~0.3，"
             "低于 0.15 说明写入是主要瓶颈", ratio_role),
            ("测试文件", f"{s.size_mb:,} MB", t.fs or "未知格式",
             f"写在 {t.root}，测完整盘占用已释放", "faint"),
        ]
        self.model.set_rows(rows)
        self.empty.hide()
        self.table.show()

        self.k_read.set_value(_fmt_speed(s.read_mbs), read_g,
                              _role_color(read_role))
        self.k_write.set_value(_fmt_speed(s.write_mbs), write_g,
                               _role_color(write_role))
        self.k_ratio.set_value(f"{s.ratio:.2f}" if s.ratio else "-",
                               ratio_g, _role_color(ratio_role))
        self.k_bus.set_value(t.bus or ("USB" if t.is_usb else "未知"),
                             t.label or "未命名盘")

        self._render_verdict(rep)
        self.hist_model.set_rows(usbperf.history_rows(t))
        self.status.emit(f"{t.letter} 测速完成：{rep.headline}")

    def _render_verdict(self, rep) -> None:
        s = rep.sample
        color = _role_color(rep.level)
        t = rep.target
        parts = [
            f"<div style='color:{color}'><b>{t.text}：{rep.headline}</b></div>"
        ]
        meta = [f"接口 {t.bus or '未知'}", f"格式 {t.fs or '未知'}",
                f"容量 {t.total / 2 ** 30:.0f} GB",
                f"测试 {s.size_mb} MB / {s.seconds:.1f} 秒"]
        parts.append("<div style='color:%s'>%s</div>"
                     % (_token("dim"), " · ".join(meta)))
        hints = rep.hints
        if hints:
            items = "".join(
                "<li style='margin-bottom:3px'>%s</li>" % h for h in hints)
            parts.append("<ul style='margin-top:6px;margin-left:-18px'>%s</ul>"
                         % items)
        elif rep.level == "ok":
            parts.append("<div style='color:%s;margin-top:5px'>"
                         "各项指标都在该接口的正常范围内，没发现异常。</div>"
                         % _token("dim"))
        else:
            parts.append("<div style='color:%s;margin-top:5px'>"
                         "盘能用，但上表有评级偏低的项 —— 多数是该型号的固有特性，"
                         "不影响数据安全；若写入速度比以往明显下降，再看看历史那一栏。"
                         "</div>" % _token("dim"))
        self.verdict.setText("".join(parts))

    # ------------------------------------------------------------------ 收尾
    def shutdown(self) -> None:
        stop_all(self)


def _token(name: str) -> str:
    from .. import theme
    return theme.current().get(name, theme.current()["text"])


def _role_color(name: str) -> str:
    """把 ok / warn / bad / faint 映射成主题色。"""
    table = {"ok": "ok", "warn": "warn", "bad": "crit", "faint": "faint"}
    return _token(table.get(name, "text"))
