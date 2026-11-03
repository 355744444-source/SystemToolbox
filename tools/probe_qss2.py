# -*- coding: utf-8 -*-
"""主题切换成本拆解：setPalette vs setStyleSheet，以及 QSS 体积的影响。"""
import os
import sys
import time

os.environ["QT_QPA_PLATFORM"] = "windows"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "probe_qss2.log")
lines = []


def log(s):
    lines.append(s)
    print(s)


from PyQt5.QtWidgets import QApplication, QLabel, QPushButton, QVBoxLayout, QWidget
from app import theme

app = QApplication(sys.argv)
app.setStyle("Fusion")

D = theme.THEMES["dark"]
L = theme.THEMES["light"]
qss_d, qss_l = theme.qss(D), theme.qss(L)
log("QSS 长度 dark=%d light=%d 条规则≈%d"
    % (len(qss_d), len(qss_l), qss_d.count("{")))

host = QWidget()
lay = QVBoxLayout(host)
for i in range(200):
    lay.addWidget(QLabel("l%d" % i))
    lay.addWidget(QPushButton("x%d" % i))
host.resize(800, 600)
host.show()
app.processEvents()
log("控件数=%d" % len(app.allWidgets()))

# 1) 首装（控件还没样式）
t = time.perf_counter()
app.setStyleSheet(qss_d)
app.processEvents()
log("首次 setStyleSheet(完整 QSS) %.0f ms" % ((time.perf_counter() - t) * 1000))

# 2) 换成另一套主题的 QSS（每次切主题的真实动作）
t = time.perf_counter()
app.setStyleSheet(qss_l)
app.processEvents()
log("换主题 setStyleSheet        %.0f ms" % ((time.perf_counter() - t) * 1000))

# 3) 同一份 QSS 再来一遍（区分「解析」和「polish」）
t = time.perf_counter()
app.setStyleSheet(qss_l)
app.processEvents()
log("同 QSS 重复 setStyleSheet   %.0f ms" % ((time.perf_counter() - t) * 1000))

# 4) setPalette 单独成本
t = time.perf_counter()
app.setPalette(theme.palette(L))
app.processEvents()
log("setPalette(light)           %.0f ms" % ((time.perf_counter() - t) * 1000))

# 5) 极简 QSS 基线
t = time.perf_counter()
app.setStyleSheet("QWidget { color: #ccc; }")
app.processEvents()
log("极简 QSS                    %.0f ms" % ((time.perf_counter() - t) * 1000))

with open(OUT, "w", encoding="utf-8") as f:
    f.write("\n".join(lines))
