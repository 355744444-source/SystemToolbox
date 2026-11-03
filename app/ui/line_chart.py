# -*- coding: utf-8 -*-
"""极简折线图控件（体检页的实时曲线用）。

没上 matplotlib / Chart.js：这一页只要「最近三分钟的趋势」，
引入依赖换不来什么，反而拖慢启动、还要处理打包。
"""
from __future__ import annotations

from PyQt5.QtCore import QPointF, QRectF, Qt
from PyQt5.QtGui import QColor, QPainter, QPainterPath, QPen
from PyQt5.QtWidgets import QWidget

from .. import theme
from .health_tabs.common import MAX_POINTS


class LineChart(QWidget):
    """极简折线图：固定 0~y_max（或自动缩放），画网格 + 多条折线 + 图例。

    没有用 matplotlib/Chart.js —— 这一页只需要「最近三分钟的趋势」，
    引入依赖换不来什么，反而拖慢启动、还要处理打包。
    """

    def __init__(self, series, y_max: float = 100.0, auto: bool = False,
                 unit: str = "%", parent=None, empty_text: str = ""):
        super().__init__(parent)
        self.series = series                # [(key, 标签, 主题色 token)]
        self.y_max = y_max
        self.auto = auto
        self.unit = unit
        self.empty_text = empty_text        # 还没有数据时居中显示的提示
        self.data = {k: [] for k, _l, _c in series}
        # 两个图叠在一页里，最小高度必须给足：低于 120 时 Y 轴刻度会挤成一团，
        # 曲线也分不出上下（实测截图里两张图各剩 90px，几乎看不出趋势）
        self.setMinimumHeight(140)

    def clear(self) -> None:
        for k in self.data:
            self.data[k] = []
        self.update()

    def push(self, values: dict) -> None:
        for k in self.data:
            v = float(values.get(k, 0.0))
            self.data[k].append(v)
            if len(self.data[k]) > MAX_POINTS:
                del self.data[k][0:len(self.data[k]) - MAX_POINTS]
        self.update()

    def _top(self) -> float:
        if not self.auto:
            return max(1.0, self.y_max)
        hi = 1.0
        for arr in self.data.values():
            if arr:
                hi = max(hi, max(arr))
        # 取「好看」的上界，避免曲线贴着顶端
        for step in (1, 2, 5, 10, 20, 50, 100, 200, 500, 1000, 2000, 5000,
                     10000, 20000, 50000, 100000):
            if hi <= step:
                return float(step)
        return hi

    def paintEvent(self, ev):
        t = theme.current()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        w, h = self.width(), self.height()
        top = self._top()
        n = max((len(a) for a in self.data.values()), default=0)

        f = self.font()
        f.setPointSizeF(max(7.0, f.pointSizeF() - 1.5))
        p.setFont(f)
        fm = p.fontMetrics()
        labels = []
        for i in range(4):
            val = top * (3 - i) / 3.0
            labels.append(f"{val:.0f}{self.unit}" if top >= 10
                          else f"{val:.1f}{self.unit}")
        # 刻度文字宽度必须实测：猜字符宽度会把 "5000 KB/s" 裁成 "30 KB/s"
        pad_l = max(fm.horizontalAdvance(s) for s in labels) + 10
        pad_r, pad_t, pad_b = 10, 24, 18
        cw, ch = max(1, w - pad_l - pad_r), max(1, h - pad_t - pad_b)

        # 网格 + 纵轴刻度（4 条线足够，再多就是噪声）
        p.setPen(QPen(QColor(t["border"]), 1, Qt.DotLine))
        for i in range(4):
            y = pad_t + ch * i / 3.0
            p.drawLine(QPointF(pad_l, y), QPointF(pad_l + cw, y))
        p.setPen(QColor(t["faint"]))
        for i, txt in enumerate(labels):
            y = pad_t + ch * i / 3.0
            p.drawText(QPointF(pad_l - 6 - fm.horizontalAdvance(txt), y + 4), txt)

        if n < 2 and self.empty_text:
            p.setPen(QColor(t["faint"]))
            p.drawText(QRectF(pad_l, pad_t, cw, ch), Qt.AlignCenter, self.empty_text)

        # 折线：数据没填满窗口时铺满全宽，填满后按固定窗口滚动
        denom = (MAX_POINTS - 1) if n >= MAX_POINTS else max(1, n - 1)
        for idx, (key, _label, token) in enumerate(self.series):
            arr = self.data.get(key) or []
            if len(arr) < 2:
                continue
            color = QColor(t.get(token, t["accent"]))
            path = QPainterPath()
            for i, v in enumerate(arr):
                x = pad_l + cw * (i / denom)
                y = pad_t + ch * (1 - min(1.0, max(0.0, v / top)))
                if i == 0:
                    path.moveTo(x, y)
                else:
                    path.lineTo(x, y)
            # 第一条曲线下方加淡填充：多条线交叠时靠它区分「谁在上谁在下」
            if idx == 0:
                fill = QPainterPath(path)
                last_x = pad_l + cw * ((len(arr) - 1) / denom)
                fill.lineTo(last_x, pad_t + ch)
                fill.lineTo(pad_l, pad_t + ch)
                fill.closeSubpath()
                c2 = QColor(color)
                c2.setAlpha(26)
                p.fillPath(fill, c2)
            p.setPen(QPen(color, 1.7))
            p.drawPath(path)
            # 图例（放在顶部留白里，不会被曲线压住）
            lx = pad_l + 2 + idx * 96
            p.fillRect(int(lx), 7, 8, 8, color)
            p.setPen(QColor(t["dim"]))
            p.drawText(QPointF(lx + 12, 15), _label)
        p.end()
