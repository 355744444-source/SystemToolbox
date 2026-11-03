# -*- coding: utf-8 -*-
"""网络诊断工作台：一眼看出「连不上」卡在哪一层，外加一次宽带测速。

诊断部分只读 —— 不修网络、不改注册表、不重置 Winsock。

结论表的顺序就是排查顺序：网卡有没有地址 → ICMP 本身通不通 →
外网通不通 → 网关 → DNS → 端口 → 代理 / hosts。
上面某一条红了，下面的基本不用看。

「宽带测速」页签在诊断之后回答另一个问题：这条线到底有多快。
"""
from __future__ import annotations

import os
import time

from PyQt5.QtCore import Qt, QUrl, pyqtSignal
from PyQt5.QtGui import QDesktopServices
from PyQt5.QtWidgets import (QComboBox, QHBoxLayout, QLabel, QSplitter,
                             QTabWidget, QVBoxLayout, QWidget)

from .. import theme
from ..core import netdiag, netspeed
from .line_chart import LineChart
from .tables import Col, RowModel, make_table, run_bg, stop_all
from .widgets import Bar, KpiCard, ghost_button, primary_button

RIGHT = int(Qt.AlignRight | Qt.AlignVCenter)
SPEED_TAB = 3                     # 第 4 个页签，切过去要给曲线腾地方


def _tok(name: str) -> str:
    t = theme.current()
    return t.get(name, t["text"])



def _open(path: str) -> None:
    if not path:
        return
    if os.path.isfile(path):
        path = os.path.dirname(path)
    QDesktopServices.openUrl(QUrl.fromLocalFile(path))


def _check_color(c) -> str:
    """通过绿、异常红、纯信息灰 —— 结论表靠颜色一眼分好坏。"""
    if c.ok is True:
        return "ok"
    if c.ok is False:
        return "crit"
    return "dim"


def _hosts_color(r) -> str:
    """屏蔽（0.0.0.0 / 127. / ::1）是常规操作，只灰不红；
    把常见域名指到具体公网 IP 才是该看一眼的那种改动。"""
    kind = netdiag.hosts_kind(r[0], r[1])
    if kind == "hijack":
        return "warn"
    return "faint" if kind == "block" else ""


