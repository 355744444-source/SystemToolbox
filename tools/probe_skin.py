# -*- coding: utf-8 -*-
"""浅色皮肤移除后的界面复核：rail 少了一个按钮，布局不能歪。

只跑深色一条路 —— 没有第二条路了。抓两张图（日志页 / 体检页）+ 探按钮几何。
结果写 probe_skin.txt，别指望 stdout（Bash 里常丢）。
"""
from __future__ import annotations

import io
import os
import sys
import time

os.environ["QT_QPA_PLATFORM"] = "windows"

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

OUT = os.path.join(ROOT, "probe_out")
LOG = os.path.join(ROOT, "probe_skin.txt")
os.makedirs(OUT, exist_ok=True)
buf = []


def w(msg):
    buf.append(str(msg))
    with io.open(LOG, "w", encoding="utf-8") as f:
        f.write("\n".join(buf))


from PyQt5.QtGui import QImage  # noqa: E402
from PyQt5.QtWidgets import QApplication  # noqa: E402

app = QApplication(sys.argv[:1])
from app import theme  # noqa: E402
from app.ui.main_window import MainWindow  # noqa: E402

theme.apply_theme(app)


def pump(ms):
    end = time.time() + ms / 1000.0
    while time.time() < end:
        app.processEvents()
        time.sleep(0.01)


win = MainWindow()
win.resize(1440, 900)
win.show()
pump(400)

w("THEMES           = %s" % (list(theme.THEMES),))
w("current          = %s" % theme.current()["name"])
w("has btn_theme    = %s" % hasattr(win, "btn_theme"))
w("has theme_name   = %s" % hasattr(win, "theme_name"))
w("has toggle_theme = %s" % hasattr(win, "toggle_theme"))

rail = win.rail
w("rail 子按钮数    = %d（应为 8 个导航）" % len(win.rail_buttons))
w("rail 高度        = %d, 内容高度 = %d"
  % (rail.height(), rail.layout().sizeHint().height()))

w("btn_about 几何   = %d,%d %dx%d"
  % (win.btn_about.x(), win.btn_about.y(),
     win.btn_about.width(), win.btn_about.height()))
last = win.rail_buttons[-1]
w("最后一个导航几何 = %d,%d %dx%d（y 应小于 about 的 y）"
  % (last.x(), last.y(), last.width(), last.height()))
w("about 文本       = %r" % win.btn_about.text())
w("按钮文本         = %s" % [b.text() for b in win.rail_buttons])

# Ctrl+T 现在不该有任何反应
from PyQt5.QtCore import Qt  # noqa: E402
from PyQt5.QtGui import QKeyEvent  # noqa: E402
from PyQt5.QtCore import QEvent  # noqa: E402
ev = QKeyEvent(QEvent.KeyPress, Qt.Key_T, Qt.ControlModifier)
win.keyPressEvent(ev)
pump(120)
w("按 Ctrl+T 之后    = %s（不该崩、不该变）" % theme.current()["name"])

win.grab().save(os.path.join(OUT, "skin_log.png"))

# 体检页也看一眼（结论条 + 9 项）
win.btn_health.setChecked(True)
win._switch(6)
hp = win.page(6)
hp.run_all()
t0 = time.time()
while time.time() - t0 < 60 and not hp.verdict.isVisible():
    pump(300)
pump(300)
w("体检页 score      = %r verdict=%s"
  % (hp.v_score.text(), hp.verdict.isVisible()))
win.grab().save(os.path.join(OUT, "skin_health.png"))

# 像素：底该是深色（#0D1117 系），不是白
img = QImage(os.path.join(OUT, "skin_log.png"))
w("截图中部像素      = %s"
  % img.pixelColor(img.width() // 2, img.height() - 60).name())
w("导航条中部像素    = %s"
  % img.pixelColor(40, img.height() // 2).name())

win.close()
pump(300)
w("RESULT: OK")
os._exit(0)
