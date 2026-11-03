# -*- coding: utf-8 -*-
"""压力验证：测速跑到一半直接关窗，看会不会崩（0xC0000409 / 退出码 127）。

跑法：python tools/probe_speed_close.py ；退出码与 probe_speed_close.log 都要看。
"""
from __future__ import annotations

import os
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "windows")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

OUT = os.path.join(ROOT, "probe_speed_close.log")
LOG = open(OUT, "w", encoding="utf-8", buffering=1)


def w(s):
    LOG.write(str(s) + "\n")


from PyQt5.QtCore import QThread  # noqa: E402
from PyQt5.QtWidgets import QApplication  # noqa: E402

from app import theme  # noqa: E402
from app.ui.main_window import MainWindow  # noqa: E402


def main() -> int:
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    theme.apply_theme(app, "dark")
    win = MainWindow()
    win.resize(1440, 900)
    win.show()
    app.processEvents()

    win.btn_net.setChecked(True)
    win._switch(5)
    t0 = time.time()
    while win.net_page.rep is None and time.time() - t0 < 60:
        app.processEvents()
        time.sleep(0.03)
    w(f"diagnose done {time.time() - t0:.1f}s")

    np = win.net_page
    np.tabs.setCurrentIndex(3)
    np.cmb_sec.setCurrentIndex(0)
    np.start_speed()
    w(f"speed started busy={np._speed_busy}")

    # 等它进到下载中段再关
    t1 = time.time()
    while time.time() - t1 < 4.0:
        app.processEvents()
        time.sleep(0.03)
    running = [t for t in win.findChildren(QThread) if t.isRunning()]
    w(f"before close: elapsed={time.time() - t1:.1f}s threads_running={len(running)} "
      f"note={np.speed_note.text()[:40]!r} down_kpi={np.k_dl.val.text()!r}")

    win.close()
    app.processEvents()
    time.sleep(0.2)
    app.processEvents()

    left = []
    try:
        left = [t for t in win.findChildren(QThread) if t.isRunning()]
    except RuntimeError:
        w("window already destroyed")
    w(f"after close: threads_running={len(left)}")
    w("closed cleanly")
    return 0


if __name__ == "__main__":
    rc = main()
    w(f"probe rc={rc}")
    sys.exit(rc)
