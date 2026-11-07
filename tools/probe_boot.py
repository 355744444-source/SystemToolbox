# -*- coding: utf-8 -*-
"""第九工作台（开机诊断）探针：用合成事件渲染，检查布局与截图文案。

真机上这个通道要管理员才读得到，开发机拿不到数据，所以这里把
`tests/boot_fixtures.make_scan()` 造的样本直接灌进页面，再截图。

查三件事：
1. 九个导航按钮在最小窗口高度下放不放得下（第 10 个会溢出）；
2. 结论条、KPI、堆叠柱、阶段表的实际几何有没有挤在一起；
3. 四个页签都能渲染、表格行数与样本一致。
"""
from __future__ import annotations

import os
import sys
import time

# 必须用真平台 + WA_DontShowOnScreen：offscreen 插件在本机不上屏，
# 但它拿不到字体引擎 —— 截出来的图只有色块，没有一行文字（实测 567 B 的空白图）。
os.environ.setdefault("QT_QPA_PLATFORM", os.environ.get("CLS_QPA", "windows"))
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))

DOCS = os.path.join(ROOT, "docs")


def main() -> int:
    from PyQt5.QtCore import Qt
    from PyQt5.QtGui import QFont
    from PyQt5.QtWidgets import QApplication

    import boot_fixtures as F

    from app import theme
    from app.ui.main_window import MainWindow

    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setFont(QFont("Microsoft YaHei", 9))
    theme.apply_theme(app)

    win = MainWindow()
    win.setAttribute(Qt.WA_DontShowOnScreen, True)
    win.resize(1440, 900)
    win.show()

    def pump(n=8):
        for _ in range(n):
            app.processEvents()
            time.sleep(0.01)

    # 先把第九页建出来、掐掉它自己的自动读取，再切过去 —— 否则真实扫描
    # （本机没有管理员权限，读到 0 条）会盖掉刚灌进去的样本
    page = win.page(8)
    page._started = True
    page._boot.stop()
    win.rail_buttons[8].setChecked(True)
    win._switch(8)

    scan = F.make_scan(count=6, boot_ms=62000)
    page._done(scan)
    pump(30)

    print("=== 导航几何 ===")
    rail = win.rail
    last = win.rail_buttons[-1]
    print("rail h=%d, 最后一个按钮 bottom=%d, 关于 bottom=%d"
          % (rail.height(), last.geometry().bottom(),
             win.btn_about.geometry().bottom()))
    print("溢出：" + ("是（按钮被裁掉）" if win.btn_about.geometry().bottom() > rail.height()
                     else "否"))

    print("=== 页面数据 ===")
    print("开机记录 %d · 慢项 %d · 关机睡眠 %d · 结论条可见=%s"
          % (scan.count, len(scan.slow), len(scan.misc), page.verdict.isVisible()))
    print("KPI 最近一次=%r 基线=%r 进系统前=%r 桌面后=%r 慢项=%r"
          % (page.k_last.val.text(), page.k_base.val.text(),
             page.k_main.val.text(), page.k_post.val.text(),
             page.k_slow.val.text()))
    print("KPI 脚注 基线=%r 慢项=%r"
          % (page.k_base.sub.text(), page.k_slow.sub.text()))
    print("阶段表 %d 行 · 慢项表 %d 行 · 历史表 %d 行 · 关机表 %d 行"
          % (page.model_phase.rowCount(), page.model_slow.rowCount(),
             page.model_hist.rowCount(), page.model_misc.rowCount()))

    names = []
    for tab, name in ((0, "42_开机诊断_概况_深色.png"),
                      (1, "43_开机诊断_慢项排行_深色.png"),
                      (2, "44_开机诊断_开机历史_深色.png"),
                      (3, "45_开机诊断_关机睡眠_深色.png"),
                      ):
        page.tabs.setCurrentIndex(tab)
        pump(24)
        path = os.path.join(DOCS, name)
        ok = win.grab().save(path)
        names.append((name, ok, os.path.getsize(path) if ok else 0))
    for name, ok, size in names:
        print("截图 %s ok=%s %dB" % (name, ok, size))

    page.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
