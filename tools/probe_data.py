# -*- coding: utf-8 -*-
"""带数据的离屏截图：把真实扫描结果灌进各表格，检查渲染（勾选框/配色/列宽）。

只调用只读扫描函数，不删任何东西。
"""
from __future__ import annotations

import os
import sys
import traceback

os.environ.setdefault("QT_QPA_PLATFORM", os.environ.get("CLS_QPA", "windows"))
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

OUT = os.path.join(ROOT, "probe_data.log")
SHOTS = os.path.join(ROOT, "shots")


def main() -> int:
    lines = []
    try:
        from PyQt5.QtCore import Qt
        from PyQt5.QtGui import QFont
        from PyQt5.QtWidgets import QApplication

        from app import theme
        from app.core import cleaner, registry_clean as reg, residue, space
        from app.ui.main_window import MainWindow

        app = QApplication(sys.argv)
        app.setStyle("Fusion")
        app.setFont(QFont("Microsoft YaHei", 9))
        theme.apply_theme(app, "dark")
        win = MainWindow()
        win.resize(1440, 900)
        win.show()
        for _ in range(6):
            app.processEvents()
        os.makedirs(SHOTS, exist_ok=True)

        def shot(name: str) -> None:
            for _ in range(5):
                app.processEvents()
            win.grab().save(os.path.join(SHOTS, name))
            lines.append("shot " + name)

        # ---- 垃圾清理：只扫前 4 条规则，够填满表格
        win.rail_buttons[2].setChecked(True)
        win._switch(2)
        cp = win.clean_page
        cp.tabs.setCurrentIndex(0)
        gt = cp.tab_garbage
        results = cleaner.scan_rules(cleaner.RULES[:5])
        gt._on_done(results)
        gt.model.check_indices([i for i, r in enumerate(results)
                                if r.rule.default_on and r.count > 0])
        if gt.model.rows():
            gt.table.selectRow(0)
        shot("d_garbage.png")
        lines.append(f"garbage rows={len(results)} "
                     f"checked={gt.model.checked_count()}")

        # ---- 大文件：扫用户目录 100MB
        cp.tabs.setCurrentIndex(1)
        lt = cp.tab_large
        big = space.find_large_files(os.path.expandvars("%USERPROFILE%"),
                                     100 * 1024 * 1024, limit=40)
        lt._on_done(big)
        if big:
            lt.table.selectRow(0)
        shot("d_large.png")
        lines.append(f"large rows={len(big)}")

        # ---- 大目录：分析 Program Files
        cp.tabs.setCurrentIndex(2)
        dt = cp.tab_dir
        target = os.path.expandvars("%ProgramFiles%")
        node = space.analyze_dir(target)
        dt._on_done((target, node))
        shot("d_dir.png")
        lines.append(f"dir children={len(node.children)}")

        # ---- 磁盘优化
        cp.tabs.setCurrentIndex(3)
        for _ in range(10):
            app.processEvents()
        shot("d_disk.png")

        # ---- 注册表
        win.rail_buttons[3].setChecked(True)
        win._switch(3)
        rp = win.residue_page
        rp.tabs.setCurrentIndex(0)
        rt = rp.tab_reg
        issues = reg.scan_all()
        rt._on_done(issues)
        if rt.model.rows():
            rt.table.selectRow(0)
        shot("d_registry.png")
        lines.append(f"registry rows={len(issues)}")

        # ---- 卸载残留
        rp.tabs.setCurrentIndex(1)
        lt2 = rp.tab_left
        items, checked = residue.scan_leftovers()
        lt2._on_done((items, checked))
        if items:
            lt2.table.selectRow(0)
        shot("d_leftover.png")
        lines.append(f"leftover rows={len(items)} checked_dirs={checked}")

        # ---- 浅色
        # 浅色皮肤已移除（v2.0.0），此处不再切主题
        win.rail_buttons[3].setChecked(True)
        win._switch(3)
        for _ in range(6):
            app.processEvents()
        rp2 = win.residue_page
        rp2.tabs.setCurrentIndex(1)
        rp2.tab_left._on_done((items, checked))
        if items:
            rp2.tab_left.table.selectRow(0)
        shot("d_leftover_light.png")
        win.rail_buttons[2].setChecked(True)
        win._switch(2)
        for _ in range(6):
            app.processEvents()
        cp2 = win.clean_page
        cp2.tabs.setCurrentIndex(0)
        cp2.tab_garbage._on_done(results)
        shot("d_garbage_light.png")
    except Exception as exc:        # noqa: BLE001
        lines.append("FAILED: " + repr(exc))
        lines.append(traceback.format_exc())
    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
