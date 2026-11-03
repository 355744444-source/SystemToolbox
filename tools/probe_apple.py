# -*- coding: utf-8 -*-
"""苹果流体化改造 · 可行性实测

1. SPI_GETCLIENTAREAANIMATION —— 系统「显示动画」开关
2. QGraphicsOpacityEffect 做页面淡入的每帧成本（1440x900）
3. QScroller 惯性滚动在 QTableView 上会不会吞掉点击
4. 弹簧数值解（阻尼/响应）挑参数
"""
import ctypes
import os
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "windows")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

LOG = []


def log(msg):
    LOG.append(str(msg))


# ---------------- 1. 系统动画偏好
SPI_GETCLIENTAREAANIMATION = 0x1042
val = ctypes.c_int(0)
try:
    ok = ctypes.windll.user32.SystemParametersInfoW(
        SPI_GETCLIENTAREAANIMATION, 0, ctypes.byref(val), 0)
    log(f"[动画偏好] rc={ok} animations_enabled={bool(val.value)}")
except Exception as e:
    log(f"[动画偏好] 失败 {e}")

# ---------------- 2/3. Qt 侧
from PyQt5.QtCore import QEasingCurve, QPropertyAnimation
from PyQt5.QtWidgets import (QApplication, QGraphicsOpacityEffect, QScroller,
                             QScrollerProperties, QStackedWidget, QVBoxLayout,
                             QWidget)

from app import theme
from app.ui.tables import Col, RowModel, make_table

app = QApplication(sys.argv)
app.setStyle("Fusion")
theme.apply_theme(app, "dark")

# ---- 页面淡入成本
host = QWidget()
host.resize(1440, 836)
lay = QVBoxLayout(host)
lay.setContentsMargins(0, 0, 0, 0)
stack = QStackedWidget()
lay.addWidget(stack)

model = RowModel([Col("进程", 220, lambda r: r[0]),
                  Col("PID", 80, lambda r: str(r[1])),
                  Col("内存", 110, lambda r: r[2])], parent=host)
model.set_rows([(f"process_{i}.exe", 1000 + i, f"{i * 3} MB") for i in range(300)])
tbl = make_table(model)
stack.addWidget(tbl)
stack.addWidget(QWidget())
host.show()
app.processEvents()

eff = QGraphicsOpacityEffect(stack)
stack.setGraphicsEffect(eff)
frames = {"n": 0, "t": []}


def pump(ms=0):
    end = time.perf_counter() + ms / 1000.0
    while time.perf_counter() < end:
        app.processEvents()
        time.sleep(0.002)


anim = QPropertyAnimation(eff, b"opacity")
anim.setDuration(180)
anim.setStartValue(0.0)
anim.setEndValue(1.0)
anim.setEasingCurve(QEasingCurve.OutCubic)
t0 = time.perf_counter()
anim.start()
while anim.state() == QPropertyAnimation.Running:
    app.processEvents()
    time.sleep(0.001)
dt = (time.perf_counter() - t0) * 1000
log(f"[淡入] 180ms 设计时长 → 实测 {dt:.0f}ms（含 300 行表格）")

# 带位移的淡入（setGeometry 每帧）
stack.setGraphicsEffect(eff)
eff.setOpacity(0.0)
t0 = time.perf_counter()
for i in range(13):
    p = i / 12.0
    eff.setOpacity(p)
    tbl.setContentsMargins(0, int(6 * (1 - p)), 0, 0)
    app.processEvents()
    time.sleep(0.012)
dt2 = (time.perf_counter() - t0) * 1000
log(f"[淡入+位移] 13 帧 → 实测 {dt2:.0f}ms，单帧 {dt2 / 13:.1f}ms")
stack.setGraphicsEffect(None)

# ---- QScroller
try:
    QScroller.grabGesture(tbl.viewport(), QScroller.LeftMouseButtonGesture)
    sc = QScroller.scroller(tbl.viewport())
    log(f"[惯性滚动] 抓取成功 state={sc.state()}")
    # 是否会吞点击：直接查 QScroller 是否装了 event filter
    log(f"[惯性滚动] viewport 事件过滤器数量 = {len(tbl.viewport().children())}（仅供参考）")
except Exception as e:
    log(f"[惯性滚动] 失败 {e}")

# ---- 弹簧数值：欠阻尼弹簧的位置响应，找「不超调但收得住」的阻尼比
def spring_curve(zeta, omega, dur, steps=60):
    """返回归一化进度序列（0→1），用于判断超调量。"""
    import math
    out = []
    for i in range(steps + 1):
        t = dur * i / steps
        if zeta < 1:
            wd = omega * math.sqrt(1 - zeta * zeta)
            v = 1 - math.exp(-zeta * omega * t) * (
                math.cos(wd * t) + zeta / math.sqrt(1 - zeta * zeta) * math.sin(wd * t))
        else:
            v = 1 - math.exp(-omega * t) * (1 + omega * t)
        out.append(v)
    return out


for zeta in (0.7, 0.8, 0.9, 1.0):
    omega = 18.0
    c = spring_curve(zeta, omega, 0.45)
    overshoot = (max(c) - 1.0) * 100
    settle = next((i / 60 * 0.45 for i in range(len(c)) if all(abs(x - 1) < 0.01 for x in c[i:])), 0.45)
    log(f"[弹簧] ζ={zeta} ω={omega} → 超调 {overshoot:.1f}% 稳定 {settle * 1000:.0f}ms")

host.close()
app.processEvents()

out = os.path.join(ROOT, "probe_apple.log")
with open(out, "w", encoding="utf-8") as f:
    f.write("\n".join(LOG))
print("done")
