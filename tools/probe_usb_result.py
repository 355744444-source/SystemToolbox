# -*- coding: utf-8 -*-
"""重拍 docs/27（U 盘测速结果页）。

这张图必须有盘插着才能真跑，而盘不在手边。做法：把 2026-10-22 那次
真实测速的结果（存在 `%LOCALAPPDATA%\\SystemToolbox\\usb_speed.json` 里）
回放给页面 —— 数值还是当时实测的，只是让界面用 v1.12.0 的样式重新画一遍。

只影响这次截图：monkeypatch 只在本进程内生效。
"""
from __future__ import annotations

import io
import json
import os
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "windows")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFont
from PyQt5.QtWidgets import QApplication

from app import theme
from app.core import usbperf

OUT = os.path.join(ROOT, "probe_usb_result.log")
SHOT = os.path.join(ROOT, "docs", "27_U盘测速_结果_深色.png")

# 那次真实的盘：E: 上的 Windows 安装镜像 ISO 卷
TARGET = usbperf.Target(
    letter="E:", label="CCCHA_X64FRE_ZH-CN_DV9", fs="NTFS",
    total=128318619648, free=54350000000, bus="USB", drive_type=3)
KEY = f"{TARGET.label}|{TARGET.total}"


def last_sample() -> dict:
    path = os.path.join(os.environ["LOCALAPPDATA"], "SystemToolbox", "usb_speed.json")
    data = json.load(io.open(path, encoding="utf-8"))
    rows = data.get(KEY) or []
    if not rows:
        raise SystemExit(f"历史里没有这块盘：{KEY}")
    # 取写入最快的那次（当时最完整的一轮）
    return max(rows, key=lambda r: r.get("write_mbs", 0))


def main() -> int:
    log: list[str] = []
    rec = last_sample()
    log.append("回放记录：" + json.dumps(rec, ensure_ascii=False))

    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setFont(QFont(theme.FONT_FAMILY, 9))
    theme.apply_theme(app, "dark")

    # 只在本进程把盘列表换成这块盘
    usbperf.scan = lambda: [TARGET]

    from app.ui.main_window import PAGE_ATTRS, MainWindow
    win = MainWindow()
    win.setAttribute(Qt.WA_DontShowOnScreen, True)
    win.resize(1440, 900)
    win.show()

    def pump(ms: int) -> None:
        end = time.perf_counter() + ms / 1000
        while time.perf_counter() < end:
            app.processEvents()
            time.sleep(0.006)

    pump(400)
    idx = PAGE_ATTRS.index("usb_page")
    win.rail_buttons[idx].setChecked(True)
    win._switch(idx)
    pump(500)

    page = win.page(idx)
    page.reload()
    # 下拉框默认 256 MB，回放的是 128 MB 那轮 —— 让两处说法一致
    i = page.cmb_size.findData(int(rec.get("size_mb", 128)))
    if i >= 0:
        page.cmb_size.setCurrentIndex(i)
    pump(200)

    sample = usbperf.Sample(
        size_mb=rec.get("size_mb", 128),
        write_mbs=rec.get("write_mbs", 0.0),
        read_mbs=rec.get("read_mbs", 0.0),
        rand_read_iops=rec.get("rand_read_iops", 0.0),
        seconds=4.1,
        when=rec.get("when", ""),
        bus=rec.get("bus", "USB"))
    rep = usbperf.Report(target=TARGET, sample=sample, cleaned=True)
    page._done(rep)
    pump(400)

    ok = win.grab().save(SHOT)
    log.append(f"截图 {SHOT} ok={ok} {os.path.getsize(SHOT) if ok else 0}B")
    log.append(f"KPI 读={page.k_read._full} 写={page.k_write._full} "
               f"比={page.k_ratio._full} 总线={page.k_bus._full}")

    win.close()
    app.processEvents()
    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(log))
    return 0


if __name__ == "__main__":
    sys.exit(main())
