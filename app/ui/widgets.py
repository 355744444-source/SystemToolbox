# -*- coding: utf-8 -*-
"""通用控件：图标、KPI 卡、等级徽标、诊断发现卡、拖放区、滚动列。"""
from __future__ import annotations

import math
import os
import re
import sys

from PyQt5.QtCore import QPointF, QRectF, QSize, Qt, pyqtSignal
from PyQt5.QtGui import (QBrush, QColor, QFont, QFontMetrics, QIcon, QPainter,
                         QPainterPath, QPen, QPixmap)
from PyQt5.QtWidgets import (QFrame, QHBoxLayout, QLabel, QPushButton,
                             QScrollArea, QSizePolicy, QVBoxLayout, QWidget)

from .. import theme

SEV_COLOR = {
    "临界": "crit",
    "高": "error",
    "中": "warn",
    "提示": "info",
    "正常": "ok",
}

#: 每个工作台页面的根布局内边距，**唯一出处**。
#:
#: 原来 9 个页面各写各的：日志/蓝屏 `18,14`、清理/残留 `18,12`、优化/网络/体检
#: `16,10`、U 盘/开机 `0,12`。后果是切页时内容整体左右跳 18px、上下跳 4px，
#: 而 `0,12` 那两页的 KPI 卡直接压在导航栏的分隔线上（截图实测贴着边）。
#: 统一成一组之后，页与页之间只剩内容差异，没有位置差异。
PAGE_MARGINS = (18, 14, 18, 14)

# 内核侧（analysis / dumpcfg / pathenv）用的是英文等级码，界面上一律显示中文。
# 早期 FindingCard 直接把 severity 当文案，结果卡片上写着 "info" / "warn"。
SEV_CN = {
    "crit": "临界",
    "error": "高",
    "warn": "中",
    "info": "提示",
    "ok": "正常",
    "fatal": "临界",
}


def sev_cn(sev: str) -> str:
    """把 crit / warn / info / ok 这类等级码转成中文徽章文案。"""
    if not sev:
        return "提示"
    if sev in SEV_COLOR:
        return sev
    return SEV_CN.get(sev, "提示")

# 「数值 + 空格 + 短单位」才拆。单位只认 ASCII（Gbps / MB / ms / % / ℃ 的 ASCII 写法），
# 这样「正常」「MEMORY.DMP」「14,882」这类不会被误拆。
_UNIT_RE = re.compile(r"^([-+]?\d[\d,]*(?:\.\d+)?)\s+([A-Za-z%/℃][^\s]{0,5})$")


def split_value(text: str) -> tuple[str, str]:
    """把 KPI 值拆成 (数值, 单位)；拆不出来时单位为空串、数值原样返回。

    "2.5 Gbps" → ("2.5", "Gbps")　"163 ms" → ("163", "ms")　"23.0 MB" → ("23.0", "MB")
    "14,882" / "-" / "正常" / "MEMORY.DMP" / "5.0s"（无空格）→ 整串当数值
    """
    m = _UNIT_RE.match((text or "").strip())
    if not m:
        return text, ""
    return m.group(1), m.group(2)


def app_icon() -> QIcon:
    """应用图标（任务栏 / Alt-Tab / 标题栏都读它）。

    打包成单文件后 assets 会在 `sys._MEIPASS` 下解出来，所以必须按
    `_MEIPASS → exe 同目录 → 源码目录` 的顺序找；找不到返回空 QIcon，
    调用方据此不覆盖窗口图标（否则 Windows 会画成占位符）。
    """
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    roots = []
    meipass = getattr(sys, "_MEIPASS", "")
    if meipass:
        roots.append(meipass)
    if getattr(sys, "frozen", False):
        roots.append(os.path.dirname(os.path.abspath(sys.argv[0])))
    roots.append(os.path.dirname(here))
    roots.append(os.path.dirname(os.path.dirname(here)))
    for base in roots:
        for name in ("app.ico", "app.png"):
            p = os.path.join(base, "assets", name)
            if os.path.isfile(p):
                ic = QIcon(p)
                if not ic.isNull():
                    return ic
    return QIcon()



def alpha(hex_color: str, a: float = 0.16) -> str:
    c = QColor(hex_color)
    return f"rgba({c.red()}, {c.green()}, {c.blue()}, {a:.2f})"


def _apply_families(f: QFont, families) -> QFont:
    """优先用 setFamilies 做逐字回落（Qt 5.13+），老版本退回首选项。"""
    if hasattr(f, "setFamilies"):
        f.setFamilies(list(families))
    return f


