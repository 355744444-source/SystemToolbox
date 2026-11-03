# -*- coding: utf-8 -*-
"""v1.8.0 视觉回归：确认启动首帧显示的是「日志分析」而不是空白占位。"""
from __future__ import annotations

import os
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "windows")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from PyQt5.QtCore import Qt  # noqa: E402
from PyQt5.QtGui import QFont  # noqa: E402
from PyQt5.QtWidgets import QApplication  # noqa: E402

from app import theme  # noqa: E402
from app.ui.main_window import MainWindow  # noqa: E402

app = QApplication(sys.argv)
app.setStyle("Fusion")
app.setFont(QFont("Microsoft YaHei", 9))
theme.apply_theme(app, "dark")

win = MainWindow()
win.setAttribute(Qt.WA_DontShowOnScreen, True)
win.resize(1440, 900)
win.show()


def pump(n=10):
    for _ in range(n):
        app.processEvents()
        time.sleep(0.01)


pump(12)
out = os.path.join(ROOT, "probe_shots", "v180_首帧.png")
os.makedirs(os.path.dirname(out), exist_ok=True)
ok = win.grab().save(out)
print("首帧 currentIndex=%d (应为 0) 保存=%s %s"
      % (win.stack.currentIndex(), ok, out), flush=True)

# 主题切换后再看首帧
# 浅色皮肤已移除（v2.0.0），此处不再切主题
pump(12)
out2 = os.path.join(ROOT, "probe_shots", "v180_切浅色后.png")
ok2 = win.grab().save(out2)
print("切浅色后 currentIndex=%d 保存=%s" % (win.stack.currentIndex(), ok2), flush=True)

win.close()
print("done", flush=True)
