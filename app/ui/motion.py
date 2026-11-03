# -*- coding: utf-8 -*-
"""流体交互层：把「即时响应 / 可中途打断 / 弹簧收敛」三件事落到 Qt 上。

参数不是拍脑袋，来自 tools/probe_apple.py 的本机实测：

| 项 | 实测 | 结论 |
|---|---|---|
| ζ=0.7 ω=18 | 超调 4.6%，368ms 静下 | 太晃，取证类工具上显得轻浮 |
| ζ=0.8 ω=18 | 超调 1.5%，360ms 静下 | 可以，但边界感还是弱 |
| **ζ=0.9 ω=18** | **超调 0.2%，285ms 静下** | **采用** —— 有弹性，不来回摆 |
| 纯 opacity 淡入 180ms | 实测 184ms（300 行表格页面） | 几乎零成本，做 |
| 淡入再叠位移 | 单帧 24.4ms → 41fps | 不值得，**只做透明度** |
| QScroller 左键惯性 | 抓取成功但会吞掉点选行 | **不启用**，用户是鼠标点行不是刷手机 |

还有一条原则：**动效必须可关**。Windows「显示动画」关掉的人通常有原因
（晕动症、远程桌面、录屏），这时所有时长直接归零，界面立刻可用。
"""
from __future__ import annotations

import ctypes
import math
import time

from PyQt5.QtCore import (QEasingCurve, QObject, QPropertyAnimation, QRectF, Qt,
                          QTimer)
from PyQt5.QtGui import QColor, QPainter, QPen
from PyQt5.QtWidgets import QGraphicsOpacityEffect, QWidget

from .. import theme

# 系统「在 Windows 中显示动画」开关（SPI_GETCLIENTAREAANIMATION）
_SPI_GETCLIENTAREAANIMATION = 0x1042

# 弹簧：阻尼比与角频率。改这两个数要重跑 probe_apple.py 看超调量
# ζ=0.9/ω=18 虽然"干净"（超调 0.2%），但实测前 50ms 只走了 7.8% ——
# 点下去半帧内看不出动静，跟「即时响应」是反的。改成 ζ=0.86/ω=22：
# 前 50ms 走到 32%、约 250ms 静下，超调仍只有 0.5%（330px 行程上 1.6px，看不见）。
ZETA = 0.86
OMEGA = 22.0
# 350 而不是 300：275ms 是超调峰（+0.46%），300ms 时还差 1.5px 没收回，
# 计时器一到点强行落位就会看见一次 1.5px 的硬跳。350ms 时偏差只剩
# 0.23%（不到 1px），收尾那一下彻底看不见。
SPRING_MS = 350
FADE_MS = 150            # 页面切入
FADE_FROM = 0.35         # 不从 0 起 —— 见 fade_in 注释

_cache: dict[str, bool] = {}


def animations_enabled() -> bool:
    """跟随系统「显示动画」设置；读不到就当开启（Win32 调用失败极罕见）。"""
    if "on" not in _cache:
        on = True
        try:
            v = ctypes.c_int(1)
            rc = ctypes.windll.user32.SystemParametersInfoW(
                _SPI_GETCLIENTAREAANIMATION, 0, ctypes.byref(v), 0)
            if rc:
                on = bool(v.value)
        except Exception:
            pass
        _cache["on"] = on
    return _cache["on"]


def spring_progress(t: float, zeta: float = ZETA, omega: float = OMEGA) -> float:
    """归一化弹簧响应 0→1（t 秒）。欠阻尼，末尾会轻微过冲再收回。"""
    if t <= 0:
        return 0.0
    if zeta < 1.0:
        wd = omega * math.sqrt(1.0 - zeta * zeta)
        return 1.0 - math.exp(-zeta * omega * t) * (
            math.cos(wd * t) + zeta / math.sqrt(1.0 - zeta * zeta) * math.sin(wd * t))
    return 1.0 - math.exp(-omega * t) * (1.0 + omega * t)


class Spring(QObject):
    """把某个标量按弹簧曲线推到目标值。

    **可打断**是这个类的全部意义：`to()` 每次从调用方给的实际当前值起跳，
    所以连点三下导航，块是拐着弯滑过去，而不是排队滑三次。

    为什么不用 QVariantAnimation：实测它 start() 之后头两帧不推进
    （36ms 内位置纹丝不动），而这恰好是「点下去有没有反应」的那段时间。
    改成 QTimer + 真实经过时间，首帧延迟降到 12ms 以内，曲线也不会被
    定时器抖动拉变形。
    """

    def __init__(self, parent: QObject | None = None, duration_ms: int = SPRING_MS,
                 interval_ms: int = 12):
        super().__init__(parent)
        self._dur = int(duration_ms)
        self._from = 0.0
        self._to = 0.0
        self._cb = None
        self._t0 = 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(int(interval_ms))
        self._timer.setTimerType(Qt.PreciseTimer)
        self._timer.timeout.connect(self._tick)

    def to(self, target: float, current: float, on_value) -> None:
        self._timer.stop()
        self._from, self._to, self._cb = float(current), float(target), on_value
        if (not animations_enabled() or self._dur <= 0
                or abs(self._to - self._from) < 0.5):
            on_value(float(target))
            return
        self._t0 = time.perf_counter()
        self._timer.start()

    def stop(self) -> None:
        self._timer.stop()
        self._cb = None

    def _tick(self) -> None:
        if self._cb is None:
            self._timer.stop()
            return
        t = (time.perf_counter() - self._t0)
        try:
            if t >= self._dur / 1000.0:
                self._timer.stop()
                self._cb(self._to)
                return
            p = spring_progress(t)
            self._cb(self._from + (self._to - self._from) * p)
        except RuntimeError:
            # 宿主控件已经被主题重建销毁（旧树退役），停止回放即可
            self._timer.stop()
            self._cb = None