def ui_font(size: int = 9, bold: bool = False) -> QFont:
    """界面字体：微软雅黑。"""
    f = QFont(theme.FONT_FAMILY)
    f.setPointSize(size)
    f.setBold(bold)
    return _apply_families(f, theme.FONT_FAMILIES)


def mono_font(size: int = 9, bold: bool = False) -> QFont:
    """技术数据字体：等宽，中文自动回落微软雅黑。"""
    f = QFont(theme.MONO_FAMILY)
    f.setPointSize(size)
    f.setBold(bold)
    return _apply_families(f, theme.MONO_FAMILIES)


# ------------------------------------------------------------------ 矢量图标
def _draw(kind: str, p: QPainter, color: QColor) -> None:
    pen = QPen(color, 1.7, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
    p.setPen(pen)
    if kind == "log":
        p.drawRoundedRect(QRectF(4.5, 3.5, 15, 17), 2.2, 2.2)
        for i, y in enumerate((8.2, 12.0, 15.8)):
            p.drawLine(QPointF(8.2, y), QPointF(16.0, y))
        p.setBrush(QBrush(color))
        for y in (8.2, 12.0, 15.8):
            p.drawEllipse(QPointF(6.6, y), 0.55, 0.55)
    elif kind == "crash":
        path = QPainterPath()
        path.moveTo(12, 3.6)
        path.lineTo(21.0, 19.4)
        path.lineTo(3.0, 19.4)
        path.closeSubpath()
        p.drawPath(path)
        p.drawLine(QPointF(12, 9.0), QPointF(12, 14.2))
        p.setBrush(QBrush(color))
        p.drawEllipse(QPointF(12, 16.9), 0.75, 0.75)
    elif kind == "folder":
        path = QPainterPath()
        path.moveTo(3.2, 6.6)
        path.lineTo(9.4, 6.6)
        path.lineTo(11.2, 9.0)
        path.lineTo(20.8, 9.0)
        path.lineTo(20.8, 19.0)
        path.lineTo(3.2, 19.0)
        path.closeSubpath()
        p.drawPath(path)
    elif kind == "play":
        path = QPainterPath()
        path.moveTo(7.5, 4.8)
        path.lineTo(19.0, 12.0)
        path.lineTo(7.5, 19.2)
        path.closeSubpath()
        p.setBrush(QBrush(color))
        p.setPen(Qt.NoPen)
        p.drawPath(path)
    elif kind == "stop":
        p.setBrush(QBrush(color))
        p.setPen(Qt.NoPen)
        p.drawRoundedRect(QRectF(6.5, 6.5, 11, 11), 2, 2)
    elif kind == "export":
        p.drawLine(QPointF(12, 3.5), QPointF(12, 13.5))
        p.drawLine(QPointF(8.2, 9.6), QPointF(12, 13.5))
        p.drawLine(QPointF(15.8, 9.6), QPointF(12, 13.5))
        p.drawLine(QPointF(4.5, 15.2), QPointF(4.5, 20.0))
        p.drawLine(QPointF(4.5, 20.0), QPointF(19.5, 20.0))
        p.drawLine(QPointF(19.5, 15.2), QPointF(19.5, 20.0))
    elif kind == "moon":
        path = QPainterPath()
        path.moveTo(15.6, 3.4)
        path.arcTo(QRectF(4.0, 3.0, 16.0, 18.0), 68, 232)
        path.arcTo(QRectF(8.4, 6.4, 14.0, 14.0), 300, -220)
        path.closeSubpath()
        p.setBrush(QBrush(color))
        p.setPen(Qt.NoPen)
        p.drawPath(path)
    elif kind == "sun":
        p.drawEllipse(QPointF(12, 12), 4.0, 4.0)
        for dx, dy in ((0, -8.6), (0, 8.6), (-8.6, 0), (8.6, 0),
                       (-6.1, -6.1), (6.1, -6.1), (-6.1, 6.1), (6.1, 6.1)):
            p.drawLine(QPointF(12 + dx * 0.72, 12 + dy * 0.72),
                       QPointF(12 + dx, 12 + dy))
    elif kind == "search":
        p.drawEllipse(QPointF(10.8, 10.8), 5.6, 5.6)
        p.drawLine(QPointF(15.0, 15.0), QPointF(20.2, 20.2))
    elif kind == "info":
        p.drawEllipse(QPointF(12, 12), 8.6, 8.6)
        p.drawLine(QPointF(12, 11.0), QPointF(12, 16.6))
        p.setBrush(QBrush(color))
        p.setPen(Qt.NoPen)
        p.drawEllipse(QPointF(12, 7.9), 0.85, 0.85)
    elif kind == "clean":
        # 垃圾桶：盖子 + 桶身 + 竖纹
        p.drawLine(QPointF(4.2, 6.6), QPointF(19.8, 6.6))
        p.drawLine(QPointF(9.4, 6.6), QPointF(9.4, 4.4))
        p.drawLine(QPointF(9.4, 4.4), QPointF(14.6, 4.4))
        p.drawLine(QPointF(14.6, 4.4), QPointF(14.6, 6.6))
        path = QPainterPath()
        path.moveTo(6.2, 6.6)
        path.lineTo(7.4, 19.6)
        path.lineTo(16.6, 19.6)
        path.lineTo(17.8, 6.6)
        p.drawPath(path)
        p.drawLine(QPointF(10.4, 9.6), QPointF(10.8, 16.6))
        p.drawLine(QPointF(13.6, 9.6), QPointF(13.2, 16.6))
    elif kind == "shield":
        path = QPainterPath()
        path.moveTo(12, 3.4)
        path.lineTo(19.6, 6.4)
        path.lineTo(19.6, 12.2)
        path.cubicTo(19.6, 16.6, 16.2, 19.4, 12, 20.8)
        path.cubicTo(7.8, 19.4, 4.4, 16.6, 4.4, 12.2)
        path.lineTo(4.4, 6.4)
        path.closeSubpath()
        p.drawPath(path)
        p.drawLine(QPointF(8.3, 11.9), QPointF(11.0, 14.6))
        p.drawLine(QPointF(11.0, 14.6), QPointF(15.8, 9.2))
    elif kind == "refresh":
        p.drawArc(QRectF(4.8, 4.8, 14.4, 14.4), 55 * 16, 265 * 16)
        p.drawLine(QPointF(16.2, 2.6), QPointF(18.9, 8.4))
        p.drawLine(QPointF(18.9, 8.4), QPointF(12.7, 7.6))
    elif kind == "speed":
        # 速度表：上半圆弧 + 三根刻度 + 指针，比画个 U 盘更能表达「测速」
        p.drawArc(QRectF(3.6, 6.0, 16.8, 16.8), 0, 180 * 16)
        for ang, ln in ((35, 2.2), (90, 2.8), (145, 2.2)):
            rad = math.radians(ang)
            cx, cy, r = 12.0, 14.4, 8.4
            p.drawLine(QPointF(cx + r * math.cos(rad), cy - r * math.sin(rad)),
                       QPointF(cx + (r - ln) * math.cos(rad),
                               cy - (r - ln) * math.sin(rad)))
        p.drawLine(QPointF(12.0, 14.4), QPointF(17.0, 9.4))
        p.setBrush(QBrush(color))
        p.setPen(Qt.NoPen)
        p.drawEllipse(QPointF(12.0, 14.4), 1.2, 1.2)
        p.setBrush(Qt.NoBrush)
        p.setPen(pen)
        p.drawLine(QPointF(3.6, 14.4), QPointF(20.4, 14.4))
    elif kind == "tune":
        # 三根滑杆：一根横线 + 一个圆形滑块，最直观的「调优」语义
        for y, x in ((7.2, 15.2), (12.0, 8.8), (16.8, 13.4)):
            p.drawLine(QPointF(4.4, y), QPointF(19.6, y))
            p.drawEllipse(QPointF(x, y), 2.1, 2.1)
    elif kind == "net":
        # 地球：外圈 + 赤道 + 两条纬线 + 一条经线。
        # 不用「三节点连线」是因为 22px 下那些圆点会糊成一个色块。
        p.drawEllipse(QPointF(12, 12), 8.4, 8.4)
        p.drawLine(QPointF(3.6, 12.0), QPointF(20.4, 12.0))
        p.drawEllipse(QPointF(12, 12), 3.8, 8.4)
        p.drawLine(QPointF(4.9, 7.9), QPointF(19.1, 7.9))
        p.drawLine(QPointF(4.9, 16.1), QPointF(19.1, 16.1))
    elif kind == "health":
        # 心电图：一条折线 + 一个尖峰，比听诊器/十字更容易在小尺寸认出来
        path = QPainterPath()
        path.moveTo(3.2, 12.6)
        path.lineTo(7.0, 12.6)
        path.lineTo(9.0, 7.4)
        path.lineTo(11.6, 17.0)
        path.lineTo(13.8, 10.4)
        path.lineTo(15.4, 12.6)
        path.lineTo(20.8, 12.6)
        p.drawPath(path)
    elif kind == "disk":
        p.drawEllipse(QPointF(12, 7.0), 7.4, 2.7)
        p.drawLine(QPointF(4.6, 7.0), QPointF(4.6, 17.0))
        p.drawLine(QPointF(19.4, 7.0), QPointF(19.4, 17.0))
        path = QPainterPath()
        path.moveTo(4.6, 17.0)
        path.arcTo(QRectF(4.6, 14.3, 14.8, 5.4), 180, -180)
        p.drawPath(path)
    elif kind == "boot":
        # 电源符号：顶部留口的圆环 + 一根竖线。
        # 比画「开机进度条」好认 —— 22px 下条状图形会缩成一根线。
        p.drawArc(QRectF(4.6, 5.0, 14.8, 14.8), 112 * 16, 316 * 16)
        p.drawLine(QPointF(12, 3.0), QPointF(12, 10.6))
        # 圆环右侧一个向上的小箭头：把「耗时」的语义带进来
        p.drawLine(QPointF(17.4, 8.6), QPointF(19.6, 6.4))
        p.drawLine(QPointF(19.6, 6.4), QPointF(19.6, 9.2))


#: `make_icon` 的结果缓存。左侧导航每次切页都要重刷 10 个图标，
#: 每次都重新走一遍 QPainter 就是白扔几十毫秒；图标只是 (图形, 颜色, 尺寸)
#: 的函数，直接按这三样缓存。
#: 设备像素比固定按 2x 出图（见下），不随显示器变化，所以不必进 key。
_ICON_CACHE: dict = {}


def make_icon(kind: str, color: str = "", size: int = 20) -> QIcon:
    """按 24 网格矢量绘制图标，带 2x 缩放保证高 DPI 清晰。"""
    color = color or theme.current()["text"]
    key = (kind, color, size)
    hit = _ICON_CACHE.get(key)
    if hit is not None:
        return hit
    pm = QPixmap(size * 2, size * 2)
    pm.setDevicePixelRatio(2.0)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing, True)
    p.scale(size / 24.0, size / 24.0)
    _draw(kind, p, QColor(color))
    p.end()
    icon = QIcon(pm)
    _ICON_CACHE[key] = icon
    return icon


