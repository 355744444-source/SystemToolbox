# -*- coding: utf-8 -*-
"""流体改造的压力测试：连切 / 动画中切主题 / 动画中关窗。

三个都是「动画对象比宿主活得久」的经典踩法，必须逐个验。
"""
from __future__ import annotations

import os
import subprocess
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "windows")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
OUT = os.path.join(ROOT, "probe_motion_stress.log")


def run_case(name: str) -> list[str]:
    """每个场景单开进程跑，这样退出码才有意义（崩了的话上面一个 case 白跑）。"""
    log = []
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
            time.sleep(0.005)

    pump(250)

    if name == "burst":
        # 连切 3 轮 × 8 页，中间不停，专门打可打断逻辑
        t0 = time.perf_counter()
        for _ in range(3):
            for i in range(8):
                win._switch(i)
                pump(18)
        pump(500)
        rail = win.rail
        ok_pill = abs(rail.pill._y - rail.button_at(7).y()) < 2
        log.append(f"连切 24 次耗时 {(time.perf_counter() - t0) * 1000:.0f}ms，"
                   f"pill 落位正确={ok_pill}（pill_y={rail.pill._y:.1f} "
                   f"应={rail.button_at(7).y()}）")
        log.append(f"当前页={win.stack.currentIndex()} 页面对象齐全="
                   f"{all(p is not None for p in win._pages)}")

    elif name == "theme_mid":
        # 动画跑到一半就切主题：旧 rail 的 Spring 计时器必须能安全收尾
        win._switch(5)
        pump(80)                 # 弹簧正在跑
        # 浅色皮肤已移除（v2.0.0），此处不再切主题
        pump(60)
        win._switch(2)
        pump(80)
        # 浅色皮肤已移除（v2.0.0），此处不再切主题
        pump(900)
        log.append(f"两次动画中切主题后 pill_y={win.rail.pill._y:.0f} "
                   f"应={win.rail.button_at(win.stack.currentIndex()).y()}")
        log.append(f"退役树 {len(win._retired)} 棵")

    elif name == "close_mid":
        # 淡入 + 滑动都在跑的时候关窗
        win._switch(4)
        pump(60)
        win.close()
        app.processEvents()
        log.append("close 返回了（没被 os._exit 提前截断）")

    with open(os.path.join(ROOT, f"_stress_{name}.log"), "w", encoding="utf-8") as f:
        f.write("\n".join(log))
    # 必须真的走一遍关窗：不关就把 C++ 对象丢给解释器退出，
    # 必定 0xC0000409（这项目的老毛病，跟本次改动无关）
    win.close()
    app.processEvents()
    return log


if __name__ == "__main__":
    if len(sys.argv) > 1:
        run_case(sys.argv[1])
        sys.exit(0)

    lines = []
    for case in ("burst", "theme_mid", "close_mid"):
        # 透传环境变量给子进程（清空 session id 避免 safe-delete 补丁）
        env = dict(os.environ)
        env.pop("CODEBUDDY_SESSION_ID", None)
        env.pop("CLAUDE_SESSION_ID", None)
        p = subprocess.run([sys.executable, os.path.abspath(__file__), case],
                           capture_output=True, text=True, env=env, timeout=180)
        rc = p.returncode
        detail = ""
        path = os.path.join(ROOT, f"_stress_{case}.log")
        if os.path.isfile(path):
            with open(path, encoding="utf-8") as f:
                detail = f.read().strip()
        lines.append(f"[{case}] rc={rc}")
        if detail:
            lines.append("  " + detail.replace("\n", "\n  "))
        if p.stderr.strip():
            lines.append("  STDERR: " + p.stderr.strip()[-600:])
    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print("done")
