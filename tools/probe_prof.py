# -*- coding: utf-8 -*-
"""给最慢的两个页面做 profile，看时间到底花在哪。"""
from __future__ import annotations

import cProfile
import io as _io
import os
import pstats
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "windows")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

OUT = os.path.join(ROOT, "probe_prof.log")


def main() -> int:
    from PyQt5.QtCore import Qt
    from PyQt5.QtGui import QFont
    from PyQt5.QtWidgets import QApplication

    from app import theme

    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setFont(QFont("Microsoft YaHei", 9))
    theme.apply_theme(app, "dark")

    lines = []

    for name, mod, cls in (("usb", "app.ui.usb_page", "UsbPage"),
                           ("health", "app.ui.health_page", "HealthPage")):
        m = __import__(mod, fromlist=[cls])
        pr = cProfile.Profile()
        pr.enable()
        w = getattr(m, cls)()
        pr.disable()
        buf = _io.StringIO()
        st = pstats.Stats(pr, stream=buf).sort_stats("cumulative")
        st.print_stats(18)
        body = buf.getvalue()
        # 只留最有用的几行
        keep = body.splitlines()[:26]
        lines.append("===== %s =====" % cls)
        lines.extend(keep)
        w.deleteLater()
        app.processEvents()

    open(OUT, "w", encoding="utf-8").write("\n".join(lines))
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
