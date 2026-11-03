# -*- coding: utf-8 -*-
"""源码模式验证关窗：检查窗口关闭时是否还有 QThread 在跑、进程退出码多少。"""
from __future__ import annotations

import os
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "windows")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

LOG = open("probe_close.log", "w", encoding="utf-8", buffering=1)


def w(s):
    LOG.write(str(s) + "\n")


from PyQt5.QtCore import QThread, QTimer  # noqa: E402
from PyQt5.QtWidgets import QApplication  # noqa: E402

import main as app_main  # noqa: E402


def main():
    app = app_main._make_app([sys.argv[0]])
    win = app_main.MainWindow()
    win.show()

    def check():
        ths = [t for t in win.findChildren(QThread)]
        running = [t for t in ths if t.isRunning()]
        w(f"threads={len(ths)} running={len(running)}")
        for t in ths:
            par = t.parent()
            w(f"  {type(t).__name__} running={t.isRunning()} "
              f"parent={type(par).__name__ if par else None} fn={getattr(getattr(t,'fn',None),'__name__','-')}")
        w("closing window")
        ok = win.close()
        w(f"close() -> {ok}, visible={win.isVisible()}")
        ths2 = [t for t in win.findChildren(QThread) if t.isRunning()]
        w(f"after close running={len(ths2)}")
        for t in ths2:
            w(f"  still running: fn={getattr(getattr(t,'fn',None),'__name__','-')}")
        app.quit()

    QTimer.singleShot(2500, check)
    rc = app.exec_()
    w(f"app.exec_ rc={rc}")
    w("DONE")
    LOG.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
