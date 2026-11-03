# -*- coding: utf-8 -*-
"""import 耗时分解。"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "probe_import.log")
lines = []


def log(s):
    lines.append(s)
    print(s)


mods = [
    "PyQt5.QtCore", "PyQt5.QtGui", "PyQt5.QtWidgets",
    "app.theme",
    "app.core.report", "app.core.analysis", "app.core.minidump",
    "app.core.knowledge", "app.core.evtx", "app.core.cleaner",
    "app.core.disk", "app.core.registry_clean", "app.core.residue",
    "app.core.sysinfo", "app.core.startup", "app.core.services",
    "app.core.tweaks", "app.core.changes", "app.core.netdiag",
    "app.core.devices", "app.core.upgrade", "app.core.perf",
    "app.core.procs", "app.core.security", "app.core.component",
    "app.core.smart", "app.core.hwevent",
]
for m in mods:
    a = time.perf_counter()
    __import__(m)
    b = time.perf_counter()
    log("%-26s %6.0f ms" % (m, (b - a) * 1000))

# 已导入模块的内存占用
import tracemalloc
log("")
log("loaded modules=%d" % len(sys.modules))

with open(OUT, "w", encoding="utf-8") as f:
    f.write("\n".join(lines))
