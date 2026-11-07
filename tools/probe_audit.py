# -*- coding: utf-8 -*-
"""界面一致性探针：把 9 个工作台建出来，量「页头 / 边距 / 标题字号」这三件事。

为什么要有这个脚本
------------------
「各页看起来不一样」是没法用眼睛比对截图下结论的 —— 截图缩放会吃掉 18px
的平移，而 2px 的字号差在 900px 高的图里根本看不出来。所以这里全部改成实测：

- **页边距**：读每页根布局的 `getContentsMargins()`，并取页面里第一个子控件的
  `geometry().x()`（＝内容真实左边界，比读布局参数更可信，能抓到「布局说 0
  但里面还套了一层」的情况）
- **标题字号**：`QLabel` 的 `font().pixelSize()` 是 QSS 生效后的真实值。
  objectName 选择器（`QLabel#H2`）与属性选择器（`QLabel[role="H2"]`）只有前者
  生效时，这两组数字会不一样 —— 这就是「6 个工作台的标题比另外 3 个小两档」
  的量化证据
- **KPI 行**：行内每张卡的宽度，以及 1180 最小窗口下会不会掉到 `KpiCard`
  的 118 最小宽以下（掉下去就是「同一行两个数字字号不同」）

跑法：`python tools/probe_audit.py`（不加参数只量，不截图）
"""
from __future__ import annotations

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "windows")   # offscreen 没有字体引擎

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from PyQt5.QtCore import Qt                                    # noqa: E402
from PyQt5.QtWidgets import (QApplication, QLabel, QVBoxLayout,  # noqa: E402
                             QWidget)

from app import theme                                          # noqa: E402
from app.ui.main_window import TABS, MainWindow                # noqa: E402
from app.ui.widgets import KpiCard                             # noqa: E402


def _first_child_x(page: QWidget) -> int:
    """页面里最上层的第一个子控件相对页面的 x（＝内容真实左边界）。"""
    kids = [w for w in page.findChildren(QWidget, options=Qt.FindDirectChildrenOnly)
            if w.isVisible() or True]
    if not kids:
        return -1
    return min(w.geometry().x() for w in kids)


def main() -> int:
    app = QApplication(sys.argv)
    theme.apply_theme(app, "dark")

    win = MainWindow()
    win.resize(1180, 800)
    win.setAttribute(Qt.WA_DontShowOnScreen, True)
    win.show()

    print("=== 角色字号（QSS 实测）===")
    for how in ("objectName", "property"):
        lbl = QLabel("测试标题")
        if how == "objectName":
            lbl.setObjectName("H2")
        else:
            lbl.setProperty("role", "H2")
        box = QWidget()
        lay = QVBoxLayout(box)
        lay.addWidget(lbl)
        box.ensurePolished()
        lbl.ensurePolished()
        print("  %-11s H2 -> pixelSize=%s weight=%s"
              % (how, lbl.font().pixelSize(), lbl.font().weight()))

    print("=== 各页边距与内容左边界（窗口 1180×800）===")
    print("  %-10s %-22s %-8s %-8s" % ("工作台", "根布局 margins", "首个控件x", "页面宽"))
    for i, (name, _key, _hint) in enumerate(TABS):
        page = win.page(i)
        lay = page.layout()
        mg = lay.getContentsMargins() if lay is not None else (0, 0, 0, 0)
        print("  %-10s %-22s %-10s %-8s"
              % (name, "%d,%d,%d,%d" % mg, _first_child_x(page), page.width()))

    print("=== KPI 行（1180 下每张卡宽度，最小宽 118）===")
    for i, (name, _key, _hint) in enumerate(TABS):
        page = win.page(i)
        cards = page.findChildren(KpiCard)
        if not cards:
            continue
        rows: dict = {}
        for c in cards:
            rows.setdefault(id(c.parentWidget()), []).append(c)
        for _pid, group in rows.items():
            ws = [c.width() for c in group]
            if not ws:
                continue
            print("  %-10s %d 张：宽 %d~%d，低于最小宽 %d 的 %d 张"
                  % (name, len(ws), min(ws), max(ws), KpiCard.NUM_PX and 118,
                     sum(1 for w in ws if w < 118)))

    # 标题字号：统计用 role=H2 但拿不到 13px 的标签
    bad = []
    for i, (name, _key, _hint) in enumerate(TABS):
        page = win.page(i)
        for lbl in page.findChildren(QLabel):
            if lbl.property("role") == "H2" and lbl.font().pixelSize() != 13:
                bad.append((name, lbl.text()[:18], lbl.font().pixelSize()))
    print("=== role=H2 但字号不是 13px 的标签：%d 个 ===" % len(bad))
    for name, txt, px in bad[:12]:
        print("  %-10s %-20s %spx" % (name, txt, px))

    win.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
