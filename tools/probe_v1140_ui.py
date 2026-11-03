"""v1.14.0 界面探针：文件占用页签 + 环境变量页签。"""
import ctypes
import ctypes.wintypes as wt
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

SHOT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "probe_shots")
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "probe_v1140_ui.log")
log = []


def p(s=""):
    log.append(str(s))


def flush():
    with open(OUT, "w", encoding="utf-8") as fh:
        fh.write("\n".join(log) + "\n")


def main() -> int:
    from PyQt5.QtCore import Qt
    from PyQt5.QtWidgets import QApplication

    app = QApplication(sys.argv)
    from app import theme
    theme.apply_theme(app, "dark")     # 探针最容易漏的一步：不挂 QSS 就是 Qt 默认白底
    from app.ui.main_window import MainWindow

    win = MainWindow()
    win.resize(1400, 900)
    win.show()
    win.raise_()

    def pump(ms=140):
        end = time.perf_counter() + ms / 1000.0
        while time.perf_counter() < end:
            app.processEvents()
            time.sleep(0.01)

    def grab(name):
        pump(520)
        pm = win.grab()
        path = os.path.join(SHOT_DIR, name)
        pm.save(path, "PNG")
        p("shot %s %dx%d" % (name, pm.width(), pm.height()))

    # ---------- 文件占用
    win._switch(3)
    pump(900)
    page = win.page(3)
    page.tabs.setCurrentWidget(page.tab_lock)
    pump(300)
    tab = page.tab_lock

    tmp = tempfile.mkdtemp(prefix="cls_lockui_")
    held = os.path.join(tmp, "被占用-示例.dat")
    open(held, "wb").write(b"x" * 8192)
    open(os.path.join(tmp, "空闲-示例.dat"), "wb").write(b"y" * 128)
    os.makedirs(os.path.join(tmp, "sub"), exist_ok=True)

    k32 = ctypes.WinDLL("kernel32")
    k32.CreateFileW.restype = wt.HANDLE
    h = k32.CreateFileW(held, 0x80000000, 1, None, 3, 0, None)
    p("持有句柄 %r pid=%d" % (h, os.getpid()))

    tab.path_edit.setText(tmp)
    tab.scan()
    for _ in range(60):
        pump(120)
        if tab._rep is not None:
            break
    rep = tab._rep
    p("查询结果：files=%d locked=%d" % (rep.total if rep else -1,
                                        rep.locked_count if rep else -1))
    if rep:
        p("headline: %s" % tab.headline.text())
        p("KPI: files=%r locked=%r proc=%r cost=%r"
          % (tab.kpi_files.val.text(), tab.kpi_locked.val.text(),
             tab.kpi_proc.val.text(), tab.kpi_cost.val.text()))
        p("表格行数=%d 目录内文件=%s"
          % (tab.file_model.rowCount(), os.listdir(tmp)))
        for f in rep.files:
            p("   %-14s %-6s rc=%d %s" % (f.name, f.verdict, f.rc, f.detail[:40]))
    grab("lock_dark.png")

    # 单个被占文件
    tab.path_edit.setText(held)
    tab.scan()
    for _ in range(60):
        pump(120)
        if tab._rep is not None and tab._busy is False:
            break
    grab("lock_dark_single.png")

    # 系统独占样本
    tab.path_edit.setText(r"C:\pagefile.sys")
    tab.scan()
    for _ in range(40):
        pump(120)
        if tab._busy is False:
            break
    p("pagefile: headline=%s" % tab.headline.text())
    p("pagefile 文件表行数=%d" % tab.file_model.rowCount())

    # ---------- 环境变量
    win._switch(6)
    pump(700)
    hp = win.page(6)
    hp.tabs.setCurrentWidget(hp.tab_env)
    for _ in range(80):
        pump(120)
        if hp.env_rep is not None:
            break
    p("env: headline=%s" % hp.env_headline.text())
    p("env: KPI total=%r bad=%r dup=%r len=%r"
      % (hp.env_total.val.text(), hp.env_bad.val.text(),
         hp.env_dup.val.text(), hp.env_len.val.text()))
    p("env 表格行数=%d 页签=%s"
      % (hp.env_model.rowCount(),
         [hp.tabs.tabText(i) for i in range(hp.tabs.count())]))
    p("env 列宽=%s" % [hp.env_stack.widget(0).columnWidth(i)
                       for i in range(hp.env_model.columnCount())])
    p("env 卡片数=%d" % (hp.env_cards._lay.count() - 1))
    grab("env_dark.png")

    # 浅色主题（走整窗重建，别手工 apply_theme）
    # 浅色皮肤已移除（v2.0.0），此处不再切主题
    pump(900)
    hp = win.page(6)
    hp.tabs.setCurrentWidget(hp.tab_env)
    for _ in range(60):
        pump(120)
        if hp.env_rep is not None:
            break
    grab("env_light.png")

    # 文件占用浅色
    win._switch(3)
    pump(600)
    page = win.page(3)
    page.tabs.setCurrentWidget(page.tab_lock)
    page.tab_lock.path_edit.setText(tmp)
    page.tab_lock.scan()
    for _ in range(60):
        pump(120)
        if page.tab_lock._busy is False and page.tab_lock._rep is not None:
            break
    grab("lock_light.png")

    k32.CloseHandle(h)
    p("exit clean")
    flush()
    win.close()
    pump(60)
    return 0


if __name__ == "__main__":
    try:
        rc = main()
    except Exception as exc:               # noqa: BLE001
        import traceback
        p("EXC: %s" % exc)
        p(traceback.format_exc())
        flush()
        rc = 1
        os._exit(rc)                       # 别等后台线程，日志已落盘
    sys.exit(rc)
