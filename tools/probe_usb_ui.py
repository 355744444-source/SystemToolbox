"""探针：第八工作台的构建、渲染与真实测速。

用 windows 平台 + WA_DontShowOnScreen 抓图（offscreen 平台不渲染文字）。
"""
from __future__ import annotations

import os
import sys
import time

os.environ["QT_QPA_PLATFORM"] = "windows"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import QFont
from PyQt5.QtWidgets import QApplication

from app import theme
from app.ui.main_window import MainWindow

LOG = os.path.join(ROOT, "tools", "probe_usb_ui.log")
SHOT = os.path.join(ROOT, "probe_shots")
lines: list[str] = []


def log(s: str) -> None:
    lines.append(s)
    with open(LOG, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


def pump(app, seconds: float) -> None:
    end = time.perf_counter() + seconds
    while time.perf_counter() < end:
        app.processEvents()
        time.sleep(0.01)


def main() -> None:
    os.makedirs(SHOT, exist_ok=True)
    app = QApplication([])
    app.setStyle("Fusion")
    app.setFont(QFont("Microsoft YaHei", 9))
    theme.apply_theme(app, "dark")
    win = MainWindow()
    win.setAttribute(Qt.WA_DontShowOnScreen, True)
    win.resize(1440, 900)
    win.show()
    pump(app, 0.4)

    log("stack 页数 = %d" % win.stack.count())
    win._switch(7)
    pump(app, 0.8)
    page = win.page(7)
    log("第 8 页 = %s" % type(page).__name__)
    log("可选盘 = %s" % [page.box.itemText(i) for i in range(page.box.count())])
    log("默认选中 = %r" % page.box.currentText())
    log("测试尺寸项 = %s" % [page.cmb_size.itemText(i)
                          for i in range(page.cmb_size.count())])
    win.grab().save(os.path.join(SHOT, "usb_空态.png"))
    log("空态截图已存")

    # 选一个 USB 盘
    usb_idx = -1
    for i, t in enumerate(page._targets):
        if t.is_usb and t.free > 1 << 30:
            usb_idx = i
            break
    if usb_idx < 0:
        log("没有可测的 USB 盘，跳过实测")
    else:
        page.box.setCurrentIndex(usb_idx)
        page.cmb_size.setCurrentIndex(0)          # 128 MB
        pump(app, 0.2)
        log("开始测 %s" % page.box.currentText())
        t0 = time.perf_counter()
        page.start()
        while page._busy and time.perf_counter() - t0 < 90:
            pump(app, 0.05)
        pump(app, 0.5)
        log("耗时 %.1f s" % (time.perf_counter() - t0))
        log("进度文字 = %s" % page.lbl_prog.text())
        log("KPI 读 = %s / 写 = %s / 比 = %s / 接口 = %s"
            % (page.k_read._full, page.k_write._full,
               page.k_ratio._full, page.k_bus._full))
        log("结果行 = %d" % page.model.rowCount())
        for r in page.model.rows():
            log("  %-8s %-12s %-14s %s" % r[:4])
        log("历史行 = %d" % page.hist_model.rowCount())
        win.grab().save(os.path.join(SHOT, "usb_结果.png"))
        log("结果截图已存")

    # 浅色主题下再看一眼重建是否正常
    # 浅色皮肤已移除（v2.0.0），此处不再切主题
    pump(app, 0.6)
    page2 = win.page(7)
    log("浅色重建后第 8 页 = %s, 盘数 = %d"
        % (type(page2).__name__, page2.box.count()))
    win.grab().save(os.path.join(SHOT, "usb_浅色.png"))
    log("浅色截图已存")
    win.close()
    pump(app, 0.2)


if __name__ == "__main__":
    main()
