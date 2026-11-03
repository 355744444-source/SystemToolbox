# -*- coding: utf-8 -*-
"""网络诊断工作台（第六工作台）渲染探针。

验证：rail 接入 / Ctrl+6 切页 / 结论表回填 / 三个页签 / KPI 文案 /
主题来回切换不丢数据。结果写 probe_net_ui.log。
"""
from __future__ import annotations

import os
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", os.environ.get("CLS_QPA", "windows"))
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

OUT = os.path.join(ROOT, "probe_net_ui.log")
SHOTS = os.path.join(ROOT, "probe_shots")


def main() -> int:
    log = []
    try:
        from PyQt5.QtCore import Qt
        from PyQt5.QtGui import QFont
        from PyQt5.QtWidgets import QApplication

        from app import theme
        from app.core import netdiag
        from app.ui.main_window import TABS, MainWindow

        QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
        QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
        app = QApplication(sys.argv)
        app.setStyle("Fusion")
        app.setFont(QFont("Microsoft YaHei", 9))
        theme.apply_theme(app, "dark")

        win = MainWindow()
        win.setAttribute(Qt.WA_DontShowOnScreen, True)
        win.resize(1440, 900)
        win.show()

        def pump(n=8):
            for _ in range(n):
                app.processEvents()
                time.sleep(0.01)

        os.makedirs(SHOTS, exist_ok=True)

        def shot(name):
            pump(10)
            path = os.path.join(SHOTS, name)
            ok = win.grab().save(path)
            log.append(f"  shot {name} ok={ok} "
                       f"{os.path.getsize(path) if ok else 0}B")

        pump(10)
        log.append(f"TABS={len(TABS)} last={TABS[-1][0]}/{TABS[-1][1]}")
        log.append(f"rail_buttons={len(win.rail_buttons)} "
                   f"net_btn_text={win.btn_net.text()}")
        log.append(f"stack_count={win.stack.count()}")

        # ---- 切到网络诊断，showEvent 会自己起一轮诊断
        win.btn_net.setChecked(True)
        win._switch(5)
        np_ = win.net_page
        t0 = time.time()
        while np_.rep is None and time.time() - t0 < 60:
            app.processEvents()
            time.sleep(0.05)
        pump(12)
        log.append(f"wait={time.time() - t0:.1f}s busy={np_._busy} "
                   f"rep={'None' if np_.rep is None else 'ok'}")
        if np_.rep is None:
            log.append("FAILED: 诊断没回来")
        else:
            rep = np_.rep
            log.append(f"report elapsed={rep.elapsed:.2f}s "
                       f"checks={len(rep.checks)} adapters={len(rep.adapters)} "
                       f"listeners={len(rep.listeners)} "
                       f"hosts={len(rep.hosts_entries)} "
                       f"problems={len(rep.problems)}")
            log.append(f"kpi state='{np_.kpi_state._full}' "
                       f"sub='{np_.kpi_state.sub.text()}'")
            log.append(f"kpi nic  ='{np_.kpi_nic._full}'")
            log.append(f"kpi net  ='{np_.kpi_net._full}'")
            log.append(f"kpi dns  ='{np_.kpi_dns._full}'")
            log.append(f"foot='{np_.foot.text()}'")
            log.append(f"hosts_note='{np_.hosts_note.text()[:90]}'")

            # 表格行数与表头对齐（右对齐的数值列最容易出「斜列」）
            log.append(f"rows check={np_.check_model.rowCount()} "
                       f"nic={np_.nic_model.rowCount()} "
                       f"port={np_.port_model.rowCount()} "
                       f"hosts={np_.hosts_model.rowCount()}")
            bad = 0
            for tab, m in (("check", np_.check_model), ("nic", np_.nic_model),
                           ("port", np_.port_model), ("hosts", np_.hosts_model)):
                for i, c in enumerate(m.cols):
                    h = m.headerData(i, Qt.Horizontal, Qt.TextAlignmentRole)
                    if h != c.align:
                        bad += 1
                        log.append(f"  hdr MISMATCH {tab}[{i}] {c.title} "
                                   f"align={h} want={c.align}")
            log.append(f"header align mismatch={bad}")

            # hosts 着色：屏蔽记录必须判为 faint（不是 warn）
            kinds = {}
            for r in (rep.hosts_entries or []):
                k = netdiag.hosts_kind(*r)
                kinds[k] = kinds.get(k, 0) + 1
            log.append(f"hosts kinds={kinds} color0="
                       f"{np_.hosts_model.cols[0].color_fn(rep.hosts_entries[0]) if rep.hosts_entries else '-'}")

            # 三页签都要能画出来
            for i, nm in ((0, "网卡与地址"), (1, "监听端口"), (2, "hosts 与代理")):
                np_.tabs.setCurrentIndex(i)
                shot(f"net_tab{i}.png")
                log.append(f"  tab{i} {nm} title={np_.tabs.tabText(i)}")

        # ---- 主题来回切：数据要保住，不能重跑诊断
        before = np_.rep
        # 浅色皮肤已移除（v2.0.0），此处不再切主题
        pump(16)
        np2 = win.net_page
        log.append(f"light rep kept={np2.rep is not None} "
                   f"same={'yes' if np2.rep is before else 'no'} "
                   f"checks={np2.check_model.rowCount()}")
        win.rail_buttons[5].setChecked(True)
        win._switch(5)
        shot("net_light.png")
        # 浅色皮肤已移除（v2.0.0），此处不再切主题
        pump(16)
        log.append(f"dark  rep kept={win.net_page.rep is not None} "
                   f"checks={win.net_page.check_model.rowCount()}")

        # ---- 快捷键 Ctrl+6
        win.stack.setCurrentIndex(0)
        from PyQt5.QtGui import QKeyEvent
        from PyQt5.QtCore import QEvent, QTimer
        QTimer.singleShot(0, lambda: None)
        ev = QKeyEvent(QEvent.KeyPress, Qt.Key_6, Qt.ControlModifier)
        win.keyPressEvent(ev)
        pump(6)
        log.append(f"ctrl6 -> index={win.stack.currentIndex()} "
                   f"hint='{win.hint.text()}'")
        log.append(f"title='{win.windowTitle()}'")
        log.append(f"status='{win.status_label.text()}'")

        log.append(f"RESULT: {'OK' if np_.rep is not None and win.stack.currentIndex() == 5 else 'FAIL'}")
    except Exception as exc:                       # noqa: BLE001
        import traceback
        log.append("FAILED: " + repr(exc))
        log.append(traceback.format_exc())
    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(log))
    return 0


if __name__ == "__main__":
    sys.exit(main())
