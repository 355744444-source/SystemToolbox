# -*- coding: utf-8 -*-
"""只重拍一张：docs/39_蓝屏转储_本机蓝屏码_深色.png。

gen_docs.py 全套要跑三分钟（等 cdb、等体检、等测速），
调完列宽只为了这一张图时用它。参数与 gen_docs 里的同段保持一致。
"""
from __future__ import annotations

import os
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "windows")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

DOCS = os.path.join(ROOT, "docs")
OUT = os.path.join(ROOT, "probe_shot39.log")


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
    pump(500)

    win.btn_dump.setChecked(True)
    win._switch(1)
    pump(300)
    p1 = win.dump_page
    p1.scan_local()
    t0 = time.time()
    while getattr(p1, "_bc_busy", False) and time.time() - t0 < 30:
        app.processEvents()
        time.sleep(0.05)
    pump(200)

    # 把「名称」列的真实宽度和是否需要省略号量一下，别靠肉眼判断。
    # 单元格左右各留约 4px 内边距（QStyle::PM_FocusFrameHMargin），
    # 只比文本宽 8px 是不够的 —— 第一版就是这么被省略号坑了一次。
    fm = p1.bc_table.fontMetrics()
    need = max((fm.horizontalAdvance(r.name_text) for r in p1.bc_model.rows()),
               default=0)
    width = p1.bc_table.columnWidth(2)
    w("名称列宽=%d 文本宽=%d 含边距=%d 会省略=%s"
      % (width, need, need + 16, need + 16 > width))
    # 其余几列也一起量，省得修好一列又挤坏另一列
    for i, label in ((0, "时间"), (1, "蓝屏码"), (3, "来源"), (4, "说明")):
        want = max((fm.horizontalAdvance(
            str(p1.bc_model.data(p1.bc_model.index(r, i)) or ""))
            for r in range(p1.bc_model.rowCount())), default=0)
        cw = p1.bc_table.columnWidth(i)
        w("  %s 列宽=%d 文本宽=%d 会省略=%s" % (label, cw, want, want + 16 > cw))

    path = os.path.join(DOCS, "39_蓝屏转储_本机蓝屏码_深色.png")
    ok = win.grab().save(path, "PNG")
    w("shot=%s ok=%s size=%dB" % (os.path.basename(path), ok, os.path.getsize(path)))

    win.close()
    pump(300)
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
