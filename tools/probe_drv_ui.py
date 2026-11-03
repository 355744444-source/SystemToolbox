# -*- coding: utf-8 -*-
"""驱动体检页签的离屏实测：建窗 → 切到该页 → 跑一次 → 抓数据 + 截图。"""
import os
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "windows")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
LOG = os.path.join(ROOT, "probe_drv_ui.log")
SHOT = os.path.join(ROOT, "probe_drv_ui.png")


def main():
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

    def pump(n=20):
        for _ in range(n):
            app.processEvents()
            time.sleep(0.01)

    pump(10)
    out = []
    t0 = time.time()
    win.rail_buttons[6].setChecked(True)
    win._switch(6)
    pump(10)
    hp = win.health_page
    out.append("页签数=%d 标题=%s"
               % (hp.tabs.count(),
                  [hp.tabs.tabText(i) for i in range(hp.tabs.count())]))
    hp.tabs.setCurrentWidget(hp.tab_drv)
    hp.load_drivers()
    while hp._busy.get("drv") and time.time() - t0 < 120:
        app.processEvents()
        time.sleep(0.05)
    pump(20)
    out.append("耗时 %.1fs busy=%s" % (time.time() - t0, hp._busy.get("drv")))
    rep = hp.drv_rep
    if rep is None:
        out.append("drv_rep 为空！headline=%s" % hp.drv_headline.text())
    else:
        out.append("headline=%s" % hp.drv_headline.text())
        out.append("包=%d 第三方=%d 文件=%d 未签名=%d 可疑=%d 模块=%d 缓存=%d"
                   % (len(rep.packages), len(rep.third_packages), len(rep.files),
                      len(rep.unsigned), len(rep.suspicious_sign), rep.modules,
                      rep.cache_hits))
        out.append("卡=%d 行(第三方包)=%d 行(文件,当前档)=%d 计数文案=%s"
                   % (len(hp.drv_findings), hp.drv_pkg_model.rowCount(),
                      hp.drv_file_model.rowCount(), hp.drv_count.text()))
        out.append("KPI: %s | %s | %s | %s"
                   % (hp.drv_kpi_pkg.val.text() if hasattr(hp.drv_kpi_pkg, "val") else "?",
                      hp.drv_kpi_files.val.text(), hp.drv_kpi_bad.val.text(),
                      hp.drv_kpi_mod.val.text()))
        out.append("子页签=%s" % [hp.drv_tabs.tabText(i) for i in range(hp.drv_tabs.count())])
        for f in hp.drv_findings:
            out.append("  [%s] %s" % (f.severity, f.title))
        # 三个档位都过一遍，确认不炸
        for k in ("live", "all", "bad"):
            hp.drv_chips.set_current(k)
            pump(4)
            out.append("  档位 %-4s → %d 行 %s" % (k, hp.drv_file_model.rowCount(),
                                                 hp.drv_count.text()))
    ok = win.grab().save(SHOT)
    out.append("截图 ok=%s %s" % (ok, os.path.getsize(SHOT) if ok else 0))

    with open(LOG, "w", encoding="utf-8") as fh:
        fh.write("\n".join(out))
    os._exit(0)


if __name__ == "__main__":
    main()
