# -*- coding: utf-8 -*-
"""验证偏好：切主题立刻落盘、关窗存几何、下次启动恢复。"""
from __future__ import annotations

import json
import os
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "windows")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

OUT = os.path.join(ROOT, "probe_prefs.log")


def main() -> int:
    log = []

    def w(s=""):
        log.append(str(s))
        with open(OUT, "w", encoding="utf-8") as f:
            f.write("\n".join(log))

    from PyQt5.QtCore import Qt
    from PyQt5.QtGui import QFont
    from PyQt5.QtWidgets import QApplication

    from app import theme
    from app.core import settings
    from app.ui.main_window import MainWindow

    real = settings.path()
    if os.path.isfile(real):
        os.remove(real)         # 从干净状态起跑
    w("偏好文件 %s 存在=%s" % (real, os.path.isfile(real)))

    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setFont(QFont("Microsoft YaHei", 9))
    theme.apply_theme(app, "dark")

    def pump(ms):
        end = time.perf_counter() + ms / 1000.0
        while time.perf_counter() < end:
            app.processEvents()
            time.sleep(0.01)

    # ---- 1) 首次启动：没有偏好，走默认
    win = MainWindow()
    win.setAttribute(Qt.WA_DontShowOnScreen, True)
    win.show()
    pump(400)
    w("首次启动主题=%s 几何=%s" % (win.theme_name,
                                   (win.width(), win.height())))

    # ---- 2) 切到浅色：不关窗也应该已经写进文件
    # 浅色皮肤已移除（v2.0.0），此处不再切主题
    pump(600)
    raw = json.load(open(real, encoding="utf-8")) if os.path.isfile(real) else {}
    w("切浅色后 文件存在=%s theme=%s" % (os.path.isfile(real), raw.get("theme")))
    w("重建后窗口的 theme_name=%s" % win.theme_name)

    # ---- 3) 挪个位置再关
    win.setGeometry(120, 90, 1300, 820)
    pump(200)
    win.close()
    pump(400)
    raw = json.load(open(real, encoding="utf-8"))
    w("关窗后 文件内容=%s" % raw)
    w("几何解析=%s" % (settings.geometry(),))

    # ---- 4) 再启动：主题与位置应当恢复
    theme.apply_theme(app, str(settings.get("theme", "dark")))
    win2 = MainWindow()
    win2.setAttribute(Qt.WA_DontShowOnScreen, True)
    win2.show()
    pump(400)
    g = win2.geometry()
    w("二次启动主题=%s 几何=(%d,%d,%d,%d)"
      % (win2.theme_name, g.x(), g.y(), g.width(), g.height()))
    ok_theme = win2.theme_name == "light"
    ok_geom = (g.width(), g.height()) == (1300, 820)
    w("主题恢复=%s 几何恢复=%s" % (ok_theme, ok_geom))

    win2.close()
    pump(300)
    if os.path.isfile(real):
        os.remove(real)         # 别把测试值留在用户目录
    w("清理测试偏好 存在=%s" % os.path.isfile(real))
    w("RESULT: %s" % ("OK" if (ok_theme and ok_geom) else "FAILED"))
    print("\n".join(log))
    return 0 if (ok_theme and ok_geom) else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        import traceback
        with open(OUT, "a", encoding="utf-8") as f:
            f.write("\n=== FAILED ===\n" + traceback.format_exc())
        sys.exit(1)
