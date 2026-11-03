# -*- coding: utf-8 -*-
"""表格排序/筛选实测：真窗口 + 真数据，点表头、输关键字、看行数与顺序。

结果写 probe_tables.txt（Bash 里 stdout 常丢）。
"""
import io
import os
import sys
import time

os.environ["QT_QPA_PLATFORM"] = "windows"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

LOG = os.path.join(ROOT, "probe_tables.txt")
OUT = os.path.join(ROOT, "probe_out")
os.makedirs(OUT, exist_ok=True)
buf = []


def w(msg):
    buf.append(str(msg))
    with io.open(LOG, "w", encoding="utf-8") as f:
        f.write("\n".join(buf))


from PyQt5.QtCore import Qt  # noqa: E402
from PyQt5.QtWidgets import QApplication  # noqa: E402

QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
app = QApplication(sys.argv)

from app import theme  # noqa: E402
from app.ui.main_window import MainWindow  # noqa: E402

theme.apply_theme(app, "dark")


def pump(ms):
    end = time.time() + ms / 1000.0
    while time.time() < end:
        app.processEvents()
        time.sleep(0.01)


try:
    win = MainWindow()
    win.resize(1440, 900)
    win.show()
    pump(400)

    # ---------- 体检页：设备清单（354 行）+ IO 告警（418 行）
    win.rail_buttons[6].setChecked(True)
    win._switch(6)
    pump(300)
    page = win.page(6)
    t0 = time.time()
    while time.time() < t0 + 60 and page.dev_stats is None:
        pump(200)
    pump(600)

    pane = page.dev_pane
    m = page.dev_model
    w("设备清单：源 %d 行，显示 %d 行，提示=%r"
      % (m.total_count(), len(m.rows()), pane.hint.text()))
    first = [m.rows()[i].name for i in range(min(5, len(m.rows())))]
    w("  原始前 5: %s" % first)

    # 先直接调模型（不经表头），把「模型 reset」和「表头信号」两件事分开验
    m.sort_by(1)
    pump(200)
    w("  直接 sort_by(1)：sort_state=%s 前 5=%s"
      % (m.sort_state(),
         [m.rows()[i].name for i in range(min(5, len(m.rows())))]))
    m.sort_by(-1)
    pump(120)

    # 点「设备」列表头 → 走 Qt 自己的路径（等价于真点一下）
    pane.view.sortByColumn(1, Qt.AscendingOrder)
    pump(120)
    w("  点表头1后 sort_state=%s 指示器=%s" %
      (m.sort_state(), pane.view.horizontalHeader().isSortIndicatorShown()))
    w("  排序后前 5: %s" % [m.rows()[i].name for i in range(min(5, len(m.rows())))])
    pane.view.sortByColumn(1, Qt.DescendingOrder)
    pump(120)
    w("  再点一次（降序）: %s" % (m.sort_state(),))

    # 筛选
    pane.edit.setText("BULK")
    pump(200)
    shown = len(m.rows())
    w("  筛选 BULK：显示 %d / %d，提示=%r"
      % (shown, m.total_count(), pane.hint.text()))
    w("  筛选结果前 3: %s" % [r.name for r in m.rows()[:3]])
    if shown >= m.total_count() or shown == 0:
        w("  FAILED: 筛选没生效")
    pane.edit.setText("")
    pump(150)
    w("  清空筛选：显示 %d" % len(m.rows()))

    # 横着看一眼筛选条有没有把表格挤歪
    win.grab().save(os.path.join(OUT, "tables_dev.png"))

    # ---------- 磁盘页 IO 告警 418 行（页签是懒建的，主动扫一次）
    page.tabs.setCurrentIndex(5)
    pump(300)
    page.load_smart()
    t1 = time.time()
    while time.time() < t1 + 60 and page.io_model.total_count() == 0:
        pump(300)
    io_m = page.io_model
    w("IO 告警：源 %d 行，提示=%r" % (io_m.total_count(), page.io_pane.hint.text()))
    page.io_pane.view.sortByColumn(3, Qt.AscendingOrder)   # 次数列
    pump(150)
    counts = [io_m.rows()[i][6] for i in range(min(5, len(io_m.rows())))]
    w("  按次数升序前 5: %s" % counts)
    if counts != sorted(counts):
        w("  FAILED: 次数列没按数值升序")
    page.io_pane.edit.setText("ID 51")
    pump(200)
    w("  筛选 'ID 51'：%d 行" % len(io_m.rows()))
    page.io_pane.edit.setText("")

    # ---------- 残留治理：注册表冗余
    win.rail_buttons[3].setChecked(True)
    win._switch(3)
    pump(500)
    rp = win.page(3)
    tab = rp.tab_reg
    tab.scan()
    t2 = time.time()
    while time.time() < t2 + 90 and tab.model.total_count() == 0:
        pump(300)
    pump(300)
    w("注册表冗余 源 %d 行 提示=%r 排序入口=%s"
      % (tab.model.total_count(), tab.pane.hint.text(),
         tab.view_is_sortable if hasattr(tab, "view_is_sortable") else
         tab.pane.view.isSortingEnabled()))
    if tab.model.total_count():
        tab.pane.view.sortByColumn(1, Qt.AscendingOrder)
        pump(150)
        w("  点「类别」表头 sort_state=%s 前 3=%s"
          % (tab.model.sort_state(),
             [r.category for r in tab.model.rows()[:3]]))
        win.grab().save(os.path.join(OUT, "tables_residue.png"))
    w("DONE")
except Exception as exc:                      # noqa: BLE001
    import traceback
    w("EXC " + repr(exc))
    w(traceback.format_exc())

os._exit(0)
