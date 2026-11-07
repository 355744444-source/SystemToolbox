# -*- coding: utf-8 -*-
"""开机与登录诊断工作台（第九个工作台）。

这一页只回答三个问题：

1. **最近一次开机到底慢不慢** —— 跟本机自己的基线比，而不是跟「45 秒」比；
2. **慢在哪一段** —— 进系统前（引导 / 驱动 / 服务 / 配置）还是桌面出来后
   （启动项在抢资源）；这两段的处理手法完全不同；
3. **是谁拖的** —— 事件 101~110 点名的应用、驱动、服务、设备，重复出现的排前面。

图表没有引入任何绘图库：每次开机一根堆叠柱，四到七段颜色区分，
高度就是总耗时，基线画一条虚线 —— 一眼能看出「这次是不是比平时高」。
"""
from __future__ import annotations

from PyQt5.QtCore import QPoint, QPointF, QRectF, Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QColor, QPainter, QPen
from PyQt5.QtWidgets import (QFrame, QHBoxLayout, QLabel, QSplitter,
                             QTabWidget, QToolTip, QVBoxLayout, QWidget)

from .. import theme
from ..core import boot
from .tables import Col, RowModel, TablePane, run_bg, stop_all
from .widgets import KpiCard, ghost_button

# 堆叠柱每一段的颜色。顺序与 boot.PHASES 一致，末尾两段是残差与登录后后台。
SEGMENT_TOKENS = {
    "kernel": "info",
    "driver": "accent",
    "prefetch": "dim",
    "session": "warn",
    "desk": "ok",
    "rest": "faint",
    "post": "error",
}


def _token(name: str) -> str:
    return theme.current().get(name, theme.current()["text"])


