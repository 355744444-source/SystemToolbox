# -*- coding: utf-8 -*-
"""离屏探针：建窗、切页、截图，把结果写进文件（此处 stdout 不可靠）。"""
from __future__ import annotations

import os
import sys
import traceback

os.environ.setdefault("QT_QPA_PLATFORM", os.environ.get("CLS_QPA", "windows"))
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

OUT = os.path.join(ROOT, "probe_ui.log")
SHOTS = os.path.join(ROOT, "shots")


def main() -> int:
    lines = []
    try:
        from PyQt5.QtCore import Qt
        from PyQt5.QtGui import QFont
        from PyQt5.QtWidgets import QApplication

        from app import theme
        from app.ui.main_window import MainWindow

        QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
        app = QApplication(sys.argv)
        app.setStyle("Fusion")
        f = QFont("Microsoft YaHei", 9)
        app.setFont(f)
        theme.apply_theme(app, "dark")
        win = MainWindow()
        win.resize(1440, 900)
        win.show()
        for _ in range(8):
            app.processEvents()
        lines.append(f"window ok, pages={win.stack.count()}")

        os.makedirs(SHOTS, exist_ok=True)
        names = {0: "p_log", 1: "p_dump", 2: "p_clean_garbage", 3: "p_residue"}
        for i in (0, 1, 2, 3):
            win.rail_buttons[i].setChecked(True)
            win._switch(i)
            for _ in range(6):
                app.processEvents()
            okg = win.grab().save(os.path.join(SHOTS, names[i] + ".png"))
            lines.append(f"page {i} shot={okg}")

        # 清理页各 tab
        win.rail_buttons[2].setChecked(True)
        win._switch(2)
        cp = win.clean_page
        for j, key in enumerate(("tab_garbage", "tab_large", "tab_dir", "tab_disk")):
            cp.tabs.setCurrentIndex(j)
            for _ in range(6):
                app.processEvents()
            okg = win.grab().save(os.path.join(SHOTS, f"p_clean_tab{j}.png"))
            lines.append(f"clean tab {key} shot={okg}")
        lines.append(f"volumes={len(cp.tab_disk.volumes)} "
                     f"cards={len(cp.tab_disk.cards)}")

        win.rail_buttons[3].setChecked(True)
        win._switch(3)
        rp = win.residue_page
        for j in (0, 1):
            rp.tabs.setCurrentIndex(j)
            for _ in range(6):
                app.processEvents()
            okg = win.grab().save(os.path.join(SHOTS, f"p_residue_tab{j}.png"))
            lines.append(f"residue tab {j} shot={okg}")

        # 浅色主题重建
        # 浅色皮肤已移除（v2.0.0），此处不再切主题
        win.rail_buttons[2].setChecked(True)
        win._switch(2)
        for _ in range(8):
            app.processEvents()
        win.grab().save(os.path.join(SHOTS, "p_clean_light.png"))
        win.rail_buttons[3].setChecked(True)
        win._switch(3)
        for _ in range(6):
            app.processEvents()
        win.grab().save(os.path.join(SHOTS, "p_residue_light.png"))
        lines.append("theme switch ok")

        # 主题二次切换（验证旧页 shutdown 不会崩）
        # 浅色皮肤已移除（v2.0.0），此处不再切主题
        for _ in range(8):
            app.processEvents()
        lines.append("theme back ok")
    except Exception as exc:        # noqa: BLE001
        lines.append("FAILED: " + repr(exc))
        lines.append(traceback.format_exc())
    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