# ------------------------------------------------------------------ 基础件
def hline() -> QFrame:
    f = QFrame()
    f.setProperty("role", "hr")
    f.setFixedHeight(1)
    return f


class Dot(QWidget):
    def __init__(self, color: str, size: int = 8, parent=None):
        super().__init__(parent)
        self._color = color
        self._size = size
        self.setFixedSize(size + 2, size + 2)

    def set_color(self, color: str) -> None:
        self._color = color
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(QColor(self._color)))
        p.drawEllipse(self.rect().center(), self._size // 2 - 1, self._size // 2 - 1)
        p.end()


class Badge(QLabel):
    def __init__(self, text: str, sev: str = "提示", parent=None):
        super().__init__(text, parent)
        self.setAlignment(Qt.AlignCenter)
        self.set_size_policy()
        self.set_severity(sev)

    def set_size_policy(self) -> None:
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)

    def set_severity(self, sev: str) -> None:
        t = theme.current()
        color = t.get(SEV_COLOR.get(sev, "dim"), t["dim"])
        self.setStyleSheet(
            f"background:{alpha(color, 0.15)}; color:{color};"
            f"border:1px solid {alpha(color, 0.32)}; border-radius:9px;"
            f"padding:1px 8px; font-size:11px; font-weight:600;")
        self.setText(sev)


