# -*- coding: utf-8 -*-
"""KpiCard 构造微基准：确认字体度量缓存的收益。"""
from __future__ import annotations

import os
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "windows")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def main() -> int:
    from PyQt5.QtCore import Qt
    from PyQt5.QtGui import QFont
    from PyQt5.QtWidgets import QApplication

    from app import theme
    from app.ui import widgets

    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setFont(QFont("Microsoft YaHei", 9))
    theme.apply_theme(app, "dark")

    out = []
    for rnd in range(3):
        widgets._FM_CACHE.clear()      # 冷缓存
        t0 = time.perf_counter()
        cards = [widgets.KpiCard("指标 %d" % i, "123.4 MB") for i in range(20)]
        ms = (time.perf_counter() - t0) * 1000
        out.append("冷缓存 20 张 %.1f ms（每张 %.2f）" % (ms, ms / 20))
        for c in cards:
            c.deleteLater()
        app.processEvents()

        t0 = time.perf_counter()
        cards = [widgets.KpiCard("指标 %d" % i, "123.4 MB") for i in range(20)]
        ms = (time.perf_counter() - t0) * 1000
        out.append("热缓存 20 张 %.1f ms（每张 %.2f）" % (ms, ms / 20))
        for c in cards:
            c.deleteLater()
        app.processEvents()

    # 大数值触发降字号循环
    t0 = time.perf_counter()
    cards = [widgets.KpiCard("x", "1,234,567,890.12 MB/s") for _ in range(20)]
    ms = (time.perf_counter() - t0) * 1000
    out.append("超长值 20 张 %.1f ms（每张 %.2f）" % (ms, ms / 20))

    text = "\n".join(out)
    open(os.path.join(ROOT, "probe_kpi.log"), "w", encoding="utf-8").write(text)
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