class NetPage(QWidget):
    status = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._busy = False
        self.rep: netdiag.Report | None = None
        # 测速：_live 是后台线程往里塞的瞬时速度，GUI 线程在 _tick 里取走
        self._speed_busy = False
        self._speed_worker = None
        self._live: list[float] = []
        self._speed_roomy = False
        self.speed_rep: netspeed.Report | None = None
        self._wifi = False
        self._build()

    # ------------------------------------------------------------------ UI
    def _build(self) -> None:
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 10, 16, 10)
        lay.setSpacing(9)

        top = QHBoxLayout()
        top.setSpacing(8)
        title = QLabel("网络诊断")
        title.setObjectName("H2")
        top.addWidget(title)
        self.sub_label = QLabel("按「网卡 → 外网 → 网关 → DNS → 端口 → 代理」的顺序逐层排查")
        self.sub_label.setProperty("role", "faint")
        top.addWidget(self.sub_label)
        top.addStretch(1)
        self.btn_hosts = ghost_button("打开 hosts 所在目录")
        self.btn_hosts.clicked.connect(
            lambda: _open(os.path.dirname(netdiag.hosts_path())))
        top.addWidget(self.btn_hosts)
        self.btn_run = primary_button("开始诊断", "search")
        self.btn_run.clicked.connect(self.diagnose)
        top.addWidget(self.btn_run)
        lay.addLayout(top)

        kpis = QHBoxLayout()
        kpis.setSpacing(10)
        self.kpi_state = KpiCard("诊断结果", "-", "尚未诊断")
        self.kpi_nic = KpiCard("主网卡", "-", "-")
        self.kpi_net = KpiCard("外网延迟", "-", "-")
        self.kpi_dns = KpiCard("DNS 解析", "-", "-")
        for k in (self.kpi_state, self.kpi_nic, self.kpi_net, self.kpi_dns):
            kpis.addWidget(k, 1)
        lay.addLayout(kpis)

        split = QSplitter(Qt.Vertical)
        split.setChildrenCollapsible(False)
        self.split = split

        upper = QWidget()
        ul = QVBoxLayout(upper)
        ul.setContentsMargins(0, 0, 0, 0)
        ul.setSpacing(6)
        h1 = QLabel("检查结论")
        h1.setObjectName("H3")
        ul.addWidget(h1)
        self.check_model = RowModel([
            Col("检查项", 168, lambda r: r.name),
            Col("结果", 326, lambda r: r.value,
                color_fn=_check_color, tip=lambda r: r.value),
            Col("说明", 0, lambda r: r.hint, color="dim",
                stretch=True, tip=lambda r: r.hint),
        ], parent=self)
        self.check_table = make_table(self.check_model, row_height=25)
        ul.addWidget(self.check_table, 1)
        split.addWidget(upper)

        self.tabs = QTabWidget()
        self.tab_nic = self._build_nic()
        self.tab_port = self._build_port()
        self.tab_hosts = self._build_hosts()
        self.tab_speed = self._build_speed()
        self.tabs.addTab(self.tab_nic, "网卡与地址")
        self.tabs.addTab(self.tab_port, "监听端口")
        self.tabs.addTab(self.tab_hosts, "hosts 与代理")
        self.tabs.addTab(self.tab_speed, "宽带测速")
        self.tabs.currentChanged.connect(self._on_tab_changed)
        split.addWidget(self.tabs)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 2)
        lay.addWidget(split, 1)

        self.foot = QLabel("点右上角「开始诊断」跑一遍检查；全部只读，不会改动任何设置")
        self.foot.setProperty("role", "faint")
        self.foot.setWordWrap(True)
        lay.addWidget(self.foot)

    def _build_nic(self) -> QWidget:
        w = QWidget()
        l = QVBoxLayout(w)
        l.setContentsMargins(0, 6, 0, 0)
        l.setSpacing(4)
        self.nic_model = RowModel([
            Col("网卡", 268, lambda r: r.title, tip=lambda r: r.desc or r.name),
            Col("IPv4 地址", 190, lambda r: f"{r.ip}/{r.mask}" if r.mask else r.ip,
                mono=True),
            Col("网关", 132, lambda r: r.gateway or "—", mono=True),
            Col("DNS", 132, lambda r: r.dns or "—", color="dim"),
            Col("速率", 88, lambda r: r.speed_text, align=RIGHT),
            Col("链路 / 方式", 0,
                lambda r: f"{r.link_text} · {r.dhcp and 'DHCP' or '静态'}"
                          + (" · 虚拟" if r.is_virtual else ""),
                color_fn=lambda r: "faint" if r.is_virtual else (
                    "ok" if r.up else "warn"), stretch=True),
        ], parent=self)
        l.addWidget(make_table(self.nic_model, row_height=25), 1)
        return w

    def _build_port(self) -> QWidget:
        w = QWidget()
        l = QVBoxLayout(w)
        l.setContentsMargins(0, 6, 0, 0)
        l.setSpacing(4)
        self.port_model = RowModel([
            Col("端口", 90, lambda r: str(r.port), align=RIGHT, mono=True),
            Col("监听地址", 150, lambda r: r.addr, mono=True),
            Col("进程", 220, lambda r: r.proc or "—", tip=lambda r: r.proc),
            Col("PID", 88, lambda r: str(r.pid), align=RIGHT, color="faint", mono=True),
        ], parent=self)
        self.port_table = make_table(self.port_model, row_height=24)
        self.port_table.doubleClicked.connect(self._open_proc_dir)
        l.addWidget(self.port_table, 1)
        self.port_note = QLabel("只列 TCP 监听端口；双击一行可打开该进程所在目录")
        self.port_note.setProperty("role", "faint")
        l.addWidget(self.port_note)
        return w

    def _build_hosts(self) -> QWidget:
        w = QWidget()
        l = QVBoxLayout(w)
        l.setContentsMargins(0, 6, 0, 0)
        l.setSpacing(4)
        self.hosts_note = QLabel("")
        self.hosts_note.setProperty("role", "dim")
        self.hosts_note.setWordWrap(True)
        l.addWidget(self.hosts_note)
        self.hosts_model = RowModel([
            Col("指向地址", 148, lambda r: r[0], mono=True,
                color_fn=_hosts_color),
            Col("域名", 0, lambda r: r[1], stretch=True,
                color_fn=_hosts_color),
        ], parent=self)
        l.addWidget(make_table(self.hosts_model, row_height=23), 1)
        return w

    # ------------------------------------------------------------------ 测速
    def _build_speed(self) -> QWidget:
        w = QWidget()
        l = QVBoxLayout(w)
        l.setContentsMargins(0, 6, 0, 0)
        l.setSpacing(7)

        row = QHBoxLayout()
        row.setSpacing(8)
        cap = QLabel("节点")
        cap.setProperty("role", "faint")
        row.addWidget(cap)
        self.cmb_node = QComboBox()
        self.cmb_node.setMinimumWidth(196)
        self.cmb_node.addItem("自动（先试再选）", "auto")
        for n in netspeed.NODES:
            self.cmb_node.addItem(f"{n.name} · {n.where}", n.key)
        self.cmb_node.setToolTip(
            "「自动」会先给每个节点各拉 0.8 秒，按真实吞吐挑出前两名，"
            "再让它们各开 4 条连接同时压。\n"
            "各节点之间能差两个数量级（实测腾讯 308 / 华为 162 / 阿里云 5 Mbps），"
            "所以不能按延迟挑，只能试。")
        row.addWidget(self.cmb_node)

        cap2 = QLabel("时长")
        cap2.setProperty("role", "faint")
        row.addWidget(cap2)
        self.cmb_sec = QComboBox()
        for s in netspeed.SIZES:
            self.cmb_sec.addItem(f"{s} 秒", s)
        self.cmb_sec.setToolTip("越长越稳，流量也越大：千兆线 5 秒约 600 MB。")
        row.addWidget(self.cmb_sec)

        row.addStretch(1)
        self.btn_speed_stop = ghost_button("停止")
        self.btn_speed_stop.setEnabled(False)
        self.btn_speed_stop.clicked.connect(self._stop_speed)
        row.addWidget(self.btn_speed_stop)
        self.btn_speed = primary_button("开始测速", "speed")
        self.btn_speed.clicked.connect(self.start_speed)
        row.addWidget(self.btn_speed)
        l.addLayout(row)

        kpi = QHBoxLayout()
        kpi.setSpacing(9)
        self.k_dl = KpiCard("下载", "-", "尚未测速", "accent")
        self.k_ul = KpiCard("上传", "-", "", "ok")
        self.k_lat = KpiCard("网络延迟", "-", "TCP 握手 8 次取均", "dim")
        self.k_jit = KpiCard("抖动", "-", "越小越稳", "dim")
        for k in (self.k_dl, self.k_ul, self.k_lat, self.k_jit):
            kpi.addWidget(k, 1)
        l.addLayout(kpi)

        self.speed_bar = Bar(0.0, 6)
        l.addWidget(self.speed_bar)
        self.speed_note = QLabel("测速会下载/上传随机测试数据（不含任何本机信息），"
                                 "千兆线跑满 5 秒约 600 MB 流量")
        self.speed_note.setProperty("role", "faint")
        l.addWidget(self.speed_note)

        box = QSplitter(Qt.Horizontal)
        box.setChildrenCollapsible(False)
        self.chart = LineChart([("down", "下载", "accent"), ("up", "上传", "ok")],
                               y_max=100.0, auto=True, unit=" Mbps",
                               empty_text="还没有测速数据")
        self.chart.setMinimumHeight(112)
        box.addWidget(self.chart)
        box.addWidget(self._build_speed_table())
        box.setSizes([640, 460])
        l.addWidget(box, 1)

        self.speed_verdict = QLabel("")
        self.speed_verdict.setWordWrap(True)
        self.speed_verdict.setTextFormat(Qt.RichText)
        l.addWidget(self.speed_verdict)
        self._speed_placeholder()
        return w

    def _build_speed_table(self) -> QWidget:
        w = QWidget()
        l = QVBoxLayout(w)
        l.setContentsMargins(0, 0, 0, 0)
        l.setSpacing(4)
        h = QLabel("本次结果")
        h.setObjectName("H3")
        l.addWidget(h)
        self.speed_model = RowModel([
            Col("项目", 84, lambda r: r[0]),
            Col("值", 160, lambda r: r[1], mono=True, align=RIGHT,
                color_fn=lambda r: r[3]),
            Col("说明", 0, lambda r: r[2], color="faint", stretch=True,
                tip=lambda r: r[2]),
        ], parent=self)
        l.addWidget(make_table(self.speed_model, row_height=23), 1)
        return w

    def _speed_placeholder(self) -> None:
        if self.speed_rep is not None:
            return
        self.speed_model.set_rows([
            ("测速节点", "—", "各节点先试 0.8 秒，按吞吐排序", ""),
            ("并发连接", f"2 节点 × {netspeed.DL_THREADS} 线",
             f"上传 {netspeed.UP_THREADS} 线", ""),
            ("下载峰值", "—", "窗口内最高 1 秒速度", ""),
            ("达成率", "—", "相对网卡协商速率", ""),
        ])
        self.speed_verdict.setText(
            "<span style='color:%s'>点右上角「开始测速」跑一遍。</span>"
            "　<span style='color:%s'>下载走国内镜像站，上传走 Cloudflare；"
            "全程只收发随机数据，不上传任何本机信息。</span>"
            % (_tok("dim"), _tok("faint")))
        self._fit_verdict()

    def _on_tab_changed(self, i: int) -> None:
        """测速页有曲线，横向宽度够但高度不够时给它多分一点。

        只在切换页签时调整一次，不跟用户手动拖分隔条抢位置。
        """
        h = self.split.height()
        if h < 200:
            return
        if i == SPEED_TAB:
            if not getattr(self, "_speed_roomy", False):
                self.split.setSizes([int(h * 0.24), h - int(h * 0.24)])
                self._speed_roomy = True
        elif getattr(self, "_speed_roomy", False):
            self.split.setSizes([int(h * 0.55), h - int(h * 0.55)])
            self._speed_roomy = False

    # ------------------------------------------------------------------ 动作
    def showEvent(self, e):
        super().showEvent(e)
        if not getattr(self, "_sized", False) and self.split.height() > 240:
            h = self.split.height()
            self.split.setSizes([int(h * 0.55), int(h * 0.45)])
            self._sized = True
        if self.rep is None and not self._busy:
            self.diagnose()

    def shutdown(self) -> None:
        stop_all(self)

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        self._fit_verdict()

    def _fit_verdict(self) -> None:
        """富文本 QLabel 在 QVBoxLayout 里高度会被低估，长结论会被裁掉半行。

        Qt 对 wordWrap 的 QLabel 只按 sizeHint 给高度，而 sizeHint 假设的是
        「一行放得下」的宽度。所以拿真实宽度问一次 heightForWidth 再设最小高度。
        """
        lbl = getattr(self, "speed_verdict", None)
        if lbl is None:
            return
        w = lbl.width()
        if w < 80:
            return
        h = lbl.heightForWidth(w)
        if 0 < h and abs(h - lbl.minimumHeight()) > 1:
            lbl.setMinimumHeight(h)

    def restore(self, rep, speed=None) -> None:
        """主题切换整窗重建后回填上一次的报告，避免再跑 5 秒。"""
        if speed is not None:
            self.speed_rep = speed
            self._speed_done(speed, replay=True)
        if rep is None:
            return
        self._on_done(rep)

    def diagnose(self) -> None:
        if self._busy:
            return
        if self._speed_busy:
            # 测速本身就把带宽打满了，同时跑诊断只会两边都测不准
            self.status.emit("正在测速，等测完再诊断")
            return
        self._busy = True
        self.btn_run.setEnabled(False)
        self.status.emit("正在诊断网络…")

        def job(tick, stop):
            return netdiag.diagnose(stop, progress=tick)

        def on_tick(i, n, msg):
            if msg:
                self.status.emit(f"网络诊断：{msg}")

        run_bg(self, job, self._on_done, on_tick, self._on_fail)

    def _on_fail(self, msg: str) -> None:
        self._busy = False
        self.btn_run.setEnabled(True)
        self.status.emit(f"网络诊断失败：{msg}")

    # ------------------------------------------------------------------ 回填
    def _on_done(self, rep) -> None:
        self._busy = False
        self.btn_run.setEnabled(True)
        if rep is None:
            return
        self.rep = rep
        self.check_model.set_rows(rep.checks)
        self.nic_model.set_rows(rep.adapters)
        self.port_model.set_rows(rep.listeners)
        self.hosts_model.set_rows(rep.hosts_entries or [("", "没有自定义记录")])

        bad = rep.problems
        self.kpi_state.set_value(
            "正常" if not bad else f"{len(bad)} 项异常",
            f"共 {len(rep.checks)} 项检查 · {rep.elapsed:.1f}s",
            "ok" if not bad else "crit")

        pri = netdiag.primary(rep.adapters)
        if pri:
            self.kpi_nic.set_value(pri.speed_text if pri.speed else "已连接",
                                   f"{pri.ip} · {pri.title[:22]}")
        else:
            self.kpi_nic.set_value("无", "没有可用的默认网关", "warn")

        ext = next((c for c in rep.checks
                    if c.name == "外网连通" and c.ok), None)
        self.kpi_net.set_value(
            f"{ext.metric:.0f} ms" if ext else "-",
            ext.value if ext else "外网不可达",
            "" if ext else "crit")

        dns = next((c for c in rep.checks if c.name == "DNS 解析"), None)
        if dns and dns.ok is not None:
            self.kpi_dns.set_value(
                f"{dns.metric:.0f} ms" if dns.metric else "-",
                dns.value, "" if dns.ok else "crit")
        else:
            self.kpi_dns.set_value("-", dns.value if dns else "未检查", "crit")

        # hosts / 代理 说明
        bits = []
        if rep.proxy_enabled:
            bits.append(f"系统代理：{rep.proxy_server or '已开启'}")
        elif rep.proxy_server:
            bits.append(f"系统代理未启用（配置里保留 {rep.proxy_server}）")
        else:
            bits.append("系统代理未启用")
        if rep.proxy_pac:
            bits.append(f"PAC：{rep.proxy_pac}")
        bits.append(f"hosts 共 {len(rep.hosts_entries)} 条自定义记录")
        self.hosts_note.setText(" · ".join(bits)
                                + (f"\n{rep.hosts_warning}" if rep.hosts_warning else ""))

        self.port_note.setText(
            f"共 {len(rep.listeners)} 个 TCP 监听端口；双击一行可打开该进程所在目录")

        self.sub_label.setText(
            f"诊断于 {time.strftime('%H:%M:%S')} · 全程只读")
        self.foot.setText(
            f"结论：{'未发现异常' if not bad else '；'.join(c.name for c in bad)}"
            f"　（{rep.elapsed:.1f} 秒，{len(rep.checks)} 项检查）")
        self.status.emit(
            f"网络诊断完成：{len(rep.checks)} 项检查，"
            f"{'无异常' if not bad else str(len(bad)) + ' 项异常'}")

    def _open_proc_dir(self, index) -> None:
        if not index.isValid():
            return
        it = self.port_model.row_at(index.row())
        if not it or not it.pid:
            return
        path = netdiag._proc_path(it.pid)
        if path and os.path.isfile(path):
            _open(path)
        else:
            self.status.emit(f"PID {it.pid}（{it.proc or '未知进程'}）路径不可读")

    # ------------------------------------------------------------------ 测速
    def _link_info(self) -> tuple[float, bool]:
        """主网卡的协商速率（Mbps）与是不是无线 —— 用来判断「跑满了没有」。"""
        if not self.rep:
            return 0.0, False
        pri = netdiag.primary(self.rep.adapters)
        if pri is None:
            return 0.0, False
        medium = pri.medium or ""
        wifi = "802.11" in medium or "无线" in medium
        return (pri.speed / 1e6 if pri.speed else 0.0), wifi

    def start_speed(self) -> None:
        if self._speed_busy:
            return
        if self._busy:
            self.status.emit("正在诊断网络，等诊断完再测速")
            return
        node_key = self.cmb_node.currentData() or "auto"
        seconds = int(self.cmb_sec.currentData() or 5)
        link, wifi = self._link_info()
        self._wifi = wifi

        self._speed_busy = True
        self._live = []
        self.speed_rep = None
        self.btn_speed.setEnabled(False)
        self.btn_speed_stop.setEnabled(True)
        self.btn_run.setEnabled(False)
        self.cmb_node.setEnabled(False)
        self.cmb_sec.setEnabled(False)
        self.chart.clear()
        self.speed_bar.set_ratio(0.0, _tok("accent"))
        self.speed_note.setText("准备中…")
        self.speed_model.set_rows([])
        self.speed_verdict.setText(
            "<span style='color:%s'>正在测速，别关窗口…</span>" % _tok("dim"))
        self.k_dl.set_value("-", "测速中")
        self.k_ul.set_value("-", "")
        self.k_lat.set_value("-", "")
        self.k_jit.set_value("-", "")
        self.status.emit(f"正在测速（{seconds} 秒）…")

        def job(tick, stop_flag):
            def cb(frac, msg, inst):
                if inst:
                    self._live.append(inst)
                tick(int(frac * 100), 100, msg)
            return netspeed.run(node_key, seconds, on_progress=cb,
                                stop_flag=lambda: stop_flag[0], link_mbps=link)

        self._speed_worker = run_bg(self, job, self._speed_done,
                                    self._speed_tick, self._speed_fail)

    def _stop_speed(self) -> None:
        if self._speed_worker is not None:
            self._speed_worker.stop()
            self.speed_note.setText("正在停止（等这一次采样结束）…")
            self.status.emit("已请求停止测速")

    def _speed_tick(self, done: int, total: int, msg: str) -> None:
        self.speed_bar.set_ratio(done / total if total else 0.0, _tok("accent"))
        if msg:
            self.speed_note.setText(msg)
        # 后台线程塞进来的是「最近 1 秒的瞬时速度」，这里按阶段画到对应曲线上
        vals, self._live = self._live, []
        key = "down" if done < 63 else "up"
        for v in vals:
            self.chart.push({key: v})

    def _speed_fail(self, msg: str) -> None:
        self._speed_finish()
        self.speed_bar.set_ratio(0.0, _tok("crit"))
        self.speed_note.setText("测速失败：" + msg)
        self.speed_verdict.setText(
            "<span style='color:%s'>测速失败：%s</span>" % (_tok("crit"), msg))
        self.status.emit("测速失败：" + msg)

    def _speed_finish(self) -> None:
        self._speed_busy = False
        self._speed_worker = None
        self.btn_speed.setEnabled(True)
        self.btn_speed_stop.setEnabled(False)
        self.btn_run.setEnabled(True)
        self.cmb_node.setEnabled(True)
        self.cmb_sec.setEnabled(True)

    def _speed_done(self, rep, replay: bool = False) -> None:
        self._speed_finish()
        if rep is None:
            return
        self.speed_rep = rep

        if rep.down_mbps <= 0 and rep.errors:
            self.speed_bar.set_ratio(0.0, _tok("crit"))
            self.speed_note.setText(rep.errors[0])
            self.speed_model.set_rows([
                ("测速节点", rep.node or "—", "三个节点都没连上", "crit"),
                ("本次流量", netspeed.mb_text(rep.traffic_mb),
                 "失败前只发了探测请求", "faint"),
            ])
            self.speed_verdict.setText(
                "<span style='color:%s'>%s</span>" % (_tok("crit"), rep.errors[0]))
            self.status.emit("测速未完成")
            return

        self.speed_bar.set_ratio(1.0, _tok("ok"))
        self.speed_note.setText(
            f"{rep.node} · 用时 {rep.elapsed:.1f} 秒 · "
            f"流量约 {netspeed.mb_text(rep.traffic_mb)}"
            + ("　（已手动停止，数字只反映已跑完部分）" if rep.stopped else ""))

        # 曲线用报告里的完整序列重画：实时那一路按 250 ms 采样，比它更细
        self.chart.clear()
        for _t, d, u in rep.samples:
            self.chart.push({"down": d, "up": u})

        dg, d_role = rep.down_grade
        ug, u_role = rep.up_grade
        lg, l_role = rep.latency_grade
        jg, j_role = rep.jitter_grade
        self.k_dl.set_value(netspeed.mbps_text(rep.down_mbps), dg, _tok(d_role))
        self.k_ul.set_value(netspeed.mbps_text(rep.up_mbps) if rep.up_ok else "-",
                            ug if rep.up_ok else "未测",
                            _tok(u_role))
        self.k_lat.set_value(f"{rep.latency_ms:.0f} ms" if rep.latency_ms else "-",
                             lg, _tok(l_role))
        self.k_jit.set_value(f"{rep.jitter_ms:.1f} ms" if rep.jitter_ms else "-",
                             jg, _tok(j_role))

        ratio = rep.ratio
        if ratio:
            r_text = f"{ratio * 100:.0f}%"
            if rep.link_mbps >= 2500:
                # 2.5G/万兆网口 + 千兆宽带，比例只有三成多，标红就是误报
                r_role = "faint"
                r_note = f"网卡 {rep.link_mbps:.0f} Mbps，比宽带快，仅供参考"
            else:
                r_role = "ok" if ratio >= 0.7 else ("warn" if ratio >= 0.3 else "crit")
                r_note = f"相对网卡协商速率 {rep.link_mbps:.0f} Mbps"
        else:
            r_text, r_role, r_note = "—", "faint", "网卡协商速率未知"
        self.speed_model.set_rows([
            ("测速节点", rep.node,
             "各先试 0.8 秒，两节点并联" if len(rep.nodes) >= 2
             else "各先试 0.8 秒，只有 1 个可用", ""),
            ("并发连接", f"{len(rep.nodes) or 1} 节点 × {netspeed.DL_THREADS} 线",
             f"上传 {netspeed.UP_THREADS} 线", ""),
            ("下载峰值", netspeed.mbps_text(rep.down_peak), "窗口内最高 1 秒速度", ""),
            ("达成率", r_text, r_note, r_role),
        ])
        self._render_speed_verdict(rep)
        self.status.emit("测速完成：" + rep.headline)

    def _render_speed_verdict(self, rep) -> None:
        role = {"ok": "ok", "warn": "warn", "bad": "crit"}.get(rep.level, "dim")
        head = (f"<div><b style='color:{_tok(role)}'>{rep.headline}</b>"
                f"<span style='color:{_tok('dim')}'>　{rep.node}"
                f" · 用时 {rep.elapsed:.1f} 秒</span></div>")
        hints = rep.hints(wifi=self._wifi)
        if hints:
            body = "".join("<li style='margin-bottom:2px'>%s</li>" % h
                           for h in hints[:3])
            if len(hints) > 3:
                body += ("<li style='margin-bottom:2px;color:%s'>"
                         "另外还有 %d 条提示</li>" % (_tok("faint"), len(hints) - 3))
            head += ("<ul style='margin-top:5px;margin-bottom:0;margin-left:-18px'>"
                     + body + "</ul>")
        self.speed_verdict.setText(head)
        self._fit_verdict()

