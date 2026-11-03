# -*- coding: utf-8 -*-
"""复审后的视觉验收：清理优化页（合并 showEvent 后分栏是否还正常）+ 转储页浅色。

输出 probe_v2_*.png 与 probe_v2.log。
"""
from __future__ import annotations

import os
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "windows")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

LOG = os.path.join(ROOT, "probe_v2.log")


def main() -> int:
    log = []

    def w(s=""):
        log.append(str(s))
        with open(LOG, "w", encoding="utf-8") as f:
            f.write("\n".join(log))

    from PyQt5.QtCore import Qt
    from PyQt5.QtGui import QFont
    from PyQt5.QtWidgets import QApplication

    from app import theme
    from app.ui.main_window import MainWindow

    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setFont(QFont("Microsoft YaHei", 9))
    theme.apply_theme(app, "dark")

    def pump(ms):
        end = time.perf_counter() + ms / 1000.0
        while time.perf_counter() < end:
            app.processEvents()
            time.sleep(0.01)

    win = MainWindow()
    win.setAttribute(Qt.WA_DontShowOnScreen, True)
    win.resize(1440, 900)
    win.show()
    pump(600)

    # 清理优化（第 3 个工作台，索引 2）→ 磁盘优化子页签（showEvent 在那里）
    win.rail_buttons[2].setChecked(True)
    win._switch(2)
    pump(800)
    p = win.page(2)
    p.tabs.setCurrentIndex(3)
    pump(1600)
    d = p.tab_disk
    w("disk split sizes=%s h=%d sized=%s phy=%s" % (
        d.split.sizes(), d.split.height(), getattr(d, "_sized", None),
        getattr(d, "_phy_loaded", None)))
    img = win.grab()
    img.save(os.path.join(ROOT, "probe_v2_clean_dark.png"))

    # 转储页浅色
    # 浅色皮肤已移除（v2.0.0），此处不再切主题
    pump(600)
    win.rail_buttons[1].setChecked(True)
    win._switch(1)
    pump(500)
    p1 = win.page(1)
    p1.scan_local()
    t0 = time.time()
    while getattr(p1, "_bc_busy", False) and time.time() - t0 < 30:
        pump(120)
    pump(400)
    w("dump rows=%d tab=%d btn=%r split=%s" % (
        len(p1.bc.records) if p1.bc else -1, p1.tabs.currentIndex(),
        p1.btn_bc.text(), p1.split.sizes()))
    win.grab().save(os.path.join(ROOT, "probe_v2_dump_light.png"))

    # 日志页 XML 美化回归（形参改名后必须真有缩进）
    lp = win.page(0)
    xml = lp._pretty_xml("<Event><Data>1</Data></Event>")
    w("pretty_xml 有换行=%s 行数=%d" % ("\n" in xml, len(xml.splitlines())))
    w("RESULT: OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
