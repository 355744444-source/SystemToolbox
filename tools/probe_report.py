# -*- coding: utf-8 -*-
"""体检报告探针：真实跑一遍界面九项体检 → 结论条 → 导出 HTML/Excel。

结果写 probe_report.txt（Bash 里 stdout 常丢）。
"""
import io
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "windows")

LOG = os.path.join(ROOT, "probe_report.txt")
OUT = os.path.join(ROOT, "probe_out")
buf = []


def w(msg):
    buf.append(str(msg))
    with io.open(LOG, "w", encoding="utf-8") as f:
        f.write("\n".join(buf))


from PyQt5.QtCore import Qt  # noqa: E402
from PyQt5.QtWidgets import QApplication  # noqa: E402

QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
app = QApplication(sys.argv)

from app import theme  # noqa: E402
from app.ui.main_window import MainWindow  # noqa: E402

theme.apply_theme(app, "dark")


def pump(ms):
    end = time.time() + ms / 1000.0
    while time.time() < end:
        app.processEvents()
        time.sleep(0.01)


try:
    win = MainWindow()
    win.resize(1440, 900)
    win.show()
    pump(400)

    win.rail_buttons[6].setChecked(True)
    win._switch(6)
    pump(300)
    page = win.page(6)
    w("page built: %s  tab count=%d" % (type(page).__name__, page.tabs.count()))
    w("verdict visible at start: %s" % page.verdict.isVisible())

    t0 = time.time()
    deadline = t0 + 300
    while time.time() < deadline and page.health_rep is None:
        pump(300)
    cost = time.time() - t0
    rep = page.health_rep
    w("wait %.1fs  health_rep=%s" % (cost, "OK" if rep else "NONE"))
    if rep is None:
        w("busy=%s done=%s" % (page._busy, sorted(page._done_keys)))
        raise SystemExit(1)

    w("score=%d level=%s headline=%s" % (rep.score, rep.level, rep.headline))
    w("crit=%d warn=%d sections=%d cost_ms=%d"
      % (rep.crit, rep.warn, len(rep.sections), rep.cost_ms))
    for s in rep.sections:
        w("  [%s] %s | lv=%s | %s | %s | err=%s"
          % (s.key, s.title, s.level, s.headline[:40],
             dict(list(s.counts.items())[:3]), s.error[:60]))
    w("verdict visible now: %s  score label=%r"
      % (page.verdict.isVisible(), page.v_score.text()))
    w("verdict text: %s / %s" % (page.v_head.text(), page.v_sub.text()))

    os.makedirs(OUT, exist_ok=True)
    from app.core import report

    h = report.export_health_html(os.path.join(OUT, "health.html"), rep)
    x = report.export_health_xlsx(os.path.join(OUT, "health.xlsx"), rep)
    w("html=%d bytes  xlsx=%d bytes"
      % (os.path.getsize(h), os.path.getsize(x)))
    html = io.open(h, encoding="utf-8").read()
    w("html has score circle: %s" % ("健康评分" in html))
    w("html has 优先处理: %s" % ("优先处理" in html))
    w("html item cards: %d" % html.count("class='find'"))
    w("html level cn crit present: %s" % ("临界" in html))

    win.grab().save(os.path.join(OUT, "health_dark.png"))
    w("shot saved")

    # 切浅色再抓一张，确认结论条跟着主题走
    # 浅色皮肤已移除（v2.0.0），此处不再切主题
    pump(600)
    page = win.page(6)
    w("after theme (right away): verdict=%s rep=%s"
      % (page.verdict.isVisible(), "kept" if page.health_rep else "重扫中"))
    # 切主题会整窗重建，体检页照旧重扫一遍 —— 这里等它自己跑完，顺带验证第二遍
    t1 = time.time()
    while time.time() < t1 + 120 and page.health_rep is None:
        pump(300)
    w("after theme (waited %.1fs): score=%r verdict=%s rep=%s"
      % (time.time() - t1, page.v_score.text(), page.verdict.isVisible(),
         "OK" if page.health_rep else "NONE"))
    win.grab().save(os.path.join(OUT, "health_light.png"))
    w("DONE")
except Exception as exc:                      # noqa: BLE001
    import traceback
    w("EXC " + repr(exc))
    w(traceback.format_exc())
finally:
    w("--- end ---")
    os._exit(0)
