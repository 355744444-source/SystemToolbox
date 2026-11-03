# -*- coding: utf-8 -*-
"""系统优化页冒烟 + 截图：四个页签各截一张。"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "windows")

MODE = os.environ.get("CLS_MODE", "dark")
LOG = open("probe_opt.log", "w", encoding="utf-8", buffering=1)


def w(s):
    LOG.write(str(s) + "\n")


from PyQt5.QtWidgets import QApplication  # noqa: E402

from app import theme  # noqa: E402
from app.ui.main_window import MainWindow  # noqa: E402


def pump(app, n=14, dt=0.05):
    for _ in range(n):
        app.processEvents()
        time.sleep(dt)


def wait_idle(app, tab, timeout=30):
    """等页签的后台扫描跑完（只判「不忙」，内容条件交给 wait_until）。"""
    t0 = time.time()
    while time.time() - t0 < timeout:
        if not getattr(tab, "_busy", False):
            return True
        app.processEvents()
        time.sleep(0.05)
    return False


def wait_until(app, fn, timeout=30):
    """等到 fn() 为真。用于「表里已经有行」这种内容条件。"""
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            if fn():
                return True
        except Exception:                    # noqa: BLE001
            pass
        app.processEvents()
        time.sleep(0.05)
    return False


def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    theme.apply_theme(app, MODE)
    win = MainWindow()
    win.resize(1440, 900)
    win.show()
    pump(app, 16)
    w(f"pages={win.stack.count()}")

    win.rail_buttons[4].setChecked(True)
    win._switch(4)
    pump(app, 16)
    op = win.optimize_page
    w(f"tabs={op.tabs.count()}")

    os.makedirs("shots", exist_ok=True)
    names = ("info", "startup", "services", "tweaks", "changes")
    getters = {
        "info": lambda: len(op.tab_info.sys_model.rows()) > 0,
        "startup": lambda: len(op.tab_startup.model.rows()) > 0,
        "services": lambda: len(op.tab_services.model.rows()) > 0,
        "tweaks": lambda: len(op.tab_tweaks.model.rows()) > 0,
        "changes": lambda: op.tab_changes.summary.total > 0,
    }
    for i, name in enumerate(names):
        op.tabs.setCurrentIndex(i)
        pump(app, 8)
        got = wait_until(app, getters[name])
        tab = op.tabs.currentWidget()
        ok = wait_idle(app, tab)
        pump(app, 16)
        path = f"shots/opt_{MODE}_{name}.png"
        win.grab().save(path)
        w(f"{name} data={got} idle={ok} -> {path}")

    # 数据自查（先确保三页数据都落盘了）
    wait_until(app, lambda: len(op.tab_startup.items) > 0, timeout=10)
    wait_until(app, lambda: len(op.tab_services.items) > 0, timeout=10)
    wait_until(app, lambda: len(op.tab_tweaks.model.rows()) > 0, timeout=10)
    info = op.tab_info
    w(f"sys rows={len(info.sys_model.rows())} hw rows={len(info.hw_model.rows())} "
      f"procs={len(info.proc_model.rows())}")
    w(f"startup items={len(op.tab_startup.items)}")
    w(f"services items={len(op.tab_services.items)}")

    # ---- 表头对齐：数值列（内存）的表头必须和数据同侧，否则整列是「斜」的
    from PyQt5.QtCore import Qt
    pm = info.proc_model
    ci = next(i for i, c in enumerate(pm.cols) if c.title == "内存")
    w(f"header align: mem data_right={bool(pm.cols[ci].align & Qt.AlignRight)} "
      f"header={int(pm.headerData(ci, Qt.Horizontal, Qt.TextAlignmentRole))} "
      f"header_right={bool(pm.headerData(ci, Qt.Horizontal, Qt.TextAlignmentRole) & Qt.AlignRight)}")

    # ---- 微调页：版本分支 + 不适用行不可勾选
    tt = op.tab_tweaks
    tm = tt.model
    w(f"os_bar={op.os_bar.label.text()}")
    w(f"tweaks 本机可用 rows={len(tm.rows())} label={tt.count_label.text()}")

    tt.chips.set_current("all")
    pump(app, 12)
    rows = tm.rows()
    blocked = [r for r in rows if not r.applies_to()]
    w(f"tweaks 全部 rows={len(rows)} blocked={[r.key for r in blocked]}")
    for r in blocked:
        i = rows.index(r)
        fl = tm.flags(tm.index(i, 0))
        w(f"  {r.key} os={r.os} state={r.state()} "
          f"checkable={bool(fl & Qt.ItemIsUserCheckable)} why={r.why_not()}")
    tm.check_all(True)
    w(f"check_all -> checked={tm.checked_count()} / {len(rows)}  "
      f"期望 {len(rows) - len(blocked)}")
    pump(app, 10)
    win.grab().save(f"shots/opt_{MODE}_tweaks_all.png")
    w("shot -> tweaks_all")

    # ---- 变更记录页签：时间列不能截断、三档筛选计数要对得上
    ct = op.tab_changes
    cm = ct.model
    op.tabs.setCurrentIndex(4)
    pump(app, 8)
    wait_until(app, lambda: ct.summary.total > 0)
    wait_idle(app, ct)
    pump(app, 10)
    times = [r.when for r in ct.rows]
    longest = max((len(t) for t in times), default=0)
    w(f"changes rows={len(ct.rows)} summary(total={ct.summary.total} "
      f"active={ct.summary.active_total} restored={ct.summary.restored}) "
      f"label={ct.count_label.text()}")
    w(f"changes 时间列宽={cm.cols[0].width} 最长文本={longest} "
      f"样例={times[0] if times else '-'}")
    for key, want in (("all", len(ct.rows)),
                      ("active", sum(1 for r in ct.rows if r.active)),
                      ("done", sum(1 for r in ct.rows if not r.active))):
        ct.chips.set_current(key)
        pump(app, 8)
        got = cm.rowCount()
        w(f"  chips[{key}] rows={got} want={want} {'OK' if got == want else 'MISMATCH'}")
    ct.chips.set_current("all")
    pump(app, 8)
    if cm.rows():
        ct.table.selectRow(0)
    w(f"revert btn='{ct.btn_revert.text()}' enabled={ct.btn_revert.isEnabled()}")

    # ---- 第六工作台：网络诊断
    win.btn_net.setChecked(True)
    win._switch(5)
    t0 = time.time()
    while win.net_page.rep is None and time.time() - t0 < 60:
        app.processEvents()
        time.sleep(0.05)
    pump(app, 12)
    rep = win.net_page.rep
    w(f"net checks={len(rep.checks) if rep else 0} "
      f"problems={len(rep.problems) if rep else '-'} "
      f"nics={len(rep.adapters) if rep else 0}")
    win.grab().save(f"shots/opt_{MODE}_net.png")
    w("shot -> net")
    w("DONE")
    LOG.close()


if __name__ == "__main__":
    main()
