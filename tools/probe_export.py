# -*- coding: utf-8 -*-
"""验证表格的复制 / 导出 CSV，以及右键菜单是否挂到了各页表格上。"""
from __future__ import annotations

import os
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "windows")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

OUT = os.path.join(ROOT, "probe_export.log")
CSV = os.path.join(ROOT, "probe_export.csv")


def main() -> int:
    log = []

    def w(s=""):
        log.append(str(s))
        with open(OUT, "w", encoding="utf-8") as f:
            f.write("\n".join(log))

    from PyQt5.QtCore import Qt
    from PyQt5.QtGui import QFont
    from PyQt5.QtWidgets import QApplication, QTableView

    from app import theme
    from app.ui.main_window import MainWindow
    from PyQt5.QtWidgets import QTableView as _TV

    from app.ui.tables import (Col, RowModel, copy_table, export_table_csv,
                               table_tsv, view_tsv)

    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setFont(QFont("Microsoft YaHei", 9))
    theme.apply_theme(app, "dark")

    # ---- 1) 纯逻辑：TSV / CSV
    model = RowModel([
        Col("状态", 60, lambda r: r["st"]),
        Col("条目", 0, lambda r: r["path"]),
        Col("说明", 0, lambda r: r.get("note", "")),
    ])
    model.set_rows([
        {"st": "缺失", "path": r"C:\Tools\a.exe", "note": "目录不存在"},
        {"st": "重复", "path": r"C:\Tools\b.exe", "note": "同级重复"},
    ])
    tsv = table_tsv(model)
    w("TSV 行数=%d 首行=%s" % (len(tsv.splitlines()), tsv.splitlines()[0]))
    tv0 = _TV()
    tv0.setModel(model)
    n = copy_table(tv0)
    w("复制 %d 行 -> 剪贴板%s" % (n, "非空" if QApplication.clipboard().text() else "空"))
    w("view_tsv 与 table_tsv 一致=%s" % (view_tsv(tv0) == tsv))
    n = export_table_csv(tv0, CSV)
    raw = open(CSV, "rb").read()
    w("CSV %d 行 %d 字节 BOM=%s" % (n, len(raw), raw[:3] == b"\xef\xbb\xbf"))
    w("CSV 内容: " + raw.decode("utf-8-sig").replace("\r\n", " | "))

    # ---- 2) 勾选表要跳过勾选列
    model2 = RowModel([Col("", 30, lambda r: ""), Col("名称", 0, lambda r: r)],
                      checkable=True)
    model2.set_rows(["甲", "乙"])
    w("勾选表 TSV 首行=「%s」 列数=%d"
      % (table_tsv(model2).splitlines()[0],
         len(table_tsv(model2).splitlines()[0].split("\t"))))

    # ---- 3) 真实页面里的表格都挂上了菜单
    win = MainWindow()
    win.setAttribute(Qt.WA_DontShowOnScreen, True)
    win.resize(1440, 900)
    win.show()

    def pump(ms):
        end = time.perf_counter() + ms / 1000.0
        while time.perf_counter() < end:
            app.processEvents()
            time.sleep(0.01)

    pump(500)
    total = 0
    hooked = 0
    for i in range(8):
        page = win.page(i)
        pump(120)
        tables = page.findChildren(QTableView)
        total += len(tables)
        hooked += sum(1 for t in tables if getattr(t, "_menu_on", False))
    w("八个工作台表格 %d 张，挂了菜单 %d 张" % (total, hooked))

    # ---- 4) 多选能拿到多行
    page = win.page(3)
    win._switch(3)
    pump(150)
    tv = page.findChildren(QTableView)[0]
    m = tv.model()
    if m.rowCount() == 0:
        w("残留表当前为空（未扫描），用假数据验多选")
        m.set_rows([{"a": 1}, {"a": 2}, {"a": 3}])
    from PyQt5.QtCore import QItemSelectionModel
    sel = tv.selectionModel()
    sel.select(tv.model().index(0, 0), QItemSelectionModel.Select | QItemSelectionModel.Rows)
    if tv.model().rowCount() > 1:
        sel.select(tv.model().index(1, 0), QItemSelectionModel.Select | QItemSelectionModel.Rows)
    w("多选后选中行数=%d（表格模式=%s）"
      % (len(sel.selectedRows()),
         "多选" if tv.selectionMode() == QTableView.ExtendedSelection else "单选"))

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
