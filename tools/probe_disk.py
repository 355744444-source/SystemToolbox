# -*- coding: utf-8 -*-
"""验证「磁盘优化」页签懒加载：切过去后物理盘健康能正常回填。"""
from __future__ import annotations

import os
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "windows")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

LOG = open("probe_disk.log", "w", encoding="utf-8", buffering=1)


def w(s):
    LOG.write(str(s) + "\n")


from PyQt5.QtWidgets import QApplication  # noqa: E402

import main as app_main  # noqa: E402


def pump(app, n=10, dt=0.02):
    for _ in range(n):
        app.processEvents()
        time.sleep(dt)


def main():
    app = app_main._make_app([sys.argv[0]])
    win = app_main.MainWindow()
    win.resize(1440, 900)
    win.show()
    pump(app, 20)

    win.rail_buttons[2].setChecked(True)
    win._switch(2)
    pump(app, 10)

    dt = win.clean_page.tab_disk
    w(f"before show: phy_loaded={dt._phy_loaded} text={dt.phy_label.text()!r}")
    win.clean_page.tabs.setCurrentIndex(3)
    pump(app, 10)
    w(f"after show : phy_loaded={dt._phy_loaded} text={dt.phy_label.text()[:70]!r}")

    t0 = time.time()
    while time.time() - t0 < 25:
        if "正在读取" not in dt.phy_label.text():
            break
        app.processEvents()
        time.sleep(0.1)
    w(f"settled in {time.time() - t0:.1f}s -> {dt.phy_label.text()[:120]!r}")
    os.makedirs("shots", exist_ok=True)
    win.grab().save("shots/disk_tab.png")
    w(f"volumes={len(dt.volumes)} cards={len(dt.cards)}")
    win.close()
    w("DONE")
    LOG.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
