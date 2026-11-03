"""进程表文字发灰：是绘制残留还是着色问题。"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "probe_v1140_c.log")
log = []


def p(s=""):
    log.append(str(s))


def main() -> int:
    from PyQt5.QtWidgets import QApplication
    app = QApplication(sys.argv)
    from app.ui.main_window import MainWindow
    from app.core import locks

    win = MainWindow()
    win.resize(1400, 900)
    win.show()

    def pump(ms=140):
        end = time.perf_counter() + ms / 1000.0
        while time.perf_counter() < end:
            app.processEvents()
            time.sleep(0.01)

    pump(1200)
    win._switch(3)
    pump(700)
    page = win.page(3)
    page.tabs.setCurrentWidget(page.tab_lock)
    tab = page.tab_lock
    pump(300)

    procs = [
        locks.LockProcess(pid=4242, name="explorer.exe", service="", app_type=4,
                          status=1, restartable=False,
                          exe=r"C:\Windows\explorer.exe"),
        locks.LockProcess(pid=888, name="Everything.exe", service="", app_type=2,
                          status=1, restartable=True,
                          exe=r"C:\Program Files\Everything\Everything.exe"),
    ]
    tab.proc_model.set_rows(procs)
    tab.proc_stack.setCurrentIndex(0)
    pump(120)
    win.grab().save(os.path.join("probe_shots", "_paint_a.png"), "PNG")

    pump(900)
    win.grab().save(os.path.join("probe_shots", "_paint_b.png"), "PNG")

    tab.proc_table.viewport().repaint()
    pump(150)
    win.grab().save(os.path.join("probe_shots", "_paint_c.png"), "PNG")

    # 直接问模型：这两个列返回的前景角色是什么
    from PyQt5.QtCore import Qt
    from PyQt5.QtGui import QColor
    for c in range(tab.proc_model.columnCount()):
        idx = tab.proc_model.index(0, c)
        col = tab.proc_model.cols[c]
        fg = tab.proc_model.data(idx, Qt.ForegroundRole)
        p("col=%d %-8s color=%-6r fn=%s fg=%s" %
          (c, col.title, col.color, bool(col.color_fn),
           (fg.name() if isinstance(fg, QColor) else fg)))

    from app import theme
    t = theme.current()
    p("theme text=%s dim=%s faint=%s name=%s"
      % (t["text"], t["dim"], t["faint"], theme.current_name()
         if hasattr(theme, "current_name") else "?"))

    lines = open(OUT, "w", encoding="utf-8")
    lines.write("\n".join(log) + "\n")
    lines.close()
    win.close()
    pump(80)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:      # noqa: BLE001
        import traceback
        p("EXC %s" % exc)
        p(traceback.format_exc())
        open(OUT, "w", encoding="utf-8").write("\n".join(log) + "\n")
        os._exit(1)
