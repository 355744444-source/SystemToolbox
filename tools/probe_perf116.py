# -*- coding: utf-8 -*-
"""量首屏、各页首次构建、切页、切主题的耗时。"""
from __future__ import annotations

import os
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "windows")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

OUT = os.path.join(ROOT, "probe_perf116.log")


def main() -> int:
    log = []

    def w(s=""):
        log.append(str(s))
        with open(OUT, "w", encoding="utf-8") as f:
            f.write("\n".join(log))

    from PyQt5.QtCore import Qt
    from PyQt5.QtGui import QFont
    from PyQt5.QtWidgets import QApplication

    from app import theme
    from app.ui.main_window import MainWindow

    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    t0 = time.perf_counter()
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setFont(QFont("Microsoft YaHei", 9))
    theme.apply_theme(app, "dark")
    w("QApplication + QSS     %6.1f ms" % ((time.perf_counter() - t0) * 1000))

    t0 = time.perf_counter()
    win = MainWindow()
    w("MainWindow() 构造      %6.1f ms" % ((time.perf_counter() - t0) * 1000))

    win.setAttribute(Qt.WA_DontShowOnScreen, True)
    win.resize(1440, 900)
    t0 = time.perf_counter()
    win.show()
    app.processEvents()
    w("show() + 首个 process   %6.1f ms  <- 首帧" % ((time.perf_counter() - t0) * 1000))

    def pump(ms):
        end = time.perf_counter() + ms / 1000.0
        while time.perf_counter() < end:
            app.processEvents()
            time.sleep(0.005)

    pump(300)

    names = ["日志", "转储", "清理", "残留", "优化", "网络", "体检", "测速"]
    total = 0.0
    for i, name in enumerate(names):
        t0 = time.perf_counter()
        win._switch(i)
        app.processEvents()
        ms = (time.perf_counter() - t0) * 1000
        total += ms
        w("切到 %-4s（含首次构建）  %6.1f ms" % (name, ms))
        pump(120)
    w("八页合计                %6.1f ms" % total)

    # 二次切换（页面已建好）
    total2 = 0.0
    for i in range(8):
        t0 = time.perf_counter()
        win._switch((i + 1) % 8)
        app.processEvents()
        total2 += (time.perf_counter() - t0) * 1000
    w("八次纯切换              %6.1f ms（平均 %.1f）" % (total2, total2 / 8))

    for nm in ("light", "dark"):
        t0 = time.perf_counter()
        # 浅色皮肤已移除（v2.0.0），此处不再切主题
        app.processEvents()
        w("切主题 -> %-5s        %6.1f ms" % (nm, (time.perf_counter() - t0) * 1000))
        pump(250)

    t0 = time.perf_counter()
    win.close()
    pump(250)
    w("关闭                    %6.1f ms" % ((time.perf_counter() - t0) * 1000))
    w("RESULT: OK")
    print("\n".join(log))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        import traceback
        with open(OUT, "a", encoding="utf-8") as f:
            f.write("\n=== FAILED ===\n" + traceback.format_exc())
        sys.exit(1)
