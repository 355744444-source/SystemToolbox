# -*- coding: utf-8 -*-
"""离屏 UI 冒烟测试：渲染各工作台并截图，便于人工核对视觉。

运行：QT_QPA_PLATFORM=offscreen python tests/ui_smoke.py [evtx路径]
"""
from __future__ import annotations

import glob
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from PyQt5.QtCore import Qt  # noqa: E402
from PyQt5.QtGui import QFont  # noqa: E402
from PyQt5.QtWidgets import QApplication  # noqa: E402

from app import theme  # noqa: E402
from app.core import evtx  # noqa: E402
from app.ui.main_window import MainWindow  # noqa: E402

SHOTS = os.path.join(ROOT, "shots")


def main() -> int:
    os.makedirs(SHOTS, exist_ok=True)
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setFont(QFont("Microsoft YaHei UI", 9))
    theme.apply_theme(app, "dark")

    win = MainWindow()
    win.resize(1440, 900)
    win.show()
    for _ in range(4):
        app.processEvents()

    def shot(name: str) -> None:
        for _ in range(3):
            app.processEvents()
        path = os.path.join(SHOTS, name)
        win.grab().save(path)
        print("shot:", path, os.path.getsize(path))

    shot("01_log_empty.png")

    fixtures = sorted(glob.glob(os.path.join(ROOT, "tests", "fixtures", "*.dmp")))
    kernel = [f for f in fixtures if "whea" in f]
    if kernel:
        win.btn_dump.setChecked(True)
        win._switch(1)
        win.dump_page.load_file(kernel[0])
        shot("02_dump_kernel.png")
    app_dump = [f for f in fixtures if "appcrash_av" in f]
    if app_dump:
        win.dump_page.load_file(app_dump[0])
        shot("03_dump_user.png")

    evtx_path = sys.argv[1] if len(sys.argv) > 1 else r"C:\Users\MSI\Desktop\3ａ.evtx"
    if os.path.isfile(evtx_path):
        events, stats = evtx.query_events(evtx_path, True, "*", limit=80000)
        win.btn_log.setChecked(True)
        win._switch(0)
        win.log_page._on_events(events, stats)
        shot("04_log_dark.png")

        # v2.0.0 起只有深色，原来的浅色三张截图已经删掉
    print("smoke ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
