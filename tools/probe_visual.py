# -*- coding: utf-8 -*-
"""视觉层可行性实测。

1. 微软雅黑的数字是不是等宽（决定右对齐数值列要不要换字体）
2. KPI 大字号下的 tracking（字距）对宽度的影响
3. 卡片加 QGraphicsDropShadowEffect 的真实开销（决定浅色主题要不要做阴影）
"""
from __future__ import annotations

import os
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "windows")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFont, QFontMetrics
from PyQt5.QtWidgets import (QApplication, QFrame, QGraphicsDropShadowEffect,
                             QLabel, QVBoxLayout, QWidget)
from PyQt5.QtGui import QColor

from app import theme

OUT = os.path.join(ROOT, "probe_visual.log")
log: list[str] = []


def pump(app, ms):
    end = time.perf_counter() + ms / 1000
    while time.perf_counter() < end:
        app.processEvents()
        time.sleep(0.004)


def main() -> int:
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    theme.apply_theme(app, "dark")

    # ---------- 1. 数字宽度 ----------
    for px in (12, 13, 22):
        f = QFont("Microsoft YaHei")
        f.setPixelSize(px)
        fm = QFontMetrics(f)
        widths = {c: fm.horizontalAdvance(c) for c in "0123456789"}
        uniq = sorted(set(widths.values()))
        log.append(f"YaHei {px}px 数字宽度={uniq} 等宽={len(uniq) == 1} "
                   f"单字宽={uniq[0] if len(uniq) == 1 else '-'}")
        # 真实串对比：'111' vs '888' 应该同宽才是等宽数字
        w1, w8 = fm.horizontalAdvance("111"), fm.horizontalAdvance("888")
        log.append(f"  '111'={w1} '888'={w8} 差={w1 - w8}px")

    fb = QFont("Microsoft YaHei")
    fb.setPixelSize(22)
    fb.setBold(True)
    fmb = QFontMetrics(fb)
    log.append(f"YaHei 22px Bold: '888'={fmb.horizontalAdvance('888')} "
               f"'111'={fmb.horizontalAdvance('111')}")

    # ---------- 2. 字距 tracking ----------
    for track in (0.0, -0.3, -0.6):
        f = QFont("Microsoft YaHei")
        f.setPixelSize(22)
        f.setBold(True)
        f.setLetterSpacing(QFont.AbsoluteSpacing, track)
        log.append(f"tracking {track}: '894.2'="
                   f"{QFontMetrics(f).horizontalAdvance('894.2')}px")

    # ---------- 3. 阴影开销 ----------
    class Card(QFrame):
        def __init__(self):
            super().__init__()
            self.setProperty("role", "card")
            lay = QVBoxLayout(self)
            lay.setContentsMargins(13, 10, 13, 11)
            lay.setSpacing(3)
            lay.addWidget(QLabel("下载速度"))
            v = QLabel("894.2")
            v.setProperty("role", "kpi")
            lay.addWidget(v)
            lay.addWidget(QLabel("Mbps"))

    for theme_name in ("dark", "light"):
        theme.apply_theme(app, theme_name)
        for n, shadow in ((8, False), (8, True), (1, True)):
            host = QWidget()
            host.resize(1200, 300)
            h = QVBoxLayout(host)
            h.setContentsMargins(16, 14, 16, 14)
            cards = []
            for i in range(n):
                c = Card()
                if shadow:
                    eff = QGraphicsDropShadowEffect(c)
                    eff.setBlurRadius(14)
                    eff.setOffset(0, 2)
                    eff.setColor(QColor(16, 32, 56, 26))
                    c.setGraphicsEffect(eff)
                h.addWidget(c)
                cards.append(c)
            host.setAttribute(Qt.WA_DontShowOnScreen, True)
            host.show()
            pump(app, 250)
            # 连抓 20 帧，取中位数
            ts = []
            for _ in range(20):
                t0 = time.perf_counter()
                host.grab()
                ts.append((time.perf_counter() - t0) * 1000)
            ts.sort()
            log.append(f"{theme_name} 卡片×{n} 阴影={shadow}: "
                       f"grab 中位 {ts[len(ts) // 2]:.1f}ms "
                       f"最慢 {ts[-1]:.1f}ms")
            host.close()
            host.deleteLater()
            pump(app, 120)

    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(log))
    return 0


if __name__ == "__main__":
    sys.exit(main())