#: `KpiCard._fit` 用的字体度量缓存：键是 (像素字号, 是否加粗)。
#: app 字体在启动时设一次、主题切换不改字体，所以这份缓存不会过期。
_FM_CACHE: dict = {}


class KpiCard(QFrame):
    """一张指标卡：小标题 + 大数值 + 脚注。

    数值里的单位要单独降级（22px → 13px、颜色由主文字降为 dim）。
    原来「2.5 Gbps」整串都是 22px 加粗同色，单位跟数字抢视觉，
    一眼读不出「2.5」才是这个数。拆开以后读的顺序才是「数 → 单位 → 脚注」。
    """

    NUM_PX = 22          # 数值字号（放不下时逐级往下缩，最小 13）
    UNIT_PX = 13         # 单位字号

    def __init__(self, label: str, value: str = "-", sub: str = "",
                 accent: str = "accent", parent=None):
        super().__init__(parent)
        self.setProperty("role", "kpi")
        self._accent = accent
        lay = QVBoxLayout(self)
        lay.setContentsMargins(13, 10, 13, 11)
        lay.setSpacing(3)
        top = QHBoxLayout()
        top.setSpacing(6)
        self.dot = Dot(theme.current().get(accent, theme.current()["accent"]), 7)
        self.lbl = QLabel(label)
        self.lbl.setProperty("role", "kpiLabel")
        top.addWidget(self.dot)
        top.addWidget(self.lbl)
        top.addStretch(1)
        self.val = QLabel(value)
        self.val.setProperty("role", "kpi")
        self._full = str(value)
        self._num, self._unit = split_value(self._full)
        self._val_color = theme.current()["text"]
        self.sub = QLabel(sub)
        self.sub.setProperty("role", "faint")
        lay.addLayout(top)
        lay.addWidget(self.val)
        lay.addWidget(self.sub)
        self.setMinimumWidth(118)
        self._fit()          # 首帧就按「数值 + 小号单位」排版，不等第一次 resize

    def set_value(self, value: str, sub: str = None, color: str = None) -> None:
        t = theme.current()
        self._full = str(value)
        self._num, self._unit = split_value(self._full)
        # 必须显式声明透明背景：给 QLabel 设内联样式会让它自带一层背景
        self._val_color = t.get(color, t["text"]) if color else t["text"]
        if sub is not None:
            self.sub.setText(sub)
            self.sub.setVisible(bool(sub))
        self._fit()

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        self._fit()

    # ---- 内部 ----
    @staticmethod
    def _num_font(px: int) -> QFont:
        f = ui_font(9)
        f.setPixelSize(px)
        f.setBold(True)
        return f

    @staticmethod
    def _unit_font(px: int) -> QFont:
        f = ui_font(9)
        f.setPixelSize(px)
        f.setBold(False)
        return f

    @classmethod
    def _fm(cls, px: int, bold: bool) -> QFontMetrics:
        """按字号缓存 QFontMetrics。

        `_fit()` 原来每轮循环都 `QFontMetrics(self._num_font(size))`，
        从 22px 逐级试到 13px 就是十几次新建；而 `resizeEvent` 又会反复
        调 `_fit()` —— 实测 4 张 KPI 卡构造耗时 268ms，全花在
        `horizontalAdvance` 上。字号只有三五个取值，缓存下来之后
        每张卡就只剩一次真正的测量。
        """
        key = (px, bold)
        fm = _FM_CACHE.get(key)
        if fm is None:
            f = cls._num_font(px) if bold else cls._unit_font(px)
            fm = QFontMetrics(f)
            _FM_CACHE[key] = fm
        return fm

    def _unit_px(self, num_px: int) -> int:
        # 数值缩到 18px 以下时，13px 的单位就与数字同级了，必须跟着降
        return self.UNIT_PX if num_px >= 18 else 11

    def _fit(self) -> None:
        """值过宽时逐级缩小字号，仍放不下就省略号——避免被卡片右边缘裁掉。"""
        if not getattr(self, "_full", ""):
            return
        avail = self.val.width()
        if avail < 24:                      # 首轮布局还没算完
            avail = max(24, self.width() - 26)
        # 同一份文字、同一个可用宽度，算一次就够了。布局期 resizeEvent
        # 会连着来好几轮，而绝大多数轮次的结果完全相同。
        key = (avail, self._full)
        if key == getattr(self, "_fit_key", None):
            return
        self._fit_key = key
        num, unit = self._num, self._unit
        size = self.NUM_PX
        while size >= 13:
            w = self._fm(size, True).horizontalAdvance(num)
            if unit:
                w += 3 + self._fm(self._unit_px(size), False).horizontalAdvance(unit)
            if w <= avail:
                break
            size -= 1
        else:
            size = 13
            num = self._fm(size, True).elidedText(
                self._full, Qt.ElideRight, avail)
            unit = ""
        shown = num + (" " + unit if unit else "")
        self.val.setStyleSheet(
            f"color:{self._val_color}; background:transparent; border:none;"
            f" font-size:{size}px;")
        if unit:
            # 用富文本只为了两段不同字号；nbsp 保证数值与单位之间不会被压掉空格。
            # 必须显式关掉折行：单位一旦换到第二行，卡片会凭空长高一截。
            self.val.setWordWrap(False)
            self.val.setTextFormat(Qt.RichText)
            self.val.setText(
                f"{num}<span style='font-size:{self._unit_px(size)}px;"
                f"font-weight:400;color:{theme.current()['dim']}'>&nbsp;{unit}</span>")
        else:
            # 纯数值走 PlainText：万一将来出现 "<" 之类不会被当成标签
            self.val.setTextFormat(Qt.PlainText)
            self.val.setText(num)
        self.val.setToolTip(self._full if shown != self._full else "")

    def set_label(self, text: str) -> None:
        self.lbl.setText(text)

    def set_accent(self, accent: str) -> None:
        t = theme.current()
        self.dot.set_color(t.get(accent, t["accent"]))


