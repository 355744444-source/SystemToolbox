# -*- coding: utf-8 -*-
"""把导航 pill 的滑动录成 GIF（静态截图看不出运动，这是唯一能"看到"的方式）。

采样不按固定帧率：grab 本身要几十毫秒，硬按 20ms 采样会拖慢动画。
改成记录每帧的真实时间戳，GIF 的 delay 按相邻时间戳差分配，
播放出来的节奏跟真机一致。
"""
from __future__ import annotations

import io
import os
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "windows")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

OUT_LOG = os.path.join(ROOT, "probe_gif.log")
GIF_DIR = os.path.join(ROOT, "probe_shots")

NAV_RECT = (0, 0, 84, 800)          # 只录导航：色数少，GIF 不会出色带
FULL_SCALE = 0.5                     # 整窗录制时缩一半


def main() -> int:
    log = []
    from PIL import Image
    from PyQt5.QtCore import QBuffer, QByteArray, QRect, Qt
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
            time.sleep(0.004)

    pump(300)

    def record(name, action, rect=None, scale=1.0, budget_ms=460, step_ms=20):
        """跑到 action 触发后开始取样，直到画面稳定。

        取样间隔只有 5ms（抓 84px 宽很快），但 GIF 的 delay 低于 20ms
        会被浏览器当 100ms 处理，放出来慢 6 倍。所以按时间戳抽稀到
        step_ms 一帧，delay 用真实相邻差值 —— 播放节奏才跟真机一致。
        """
        frames, stamps = [], []
        qrect = QRect(*rect) if rect else None
        t0 = time.perf_counter()
        action()
        while (time.perf_counter() - t0) * 1000 < budget_ms:
            app.processEvents()
            pm = win.grab() if qrect is None else win.grab(qrect)
            if scale != 1.0:
                pm = pm.scaled(int(pm.width() * scale), int(pm.height() * scale),
                               1, 0)          # 1 = SmoothTransformation
            # 经 QBuffer + PNG 交给 PIL：直接摸 QImage.bits() 在 PyQt5 上
            # 会拿到无效指针，实测直接 segfault
            ba = QByteArray()
            buf = QBuffer(ba)
            buf.open(QBuffer.WriteOnly)
            pm.save(buf, "PNG")
            buf.close()
            frames.append(Image.open(io.BytesIO(bytes(ba))).convert("RGB"))
            stamps.append((time.perf_counter() - t0) * 1000)

        keep, last = [], -1e9
        for i, s in enumerate(stamps):
            if s - last >= step_ms:
                keep.append(i)
                last = s
        frames = [frames[i] for i in keep]
        stamps = [stamps[i] for i in keep]
        delays = []
        for i in range(len(stamps)):
            nxt = stamps[i + 1] if i + 1 < len(stamps) else stamps[-1] + step_ms
            delays.append(max(step_ms, int(round(nxt - stamps[i]))))
        path = os.path.join(GIF_DIR, name)
        frames[0].save(path, save_all=True, append_images=frames[1:],
                       duration=delays, loop=0, optimize=True, disposal=1)
        log.append(f"{name}: {len(frames)} 帧 {frames[0].size} "
                   f"{os.path.getsize(path) / 1024:.0f}KB 总时长={sum(delays)}ms")

    # 1. 导航 pill：从第 1 项滑到第 6 项
    win.rail.set_active(0, animate=False)
    pump(80)
    record("apple_pill.gif",
           lambda: (win.btn_net.setChecked(True),
                    win.rail.set_active(5, animate=True)),
           rect=NAV_RECT)

    # 整窗那一版放弃：每帧 grab+缩放+PNG 要 40ms，520ms 只抓到 4 帧，
    # 淡入根本看不出中间态。页面淡入用两张静态图（0.35 / 1.0）说明就够。

    with open(OUT_LOG, "w", encoding="utf-8") as f:
        f.write("\n".join(log))
    win.close()
    app.processEvents()
    return 0


if __name__ == "__main__":
    sys.exit(main())
