# -*- coding: utf-8 -*-
"""系统体检工作台（第七）：十个页签，全部**只读**。

不装驱动、不改安全策略、不动系统设置、不修组件、不碰硬盘、不改 PATH。

结构：窗口骨架留在这里（KPI、页签容器、采样节奏、整机结论、报告导出），
十个页签的构建与填充在 `health_tabs/` 下按页签一个 mixin —— 一个文件
一千七百行没人看得下去，但**状态仍在同一个 `self` 上**，不做跨模块穿针。

九项静态检查跑完后用 `core.health.fold()` 折出整机结论（不重新扫描），
命令行 `--scan` 用的是同一套判断。
"""
from __future__ import annotations

import os
import time
from datetime import datetime

from PyQt5.QtCore import QTimer, pyqtSignal
from PyQt5.QtWidgets import (QFileDialog, QFrame, QHBoxLayout, QLabel, QTabWidget,
                             QVBoxLayout, QWidget)

from ..core import devices as dv
from ..core import drivers as drv
from ..core import dumpcfg as dcfg
from ..core import pathenv as pe
from ..core import hwevent
from ..core import perf
from ..core import procs
from ..core import security
from ..core import smart as smartm
from ..core import upgrade as up
from ..core import component
from ..core import health
from ..core import report
from .. import theme
from . import notice
from .tables import stop_all
from .widgets import KpiCard, ghost_button, primary_button
from .health_tabs.common import MAX_POINTS
from .health_tabs import (DevTabMixin, UpgradeTabMixin, PerfTabMixin,
                          SecurityTabMixin, ComponentTabMixin, DiskTabMixin,
                          HwTabMixin, DumpCfgTabMixin, PathEnvTabMixin,
                          DriversTabMixin)