class BootChart(QWidget):
    """开机耗时堆叠柱状图：一根柱 = 一次开机。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.records: list = []          # 旧 → 新
        self.baseline: int = 0
        self.rows: list = []             # [(rect, 文本)] 供悬浮提示用
        self.empty_text = "还没有开机记录"
        self.setMinimumHeight(190)
        self.setMouseTracking(True)

    def set_data(self, records: list, baseline: int) -> None:
        # 图从左到右是时间顺序，而引擎给的是新→旧
        self.records = list(reversed(records))[-24:]
        self.baseline = baseline
        self.update()

    def clear(self) -> None:
        self.records = []
        self.update()

    # ------------------------------------------------------------ 绘制
    def paintEvent(self, _ev):
        t = theme.current()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        w, h = self.width(), self.height()
        top = max([r.boot_ms for r in self.records] + [self.baseline, 1000])
        # 上界取整到「好看」的刻度，柱子不顶格
        for step in (10000, 20000, 30000, 45000, 60000, 90000, 120000,
                     180000, 240000, 300000, 600000, 900000, 1800000):
            if top <= step:
                top = step
                break

        f = self.font()
        f.setPointSizeF(max(7.0, f.pointSizeF() - 1.5))
        p.setFont(f)
        fm = p.fontMetrics()
        labels = ["%d 秒" % int(top * (3 - i) / 3 / 1000) for i in range(4)]
        pad_l = max(fm.horizontalAdvance(s) for s in labels) + 12
        pad_r, pad_t, pad_b = 12, 26, 22
        cw = max(1, w - pad_l - pad_r)
        ch = max(1, h - pad_t - pad_b)

        p.setPen(QPen(QColor(t["border"]), 1, Qt.DotLine))
        for i in range(4):
            y = pad_t + ch * i / 3.0
            p.drawLine(QPointF(pad_l, y), QPointF(pad_l + cw, y))
        p.setPen(QColor(t["faint"]))
        for i, txt in enumerate(labels):
            y = pad_t + ch * i / 3.0
            p.drawText(QPoint(int(pad_l - 8 - fm.horizontalAdvance(txt)),
                              int(y + 4)), txt)

        # 基线：本机中位数，虚线横穿整张图
        if self.baseline and self.records:
            by = pad_t + ch * (1 - min(1.0, self.baseline / top))
            p.setPen(QPen(QColor(t["accent_hover"]), 1, Qt.DashLine))
            p.drawLine(QPointF(pad_l, by), QPointF(pad_l + cw, by))
            txt = "基线 %s" % boot.dur_text(self.baseline)
            p.setPen(QColor(t["accent_hover"]))
            bx = pad_l + cw - fm.horizontalAdvance(txt) - 4
            p.drawText(QPoint(int(bx), int(by - 5)), txt)

        if not self.records:
            p.setPen(QColor(t["faint"]))
            p.drawText(QRectF(pad_l, pad_t, cw, ch), Qt.AlignCenter,
                       self.empty_text)
            p.end()
            return

        n = len(self.records)
        slot = cw / float(n)
        bw = max(6.0, min(38.0, slot * 0.62))
        self.rows = []
        for i, rec in enumerate(self.records):
            x = pad_l + slot * i + (slot - bw) / 2.0
            y = pad_t + ch
            for ph in rec.phases:
                if not ph.in_boot or ph.ms <= 0:
                    continue                        # 引导加载在 BootTime 之外
                seg = ch * (ph.ms / float(top))
                y -= seg
                p.fillRect(QRectF(x, y, bw, max(1.0, seg)),
                           QColor(_token(SEGMENT_TOKENS.get(ph.key, "dim"))))
            # 柱子顶端标出总时长（柱子太窄就隔一根标，避免糊成一片）
            if slot >= 26 or n <= 12:
                p.setPen(QColor(t["dim"]))
                txt = "%d" % round(rec.boot_ms / 1000.0)
                p.drawText(QPoint(int(x + bw / 2 - fm.horizontalAdvance(txt) / 2),
                                  int(y - 5)), txt)
            if i % max(1, n // 10) == 0 or i == n - 1:
                p.setPen(QColor(t["faint"]))
                lab = rec.when.strftime("%m-%d") if rec.when else "-"
                p.drawText(QPoint(int(x + bw / 2 - fm.horizontalAdvance(lab) / 2),
                                  int(pad_t + ch + 15)), lab)
            self.rows.append((QRectF(x, pad_t, bw, ch), rec))

        # 图例：一行排开，颜色与柱子一致
        lx = pad_l
        for key, label in (("kernel", "内核"), ("driver", "驱动与设备"),
                           ("prefetch", "预取自检"), ("session", "会话与服务"),
                           ("desk", "配置与桌面"), ("rest", "未细分"),
                           ("post", "登录后后台")):
            p.fillRect(int(lx), 7, 8, 8, QColor(_token(SEGMENT_TOKENS[key])))
            p.setPen(QColor(t["dim"]))
            p.drawText(QPoint(int(lx + 12), 15), label)
            lx += 12 + fm.horizontalAdvance(label) + 14
        p.end()

    def mouseMoveEvent(self, ev):
        pos = ev.pos()
        for rect, rec in self.rows:
            if pos.x() >= rect.left() - 4 and pos.x() <= rect.right() + 4:
                parts = ["%s · 总耗时 %s" % (rec.time_text, rec.boot_text)]
                for ph in rec.phases:
                    if ph.ms > 0:
                        parts.append("  %s %s" % (ph.label, ph.text))
                parts.append("  （点表头可按耗时排序；引导加载发生在 BootTime 之前，"
                             "不画进柱子）")
                QToolTip.showText(ev.globalPos(), "\n".join(parts), self)
                return
        QToolTip.hideText()


class BootPage(QWidget):
    status = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.scan_rep: boot.BootScan | None = None
        self._worker = None
        self._busy = False
        self._started = False
        self._build()
        # 读事件要 100ms~1s（通道大时更久），推到事件循环里，
        # 切页动画先出来，数据随后自己填上。定时器必须挂在本页上：
        # 页面被销毁后无宿主的 singleShot 仍会触发（PyQt5 会直接 abort）。
        self._boot = QTimer(self)
        self._boot.setSingleShot(True)
        self._boot.timeout.connect(self.reload)

    # ------------------------------------------------------------------ 构建
    def _build(self) -> None:
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 12, 0, 0)
        lay.setSpacing(9)

        top = QHBoxLayout()
        top.setSpacing(8)
        title = QLabel("开机与登录诊断")
        title.setProperty("role", "H2")
        top.addWidget(title)
        top.addSpacing(10)
        self.lbl_meta = QLabel("读取本机 Diagnostics-Performance 记录…")
        self.lbl_meta.setProperty("role", "faint")
        top.addWidget(self.lbl_meta)
        top.addStretch(1)
        self.btn_refresh = ghost_button("重新读取", "refresh")
        self.btn_refresh.setToolTip("重新读一遍本机性能诊断记录（开机 / 关机 / 睡眠）")
        self.btn_refresh.clicked.connect(self.reload)
        top.addWidget(self.btn_refresh)
        lay.addLayout(top)

        kpi = QHBoxLayout()
        kpi.setSpacing(9)
        self.k_last = KpiCard("最近一次开机", "-", "总耗时", "accent")
        self.k_base = KpiCard("本机基线", "-", "最近若干次的中位数", "dim")
        self.k_main = KpiCard("进系统前", "-", "Windows 徽标 → 桌面", "warn")
        self.k_post = KpiCard("桌面出来后", "-", "启动项还在忙", "info")
        self.k_slow = KpiCard("被点名的慢项", "-", "重复出现的排前面", "ok")
        for k in (self.k_last, self.k_base, self.k_main, self.k_post, self.k_slow):
            kpi.addWidget(k, 1)
        lay.addLayout(kpi)

        # 结论条：有记录就出现，讲一句「这次比平时快还是慢、慢在哪」
        self.verdict = QFrame()
        self.verdict.setProperty("role", "card")
        vb = QHBoxLayout(self.verdict)
        vb.setContentsMargins(14, 9, 14, 9)
        vb.setSpacing(14)
        self.v_level = QLabel("—")
        self.v_level.setObjectName("H1")
        self.v_level.setMinimumWidth(52)
        vb.addWidget(self.v_level)
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
        lay.addWidget(self.verdict)

        self.tabs = QTabWidget()
        self.tab_over = self._build_overview()
        self.tab_slow = self._build_slow()
        self.tab_hist = self._build_history()
        self.tab_misc = self._build_misc()
        self.tabs.addTab(self.tab_over, "时间花在哪")
        self.tabs.addTab(self.tab_slow, "慢项排行")
        self.tabs.addTab(self.tab_hist, "开机历史")
        self.tabs.addTab(self.tab_misc, "关机与睡眠")
        lay.addWidget(self.tabs, 1)

        self.foot = QLabel(
            "数据来自 Windows 自带的 Diagnostics-Performance 通道，全程只读："
            "不改启动项、不改电源计划、不动注册表。"
            "读这个通道需要管理员权限；通道里没有记录说明还没重启过。")
        self.foot.setProperty("role", "faint")
        self.foot.setWordWrap(True)
        lay.addWidget(self.foot)
        self.verdict.setVisible(False)

    def _build_overview(self) -> QWidget:
        wrap = QWidget()
        lay = QVBoxLayout(wrap)
        lay.setContentsMargins(0, 8, 0, 0)
        lay.setSpacing(8)

        # 图和表放分栏里：8 个阶段全列出来要 8×26 + 表头 + 筛选行 ≈ 280px，
        # 用固定 stretch 分的话表格只能露 5 行，最后三行被裁在框外（实测截图）。
        split = QSplitter(Qt.Vertical)
        split.setChildrenCollapsible(False)
        self.chart = BootChart(split)
        self.chart.setMinimumHeight(118)
        split.addWidget(self.chart)

        table_wrap = QWidget()
        tw = QVBoxLayout(table_wrap)
        tw.setContentsMargins(0, 0, 0, 0)
        tw.setSpacing(5)
        head = QLabel("最近一次开机的阶段拆解")
        head.setProperty("role", "H3")
        tw.addWidget(head)
        self.model_phase = RowModel([
            Col("阶段", 150, lambda r: r[0]),
            Col("本次耗时", 100, lambda r: r[1], mono=True,
                align=int(Qt.AlignRight | Qt.AlignVCenter),
                sort=lambda r: r[6].ms),
            Col("占开机比", 90, lambda r: r[2], mono=True,
                align=int(Qt.AlignRight | Qt.AlignVCenter),
                color="dim"),
            Col("较本机基线", 110, lambda r: r[3], mono=True,
                align=int(Qt.AlignRight | Qt.AlignVCenter),
                color_fn=lambda r: r[5]),
            Col("这一段在做什么", 0, lambda r: r[4], color="faint", stretch=True),
        ], parent=self)
        # 行高 24；最小高给 200（740 高的最小窗口下露 6 行），
        # 常规 900 高时由分栏分到 8 行全显示
        self.table_phase = TablePane(self.model_phase, row_height=24,
                                     placeholder="筛选阶段…")
        self.table_phase.setMinimumHeight(200)
        tw.addWidget(self.table_phase, 1)
        split.addWidget(table_wrap)
        split.setSizes([150, 300])
        lay.addWidget(split, 1)

        # 建议动作跟在图下面而不是单独开一页：三条动作是这一页的结论，
        # 藏在第五个页签里没人会点（试过，一页只有三行，剩下全是空的）。
        self.advice = QLabel("")
        self.advice.setWordWrap(True)
        self.advice.setTextFormat(Qt.RichText)
        lay.addWidget(self.advice)
        return wrap

    def _build_slow(self) -> QWidget:
        self.model_slow = RowModel([
            Col("类别", 64, lambda r: r[0], color="dim"),
            Col("名称", 210, lambda r: r[1]),
            Col("文件 / 服务名", 160, lambda r: r[2], color="dim"),
            Col("出现", 64, lambda r: r[3], mono=True,
                align=int(Qt.AlignRight | Qt.AlignVCenter),
                sort=lambda r: r[8].count),
            Col("超标合计", 92, lambda r: r[4], mono=True,
                align=int(Qt.AlignRight | Qt.AlignVCenter),
                sort=lambda r: r[8].total_degrade,
                color_fn=lambda r: r[8].level),
            Col("单次最多", 92, lambda r: r[5], mono=True,
                align=int(Qt.AlignRight | Qt.AlignVCenter),
                sort=lambda r: r[8].worst_ms, color="dim"),
            Col("判定", 90, lambda r: r[6], color_fn=lambda r: r[8].verdict_token),
            Col("路径 / 版本", 0, lambda r: r[7], color="faint", stretch=True,
                tip=lambda r: r[7]),
        ], parent=self)
        self.table_slow = TablePane(self.model_slow, row_height=26,
                                    placeholder="筛选应用 / 驱动 / 服务名…")
        return self.table_slow

    def _build_history(self) -> QWidget:
        self.model_hist = RowModel([
            Col("时间", 140, lambda r: r[0],
                sort=lambda r: r[8].when),
            Col("总耗时", 96, lambda r: r[1], mono=True,
                align=int(Qt.AlignRight | Qt.AlignVCenter),
                sort=lambda r: r[8].boot_ms),
            Col("进系统前", 96, lambda r: r[2], mono=True,
                align=int(Qt.AlignRight | Qt.AlignVCenter),
                sort=lambda r: r[8].main_ms, color="warn"),
            Col("桌面出来后", 100, lambda r: r[3], mono=True,
                align=int(Qt.AlignRight | Qt.AlignVCenter),
                sort=lambda r: r[8].post_ms, color="info"),
            Col("启动项", 70, lambda r: r[4], mono=True,
                align=int(Qt.AlignRight | Qt.AlignVCenter),
                sort=lambda r: r[8].apps, color="dim"),
            Col("系统标了降级", 100, lambda r: r[5], color_fn=lambda r:
                ("crit" if r[5] == "是" else "faint")),
            Col("进系统前占比", 110, lambda r: r[6], mono=True,
                align=int(Qt.AlignRight | Qt.AlignVCenter),
                sort=lambda r: r[8].main_ratio, color="dim"),
            Col("较本机基线", 0, lambda r: r[7], stretch=True,
                color_fn=lambda r: ("warn" if r[7].startswith("比基线慢")
                                    else "ok" if r[7].startswith("比基线快")
                                    else "faint")),
        ], parent=self)
        self.table_hist = TablePane(self.model_hist, row_height=26,
                                    placeholder="筛选日期…")
        return self.table_hist

    def _build_misc(self) -> QWidget:
        self.model_misc = RowModel([
            Col("类型", 70, lambda r: r[0], color="dim"),
            Col("环节", 100, lambda r: r[1]),
            Col("时间", 130, lambda r: r[2], sort=lambda r: r[6].when),
            Col("耗时", 96, lambda r: r[3], mono=True,
                align=int(Qt.AlignRight | Qt.AlignVCenter),
                sort=lambda r: r[6].ms,
                color_fn=lambda r: "warn" if r[6].ms >= 30000 else ""),
            Col("系统标了降级", 100, lambda r: r[4], color_fn=lambda r:
                ("crit" if r[4] == "是" else "faint")),
            Col("对象 / 说明", 0, lambda r: r[5], color="faint", stretch=True),
        ], parent=self)
        self.table_misc = TablePane(self.model_misc, row_height=26,
                                    placeholder="筛选类型…")
        return self.table_misc

    # ------------------------------------------------------------------ 生命周期
    def showEvent(self, e):
        super().showEvent(e)
        if not self._started:
            self._started = True
            self._boot.start(80)

    def shutdown(self) -> None:
        self._boot.stop()
        stop_all(self)

    # ------------------------------------------------------------------ 读取
    def reload(self) -> None:
        if self._busy:
            return
        self._busy = True
        self.btn_refresh.setEnabled(False)
        self.lbl_meta.setText("正在读开机诊断记录…")
        self.status.emit("正在读取开机诊断记录…")
        self._worker = run_bg(self, lambda tick, stop: boot.scan(),
                             self._done, None, self._failed)

    def _failed(self, msg: str) -> None:
        self._busy = False
        self._worker = None
        self.btn_refresh.setEnabled(True)
        self.lbl_meta.setText("读取失败：" + msg)
        self.status.emit("开机诊断读取失败：" + msg)

    def _done(self, rep: boot.BootScan) -> None:
        self._busy = False
        self._worker = None
        self.btn_refresh.setEnabled(True)
        self.scan_rep = rep
        self._render(rep)
        self.status.emit("开机诊断：" + rep.short_line)

    # ------------------------------------------------------------------ 渲染
    def _render(self, rep: boot.BootScan) -> None:
        self.lbl_meta.setText(
            "共 %d 条性能诊断事件 · %d 次开机 · 用时 %d ms%s"
            % (rep.event_total, rep.count, rep.cost_ms,
               "" if rep.fast_startup is None else
               " · 快速启动：%s" % ("已开启" if rep.fast_startup else "已关闭")))
        last = rep.latest
        if last is None:
            for k in (self.k_last, self.k_base, self.k_main, self.k_post):
                k.set_value("-", "没有记录")
            self.k_slow.set_value("—", "没有记录")
            self.chart.empty_text = rep.error or "还没有开机记录"
            self.chart.clear()
            self.model_phase.set_rows([])
            self.model_slow.set_rows([])
            self.model_hist.set_rows([])
            self.model_misc.set_rows([])
            self.advice.setText(
                "<span style='color:%s'>%s</span>"
                % (_token("warn"), rep.error or "通道里还没有开机记录"))
            self.verdict.setVisible(True)
            self.v_level.setText("—")
            self.v_head.setText(rep.error or "还没有开机记录")
            self.v_sub.setText("重启一次机器，再点右上角「重新读取」")
            return

        self.k_last.set_value(last.boot_text, last.time_text)
        base = rep.baseline_ms
        if base:
            diff = last.boot_ms - base
            self.k_base.set_value(boot.dur_text(base),
                                  ("比基线慢 %s" % boot.dur_text(diff)) if diff > 0
                                  else ("比基线快 %s" % boot.dur_text(-diff))
                                  if diff < 0 else "与基线持平",
                                  _token("warn") if diff > 0 else _token("ok"))
        else:
            self.k_base.set_value("-", "记录不足，暂无基线")
        self.k_main.set_value(last.main_text,
                              "占 %d%% · 徽标到桌面" % round(last.main_ratio * 100),
                              _token("warn") if last.main_ms >= boot.MAINPATH_LIMIT_MS
                              else None)
        self.k_post.set_value(
            last.post_text, "启动项 %d 个" % last.apps,
            _token("warn") if last.post_ms >= boot.POSTBOOT_LIMIT_MS else None)
        self.k_slow.set_value(str(len(rep.slow)),
                              "其中 %d 个重复出现" % sum(1 for s in rep.slow
                                                   if s.count >= 2))

        self.chart.empty_text = "还没有开机记录"
        self.chart.set_data(rep.records, base)
        self.model_phase.set_rows(boot.phase_rows(rep))
        self.model_slow.set_rows(boot.slow_rows(rep))
        self.model_hist.set_rows(boot.boot_rows(rep))
        self.model_misc.set_rows(boot.misc_rows(rep))
        # TablePane 的「共 N 行」不会随 set_rows 之外的路径刷新
        for pane in (self.table_phase, self.table_slow,
                     self.table_hist, self.table_misc):
            pane.refresh_hint()

        level_cn = {"crit": "很慢", "warn": "偏慢", "ok": "正常", "none": "—"}
        color = {"crit": _token("crit"), "warn": _token("warn"),
                 "ok": _token("ok")}.get(rep.level, _token("dim"))
        self.v_level.setText(level_cn.get(rep.level, "—"))
        self.v_level.setStyleSheet("color:%s;" % color)
        self.v_head.setText(rep.headline)
        if last.post_ms >= boot.POSTBOOT_LIMIT_MS and last.post_ms > last.main_ms:
            sub = ("桌面已经出来，但这之后又忙了 %s —— 这一段几乎全是启动项在"
                   "抢磁盘和 CPU，去「慢项排行」看类别为「应用」的那几条"
                   % last.post_text)
        elif last.main_ms >= boot.MAINPATH_LIMIT_MS:
            sub = ("进系统前就花了 %s —— 先看下面「时间花在哪」里最突出的那一段，"
                   "再决定是修驱动还是关服务" % last.main_text)
        else:
            sub = ("共 %d 次记录 · 平均 %s · 最慢一次 %s · 系统标了降级的 %d 次"
                   % (rep.count, boot.dur_text(rep.avg_ms),
                      boot.dur_text(rep.slowest.boot_ms) if rep.slowest else "—",
                      rep.degraded_count))
        self.v_sub.setText(sub)
        self.verdict.setVisible(True)

        tips = rep.advice
        if tips:
            items = "".join(
                "<li style='margin-bottom:3px'>%s</li>" % x for x in tips)
            self.advice.setText(
                "<div style='color:%s;margin-top:2px'><b>建议动作</b></div>"
                "<ul style='margin-top:4px;margin-left:-16px;color:%s'>%s</ul>"
                % (_token("text"), _token("dim"), items))
        else:
            self.advice.setText(
                "<span style='color:%s'>这台机器的开机耗时没有明显异常："
                "各段都在本机基线附近，也没有重复出现的慢项。</span>"
                % _token("dim"))
