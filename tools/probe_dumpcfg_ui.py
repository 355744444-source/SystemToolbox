# -*- coding: utf-8 -*-
"""探针 v1.13：崩溃转储体检页的渲染与截图。

两件事：
1. 页面能把 dumpcfg 的结论画出来（卡片、KPI、三张表）
2. 深/浅两个主题下版式不塌（KPI 不折行、表格列不被挤成 0 宽）
"""
from __future__ import annotations

import os
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "windows")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

OUT = os.path.join(ROOT, "probe_dumpcfg_ui.log")
SHOTS = os.path.join(ROOT, "probe_shots")


def main() -> int:
    log = []
    try:
        from PyQt5.QtCore import Qt
        from PyQt5.QtGui import QFont
        from PyQt5.QtWidgets import QApplication

        from app import theme
        from app.ui.main_window import MainWindow

        QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
        QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
        app = QApplication(sys.argv)
        app.setStyle("Fusion")
        app.setFont(QFont("Microsoft YaHei", 9))
        theme.apply_theme(app, "dark")

        win = MainWindow()
        win.setAttribute(Qt.WA_DontShowOnScreen, True)
        win.resize(1440, 900)
        win.show()

        def pump(n=8):
            for _ in range(n):
                app.processEvents()
                time.sleep(0.01)

        os.makedirs(SHOTS, exist_ok=True)

        def grab(name):
            pump(30)
            path = os.path.join(SHOTS, name)
            ok = win.grab().save(path)
            log.append("%s ok=%s %dB" % (name, ok,
                                         os.path.getsize(path) if ok else 0))

        pump(10)
        win._switch(6)                     # 系统体检
        pump(10)
        page = win.page(6)
        page.tabs.setCurrentWidget(page.tab_dc)
        page.load_dumpcfg()
        # 等后台线程：最多 8 秒
        t0 = time.time()
        while page.dc_snap is None and time.time() - t0 < 8:
            pump(6)
        log.append("snap=%s findings=%d" % (page.dc_snap is not None,
                                            len(page.dc_findings)))
        if page.dc_snap is not None:
            s = page.dc_snap
            log.append("type=%s pf=%dMB ram=%dGB dumps=%d crashes=%d"
                       % (s.type_text, s.pagefile_bytes // 1048576,
                          s.ram // 1073741824, len(s.dumps), len(s.crashes)))
            log.append("headline=" + page.dc_headline.text())
            log.append("kpi: type=%r pf=%r need=%r file=%r crash=%r"
                       % (page.dc_type.val.text(), page.dc_pf.val.text(),
                          page.dc_need.val.text(), page.dc_file.val.text(),
                          page.dc_crash.val.text()))
            log.append("kpi_subs: %r / %r / %r / %r / %r"
                       % (page.dc_type.sub.text(), page.dc_pf.sub.text(),
                          page.dc_need.sub.text(), page.dc_file.sub.text(),
                          page.dc_crash.sub.text()))
            # 卡片数
            log.append("cards=%d" % (page.dc_cards._lay.count() - 1))
            log.append("tabs=%s" % [page.dc_tabs.tabText(i)
                                    for i in range(page.dc_tabs.count())])
            log.append("dump_model_rows=%d crash_model_rows=%d"
                       % (page.dc_dump_model.rowCount(),
                          page.dc_crash_model.rowCount()))
            # 列宽是否被挤没；stack 的 0 号才是表格
            tbl = page.dc_crash_stack.widget(0)
            log.append("crash_table_cols=%s"
                       % [tbl.columnWidth(i)
                          for i in range(tbl.model().columnCount())])
            log.append("split_sizes=%s" % (page.dc_split.sizes(),))

        grab("dc_dark.png")

        # 切到崩溃记录页签再拍一张
        page.dc_tabs.setCurrentIndex(1)
        grab("dc_dark_crashes.png")
        page.dc_tabs.setCurrentIndex(2)
        grab("dc_dark_raw.png")
        page.dc_tabs.setCurrentIndex(0)

        # 走真实的切主题路径（整窗重建）。手工 apply_theme(target=win) 不会
        # 重新 polish 动态属性选择器，卡片会留着旧主题的内联色 —— 那是探针假象。
        # 浅色皮肤已移除（v2.0.0），此处不再切主题
        pump(40)
        win._switch(6)
        pump(10)
        page = win.page(6)
        page.tabs.setCurrentWidget(page.tab_dc)
        page.load_dumpcfg()
        t0 = time.time()
        while page.dc_snap is None and time.time() - t0 < 8:
            pump(6)
        log.append("light snap=%s findings=%d"
                   % (page.dc_snap is not None, len(page.dc_findings)))
        grab("dc_light.png")
        page.dc_tabs.setCurrentIndex(1)
        grab("dc_light_crashes.png")

        log.append("exit clean")
    except Exception as exc:               # noqa: BLE001
        import traceback
        log.append("EXC: %s" % exc)
        log.append(traceback.format_exc())
    # 先落盘再退出：体检页被抓图时会顺带启动 run_all，关窗要等 DISM 等后台任务，
    # 等不及会走 os._exit —— 日志就丢了。探针只关心渲染，写盘后直接退出。
    with open(OUT, "w", encoding="utf-8") as fh:
        fh.write("\n".join(log) + "\n")
    os._exit(0)


if __name__ == "__main__":
    sys.exit(main())
