# -*- coding: utf-8 -*-
"""启动性能探针：分段计时，结果写文件。

v1.8.0 起改为测「懒建」链路：MainWindow 只建第一个工作台，
其余在首次切换时才建，所以切换耗时也要计。
"""
import os
import sys
import time

os.environ["QT_QPA_PLATFORM"] = "windows"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "probe_perf.log")
lines = []


def log(s):
    lines.append(s)
    print(s)


t0 = time.perf_counter()

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QApplication
from PyQt5.QtGui import QFont

t_import = time.perf_counter()
log("import PyQt           %.0f ms" % ((t_import - t0) * 1000))

from app import theme
from app.ui.main_window import MainWindow
from app.ui.widgets import app_icon

t_app_import = time.perf_counter()
log("import app.*          %.0f ms" % ((t_app_import - t_import) * 1000))

QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
app = QApplication(sys.argv)
app.setStyle("Fusion")
app.setFont(QFont(theme.FONT_FAMILY, 9))
ic = app_icon()
if not ic.isNull():
    app.setWindowIcon(ic)
theme.apply_theme(app, "dark")
t_qapp = time.perf_counter()
log("QApplication+theme    %.0f ms" % ((t_qapp - t_app_import) * 1000))

win = MainWindow()
t_win = time.perf_counter()
log("MainWindow()          %.0f ms" % ((t_win - t_qapp) * 1000))

win.show()
app.processEvents()
t_show = time.perf_counter()
log("show + processEvents  %.0f ms" % ((t_show - t_win) * 1000))

built = [n for n in ("log_page", "dump_page", "clean_page", "residue_page",
                     "optimize_page", "net_page", "health_page")
         if getattr(win, n, None) is not None]
log("启动时已建页面         %s" % (built,))

# 逐页切换耗时（首次切换 = 建页 + 布局）
for i, name in enumerate(["日志", "转储", "清理", "残留", "优化", "网络", "体检"]):
    a = time.perf_counter()
    win._switch(i)
    app.processEvents()
    b = time.perf_counter()
    log("首次 switch -> %-4s  %6.0f ms" % (name, (b - a) * 1000))

# 第二次切换（已建好，只剩 setCurrentIndex + showEvent）
for i, name in enumerate(["日志", "转储", "清理", "残留", "优化", "网络", "体检"]):
    a = time.perf_counter()
    win._switch(i)
    app.processEvents()
    b = time.perf_counter()
    log("再次 switch -> %-4s  %6.0f ms" % (name, (b - a) * 1000))

t_end = time.perf_counter()
log("")
log("TOTAL to first paint  %.0f ms" % ((t_show - t0) * 1000))
log("TOTAL incl. all tabs  %.0f ms" % ((t_end - t0) * 1000))

# 主题切换（现在只重建当前页 + 第一页）
a = time.perf_counter()
win.toggle_theme()
app.processEvents()
b = time.perf_counter()
log("toggle_theme          %.0f ms" % ((b - a) * 1000))
log("切换后已建页面         %s" % ([n for n in
                                  ("log_page", "dump_page", "clean_page",
                                   "residue_page", "optimize_page", "net_page",
                                   "health_page")
                                  if getattr(win, n, None) is not None],))

win.close()
with open(OUT, "w", encoding="utf-8") as f:
    f.write("\n".join(lines))
