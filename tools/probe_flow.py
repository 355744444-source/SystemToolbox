# -*- coding: utf-8 -*-
"""验证弹窗改页内后的流程：确认条出现 -> 点确认 -> 回调真的跑了。"""
from __future__ import annotations

import inspect
import os
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "windows")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

OUT = os.path.join(ROOT, "probe_flow.log")


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

    pump(500)

    # ---- 1) 拆分出来的回调方法是否存在、参数对不对
    # 绑定方法不含 self，所以参数个数就是调用方要给的个数
    expect = [
        (2, "GarbageTab", "_do_clean", 2),
        (2, "DiskTab", "_start_action", 1),
        (2, "LargeFileTab", "_do_delete", 1),
        (3, "RegistryTab", "_do_fix", 1),
        (3, "LeftoverTab", "_do_delete", 1),
        (4, "StartupTab", "_do_toggle", 3),
        (4, "ServicesTab", "_run_change", 2),
        (4, "TweaksTab", "_do_apply", 1),
        (4, "TweaksTab", "_do_revert_all", 0),
        (4, "ChangesTab", "_do_revert", 1),
    ]
    for idx, cls, meth, nargs in expect:
        page = win.page(idx)
        found = [o for o in page.findChildren(object)
                 if type(o).__name__ == cls]
        if not found:
            w("MISSING 容器 %s（第 %d 页）" % (cls, idx))
            continue
        fn = getattr(found[0], meth, None)
        if fn is None:
            w("MISSING 方法 %s.%s" % (cls, meth))
            continue
        got = len(inspect.signature(fn).parameters)
        w("%-14s %-16s 参数 %d %s"
          % (cls, meth, got, "OK" if got == nargs else "！期望 %d" % nargs))

    # ---- 2) 真跑一次「确认 -> 回调」链路
    fired = []
    page = win.page(2)
    win._switch(2)
    pump(200)
    host = win.notice_host
    notice.ask(page, "确认清理", "即将清理 328 个文件，合计 1.2 GB。",
               ok_text="开始清理", danger=True,
               on_yes=lambda: fired.append("yes"),
               on_no=lambda: fired.append("no"))
    pump(150)
    bars = [b for b in host._bars if isinstance(b, notice.AskBar)]
    w("确认条=%d 文案=「%s」" % (len(bars), bars[0].label.text()[:30]))
    bars[0].btn_ok.click()
    pump(250)
    w("点确认后 fired=%s 剩余条数=%d" % (fired, host.count))

    # ---- 3) 取消走 on_no
    notice.ask(page, "确认删除", "测试取消路径", on_yes=lambda: fired.append("y2"),
               on_no=lambda: fired.append("n2"))
    pump(150)
    bars = [b for b in host._bars if isinstance(b, notice.AskBar)]
    bars[0].btn_cancel.click()
    pump(250)
    w("点取消后 fired=%s" % fired)

    # ---- 4) 切页把待答的确认当取消处理
    notice.ask(page, "确认删除", "切页时应算取消",
               on_yes=lambda: fired.append("y3"),
               on_no=lambda: fired.append("n3"))
    pump(120)
    win._switch(4)
    pump(250)
    w("切页后 fired=%s 条数=%d" % (fired, host.count))

    # ---- 5) 子页签能顺着父链找到宿主
    from app.ui.optimize_page import ServicesTab
    st = win.page(4).findChild(ServicesTab)
    w("子页签找到宿主=%s" % (notice._host(st) is host))
    notice.note(st, "ok", "子页签发了一条提示")
    pump(120)
    w("子页签发提示后条数=%d" % host.count)

    # ---- 6) 关于面板
    win._about()
    pump(200)
    w("关于条数=%d" % host.count)
    win.grab().save(os.path.join(ROOT, "probe_flow_about.png"), "PNG")

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
