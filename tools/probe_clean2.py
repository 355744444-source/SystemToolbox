# -*- coding: utf-8 -*-
"""UI 层完整清理链路验证：扫描 → 勾选 → 确认 → 后台删除 → 刷新。

把 cleaner.RULES 临时替换成一条指向临时目录的规则，
所以只会删掉本脚本自己造的文件。
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
import traceback

os.environ.setdefault("QT_QPA_PLATFORM", os.environ.get("CLS_QPA", "windows"))
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

OUT = os.path.join(ROOT, "probe_clean2.log")


def main() -> int:
    lines = []
    try:
        from PyQt5.QtGui import QFont
        from PyQt5.QtWidgets import QApplication, QMessageBox

        from app import theme
        from app.core import cleaner as C

        tmp = tempfile.mkdtemp(prefix="cls_uiclean_")
        made = []
        for i in range(12):
            p = os.path.join(tmp, "deep" if i % 2 else "", f"junk_{i}.tmp")
            os.makedirs(os.path.dirname(p), exist_ok=True) if os.path.dirname(p) else None
            with open(p, "wb") as f:
                f.write(b"z" * 4096)
            made.append(p)

        # 只留一条测试规则，避免误删真实缓存
        C.RULES = (C.CleanRule(key="probe", name="探针临时文件", desc="测试用",
                               paths=(tmp,), patterns=("*.tmp",), default_on=True),)

        app = QApplication(sys.argv)
        app.setStyle("Fusion")
        app.setFont(QFont("Microsoft YaHei", 9))
        theme.apply_theme(app, "dark")

        from app.ui.main_window import MainWindow

        # 关掉所有弹窗
        QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.Yes)
        QMessageBox.information = staticmethod(lambda *a, **k: QMessageBox.Ok)
        QMessageBox.warning = staticmethod(lambda *a, **k: QMessageBox.Ok)

        win = MainWindow()
        win.resize(1440, 900)
        win.show()
        for _ in range(5):
            app.processEvents()
        win.rail_buttons[2].setChecked(True)
        win._switch(2)
        gt = win.clean_page.tab_garbage
        win.clean_page.tabs.setCurrentIndex(0)

        def wait(cond, timeout=20.0):
            t0 = time.time()
            while time.time() - t0 < timeout:
                app.processEvents()
                time.sleep(0.02)
                if cond():
                    return True
            return False

        gt.scan()
        ok = wait(lambda: not gt._busy)
        lines.append(f"scan done={ok} rows={len(gt.model.rows())} "
                     f"checked={gt.model.checked_count()}")
        if gt.model.rows():
            lines.append(f"  size={gt.model.rows()[0].total_size} "
                         f"count={gt.model.rows()[0].count}")

        gt.table.selectRow(0)
        for _ in range(4):
            app.processEvents()
        lines.append(f"detail rows shown={len(gt.detail.rows())}")

        gt._clean()
        ok2 = wait(lambda: not gt._busy, timeout=30)
        left = [p for p in made if os.path.exists(p)]
        lines.append(f"clean done={ok2} remaining={len(left)}/{len(made)}")
        # 清理后会自动重新扫描一次
        wait(lambda: not gt._busy)
        lines.append(f"rescan rows={len(gt.model.rows())}")

        # 大文件页链路
        cp = win.clean_page
        cp.tabs.setCurrentIndex(1)
        lt = cp.tab_large
        lt.picker.set_path(tmp)
        lt.scan()
        wait(lambda: not lt._busy)
        lines.append(f"large rows={len(lt.model.rows())}")

        try:
            os.rmdir(tmp)
        except OSError:
            pass
    except Exception as exc:        # noqa: BLE001
        lines.append("FAILED: " + repr(exc))
        lines.append(traceback.format_exc())
    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
