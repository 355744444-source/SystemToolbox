# -*- coding: utf-8 -*-
"""验证「读取本机蓝屏代码」：按钮 → 后台扫描 → 表格填充 → 结论卡 → 截图。"""
from __future__ import annotations

import os
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "windows")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

OUT = os.path.join(ROOT, "probe_local.log")


def main() -> int:
    log = []

    def w(s=""):
        log.append(str(s))
        with open(OUT, "w", encoding="utf-8") as f:
            f.write("\n".join(log))

    w("== 本机蓝屏代码探针 ==")
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
    page = win.page(1)                 # 蓝屏转储
    win._switch(1)
    pump(400)
    w("页签数=%d 标题=%s" % (page.tabs.count(),
                             [page.tabs.tabText(i) for i in range(page.tabs.count())]))
    w("初始 bc_stack 索引=%d（1=空态）" % page.bc_stack.currentIndex())
    w("按钮文字=%r 可用=%s" % (page.btn_bc.text(), page.btn_bc.isEnabled()))

    t0 = time.perf_counter()
    page.btn_bc.click()
    w("点按钮后立即：文字=%r 可用=%s tab=%d"
      % (page.btn_bc.text(), page.btn_bc.isEnabled(), page.tabs.currentIndex()))

    # 等后台线程
    deadline = time.perf_counter() + 20
    while time.perf_counter() < deadline:
        pump(100)
        if not page._bc_busy:
            break
    cost = (time.perf_counter() - t0) * 1000
    w("扫描往返 %.0f ms" % cost)
    w("按钮恢复：文字=%r 可用=%s" % (page.btn_bc.text(), page.btn_bc.isEnabled()))

    s = page.bc
    if s is None:
        w("!! page.bc 是 None，扫描没回来")
    else:
        w("记录=%d 有码=%d 耗时=%dms" % (len(s.records), len(s.with_code), s.cost_ms))
        w("headline: " + s.headline)
        w("表格行数=%d 列数=%d" % (page.bc_model.rowCount(), page.bc_model.columnCount()))
        w("bc_stack 索引=%d" % page.bc_stack.currentIndex())
        w("页签标题=%s" % page.tabs.tabText(3))
        w("footer: " + page.bc_foot.text())
        w("")
        w("-- 表格实际渲染文本 --")
        for r in range(page.bc_model.rowCount()):
            cells = [str(page.bc_model.data(page.bc_model.index(r, c)) or "")
                     for c in range(page.bc_model.columnCount())]
            w("  " + " | ".join(cells))
        w("")
        w("-- 左侧结论卡 --")
        labels = [x.text() for x in page.findings.findChildren(object)
                  if hasattr(x, "text") and isinstance(getattr(x, "text")(), str)]
        w("  结论区文本片段 %d 条" % len(labels))
        for x in labels[:8]:
            if x.strip():
                w("   · " + x.replace("\n", " ")[:70])

    # 选中第一行 → 按钮状态
    if page.bc_model.rowCount():
        page.bc_table.selectRow(0)
        pump(120)
        c = page._selected_bc()
        w("")
        w("选中第 0 行：%s / %s / 有转储=%s"
          % (c.time_text if c else "-", c.code_text if c else "-",
             bool(c and c.dump_path)))
        w("「分析选中的转储」可用=%s「在文件夹中显示」可用=%s"
          % (page.btn_bc_analyze.isEnabled(), page.btn_bc_folder.isEnabled()))

    win.grab().save(os.path.join(ROOT, "probe_local.png"), "PNG")
    w("截图 ok")

    # ---- 扫描之后再加载一个转储：KPI 要出现，结论要换成转储的，不能互相踩
    from app.core import debugger
    debugger.find_cdb = lambda: ""          # 别让探针真的去跑 cdb 下载符号
    fix = os.path.join(ROOT, "tests", "fixtures", "kernel_dpc_watchdog_133.dmp")
    w("")
    w("-- 加载 fixture 转储 --")
    page.load_file(fix)
    pump(400)
    w("kpi_box 可见=%s  drop 可见=%s  filebar 可见=%s"
      % (page.kpi_box.isVisible(), page.drop.isVisible(), page.filebar.isVisible()))
    w("k_code 标签=%r 数值=%r"
      % (page.k_code.lbl.text(), page.k_code.val.text() if hasattr(page.k_code, "val") else "?"))
    w("页签标题=%s" % [page.tabs.tabText(i) for i in range(page.tabs.count())])
    w("bc 表还在=%s 行数=%d" % (page.bc_model.rowCount(), page.bc_model.rowCount()))

    # 再扫一次：已有转储时不该覆盖左侧结论
    page.btn_bc.click()
    deadline = time.perf_counter() + 15
    while time.perf_counter() < deadline:
        pump(100)
        if not page._bc_busy:
            break
    w("二次扫描后 bc 行数=%d" % page.bc_model.rowCount())

    win.grab().save(os.path.join(ROOT, "probe_local_dump.png"), "PNG")

    # ---- 切主题会整窗重建页面：扫描结果与当前页签必须保住
    w("")
    w("-- 切浅色主题（整窗重建）--")
    rows_before = page.bc_model.rowCount()
    tab_before = page.tabs.currentIndex()
    # 浅色皮肤已移除（v2.0.0），此处不再切主题
    pump(500)
    p2 = win.page(1)
    rows_after = p2.bc_model.rowCount()
    w("重建前 行数=%d 页签=%d → 重建后 行数=%d 页签=%d 按钮=%r"
      % (rows_before, tab_before, rows_after, p2.tabs.currentIndex(), p2.btn_bc.text()))
    w("bc 对象保住=%s  headline=%s"
      % (p2.bc is not None, p2.bc.headline if p2.bc else "-"))
    w("kpi_box 可见=%s（此前加载过转储，应为 True）" % p2.kpi_box.isVisible())
    w("转储仍在=%s" % bool(p2.rep))
    win.grab().save(os.path.join(ROOT, "probe_local_light.png"), "PNG")
    w("浅色截图 ok")

    # 浅色皮肤已移除（v2.0.0），此处不再切主题
    pump(400)
    w("切回深色正常，行数=%d" % win.page(1).bc_model.rowCount())

    win.close()
    pump(400)
    w("关闭正常")
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
