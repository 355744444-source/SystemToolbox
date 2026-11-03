# -*- coding: utf-8 -*-
"""验证磁盘下拉选择器：卷列表、取值、手输路径、下钻回填。"""
import os
import sys
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", os.environ.get("CLS_QPA", "windows"))

from PyQt5.QtWidgets import QApplication      # noqa: E402

LOG = open(os.path.join(ROOT, "probe_picker.log"), "w", encoding="utf-8", buffering=1)


def w(s: str) -> None:
    LOG.write(s + "\n")


def main() -> None:
    from app import theme
    from app.ui.main_window import MainWindow

    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    theme.apply_theme(app, "dark")
    win = MainWindow()
    win.resize(1440, 900)
    win.show()
    for _ in range(10):
        app.processEvents()

    win.rail_buttons[2].setChecked(True)
    win._switch(2)
    for _ in range(10):
        app.processEvents()

    cp = win.clean_page
    for name, tab in (("大文件", cp.tab_large), ("大目录", cp.tab_dir)):
        cp.tabs.setCurrentWidget(tab)
        for _ in range(8):
            app.processEvents()
        pk = tab.picker
        w(f"== {name} ==")
        w(f"下拉项数={pk.box.count()} 当前文本={pk.box.currentText()!r}")
        w(f"path() -> {pk.path()!r}  isdir={os.path.isdir(pk.path())}")
        for i in range(pk.box.count()):
            w(f"  item{i}: {pk.box.itemText(i)!r}  data={pk.box.itemData(i)!r}")

        if pk.box.count() > 1:
            pk.box.setCurrentIndex(1)
            pk.box.activated[int].emit(1)
            w(f"选第2项 -> path={pk.path()!r} isdir={os.path.isdir(pk.path())}")

        # 手输任意目录（模拟用户编辑）
        pk.box.setEditText("D:" + chr(92) + "some" + chr(92) + "dir")
        w(f"手输目录 -> path={pk.path()!r}")

        # 下钻回填（DirTreeTab 会 set_path 子目录）
        sub = os.path.join(os.path.expandvars("%SystemRoot%"), "System32")
        pk.set_path(sub)
        w(f"set_path(子目录) -> 文本={pk.box.currentText()!r} path={pk.path()!r} isdir={os.path.isdir(pk.path())}")

        # 回填盘符根，应命中下拉项并显示卷信息
        root = os.path.expandvars("%SystemDrive%") + chr(92)
        pk.set_path(root)
        w(f"set_path(盘符根) -> 文本={pk.box.currentText()!r} path={pk.path()!r} isdir={os.path.isdir(pk.path())}")

        os.makedirs(os.path.join(ROOT, "shots"), exist_ok=True)
        win.grab().save(os.path.join(ROOT, f"shots/picker_{name}.png"))

    cp.tabs.setCurrentWidget(cp.tab_large)
    for _ in range(8):
        app.processEvents()
    win.grab().save(os.path.join(ROOT, "shots/picker_large_page.png"))
    cp.tabs.setCurrentWidget(cp.tab_dir)
    for _ in range(8):
        app.processEvents()
    win.grab().save(os.path.join(ROOT, "shots/picker_dir_page.png"))
    w("RESULT: OK")


try:
    main()
except Exception:                                   # noqa: BLE001
    w("RESULT: FAIL\n" + traceback.format_exc())

LOG.close()