def fade_in(widget: QWidget | None, ms: int = FADE_MS) -> None:
    """页面切入的淡入。

    **不从 0 起**：实测从全透明起步时，切换后的头 45ms 截出来是一片空白，
    用户感知是「卡了半拍再出现」。改成从 0.35 起步，内容立刻就在，
    只有明暗的柔化 —— 这才是 Apple 里转场该有的分寸。

    只动透明度：叠位移的版本在真实页面上单帧 24.4ms（41fps），
    换来 5px 的滑动不值这个钱。

    动画一结束立刻摘掉 effect —— 挂着 QGraphicsOpacityEffect 的控件
    会一直走离屏渲染，表格滚动会明显变慢。
    """
    if widget is None or not animations_enabled():
        return
    prev = getattr(widget, "_fade_anim", None)
    if prev is not None:
        try:
            prev.stop()
        except RuntimeError:        # 上一个动画随旧页面一起没了
            pass
    try:
        if widget.graphicsEffect() is not None:
            widget.setGraphicsEffect(None)
        eff = QGraphicsOpacityEffect(widget)
        eff.setOpacity(FADE_FROM)
        widget.setGraphicsEffect(eff)
    except RuntimeError:
        return
    anim = QPropertyAnimation(eff, b"opacity", widget)   # 挂在页面上，随页面销毁
    anim.setDuration(int(ms))
    anim.setStartValue(FADE_FROM)
    anim.setEndValue(1.0)
    anim.setEasingCurve(QEasingCurve.OutCubic)

    def _done():
        try:
            widget._fade_anim = None
            widget.setGraphicsEffect(None)
        except RuntimeError:        # 页面已经随主题重建被销毁
            pass

    anim.finished.connect(_done)
    widget._fade_anim = anim
    anim.start(QPropertyAnimation.DeleteWhenStopped)


class PillLayer(QWidget):
    """导航的选中块（pill）：跟着当前工作台滑过去。

    为什么单开一层而不是重写父控件的 paintEvent：QWidget 子类一旦自己实现
    paintEvent，QSS 给的 background 就没人画了（要靠 style().drawPrimitive
    手动补）。拆成一层子控件，父控件的 `QWidget#Rail` 背景照常生效，
    这层只画一个圆角矩形，`lower()` 之后压在按钮底下。
    """

    def __init__(self, rail: "RailView", parent=None):
        super().__init__(parent or rail)
        self._rail = rail
        self._y = 0.0
        self._h = 0.0
        self._alpha = 1.0
        self.spring = Spring(self)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WA_OpaquePaintEvent, False)

    # ---- 几何
    def bounds(self) -> tuple[float, float, float, float]:
        b = self._rail.button_at(self._rail.active)
        if b is None:
            return (0.0, self._y, 0.0, 0.0)
        return (float(b.x()), self._y, float(b.width()), self._h or float(b.height()))

    def jump_to_active(self) -> None:
        """不播动画直接对齐（首次显示 / 切主题重建后）。"""
        b = self._rail.button_at(self._rail.active)
        if b is None:
            return
        self._y = float(b.y())
        self._h = float(b.height())
        self.update()

    def glide_to_active(self) -> None:
        b = self._rail.button_at(self._rail.active)
        if b is None:
            return
        self._h = float(b.height())
        self.spring.to(float(b.y()), self._y, self._set_y)

    def _set_y(self, y: float) -> None:
        self._y = float(y)
        self.update()

    # ---- 绘制
    def paintEvent(self, _ev) -> None:
        if self._h <= 1:
            return
        t = theme.current()
        dark = t.get("name") == "dark"
        x, y, w, h = self.bounds()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        # 填充：用按比例叠的 accent_soft，跟页面底色拉开一档
        fill = QColor(t["accent_soft"])
        fill.setAlphaF(0.92 if dark else 1.0)
        # 描边：不给这条线，块和页底亮度接近，会糊成一片
        edge = QColor(t["accent"])
        edge.setAlphaF(0.24 if dark else 0.32)
        p.setBrush(fill)
        p.setPen(QPen(edge, 1.0))
        # +0.5 让 1px 描边落在像素中心，不然会糊成 2px 灰边
        p.drawRoundedRect(QRectF(x + 0.5, y + 0.5, w - 1.0, h - 1.0), 11.0, 11.0)
        p.end()


class RailView(QWidget):
    """左侧导航容器：按钮 + 选中块。

    `main_window` 只跟这个类打交道：`add(button)` / `active` / `set_active()`。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("Rail")
        self.buttons: list = []
        self.active = 0
        self.pill = PillLayer(self, self)
        self.pill.lower()

    def add(self, b) -> None:
        self.buttons.append(b)
        # 新按钮要压在 pill 之上，pill 始终最底
        self.pill.lower()

    def button_at(self, idx: int):
        if 0 <= idx < len(self.buttons):
            return self.buttons[idx]
        return None

    def set_active(self, idx: int, animate: bool = True) -> None:
        if idx == self.active and animate:
            return
        self.active = idx
        if animate:
            self.pill.glide_to_active()
        else:
            self.pill.jump_to_active()

    def resizeEvent(self, ev) -> None:
        super().resizeEvent(ev)
        self.pill.setGeometry(self.rect())
        # resizeEvent 里按钮的 geometry 可能还是上一轮的：先强制走一遍布局，
        # 否则 pill 会按 y=0 画在最顶上（首次显示必然踩到）
        lay = self.layout()
        if lay is not None:
            lay.activate()
        self.pill.jump_to_active()
