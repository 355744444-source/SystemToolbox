# -*- coding: utf-8 -*-
"""验证流体改造：pill 滑动曲线 + 页面淡入 + 双主题外观。"""
from __future__ import annotations

import os
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "windows")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

OUT = os.path.join(ROOT, "probe_apple_ui.log")
SHOTS = os.path.join(ROOT, "probe_shots")


def main() -> int:
    log = []
    from PyQt5.QtCore import Qt
    from PyQt5.QtGui import QFont
    from PyQt5.QtWidgets import QApplication

    from app import theme
    from app.ui import motion
    from app.ui.main_window import MainWindow

    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setFont(QFont("Microsoft YaHei", 9))
    theme.apply_theme(app, "dark")

    log.append(f"animations_enabled={motion.animations_enabled()}")

    win = MainWindow()
    win.setAttribute(Qt.WA_DontShowOnScreen, True)
    win.resize(1440, 900)
    win.show()

    def pump(ms=120):
        end = time.perf_counter() + ms / 1000.0
        while time.perf_counter() < end:
            app.processEvents()
            time.sleep(0.008)

    def shot(name, settle=160):
        pump(settle)
        os.makedirs(SHOTS, exist_ok=True)
        path = os.path.join(SHOTS, name)
        ok = win.grab().save(path)
        log.append(f"{name} ok={ok} {os.path.getsize(path) if ok else 0}B")

    pump(300)
    rail = win.rail
    log.append(f"rail={rail.width()}x{rail.height()} "
               f"pill={rail.pill.geometry().getRect()} "
               f"btn0_y={rail.button_at(0).y()} btn4_y={rail.button_at(4).y()}")
    shot("apple_1_深色_首页.png")

    # ---- pill 滑动曲线（从第 0 项跳到第 5 项）
    target_y = rail.button_at(5).y()
    rail.set_active(5, animate=True)
    curve = []
    t0 = time.perf_counter()
    for _ in range(26):
        app.processEvents()
        time.sleep(0.012)
        curve.append((round((time.perf_counter() - t0) * 1000),
                      round(rail.pill._y - target_y, 1)))
    log.append(f"目标 y={target_y}")
    log.append("曲线(ms, 相对目标偏移px): " + ", ".join(f"{m}:{d}" for m, d in curve))

    # 中途帧：grab 本身会强制重绘，不能再 pump，否则动画已经跑完
    rail.set_active(0, animate=False)
    pump(60)
    rail.set_active(5, animate=True)
    pump(95)
    log.append(f"滑动中 pill_y={rail.pill._y:.0f}（目标 {target_y}）")
    shot("apple_2_深色_滑动中.png", settle=0)
    pump(500)
    shot("apple_3_深色_第6项.png")

    # ---- 页面淡入（真实切页）
    win.btn_net.setChecked(True)
    t0 = time.perf_counter()
    win._switch(5)
    fade_ms = (time.perf_counter() - t0) * 1000
    eff = win.page(5).graphicsEffect()
    log.append(f"_switch(5) 返回耗时 {fade_ms:.0f}ms，淡入 effect 存在={eff is not None}")
    pump(45)
    shot("apple_4_深色_淡入中.png", settle=0)
    log.append(f"淡入中 opacity={eff.opacity():.2f}" if eff is not None else "无 effect")
    pump(500)
    log.append(f"淡入结束后 effect 已摘={win.page(5).graphicsEffect() is None}")
    shot("apple_5_深色_网络页.png")

    # ---- 连点打断：0 → 7 → 3，看 pill 是否从当前位置拐弯
    rail.set_active(0, animate=False)
    pump(50)
    rail.set_active(7, animate=True)
    pump(80)
    mid_a = rail.pill._y
    rail.set_active(3, animate=True)
    pump(30)
    after = rail.pill._y
    log.append(f"连点打断：0→7 途中 y={mid_a:.0f}，改道去 3 后 y={after:.0f} "
               f"（应介于 7 的位置与 3 的位置之间，且不回跳）")
    pump(500)
    log.append(f"最终落在 3 的位置={rail.button_at(3).y()}，pill_y={rail.pill._y:.0f}")

    # 浅色必看：accent_soft 在白底上很容易糊成一片
    # 浅色皮肤已移除（v2.0.0），此处不再切主题
    pump(700)
    win.btn_health.setChecked(True)
    win._switch(6)
    pump(600)
    shot("apple_6_浅色_体检页.png")
    log.append(f"浅色 pill_y={win.rail.pill._y:.0f} "
               f"btn6_y={win.rail.button_at(6).y()}")

    # 先落盘再关窗：closeEvent 里线程卡住会走 os._exit(0)，后面的写文件全丢
    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(log))
    win.close()
    app.processEvents()
    return 0


if __name__ == "__main__":
    sys.exit(main())
