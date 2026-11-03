# -*- coding: utf-8 -*-
"""生成 docs/ 下的产品截图（1440x900）。

用 WA_DontShowOnScreen 让窗口在屏幕外按目标尺寸渲染，
不受当前显示器可用区域限制，也不会弹窗打扰。
"""
from __future__ import annotations

import glob
import os
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", os.environ.get("CLS_QPA", "windows"))
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

OUT = os.path.join(ROOT, "probe_docs.log")
DOCS = os.path.join(ROOT, "docs")
EVTX = r"C:\Users\MSI\Desktop\3ａ.evtx"
KDUMP = r"C:\Users\MSI\Desktop\052026-8984-01.dmp"


def main() -> int:
    log = []
    try:
        from PyQt5.QtCore import Qt
        from PyQt5.QtGui import QFont
        from PyQt5.QtWidgets import QApplication

        from app import theme
        from app.core import (cleaner, drivers as drv, evtx, locks,
                              pathenv as pe, registry_clean as reg, residue, space)
        from app.ui import notice
        from app.ui.main_window import MainWindow

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
            # v1.11.0 起切页有 150ms 淡入、导航块有 350ms 弹簧滑行，
            # 只等 100ms 会拍到「块卡在半路 + 页面半透明」的废图。
            pump(42)
            path = os.path.join(DOCS, name)
            ok = win.grab().save(path)
            log.append(f"{name} ok={ok} {os.path.getsize(path) if ok else 0}B")

        os.makedirs(DOCS, exist_ok=True)
        pump(10)
        shot("01_空态_深色.png")

        # ---- 本机蓝屏代码（v1.17.0）：不用选文件，直接扫全机
        # 这张要在「还没加载任何转储」的时候拍 —— 那时 KPI 是隐藏的，
        # 页面只剩「扫描结果 + 结论」，最能说明这个功能是干什么的。
        win.btn_dump.setChecked(True)
        win._switch(1)
        p1 = win.dump_page
        p1.scan_local()
        t_scan = time.time()
        while getattr(p1, "_bc_busy", False) and time.time() - t_scan < 30:
            app.processEvents()
            time.sleep(0.05)
        pump(20)
        shot("39_蓝屏转储_本机蓝屏码_深色.png")
        if p1.bc is not None:
            log.append("local scan records=%d with_code=%d cost=%dms"
                       % (len(p1.bc.records), len(p1.bc.with_code), p1.bc.cost_ms))
        win.notice_host.clear()
        p1.tabs.setCurrentIndex(0)      # 后面几张截图要回到「模块清单」
        pump(10)

        # ---- 日志：真实 evtx
        if os.path.isfile(EVTX):
            events, stats = evtx.query_events(EVTX, True, "*", limit=80000)
            win.btn_log.setChecked(True)
            win._switch(0)
            win.log_page._on_events(events, stats)
            pump(14)
            shot("02_日志分析_深色.png")
            log.append(f"log events={len(events)}")

        # ---- 转储：真机内核转储
        if os.path.isfile(KDUMP):
            win.btn_dump.setChecked(True)
            win._switch(1)
            win.dump_page.load_file(KDUMP)
            pump(12)
            # 等 cdb !analyze 跑完（首轮可能要十几秒），让截图里带上调试器结论
            t0 = time.time()
            while getattr(win.dump_page, "_cdb_busy", False) and time.time() - t0 < 120:
                app.processEvents()
                time.sleep(0.05)
            pump(20)
            shot("03_蓝屏转储_真机_深色.png")
            log.append(f"cdb waited {time.time() - t0:.1f}s")
            log.append("kdump loaded")

        # ---- 转储：合成用户模式样本
        for f in glob.glob(os.path.join(ROOT, "tests", "fixtures", "*.dmp")):
            if "appcrash" in os.path.basename(f).lower():
                win.dump_page.load_file(f)
                pump(20)
                shot("04_转储分析_用户模式_深色.png")
                break

        # ---- v2.0.0 起只有深色：原来的「05_日志分析_浅色.png」已删除，
        # 编号 05 不再补拍，其余图片保持原号（说明书里的引用不用跟着改一遍）。
        # ---- 清理优化：垃圾清理（真实扫描前 6 条规则）
        win.rail_buttons[2].setChecked(True)
        win._switch(2)
        cp = win.clean_page
        cp.tabs.setCurrentIndex(0)
        gt = cp.tab_garbage
        results = cleaner.scan_rules(cleaner.RULES)
        gt._on_done(results)
        gt.model.check_rows([r for r in results
                             if r.rule.default_on and r.count > 0])
        if gt.model.rows():
            gt.table.selectRow(0)
        shot("06_清理优化_垃圾清理_深色.png")
        log.append(f"garbage rows={len(results)} checked={gt.model.checked_count()}")

        # ---- 页内提示与页内确认（v1.16.0：运行时不再弹任何模态窗口）
        # 这两张就是原来「清理完成」「确认清理」两个 QMessageBox 被替换后的样子
        notice.note(cp, "ok",
                    "清理完成：处理 328 个文件，释放 1.2 GB —— 已移入回收站，需要时可还原")
        notice.note(cp, "warn", "跳过 3 个被占用的文件（正被其他程序打开）",
                    timeout=0)
        pump(180)                       # 等淡入走完再拍
        shot("36_页内提示_完成与跳过.png")
        log.append(f"notice bars={win.notice_host.count} "
                   f"height={win.notice_host.height()}")

        notice.ask(cp, "确认清理",
                   "即将清理 1,284 个文件，合计 2.3 GB：系统临时文件、缩略图缓存、"
                   "IE 缓存 等 6 项。处理方式：移入回收站，可还原。"
                   "被程序占用的文件会自动跳过，不影响系统运行。",
                   ok_text="开始清理")
        pump(180)
        shot("37_页内确认_清理前.png")
        win.notice_host.clear()
        pump(60)

        # 不可逆那一档：取消勾选「删除到回收站」后是永久删除，确认条转红
        gt.chk_recycle.setChecked(False)
        notice.ask(cp, "确认清理",
                   "即将清理 1,284 个文件，合计 2.3 GB：系统临时文件、缩略图缓存、"
                   "IE 缓存 等 6 项。处理方式：永久删除，不可恢复。"
                   "被程序占用的文件会自动跳过，不影响系统运行。",
                   ok_text="永久删除", danger=True)
        pump(180)
        shot("38_页内确认_永久删除.png")
        win.notice_host.clear()
        gt.chk_recycle.setChecked(True)
        pump(60)

        # ---- 磁盘优化（等物理盘健康回来：首次显示页签才会去查 PowerShell）
        cp.tabs.setCurrentIndex(3)
        t0 = time.time()
        while time.time() - t0 < 25:
            if "正在读取" not in cp.tab_disk.phy_label.text():
                break
            app.processEvents()
            time.sleep(0.1)
        pump(10)
        log.append(f"phy={cp.tab_disk.phy_label.text()[:40]}")
        shot("07_清理优化_磁盘优化_深色.png")
        log.append(f"volumes={len(cp.tab_disk.volumes)}")

        # ---- 大文件清理（磁盘选择器 + 真实结果）
        cp.tabs.setCurrentIndex(1)
        pump(8)
        cp.tab_large.size_box.setCurrentIndex(0)        # 100 MB，样本更多
        large_root = os.path.expandvars("%SystemDrive%\\")
        cp.tab_large.picker.set_path(large_root)
        found = space.find_large_files(large_root, 100 * 1024 * 1024, limit=60,
                                       max_files=250_000)
        cp.tab_large._on_done(found)
        if found:
            cp.tab_large.table.selectRow(0)
        pump(6)
        shot("11_清理优化_大文件清理_深色.png")
        log.append(f"large files={len(found)}")

        # ---- 残留治理：卸载残留
        win.rail_buttons[3].setChecked(True)
        win._switch(3)
        rp = win.residue_page
        rp.tabs.setCurrentIndex(1)
        items, checked = residue.scan_leftovers()
        rp.tab_left._on_done((items, checked))
        if items:
            rp.tab_left.table.selectRow(0)
        shot("08_残留治理_卸载残留_深色.png")
        log.append(f"leftover rows={len(items)} checked_dirs={checked}")

        # ---- 注册表冗余（有结果才拍）
        rp.tabs.setCurrentIndex(0)
        issues = reg.scan_all()
        rp.tab_reg._on_done(issues)
        if issues:
            rp.tab_reg.table.selectRow(0)
            shot("09_残留治理_注册表冗余_深色.png")
        log.append(f"registry rows={len(issues)}")

        # ---- 文件占用（v1.14.0）：拿真被占着的 explorer.exe 演示
        rp.tabs.setCurrentWidget(rp.tab_lock)
        lock_target = os.path.join(os.environ["SystemRoot"], "explorer.exe")
        lrep = locks.query(lock_target)
        rp.tab_lock.path_edit.setText(lock_target)
        rp.tab_lock._rep = lrep
        rp.tab_lock._fill(lrep)
        pump(8)
        shot("32_残留治理_文件占用_深色.png")
        log.append(f"lock tab total={lrep.total} locked={lrep.locked_count} "
                   f"cost={lrep.elapsed_ms:.0f}ms headline={lrep.headline!r}")

        # ---- 大目录分析
        win.rail_buttons[2].setChecked(True)
        win._switch(2)
        cp.tabs.setCurrentIndex(2)
        target = os.path.expandvars("%ProgramFiles%")
        node = space.analyze_dir(target)
        cp.tab_dir.picker.set_path(target)      # 让磁盘选择器显示当前分析目录
        cp.tab_dir._on_done((target, node))
        pump(6)
        shot("10_清理优化_大目录分析_深色.png")
        log.append(f"dir children={len(node.children)}")

        # ---- 系统优化（四个页签，等后台扫描跑完再拍）
        win.rail_buttons[4].setChecked(True)
        win._switch(4)
        op = win.optimize_page

        def wait_idle(tab, timeout=30.0):
            t0 = time.time()
            while getattr(tab, "_busy", False) and time.time() - t0 < timeout:
                app.processEvents()
                time.sleep(0.05)

        op.tabs.setCurrentIndex(0)
        pump(8)
        wait_idle(op.tab_info)
        pump(8)
        shot("12_系统优化_系统信息_深色.png")
        log.append(f"sysinfo rows={len(op.tab_info.sys_model.rows())} "
                   f"hw={len(op.tab_info.hw_model.rows())}")

        op.tabs.setCurrentIndex(1)
        pump(8)
        wait_idle(op.tab_startup)
        pump(8)
        if op.tab_startup.model.rows():
            op.tab_startup.table.selectRow(1)
        pump(6)
        shot("13_系统优化_启动项_深色.png")
        log.append(f"startup rows={len(op.tab_startup.model.rows())}")

        op.tabs.setCurrentIndex(2)
        pump(8)
        wait_idle(op.tab_services)
        pump(8)
        if op.tab_services.model.rows():
            op.tab_services.table.selectRow(0)
        pump(6)
        shot("14_系统优化_服务_深色.png")
        log.append(f"services rows={len(op.tab_services.model.rows())}")

        op.tabs.setCurrentIndex(3)
        pump(8)
        # 切到「全部」：让 4 项 Win10 专属的灰显状态进画面，双分支一眼可见
        op.tab_tweaks.chips.set_current("all")
        pump(8)
        if op.tab_tweaks.model.rows():
            op.tab_tweaks.table.selectRow(0)
        pump(6)
        shot("15_系统优化_响应微调_深色.png")
        log.append(f"tweaks rows={len(op.tab_tweaks.model.rows())} "
                   f"usable={len(op.tab_tweaks.model.checked_rows())} "
                   f"label={op.tab_tweaks.count_label.text()}")

        # ---- 变更记录（三个来源折成时间线）
        op.tabs.setCurrentIndex(4)
        pump(8)
        wait_idle(op.tab_changes)
        pump(10)
        if op.tab_changes.model.rows():
            op.tab_changes.table.selectRow(0)
        pump(6)
        shot("17_系统优化_变更记录_深色.png")
        log.append(f"changes rows={len(op.tab_changes.rows)} "
                   f"active={op.tab_changes.summary.active_total} "
                   f"restored={op.tab_changes.summary.restored} "
                   f"label={op.tab_changes.count_label.text()}")

        # ---- 网络诊断（第六工作台，等 7 步探测跑完）
        win.btn_net.setChecked(True)
        win._switch(5)
        t0 = time.time()
        while win.net_page.rep is None and time.time() - t0 < 90:
            app.processEvents()
            time.sleep(0.05)
        pump(14)
        shot("16_网络诊断_深色.png")
        nrep = win.net_page.rep
        log.append(f"net waited {time.time() - t0:.1f}s "
                   f"checks={len(nrep.checks) if nrep else 0} "
                   f"nics={len(nrep.adapters) if nrep else 0} "
                   f"ports={len(nrep.listeners) if nrep else 0} "
                   f"problems={len(nrep.problems) if nrep else '-'}")
        win.net_page.tabs.setCurrentIndex(2)
        pump(8)
        shot("18_网络诊断_hosts与代理_深色.png")

        # ---- 宽带测速（网络诊断第四个页签，真跑一轮 5 秒）
        npg = win.net_page
        npg.tabs.setCurrentIndex(3)
        pump(12)
        npg.cmb_sec.setCurrentIndex(0)
        t0 = time.time()
        npg.start_speed()
        while npg._speed_busy and time.time() - t0 < 120:
            app.processEvents()
            time.sleep(0.05)
        pump(16)
        shot("28_网络诊断_宽带测速_深色.png")
        srep = npg.speed_rep
        log.append(f"netspeed waited {time.time() - t0:.1f}s "
                   f"down={srep.down_mbps if srep else 0:.0f} "
                   f"up={srep.up_mbps if srep else 0:.0f} "
                   f"node={srep.node if srep else '-'} "
                   f"traffic={srep.traffic_mb if srep else 0:.0f}MB")
        win.net_page.tabs.setCurrentIndex(0)

        # ---- 系统体检（第七工作台，五个页签）
        win.btn_health.setChecked(True)
        win._switch(6)
        hp = win.health_page

        def wait_health(timeout=90.0):
            t0 = time.time()
            while time.time() - t0 < timeout:
                if hp.dev_stats and hp.up_rep and hp.sec_rep and hp.comp_rep:
                    return True
                app.processEvents()
                time.sleep(0.05)
            return False

        wait_health()
        pump(10)

        hp.tabs.setCurrentIndex(0)
        hp.dev_chips.set_current("all")
        pump(10)
        shot("19_系统体检_设备与驱动_深色.png")
        log.append(f"health dev rows={hp.dev_model.rowCount()} "
                   f"drv={hp.drv_model.rowCount()} count={hp.dev_count.text()}")

        hp.tabs.setCurrentIndex(1)
        pump(10)
        shot("20_系统体检_升级体检_深色.png")
        log.append(f"health upgrade ready={hp.up_rep.ready if hp.up_rep else '-'} "
                   f"rows={hp.up_model.rowCount()}")

        hp.tabs.setCurrentIndex(2)
        t0 = time.time()
        while len(hp._pts) < 6 and time.time() - t0 < 20:
            app.processEvents()
            time.sleep(0.1)
        # 等进程表出数（第二个采样点才有 CPU）
        t0 = time.time()
        while not (hp._prep and hp._prep.warm and hp.proc_model.rowCount() > 0) \
                and time.time() - t0 < 20:
            app.processEvents()
            time.sleep(0.1)
        pump(10)
        shot("21_系统体检_性能采样_深色.png")
        if hp._prep:
            log.append(f"health perf points={len(hp._pts)} "
                       f"proc rows={hp.proc_model.rowCount()} "
                       f"cpu_sum={sum(p.cpu for p in hp._prep.rows):.1f}%")
        else:
            log.append(f"health perf points={len(hp._pts)} proc=未取到")

        hp.tabs.setCurrentIndex(3)
        pump(10)
        shot("22_系统体检_安全基线_深色.png")
        log.append(f"health security score={hp.sec_rep.score if hp.sec_rep else '-'} "
                   f"rows={hp.sec_model.rowCount()}")

        # ---- 系统文件：顺带把组件存储体积统计完（十几秒，图上才有真实数字）
        hp.tabs.setCurrentIndex(4)
        pump(8)
        hp.measure_winsxs()
        t0 = time.time()
        while hp._busy.get("size") and time.time() - t0 < 180:
            app.processEvents()
            time.sleep(0.1)
        pump(12)
        shot("23_系统体检_系统文件_深色.png")
        log.append(f"health component dism="
                   f"{hp.comp_rep.dism_state if hp.comp_rep else '-'} "
                   f"rows={hp.file_model.rowCount()} winsxs={hp._winsxs}")

        # ---- 磁盘 SMART：等健康事件解析完再拍
        hp.tabs.setCurrentIndex(5)
        t0 = time.time()
        while hp.disk_rep is None and time.time() - t0 < 90:
            app.processEvents()
            time.sleep(0.1)
        pump(12)
        shot("24_系统体检_磁盘SMART_深色.png")
        d0 = hp.disk_rep.disks[0] if (hp.disk_rep and hp.disk_rep.disks) else None
        log.append(f"health disk rows={hp.disk_model.rowCount()} "
                   f"io={hp.io_model.rowCount()} headline="
                   f"{hp.disk_rep.headline if hp.disk_rep else '-'}")
        if d0 and d0.log:
            log.append(f"  {d0.title} life={d0.log.life_left}% temp={d0.log.temp_c}C "
                       f"write={d0.log.write_gb:.0f}GB hours={d0.log.hours} "
                       f"per_day={d0.per_day_gb}GB")

        # ---- 硬件错误：等 WHEA 聚合完再拍
        hp.tabs.setCurrentIndex(6)
        t0 = time.time()
        while hp.hw_rep is None and time.time() - t0 < 90:
            app.processEvents()
            time.sleep(0.1)
        pump(12)
        shot("25_系统体检_硬件错误_深色.png")
        hr = hp.hw_rep
        log.append(f"health hw rows={hp.hw_model.rowCount()} "
                   f"extra={hp.extra_model.rowCount()} "
                   f"fatal={hr.fatal if hr else '-'} "
                   f"corrected={hr.corrected if hr else '-'} "
                   f"info={sum(hr.info_ids.values()) if hr else '-'} "
                   f"unknown={hr.unknown if hr else '-'}")
        if hr:
            for r in hr.rows[:2]:
                log.append(f"  {r.sev}/{r.level} {r.part} x{r.count} "
                           f"recent7={r.recent7} ctx={r.context}")

        # ---- 崩溃转储体检（v1.13.0）：等事件读完后拍，卡片才是最终结论
        hp.tabs.setCurrentIndex(7)
        if hp.dc_snap is None:
            hp.load_dumpcfg()
        t0 = time.time()
        while hp.dc_snap is None and time.time() - t0 < 60:
            app.processEvents()
            time.sleep(0.1)
        pump(12)
        shot("30_系统体检_崩溃转储_深色.png")
        dc = hp.dc_snap
        log.append(f"health dumpcfg cards={hp.dc_cards._lay.count() - 1} "
                   f"crashes={hp.dc_crash_model.rowCount()} "
                   f"dumps={hp.dc_dump_model.rowCount()} "
                   f"verdict={hp.dc_headline.text()!r}")
        if dc:
            log.append(f"  type={dc.type_text} pf={dc.pagefile_bytes // 1048576}MB "
                       f"ram={dc.ram // 1073741824}GB "
                       f"need={dc.need_pagefile()[0] // 1048576}MB")
        hp.dc_tabs.setCurrentIndex(1)
        shot("31_系统体检_崩溃转储_事件.png")
        hp.dc_tabs.setCurrentIndex(0)

        # ---- 环境变量体检（v1.14.0）
        hp.tabs.setCurrentWidget(hp.tab_env)
        if hp.env_rep is None:
            env_rep = pe.audit()
            hp.env_rep = env_rep
            hp.env_findings = list(env_rep.findings)
            hp._fill_pathenv()
        pump(12)
        shot("33_系统体检_环境变量_深色.png")
        log.append(f"health pathenv total={hp.env_rep.total} "
                   f"missing={hp.env_rep.missing} dup={hp.env_rep.dups} "
                   f"findings={len(hp.env_findings)}")

        # ---- 驱动体检（v1.15.0）：包数据库 + 签名并行校验（冷扫 ~2 秒）
        hp.tabs.setCurrentWidget(hp.tab_drv)
        if hp.drv_rep is None:
            drep = drv.audit()
            hp.drv_rep = drep
            hp.drv_findings = drv.build_findings(drep)
            hp._fill_drv()
        pump(12)
        shot("34_系统体检_驱动体检_深色.png")
        log.append(f"health drivers pkgs={len(hp.drv_rep.packages)} "
                   f"third={len(hp.drv_rep.third_packages)} "
                   f"files={len(hp.drv_rep.files)} "
                   f"unsigned={len(hp.drv_rep.unsigned)} "
                   f"suspect={len(hp.drv_rep.suspicious_sign)} "
                   f"mods={hp.drv_rep.modules}")
        hp.drv_tabs.setCurrentIndex(1)
        hp.drv_chips.set_current("all")
        pump(10)
        shot("35_系统体检_驱动文件_深色.png")
        hp.drv_chips.set_current("bad")
        hp.drv_tabs.setCurrentIndex(0)

        # ---- U 盘测速（第八工作台）：有 USB 盘就真跑一次，让图上有真实数字
        win.btn_usb.setChecked(True)
        win._switch(7)
        pump(8)
        up = win.usb_page
        log.append(f"usb targets={up.box.count()} "
                   f"texts={[up.box.itemText(i) for i in range(up.box.count())]}")
        shot("26_U盘测速_空态_深色.png")

        pick = -1
        for i, t in enumerate(up._targets):
            if t.is_usb and t.free > 1 << 30:
                pick = i
                break
        if pick >= 0:
            up.box.setCurrentIndex(pick)
            up.cmb_size.setCurrentIndex(0)        # 128 MB，拍图不必等太久
            pump(4)
            up.start()
            t0 = time.time()
            while up._busy and time.time() - t0 < 120:
                app.processEvents()
                time.sleep(0.05)
            pump(10)
            shot("27_U盘测速_结果_深色.png")
            log.append(f"usb ran {time.time() - t0:.1f}s "
                       f"read={up.k_read._full} write={up.k_write._full} "
                       f"ratio={up.k_ratio._full} bus={up.k_bus._full} "
                       f"rows={up.model.rowCount()} "
                       f"hist={up.hist_model.rowCount()}")
            log.append(f"  progress={up.lbl_prog.text()}")
        else:
            log.append("usb 没有可测的 USB 盘，只拍空态")

        # ---- 40：九项体检跑完之后的整机结论条（v2.0 阶段二）
        # 必须等 health_rep 真的出现才拍：结论条只在九项全回来时现身，
        # 早拍一张就是「没有结论条」的旧界面，等于没拍。
        win.btn_health.setChecked(True)
        win._switch(6)
        hp = win.page(6)
        t_h = time.time()
        while (getattr(hp, "health_rep", None) is None
               and time.time() - t_h < 180):
            app.processEvents()
            time.sleep(0.05)
        pump(20)
        shot("40_系统体检_一键体检_深色.png")
        log.append("health verdict=%s score=%r waited=%.1fs"
                   % (hp.verdict.isVisible(), hp.v_score.text(), time.time() - t_h))

        # ---- 41：设备清单点表头排序 + 筛选框（v2.0 阶段三）
        # 走 sortByColumn（Qt 点表头的真实路径），别自己 emit sectionClicked ——
        # 354 行的表那样会访问违例，程序无声消失。
        hp.tabs.setCurrentIndex(0)
        pump(20)
        pane = hp.dev_pane
        pane.edit.setText("USB")
        pump(20)
        pane.view.sortByColumn(1, Qt.AscendingOrder)
        pump(20)
        shot("41_表格排序与筛选_深色.png")
        log.append("tables filter=%r shown=%d total=%d sort=%s"
                   % (pane.edit.text(), len(hp.dev_model.rows()),
                      hp.dev_model.total_count(), hp.dev_model.sort_state()))
        pane.edit.setText("")
        hp.dev_model.clear_sort()
        pump(20)
    except Exception as exc:        # noqa: BLE001
        import traceback
        log.append("FAILED: " + repr(exc))
        log.append(traceback.format_exc())
    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(log))
    return 0


if __name__ == "__main__":
    sys.exit(main())