# mixin 只是「换个文件放同一批方法」，真正的 QWidget 底必须排在最后：
# MRO 走到 QWidget 才会接住 __init__ 里的 super().__init__(parent)
class HealthPage(DevTabMixin, UpgradeTabMixin, PerfTabMixin,
                 SecurityTabMixin, ComponentTabMixin, DiskTabMixin,
                 HwTabMixin, DumpCfgTabMixin, PathEnvTabMixin,
                 DriversTabMixin, QWidget):
    status = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._busy = {"dev": False, "up": False, "sec": False,
                      "file": False, "size": False, "smart": False,
                      "hw": False, "dc": False, "env": False, "drv": False}
        self._dev: list[dv.Device] = []
        self._drv: list[dv.Driver] = []
        self.dev_stats: dv.Stats | None = None
        self.up_rep: up.Report | None = None
        self.sec_rep: security.Report | None = None
        self.comp_rep: component.Report | None = None
        self.disk_rep: smartm.Report | None = None
        self.hw_rep: hwevent.Report | None = None
        self.dc_snap: dcfg.Snap | None = None
        self.dc_findings: list = []
        self.env_rep: pe.Report | None = None
        self.env_findings: list = []
        self.drv_rep: drv.Report | None = None
        self.drv_findings: list = []
        self._drv_rows: list = []
        self._sampler: perf.Sampler | None = None
        self._psampler: procs.ProcSampler | None = None
        self._prep: procs.ProcReport | None = None
        self._proc_tick = 0
        self._winsxs: tuple[int, int, bool] | None = None
        self._pts: list[perf.Point] = []
        # 整机结论：九个 load 都跑完才合成，用来出报告（不再重扫一遍）
        self.health_rep: health.HealthReport | None = None
        self._done_keys: set = set()
        self._scan_t0 = 0.0
        self._build()
        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._tick)
        # 首次打开时把 6 个扫描错开 120ms 起步，避免和 showEvent 的布局抢时间
        self._boot = QTimer(self)
        self._boot.setSingleShot(True)
        self._boot.timeout.connect(self.run_all)
        # 合成长结论要等最后一个 done() 写完自己的 self.xxx_rep 才能做，
        # 所以延后一拍（挂在页面上，页面销毁后不会再触发）
        self._compose_timer = QTimer(self)
        self._compose_timer.setSingleShot(True)
        self._compose_timer.timeout.connect(self._compose_report)

    def _build(self) -> None:
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 10, 16, 10)
        lay.setSpacing(9)

        top = QHBoxLayout()
        top.setSpacing(8)
        title = QLabel("系统体检")
        title.setObjectName("H2")
        top.addWidget(title)
        self.sub_label = QLabel("设备驱动 · 升级条件 · 实时占用 · 安全基线 · 系统文件 · "
                                "磁盘 SMART · 硬件错误 · 崩溃转储 · 环境变量 · 驱动体检"
                                "（全部只读，不改任何设置）")
        self.sub_label.setProperty("role", "faint")
        top.addWidget(self.sub_label)
        top.addStretch(1)
        self.btn_run = primary_button("全部体检", "search")
        self.btn_run.clicked.connect(self.run_all)
        top.addWidget(self.btn_run)
        lay.addLayout(top)

        kpis = QHBoxLayout()
        kpis.setSpacing(8)
        self.kpi_dev = KpiCard("设备与驱动", "-", "尚未检查")
        self.kpi_up = KpiCard("升级体检", "-", "尚未检查")
        self.kpi_disk = KpiCard("磁盘健康", "-", "尚未检查")
        self.kpi_hw = KpiCard("硬件错误", "-", "尚未检查")
        self.kpi_sec = KpiCard("安全基线", "-", "尚未检查")
        self.kpi_file = KpiCard("系统文件", "-", "尚未检查")
        self.kpi_live = KpiCard("实时占用", "-", "启动中")
        self.kpi_dc = KpiCard("崩溃转储", "-", "尚未检查")
        for k in (self.kpi_dev, self.kpi_up, self.kpi_disk, self.kpi_hw,
                  self.kpi_sec, self.kpi_file, self.kpi_live, self.kpi_dc):
            kpis.addWidget(k, 1)
        lay.addLayout(kpis)

        # 整机结论条：九项全部跑完才出现，出现就代表「这份结论可以导出了」
        self.verdict = QFrame()
        self.verdict.setProperty("role", "card")
        self.verdict.setVisible(False)
        vb = QHBoxLayout(self.verdict)
        vb.setContentsMargins(14, 9, 14, 9)
        vb.setSpacing(14)
        self.v_score = QLabel("—")
        self.v_score.setObjectName("H1")
        self.v_score.setMinimumWidth(34)
        vb.addWidget(self.v_score)
        vtxt = QVBoxLayout()
        vtxt.setSpacing(1)
        self.v_head = QLabel("")
        self.v_head.setObjectName("H3")
        self.v_head.setWordWrap(True)
        self.v_sub = QLabel("")
        self.v_sub.setProperty("role", "faint")
        self.v_sub.setWordWrap(True)
        vtxt.addWidget(self.v_head)
        vtxt.addWidget(self.v_sub)
        vb.addLayout(vtxt, 1)
        self.btn_report = ghost_button("导出报告", "export")
        self.btn_report.setToolTip("把这次体检的结论导出为单文件 HTML 或 Excel")
        self.btn_report.clicked.connect(self._export_report)
        vb.addWidget(self.btn_report)
        lay.addWidget(self.verdict)

        self.tabs = QTabWidget()
        self.tab_dev = self._build_dev()
        self.tab_up = self._build_up()
        self.tab_perf = self._build_perf()
        self.tab_sec = self._build_sec()
        self.tab_file = self._build_file()
        self.tab_disk = self._build_disk()
        self.tab_hw = self._build_hw()
        self.tab_dc = self._build_dumpcfg()
        self.tab_env = self._build_pathenv()
        self.tab_drv = self._build_drvtab()
        self.tabs.addTab(self.tab_dev, "设备与驱动")
        self.tabs.addTab(self.tab_up, "Win11 升级体检")
        self.tabs.addTab(self.tab_perf, "性能采样")
        self.tabs.addTab(self.tab_sec, "安全基线")
        self.tabs.addTab(self.tab_file, "系统文件")
        self.tabs.addTab(self.tab_disk, "磁盘 SMART")
        self.tabs.addTab(self.tab_hw, "硬件错误")
        self.tabs.addTab(self.tab_dc, "崩溃转储")
        self.tabs.addTab(self.tab_env, "环境变量")
        self.tabs.addTab(self.tab_drv, "驱动体检")
        lay.addWidget(self.tabs, 1)

        self.foot = QLabel("十项体检都只读取现状：不装驱动、不改安全策略 / 计划任务、"
                           "不修组件、不碰硬盘、不改启动和恢复设置、不改 PATH。"
                           "驱动签名结果按文件大小 + 修改时间缓存，冷扫 2 秒、热扫 0.5 秒。"
                           "性能曲线每秒采一个点，保留最近 3 分钟。")
        self.foot.setProperty("role", "faint")
        self.foot.setWordWrap(True)
        lay.addWidget(self.foot)

    def showEvent(self, e):
        super().showEvent(e)
        if not getattr(self, "_started", False):
            self._started = True
            # 必须用「挂在本页上的 QTimer」，不能用 QTimer.singleShot：
            # 后者没有宿主对象，页面被销毁后回调照样会触发，PyQt5 对
            # 「槽里访问已析构对象」是直接 abort（切主题时必崩）。
            self._boot.start(120)
        if not self._timer.isActive() and not getattr(self, "_paused", False):
            self._ensure_sampler()
            self._timer.start()

    def shutdown(self) -> None:
        self._boot.stop()
        self._timer.stop()
        if self._sampler is not None:
            self._sampler.close()
            self._sampler = None
        self._psampler = None
        stop_all(self)

    def _ensure_sampler(self) -> None:
        if self._sampler is None:
            self._sampler = perf.Sampler()
            self._sampler.prime()
        if self._psampler is None:
            self._psampler = procs.ProcSampler()
            self._psampler.sample()          # 先取一次基准，第二个点才有 CPU

    def run_all(self) -> None:
        # 重新体检 = 结论重算；先收掉上一次的结论条，免得看着像这次结果
        self.health_rep = None
        self._done_keys.clear()
        self.verdict.setVisible(False)
        self._scan_t0 = time.perf_counter()
        self.load_devices()
        self.load_upgrade()
        self.load_security()
        self.load_component()
        self.load_smart()
        self.load_hw()
        self.load_dumpcfg()
        self.load_pathenv()
        self.load_drivers()

    def _section_done(self, key: str) -> None:
        """每个 load 的收尾处调一次。九项齐了才合成结论，中途不做半成品。"""
        self._done_keys.add(key)
        if len(self._done_keys) < len(health.KEYS):
            return
        # 必须延后一拍：done() 的写法是「先置标志、后写 self.xxx_rep」，
        # 同步合成会拿到「自己还没赋值」的半成品（实测 smart 被判「本次未运行」）
        self._compose_timer.start(0)

    def _raw_parts(self) -> dict:
        """把九个 load 已经拿到的原始报告原样递过去 —— 这一层不重新扫描。

        少一项就整条结论不出（`_section_done` 已经保证不会走到这里）。
        """
        return {
            "devices": health.DevParts(self._dev, self._drv, self.dev_stats),
            "upgrade": self.up_rep,
            "security": self.sec_rep,
            "component": self.comp_rep,
            "smart": self.disk_rep,
            "hw": self.hw_rep,
            "dumpcfg": health.DumpParts(self.dc_snap, self.dc_findings),
            "pathenv": self.env_rep,
            "drivers": self.drv_rep,
        }

    def _compose_report(self) -> None:
        cost = int((time.perf_counter() - self._scan_t0) * 1000) \
            if self._scan_t0 else 0
        self.health_rep = health.fold(self._raw_parts(), cost_ms=cost)
        self._show_verdict(self.health_rep, cost)
        self.status.emit("体检完成：%s（健康评分 %d）"
                         % (self.health_rep.headline, self.health_rep.score))

    def _show_verdict(self, rep, cost_ms: int) -> None:
        t = theme.current()
        # 内联上色必须显式声明透明底，否则 QLabel 会自带一层底色（KpiCard 同理）
        self.v_score.setStyleSheet(
            "color:%s; background:transparent;" % t.get(rep.level, t["text"]))
        self.v_score.setText(str(rep.score))
        self.v_head.setText(f"健康评分 {rep.score} · {rep.headline}")
        self.v_sub.setText(
            "检查 %d 项 · 严重 %d 项 · 注意 %d 项 · 并行耗时 %.1f 秒"
            % (len(rep.sections), rep.crit, rep.warn, cost_ms / 1000.0))
        self.verdict.setVisible(True)

    def _export_report(self) -> None:
        rep = self.health_rep
        if rep is None:
            notice.note(self, "info", "九项体检还没跑完，跑完才能导出")
            return
        path, chosen = QFileDialog.getSaveFileName(
            self, "保存体检报告",
            os.path.join(os.path.expanduser("~"), "Desktop",
                         f"系统体检报告_{datetime.now():%Y%m%d_%H%M}.html"),
            "网页报告 (*.html);;Excel 工作簿 (*.xlsx)")
        if not path:
            return
        # 用户可能只在选择器里换了类型、没改文件名 —— 按选中的类型补后缀
        if chosen and "xlsx" in chosen.lower() and not path.lower().endswith(".xlsx"):
            path += ".xlsx"
        elif chosen and "html" in chosen.lower() and not path.lower().endswith(".html"):
            path += ".html"
        try:
            if path.lower().endswith(".xlsx"):
                report.export_health_xlsx(path, rep)
            else:
                report.export_health_html(path, rep)
        except Exception as exc:            # noqa: BLE001
            notice.note(self, "error", "导出失败：%s" % exc)
            return
        self.status.emit("体检报告已保存：" + path)
        notice.note(self, "ok", "体检报告已导出：%s" % os.path.basename(path))

    def _fail(self, msg: str) -> None:
        self._busy = {k: False for k in self._busy}
        self.status.emit(f"体检失败：{msg}")

    def _toggle_pause(self) -> None:
        if self._timer.isActive():
            self._timer.stop()
            self._paused = True
            self.btn_pause.setText("继续采样")
            self.status.emit("已暂停采样")
        else:
            self._paused = False
            self._ensure_sampler()
            self._timer.start()
            self.btn_pause.setText("暂停采样")
            self.status.emit("已恢复采样")

    def _reset_chart(self) -> None:
        self._pts.clear()
        self.chart.clear()
        self.chart_net.clear()
        self.perf_note.setText("已清空曲线，继续采样中…")

    def _tick(self) -> None:
        self._ensure_sampler()
        try:
            pt = self._sampler.sample()
        except Exception:                    # noqa: BLE001
            return
        if not pt.ts or (pt.cpu == 0 and pt.mem == 0 and pt.disk == 0
                         and pt.net_total == 0):
            return                           # 基准点，不算一个样本
        self._pts.append(pt)
        if len(self._pts) > MAX_POINTS:
            del self._pts[0:len(self._pts) - MAX_POINTS]
        self.chart.push({"cpu": pt.cpu, "mem": pt.mem, "disk": pt.disk})
        self.chart_net.push({"net_in": pt.net_in, "net_out": pt.net_out})

        self.live_labels["cpu"].setText(f"{pt.cpu:.0f}%")
        self.live_labels["mem"].setText(
            f"{pt.mem:.0f}%" if pt.mem_total_gb <= 0
            else f"{pt.mem_used_gb:.1f}/{pt.mem_total_gb:.0f} GB")
        self.live_labels["disk"].setText(f"{pt.disk:.0f}%")
        self.live_labels["net"].setText(f"{pt.net_total:.0f} KB/s")
        self.kpi_live.set_value(
            f"{pt.cpu:.0f}%",
            f"内存 {pt.mem:.0f}% · 磁盘 {pt.disk:.0f}% · 网络 {pt.net_total:.0f} KB/s",
            "warn" if pt.cpu > 85 or pt.mem > 90 else "")

        sm = perf.summarize(self._pts)
        self.perf_note.setText(
            f"已采样 {sm['n']} 个点（{sm['secs']:.0f} 秒）· "
            f"CPU 均值 {sm['cpu_avg']:.0f}% / 峰值 {sm['cpu_max']:.0f}% · "
            f"内存均值 {sm['mem_avg']:.0f}% / 峰值 {sm['mem_max']:.0f}% · "
            f"磁盘峰值 {sm['disk_max']:.0f}% · 网络峰值 {sm['net_max']:.0f} KB/s")

        # 进程表每 2 秒刷一次：每秒重建几十行会让选中行和视口一直跳
        self._proc_tick += 1
        if self._proc_tick % 2 == 1 and self._psampler is not None:
            try:
                self._prep = self._psampler.sample()
            except Exception:                # noqa: BLE001
                self._prep = None
            self._fill_procs()