class SectionTitle(QWidget):
    def __init__(self, text: str, right_widget: QWidget = None, parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        lbl = QLabel(text)
        lbl.setProperty("role", "H2")
        lay.addWidget(lbl)
        lay.addStretch(1)
        if right_widget is not None:
            lay.addWidget(right_widget)


class VerdictBar(QFrame):
    """结论条：左边一个等级大字，右边「一句话结论 + 一行脚注」，右侧可挂动作按钮。

    `boot_page` 与 `health_page` 原来各写了一整份，控件树逐行相同、只有两处不同：
    左边等级字的最小宽（52 / 34）和「有没有导出按钮」。这种复制品第二次出现时
    还看不出问题，第三次一定会漂移 —— 事实上它已经漂了：一份给等级字上色时
    写了 `background:transparent`，另一份没写，于是在深色卡片上多出一块
    跟页面同色的方形底。合成一个控件，差异走参数。
    """

    def __init__(self, level_width: int = 52, parent=None):
        super().__init__(parent)
        self.setProperty("role", "card")
        box = QHBoxLayout(self)
        box.setContentsMargins(14, 9, 14, 9)
        box.setSpacing(14)
        self._box = box

        self.level = QLabel("—")
        self.level.setObjectName("H1")
        self.level.setMinimumWidth(level_width)
        box.addWidget(self.level)

        text = QVBoxLayout()
        text.setSpacing(1)
        self.head = QLabel("")
        self.head.setObjectName("H3")
        self.head.setWordWrap(True)
        self.sub = QLabel("")
        self.sub.setProperty("role", "faint")
        self.sub.setWordWrap(True)
        text.addWidget(self.head)
        text.addWidget(self.sub)
        box.addLayout(text, 1)
        self.setVisible(False)

    def add_action(self, widget: QWidget) -> None:
        """在结论条右侧挂一个动作（如「导出报告」）。"""
        self._box.addWidget(widget)

    def set_level(self, text: str, color: str = "") -> None:
        """`color` 收 token 名（`crit`）或十六进制（`#e0696b`），空串表示恢复默认色。

        内联上色**必须显式声明透明底**：QLabel 一旦带内联样式就会自绘一层
        与父级不同源的底色，在卡片上表现为一道肉眼可见的浅色方块。
        """
        self.level.setText(text)
        if not color:
            self.level.setStyleSheet("background:transparent;")
            return
        t = theme.current()
        self.level.setStyleSheet("color:%s; background:transparent;"
                                 % t.get(color, color))

    def set_text(self, head: str, sub: str = "") -> None:
        self.head.setText(head)
        self.sub.setText(sub)
        self.sub.setVisible(bool(sub))



class FindingCard(QFrame):
    """诊断发现卡：等级 + 标题 + 解释 + 建议 + 证据。点击可定位证据。"""

    clicked = pyqtSignal(list)

    def __init__(self, finding, parent=None):
        super().__init__(parent)
        self.finding = finding
        self.setProperty("role", "findingCard")
        self.setCursor(Qt.PointingHandCursor)
        t = theme.current()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(7)

        head = QHBoxLayout()
        head.setSpacing(9)
        label = sev_cn(finding.severity)
        head.addWidget(Badge(label, label))
        title = QLabel(finding.title)
        title.setWordWrap(True)
        title.setStyleSheet("font-size:13px; font-weight:600;")
        head.addWidget(title, 1)
        if finding.count > 1:
            cnt = QLabel(f"×{finding.count}")
            cnt.setFont(mono_font(8, True))
            cnt.setProperty("role", "dim")
            head.addWidget(cnt)
        lay.addLayout(head)

        if finding.time_text and finding.time_text != "-":
            tl = QLabel(finding.time_text)
            tl.setFont(mono_font(8))
            tl.setProperty("role", "faint")
            lay.addWidget(tl)

        if finding.detail:
            d = QLabel(finding.detail)
            d.setWordWrap(True)
            d.setProperty("role", "dim")
            d.setStyleSheet(f"color:{t['dim']}; line-height:150%;")
            lay.addWidget(d)

        if finding.advice:
            box = QFrame()
            box.setProperty("role", "cardFlat")
            bl = QVBoxLayout(box)
            bl.setContentsMargins(11, 8, 11, 9)
            bl.setSpacing(4)
            cap = QLabel("建议动作")
            cap.setStyleSheet(f"color:{t['accent']}; font-size:11px; font-weight:600;")
            bl.addWidget(cap)
            for a in finding.advice[:6]:
                item = QLabel("· " + a)
                item.setWordWrap(True)
                item.setStyleSheet(f"color:{t['text']}; font-size:12px;")
                bl.addWidget(item)
            lay.addWidget(box)

        if finding.evidence:
            ev = QLabel("\n".join(finding.evidence[:4]))
            ev.setWordWrap(True)
            ev.setFont(mono_font(8))
            ev.setTextInteractionFlags(Qt.TextSelectableByMouse)
            ev.setStyleSheet(
                f"color:{t['faint']}; background:{t['surface2']};"
                f"border-radius:6px; padding:7px 9px;")
            lay.addWidget(ev)

        if self._has_targets():
            hint = QLabel("点击定位相关事件 →")
            hint.setStyleSheet(f"color:{t['accent']}; font-size:11px;")
            lay.addWidget(hint, alignment=Qt.AlignRight)

    def _has_targets(self) -> bool:
        return bool(getattr(self.finding, "event_ids", None))

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton and self._has_targets():
            self.clicked.emit(list(self.finding.event_ids))
        super().mouseReleaseEvent(e)


class ScrollColumn(QScrollArea):
    """纵向堆叠卡片的滚动区。"""

    def __init__(self, spacing: int = 10, parent=None):
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._host = QWidget()
        self._host.setStyleSheet("background: transparent;")
        self._lay = QVBoxLayout(self._host)
        self._lay.setContentsMargins(0, 8, 6, 0)
        self._lay.setSpacing(spacing)
        self._lay.addStretch(1)
        self.setWidget(self._host)

    def clear(self) -> None:
        while self._lay.count() > 1:
            item = self._lay.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()

    def add(self, w: QWidget) -> None:
        self._lay.insertWidget(self._lay.count() - 1, w)

    def add_stretch(self) -> None:
        self._lay.addStretch(1)


class EmptyState(QWidget):
    def __init__(self, icon: str, title: str, hint: str = "", parent=None):
        super().__init__(parent)
        t = theme.current()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 40, 20, 40)
        lay.setSpacing(10)
        lay.setAlignment(Qt.AlignCenter)
        ic = QLabel()
        ic.setPixmap(make_icon(icon, t["faint"], 40).pixmap(40, 40))
        ic.setAlignment(Qt.AlignCenter)
        lay.addWidget(ic)
        self.title_label = QLabel(title)
        self.title_label.setAlignment(Qt.AlignCenter)
        self.title_label.setStyleSheet(f"color:{t['dim']}; font-size:13px;")
        lay.addWidget(self.title_label)
        self.hint_label = QLabel(hint)
        self.hint_label.setAlignment(Qt.AlignCenter)
        self.hint_label.setWordWrap(True)
        self.hint_label.setProperty("role", "faint")
        self.hint_label.setVisible(bool(hint))
        lay.addWidget(self.hint_label)

    def set_text(self, title: str, hint: str = "") -> None:
        """同一个空状态在不同场景下要说不同的话，别为了换文案重建控件。"""
        self.title_label.setText(title)
        self.hint_label.setText(hint)
        self.hint_label.setVisible(bool(hint))


