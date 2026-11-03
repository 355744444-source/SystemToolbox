# -*- coding: utf-8 -*-
"""系统体检工作台（第七）渲染探针：五个页签 + 曲线 + 进程 TOP + 组件健康 + Ctrl+7。"""
from __future__ import annotations

import os
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", os.environ.get("CLS_QPA", "windows"))
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

OUT = os.path.join(ROOT, "probe_health_ui.log")
SHOTS = os.path.join(ROOT, "probe_shots")


def main() -> int:
    log = []
    try:
        from PyQt5.QtCore import QEvent, Qt
        from PyQt5.QtGui import QFont, QKeyEvent
        from PyQt5.QtWidgets import QApplication

        from app import theme
        from app.ui.main_window import TABS, MainWindow
        from app.ui.tables import stop_all

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

        def shot(name):
            pump(10)
            os.makedirs(SHOTS, exist_ok=True)
            path = os.path.join(SHOTS, name)
            ok = win.grab().save(path)
            log.append(f"  shot {name} ok={ok} "
                       f"{os.path.getsize(path) if ok else 0}B")

        pump(10)
        log.append(f"TABS={len(TABS)} last={TABS[-1][0]}/{TABS[-1][1]}")
        log.append(f"rail_buttons={len(win.rail_buttons)} "
                   f"health_btn={win.btn_health.text()} pages={win.stack.count()}")

        win.btn_health.setChecked(True)
        win._switch(6)
        hp = win.health_page
        wait_until(app, lambda: hp.dev_stats is not None and hp.up_rep is not None
                   and hp.sec_rep is not None and hp.comp_rep is not None, 90)
        pump(20)
        log.append(f"dev_stats={hp.dev_stats}")
        log.append(f"tabs={hp.tabs.count()} labels="
                   f"{[hp.tabs.tabText(i) for i in range(hp.tabs.count())]}")

        # ---- 设备与驱动
        hp.tabs.setCurrentIndex(0)
        shot("health_tab0_dev.png")
        log.append(f"dev rows={hp.dev_model.rowCount()} "
                   f"drv rows={hp.drv_model.rowCount()} "
                   f"count='{hp.dev_count.text()}'")
        for key in ("bad", "off", "gone", "all"):
            hp.dev_chips.set_current(key)
            pump(6)
            log.append(f"  chips[{key}] dev rows={hp.dev_model.rowCount()}")
        hp.dev_chips.set_current("all")
        pump(6)

        # ---- 升级体检
        hp.tabs.setCurrentIndex(1)
        shot("health_tab1_upgrade.png")
        rep = hp.up_rep
        log.append(f"upgrade ready={rep.ready} blockers={len(rep.blockers)} "
                   f"headline='{rep.headline}'")
        log.append(f"  kpi_up='{hp.kpi_up._full}' sub='{hp.kpi_up.sub.text()}'")

        # ---- 性能采样：等几个点进曲线
        hp.tabs.setCurrentIndex(2)
        t0 = time.time()
        wait_until(app, lambda: len(hp._pts) >= 4, 20)
        pump(10)
        log.append(f"perf pts={len(hp._pts)} waited={time.time() - t0:.1f}s "
                   f"chart_series={list(hp.chart.data)} "
                   f"cpu_len={len(hp.chart.data['cpu'])} "
                   f"net_len={len(hp.chart_net.data['net_in'])}")
        log.append(f"  live cpu='{hp.live_labels['cpu'].text()}' "
                   f"mem='{hp.live_labels['mem'].text()}' "
                   f"disk='{hp.live_labels['disk'].text()}' "
                   f"net='{hp.live_labels['net'].text()}'")
        log.append(f"  note={hp.perf_note.text()[:80]}")
        # 进程 TOP：等第二个采样点（CPU 才有效）
        wait_until(app, lambda: hp._prep is not None and hp._prep.warm
                   and hp.proc_model.rowCount() > 0, 25)
        pump(6)
        log.append(f"  proc rows={hp.proc_model.rowCount()} "
                   f"total={hp._prep.total} warm={hp._prep.warm} "
                   f"cpu_sum={sum(p.cpu for p in hp._prep.rows):.1f}%")
        for i in range(min(3, hp.proc_model.rowCount())):
            p = hp.proc_model.row_at(i)
            log.append(f"    TOP{i + 1} {p.name[:24]} pid={p.pid} "
                       f"cpu={p.cpu:.2f}% mem={p.mem_mb:.0f}MB "
                       f"priv={p.private_mb:.0f}MB h={p.handles} t={p.threads}")
        log.append(f"  proc_count='{hp.proc_count.text()[:110]}'")
        hp.proc_chips.set_current("mem")
        pump(6)
        top_mem = hp.proc_model.row_at(0)
        log.append(f"  sort=mem -> {top_mem.name[:24]} {top_mem.mem_mb:.0f}MB")
        hp.proc_chips.set_current("handles")
        pump(6)
        top_h = hp.proc_model.row_at(0)
        log.append(f"  sort=handles -> {top_h.name[:24]} {top_h.handles}")
        hp.proc_chips.set_current("cpu")
        pump(6)
        shot("health_tab2_perf.png")
        hp._toggle_pause()
        n1 = len(hp._pts)
        pump(6)
        time.sleep(1.2)
        pump(6)
        log.append(f"  pause: active={hp._timer.isActive()} "
                   f"pts {n1} -> {len(hp._pts)} btn='{hp.btn_pause.text()}'")
        hp._toggle_pause()

        # ---- 安全基线
        hp.tabs.setCurrentIndex(3)
        shot("health_tab3_sec.png")
        srep = hp.sec_rep
        log.append(f"security score={srep.score} risks={len(srep.risks)} "
                   f"warns={len(srep.warns)} rows={hp.sec_model.rowCount()}")
        log.append(f"  kpi_sec='{hp.kpi_sec._full}' sub='{hp.kpi_sec.sub.text()}'")

        # ---- 系统文件与组件健康
        hp.tabs.setCurrentIndex(4)
        shot("health_tab4_file.png")
        crep = hp.comp_rep
        log.append(f"component dism={crep.dism_state} rows={hp.file_model.rowCount()} "
                   f"cbs={crep.cbs_size_mb:.2f}MB bad={crep.cbs_bad} "
                   f"noise={crep.cbs_noise} ubr={crep.ubr} "
                   f"patches={crep.patches}")
        log.append(f"  headline='{hp.file_headline.text()}'")
        log.append(f"  kpi_file='{hp.kpi_file._full}' sub='{hp.kpi_file.sub.text()}'")
        for r in crep.rows[:4]:
            log.append(f"  [{r.level}] {r.name} | {r.value[:70]}")
        # 体积统计：跑 3 秒后中断，验证「可中断 + 按钮复位」
        hp.measure_winsxs()
        time.sleep(3.0)
        pump(6)
        log.append(f"  winsxs running busy={hp._busy['size']} "
                   f"btn='{hp.btn_size.text()}' enabled={hp.btn_size.isEnabled()}")
        stop_all(hp)
        wait_until(app, lambda: hp._winsxs is not None, 60)
        pump(6)
        log.append(f"  winsxs result={hp._winsxs} btn='{hp.btn_size.text()}' "
                   f"enabled={hp.btn_size.isEnabled()}")
        log.append(f"  headline2='{hp.file_headline.text()}'")
        for i in range(hp.file_model.rowCount()):
            r = hp.file_model.row_at(i)
            if r.name == "组件存储体积":
                log.append(f"  size_row='{r.value}'")
                log.append(f"  size_detail='{r.detail[:60]}'")
        shot("health_tab4_file_size.png")

        # ---- 磁盘 SMART
        hp.tabs.setCurrentIndex(5)
        wait_until(app, lambda: hp.disk_rep is not None, 60)
        pump(8)
        drep = hp.disk_rep
        log.append(f"disk rows={hp.disk_model.rowCount()} io_rows={hp.io_model.rowCount()} "
                   f"headline='{drep.headline}' io_total={drep.io_total}")
        for d in drep.disks:
            log.append(f"  {d.title} | {d.bus} | {d.life_text} | {d.temp_text} | "
                       f"{d.write_text} | "
                       f"{d.log.hours if d.log else '-'}h | {d.level}/{d.status} | "
                       f"per_day={d.per_day_gb} remain={d.remain_text} samples={d.samples}")
        log.append(f"  kpi_disk='{hp.kpi_disk._full}' sub='{hp.kpi_disk.sub.text()}'")
        log.append(f"  disk_headline='{hp.disk_headline.text()[:90]}'")
        if hp.io_model.rowCount():
            r0 = hp.io_model.row_at(0)
            log.append(f"  io[0]= ID{r0[1]} {r0[2]} level={r0[4]}")
        shot("health_tab5_disk.png")

        # ---- 硬件错误
        hp.tabs.setCurrentIndex(6)
        wait_until(app, lambda: hp.hw_rep is not None, 60)
        pump(8)
        hrep = hp.hw_rep
        log.append(f"hw rows={hp.hw_model.rowCount()} extra={hp.extra_model.rowCount()} "
                   f"fatal={hrep.fatal} corrected={hrep.corrected} "
                   f"info={sum(hrep.info_ids.values())} unknown={hrep.unknown}")
        log.append(f"  hw_headline='{hp.hw_headline.text()[:100]}'")
        log.append(f"  kpi_hw='{hp.kpi_hw._full}' sub='{hp.kpi_hw.sub.text()}'")
        for i in range(hp.hw_model.rowCount()):
            r = hp.hw_model.row_at(i)
            log.append(f"  [{r.sev}/{r.level}] {r.part} ×{r.count} 近7天{r.recent7} "
                       f"context='{r.context}'")
        shot("health_tab6_hw.png")
        # 断言：WHEA 错误源通知绝不能进故障表
        assert hrep.unknown == 0, f"CPER 记录未被识别：{hrep.unknown}"
        assert not any("错误源" in str(r.part) for r in hrep.rows), "错误源通知混进了故障表"

        # ---- 主题往返
        # 浅色皮肤已移除（v2.0.0），此处不再切主题
        pump(16)
        win.rail_buttons[6].setChecked(True)
        win._switch(6)
        wait_until(app, lambda: win.health_page.sec_rep is not None
                   and win.health_page.comp_rep is not None, 90)
        win.health_page.tabs.setCurrentIndex(2)
        wait_until(app, lambda: len(win.health_page._pts) >= 3, 20)
        shot("health_light.png")
        log.append(f"light: sec={win.health_page.sec_rep is not None} "
                   f"pts={len(win.health_page._pts)}")
        # 浅色皮肤已移除（v2.0.0），此处不再切主题
        pump(16)

        # ---- Ctrl+7
        win.stack.setCurrentIndex(0)
        win.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_7, Qt.ControlModifier))
        pump(6)
        log.append(f"ctrl7 -> index={win.stack.currentIndex()} "
                   f"hint='{win.hint.text()}'")
        log.append(f"title='{win.windowTitle()}'")
        log.append(f"RESULT: {'OK' if win.stack.currentIndex() == 6 else 'FAIL'}")
    except Exception as exc:                     # noqa: BLE001
        import traceback
        log.append("FAILED: " + repr(exc))
        log.append(traceback.format_exc())
    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(log))
    return 0


def wait_until(app, fn, timeout=40):
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            if fn():
                return True
        except Exception:                        # noqa: BLE001
            pass
        app.processEvents()
        time.sleep(0.05)
    return False


if __name__ == "__main__":
    sys.exit(main())
