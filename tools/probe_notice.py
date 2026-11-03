# -*- coding: utf-8 -*-
"""验证页内提示：note / ask 能显示、能量高度、不弹模态窗口。"""
from __future__ import annotations

import os
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "windows")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

OUT = os.path.join(ROOT, "probe_notice.log")


def main() -> int:
    log = []

    def w(s=""):
        log.append(str(s))
        with open(OUT, "w", encoding="utf-8") as f:
            f.write("\n".join(log))

    w("== 页内提示探针 ==")
    from PyQt5.QtCore import Qt
    from PyQt5.QtGui import QFont
    from PyQt5.QtWidgets import QApplication

    from app import theme
    from app.ui import notice
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

    pump(600)
    host = win.notice_host
    w("初始 host 可见=%s 高度=%d 条数=%d"
      % (host.isVisible(), host.height(), host.count))

    page = win.page(2)      # 清理优化
    base = page.height()
    w("页面初始高度=%d" % base)

    notice.note(page, "ok", "清理完成：释放 1.2 GB（共 328 个文件）")
    pump(200)
    w("note 后 host 可见=%s 高度=%d 条数=%d 页面高度=%d"
      % (host.isVisible(), host.height(), host.count, page.height()))

    notice.note(page, "warn", "有 2 个文件被占用，已跳过")
    notice.note(page, "error", "导出失败：目标目录不可写")
    pump(200)
    w("三条后 条数=%d host 高=%d" % (host.count, host.height()))
    win.grab().save(os.path.join(ROOT, "probe_notice_note.png"), "PNG")

    # 确认条
    fired = {"yes": 0, "no": 0}

    def on_yes():
        fired["yes"] += 1

    notice.ask(page, "确认清理", "将把 328 个文件移入回收站，共 1.2 GB。",
               ok_text="移入回收站", danger=True, on_yes=on_yes,
               on_no=lambda: fired.update(no=fired["no"] + 1))
    pump(200)
    asks = [b for b in host._bars if isinstance(b, notice.AskBar)]
    w("确认条数量=%d 按钮=%s / %s"
      % (len(asks), asks[0].btn_ok.text(), asks[0].btn_cancel.text()))
    w("危险按钮 objectName=%s" % asks[0].btn_ok.objectName())
    win.grab().save(os.path.join(ROOT, "probe_notice_ask.png"), "PNG")

    # 点确认
    asks[0].btn_ok.click()
    pump(300)
    w("点确认后 fired=%s 条数=%d" % (fired, host.count))

    # 清场
    notice.note(page, "info", "这条会被切页清掉")
    pump(100)
    win._switch(3)
    pump(300)
    w("切页后 条数=%d host 可见=%s" % (host.count, host.isVisible()))

    # 关窗不炸
    win.close()
    pump(300)
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
