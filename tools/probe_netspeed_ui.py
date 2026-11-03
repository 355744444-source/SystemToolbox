# -*- coding: utf-8 -*-
"""抓「网络诊断 → 宽带测速」页签的截图（空态 + 真机结果 + 浅色）。"""
from __future__ import annotations

import os
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "windows")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

OUT = os.path.join(ROOT, "probe_netspeed.log")
SHOTS = os.path.join(ROOT, "probe_shots")


def main() -> int:
    log = []
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

    def shot(name):
        pump(12)
        os.makedirs(SHOTS, exist_ok=True)
        path = os.path.join(SHOTS, name)
        ok = win.grab().save(path)
        log.append(f"{name} ok={ok} {os.path.getsize(path) if ok else 0}B")

    pump(10)
    win.btn_net.setChecked(True)
    win._switch(5)
    t0 = time.time()
    while win.net_page.rep is None and time.time() - t0 < 90:
        app.processEvents()
        time.sleep(0.05)
    log.append(f"诊断 {time.time() - t0:.1f}s")
    pump(10)

    np = win.net_page
    np.tabs.setCurrentIndex(3)
    pump(12)
    shot("netspeed_1_空态.png")
    log.append(f"split={np.split.sizes()} tabs_h={np.tabs.height()} "
               f"chart_h={np.chart.height()} verdict_h={np.speed_verdict.height()}")

    # 真跑一次
    np.cmb_sec.setCurrentIndex(0)
    t1 = time.time()
    np.start_speed()
    while np._speed_busy and time.time() - t1 < 90:
        app.processEvents()
        time.sleep(0.05)
    log.append(f"测速 {time.time() - t1:.1f}s rep={np.speed_rep is not None}")
    pump(20)
    shot("netspeed_2_结果.png")
    rep = np.speed_rep
    if rep:
        log.append(f"down={rep.down_mbps:.0f} up={rep.up_mbps:.0f} "
                   f"lat={rep.latency_ms:.0f} jit={rep.jitter_ms:.1f} "
                   f"node={rep.node} traffic={rep.traffic_mb:.0f}MB "
                   f"ratio={rep.ratio:.2f} level={rep.level}")
        log.append(f"samples={len(rep.samples)} "
                   f"table_h={np.speed_model.rowCount()} "
                   f"chart_h={np.chart.height()} verdict_h={np.speed_verdict.height()}")
        log.append("verdict=" + np.speed_verdict.text()[:400].replace("\n", " "))

    # 切浅色看配色
    # 浅色皮肤已移除（v2.0.0），此处不再切主题
    pump(40)
    np = win.page(5)
    np.tabs.setCurrentIndex(3)
    pump(20)
    shot("netspeed_3_浅色.png")
    log.append(f"浅色 speed_rep={np.speed_rep is not None} "
               f"down={(np.speed_rep.down_mbps if np.speed_rep else 0):.0f}")

    win.close()
    pump(10)
    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(log))
    print("done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
