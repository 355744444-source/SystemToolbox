# -*- coding: utf-8 -*-
"""量一下系统优化各表格的实际列宽，确认设置是否生效。"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", os.environ.get("CLS_QPA", "windows"))

from PyQt5.QtWidgets import QApplication  # noqa: E402

from app import theme  # noqa: E402
from app.ui.optimize_page import OptimizePage  # noqa: E402


def main():
    log = open("probe_cols.log", "w", encoding="utf-8", buffering=1)
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    theme.apply_theme(app, "dark")
    page = OptimizePage()
    page.resize(1320, 720)
    page.show()
    for _ in range(20):
        app.processEvents()
        time.sleep(0.03)

    tabs = (
        ("启动项", page.tab_startup, page.tab_startup.table),
        ("服务", page.tab_services, page.tab_services.table),
        ("微调", page.tab_tweaks, page.tab_tweaks.table),
    )
    for name, tab, table in tabs:
        page.tabs.setCurrentWidget(tab)
        for _ in range(20):
            app.processEvents()
            time.sleep(0.03)
        widths = [table.columnWidth(i) for i in range(len(table.model().cols))]
        titles = [c.title or "☑" for c in table.model().cols]
        log.write(f"{name}: table={table.width()} viewport={table.viewport().width()}\n")
        for t, w in zip(titles, widths):
            log.write(f"   {t:<10} {w}\n")
    log.write("DONE")
    log.close()


if __name__ == "__main__":
    main()