class DropZone(QFrame):
    """拖放文件区。"""

    fileDropped = pyqtSignal(str)
    clicked = pyqtSignal()

    def __init__(self, title: str, hint: str, exts: tuple, icon: str = "folder", parent=None):
        super().__init__(parent)
        self.exts = tuple(x.lower() for x in exts)
        self.setAcceptDrops(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumHeight(116)
        self._icon = icon
        self._title = title
        self._hint = hint
        self._refresh_style()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(6)
        lay.setAlignment(Qt.AlignCenter)
        self.ic = QLabel()
        self.ic.setAlignment(Qt.AlignCenter)
        lay.addWidget(self.ic)
        self.t1 = QLabel(title)
        self.t1.setAlignment(Qt.AlignCenter)
        self.t1.setStyleSheet("font-size:13px; font-weight:600;")
        lay.addWidget(self.t1)
        self.t2 = QLabel(hint)
        self.t2.setAlignment(Qt.AlignCenter)
        self.t2.setProperty("role", "faint")
        lay.addWidget(self.t2)

    def _refresh_style(self, hot: bool = False) -> None:
        t = theme.current()
        border = t["accent"] if hot else t["border_strong"]
        bg = t["surface2"] if not hot else alpha(t["accent"], 0.08)
        self.setStyleSheet(
            f"QFrame{{background:{bg}; border:1px dashed {border}; border-radius:12px;}}")
        if hasattr(self, "ic"):
            self.ic.setPixmap(make_icon(self._icon, t["accent"] if hot else t["faint"],
                                       30).pixmap(30, 30))

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            for u in e.mimeData().urls():
                if u.toLocalFile().lower().endswith(self.exts):
                    e.acceptProposedAction()
                    self._refresh_style(True)
                    return
        e.ignore()

    def dragLeaveEvent(self, e):
        self._refresh_style(False)
        super().dragLeaveEvent(e)

    def dropEvent(self, e):
        self._refresh_style(False)
        for u in e.mimeData().urls():
            p = u.toLocalFile()
            if p.lower().endswith(self.exts):
                self.fileDropped.emit(p)
                break

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.clicked.emit()
        super().mouseReleaseEvent(e)


def ghost_button(text: str, icon: str = "") -> QPushButton:
    """动作按钮。v2.2.0 起与 `primary_button` 外观完全相同（蓝实心白字）：
    全应用只有一种动作按钮，层级交给位置和文案表达。
    函数名和 `role="ghost"` 保留，是因为 70 处调用没必要为此改一遍。"""
    b = QPushButton(text)
    b.setProperty("role", "ghost")
    b.setCursor(Qt.PointingHandCursor)
    if icon:
        b.setIcon(make_icon(icon, "#FFFFFF", 16))
        b.setIconSize(QSize(16, 16))
    return b


def bare_button(text: str, icon: str = "") -> QPushButton:
    """纯文字按钮：贴在输入框或行内的小按钮（如清除来源的 ×），不留底色。"""
    b = QPushButton(text)
    b.setProperty("role", "bare")
    b.setCursor(Qt.PointingHandCursor)
    if icon:
        b.setIcon(make_icon(icon, theme.current()["dim"], 16))
        b.setIconSize(QSize(16, 16))
    return b


def primary_button(text: str, icon: str = "") -> QPushButton:
    """动作按钮（与 `ghost_button` 同款，见上）。"""
    b = QPushButton(text)
    b.setProperty("role", "primary")
    b.setCursor(Qt.PointingHandCursor)
    if icon:
        b.setIcon(make_icon(icon, "#FFFFFF", 15))
        b.setIconSize(QSize(15, 15))
    return b


def plain_button(text: str, icon: str = "") -> QPushButton:
    """不设 role 的动作按钮 —— 同样蓝实心（v2.2.0 起 base 就是蓝实心）。"""
    b = QPushButton(text)
    b.setCursor(Qt.PointingHandCursor)
    if icon:
        b.setIcon(make_icon(icon, "#FFFFFF", 15))
        b.setIconSize(QSize(15, 15))
    return b


def chip(text: str) -> QPushButton:
    b = QPushButton(text)
    b.setProperty("role", "chip")
    b.setCheckable(True)
    b.setCursor(Qt.PointingHandCursor)
    return b


class Bar(QWidget):
    """细容量条：按占用率自动换色（正常/偏高/告急）。"""

    def __init__(self, ratio: float = 0.0, height: int = 8, parent=None):
        super().__init__(parent)
        self._ratio = max(0.0, min(1.0, ratio))
        self._color = ""
        self.setFixedHeight(height)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def set_ratio(self, ratio: float, color: str = "") -> None:
        self._ratio = max(0.0, min(1.0, ratio))
        self._color = color
        self.update()

    def _auto_color(self) -> str:
        if self._color:
            return self._color
        t = theme.current()
        if self._ratio >= 0.9:
            return t["crit"]
        if self._ratio >= 0.78:
            return t["warn"]
        return t["accent"]

    def paintEvent(self, _):
        t = theme.current()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        r = QRectF(self.rect())
        radius = r.height() / 2.0
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(QColor(t["surface3"])))
        p.drawRoundedRect(r, radius, radius)
        if self._ratio > 0:
            w = max(r.height(), r.width() * self._ratio)
            p.setBrush(QBrush(QColor(self._auto_color())))
            p.drawRoundedRect(QRectF(0, 0, w, r.height()), radius, radius)
        p.end()
