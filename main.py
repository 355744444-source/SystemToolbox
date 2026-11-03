# -*- coding: utf-8 -*-
"""系统万能工具箱 SystemToolbox —— 入口。

事件日志（.evtx / 实时通道）+ 崩溃转储（MDMP / 内核转储）一体化分析。

命令行：
  SystemToolbox.exe                 正常启动
  SystemToolbox.exe <文件>          启动并直接分析该 .evtx / .dmp
  SystemToolbox.exe --scan          全面体检并把结论写到 %TEMP%\\SystemToolbox_scan.txt
  SystemToolbox.exe --scan --json   同上，输出 JSON（便于批量采集）
  SystemToolbox.exe --scan --full   含 DISM 的系统文件检查（要管理员，几分钟）
  SystemToolbox.exe --scan --only=devices,security
  SystemToolbox.exe --selftest      自检（结果写入 %TEMP%\\cls_selftest.txt）
  SystemToolbox.exe --version       版本信息

`--scan` 的退出码：0 无严重项 / 1 有严重项 / 3 运行失败。
"""
from __future__ import annotations

import os
import sys
import time

BASE = os.path.dirname(os.path.abspath(sys.argv[0] if getattr(sys, "frozen", False) else __file__))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PyQt5.QtCore import Qt  # noqa: E402
from PyQt5.QtGui import QFont  # noqa: E402
from PyQt5.QtWidgets import QApplication  # noqa: E402

from app import theme  # noqa: E402
from app import version as _ver  # noqa: E402
from app.version import COMPANY, VERSION  # noqa: E402
from app.ui.main_window import MainWindow, PAGE_ATTRS  # noqa: E402
from app.ui.widgets import app_icon  # noqa: E402


def _make_app(argv) -> QApplication:
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
    app = QApplication(argv)
    app.setApplicationName("SystemToolbox")
    app.setApplicationDisplayName("系统万能工具箱")
    app.setApplicationVersion(VERSION)
    app.setOrganizationName(COMPANY)
    app.setStyle("Fusion")
    # 全局字体：微软雅黑正体（含逐字回落，缺字时才退到 YaHei UI / Segoe UI）
    ui = QFont(theme.FONT_FAMILY, 9)
    if hasattr(ui, "setFamilies"):
        ui.setFamilies(list(theme.FONT_FAMILIES))
    app.setFont(ui)
    # 图标必须显式设置：QApplication 没有 windowIcon 时，Windows 任务栏 /
    # Alt-Tab 会画成系统占位图（曾在单文件版重现过）
    icon = app_icon()
    if not icon.isNull():
        app.setWindowIcon(icon)
    # 只有一套深色皮肤，启动挂一次 QSS 就够了（v2.0.0 起没有运行期切换）。
    theme.apply_theme(app)
    return app


def _selftest() -> int:
    """无界面自检：建窗、灌一份合成转储，确认核心链路可用。"""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    out = os.path.join(os.environ.get("TEMP", "."), "cls_selftest.txt")
    lines = []
    ok = True
    try:
        app = _make_app([sys.argv[0]])
        win = MainWindow()
        win.resize(1280, 820)
        win.show()
        for _ in range(5):
            app.processEvents()
        lines.append(f"window ok, stack pages={win.stack.count()}")
        # 懒建防线：只应构建了第一个工作台，否则启动又被八个页面拖慢
        # 页名单跟随 PAGE_ATTRS，别硬编码 —— 加第八个工作台时这里差点漏改
        n_built = sum(1 for n in PAGE_ATTRS
                      if getattr(win, n, None) is not None)
        lines.append(f"lazy pages ok: built={n_built} placeholder="
                     f"{win.stack.count() - n_built}")
        if n_built != 1 or win.stack.count() != len(PAGE_ATTRS):
            ok = False
            lines.append("lazy FAILED: 启动时构建了多个工作台")

        # 任务栏 / Alt-Tab 读的就是窗口图标，空图标会被画成系统占位图
        ic = app.windowIcon()
        lines.append("icon ok: sizes=[%s]" % ", ".join(
            f"{s.width()}x{s.height()}" for s in ic.availableSizes()))
        if ic.isNull():
            ok = False
            lines.append("icon FAILED: windowIcon 为空，任务栏会显示占位图")

        from app.core import analysis as ana, minidump, knowledge as kb, report
        lines.append(f"knowledge bugchecks={len(kb.BUGCHECKS)} exceptions={len(kb.EXCEPTIONS)}")
        lines.append(f"report module={report.VERSION}")

        # 导出用的是 openpyxl，而打包时裁过打包清单 —— 必须在这里真建一个
        # 工作簿，否则「导出 Excel」在 exe 里才炸，源码模式永远看不见
        try:
            from openpyxl import Workbook
            _wb = Workbook()
            _ws = _wb.active
            _ws["A1"] = "probe"
            lines.append(f"openpyxl ok: sheet={_ws.title} cell={_ws['A1'].value}")
        except Exception as exc:      # noqa: BLE001
            ok = False
            lines.append(f"openpyxl FAILED: {exc!r}")

        # 新增工作台的依赖冒烟：卷检测 / 垃圾扫描 / 注册表读取 / 残留索引
        from app.core import cleaner, disk, registry_clean as regcl, residue
        vols = disk.list_volumes()
        lines.append(f"disk ok: volumes={len(vols)} "
                     f"media={[v.media_text for v in vols[:2]]}")
        rule = next(r for r in cleaner.RULES if r.key == "user_crashdumps")
        res = cleaner.scan_rule(rule)
        lines.append(f"cleaner ok: {res.count} files / {res.total_size} B "
                     f"rules={len(cleaner.RULES)}")
        lines.append(f"registry ok: shellex={len(regcl.scan_shellex())} "
                     f"apppaths={len(regcl.scan_app_paths())}")
        lines.append(f"residue ok: installed={residue.installed_index().count}")

        # 磁盘选择器：下拉项应与卷列表一致，path() 能反查到盘符根
        pk = win.page(2).tab_large.picker
        dv = pk.path()
        lines.append(f"picker ok: items={pk.box.count()} path={dv!r} "
                     f"isdir={os.path.isdir(dv)} "
                     f"roundtrip={pk.box.itemData(0) == dv}")
        if not os.path.isdir(dv) or pk.box.count() == 0:
            ok = False
            lines.append("picker FAILED")

        # 系统优化工作台：系统信息 / 启动项 / 服务 / 微调
        from app.core import services as svcmod, startup, sysinfo, tweaks, winver
        wv = winver.current()
        lines.append(f"winver ok: {wv.label} branch={wv.family} build={wv.build}")
        if not wv.build:
            ok = False
            lines.append("winver FAILED: 读不到 build 号，版本分支无法判定")
        snap = sysinfo.collect()
        lines.append(f"sysinfo ok: {snap.host} mem={snap.mem_percent}% "
                     f"procs={snap.proc_total} uptime={sysinfo.human_duration(snap.uptime)}")
        if not snap.mem_total or not snap.os_rows:
            ok = False
            lines.append("sysinfo FAILED")
        starts = startup.scan_all()
        lines.append(f"startup ok: {len(starts)} items "
                     f"(disabled={sum(1 for i in starts if not i.enabled)})")
        svcs = svcmod.scan()
        tunable = sum(1 for s in svcs if s.verdict == "可调优")
        lines.append(f"services ok: {len(svcs)} total tunable={tunable} "
                     f"display={svcs[0].display[:24] if svcs else '-'}")
        st = tweaks.stats(wv)
        lines.append(f"tweaks ok: {st['total']} items usable={st['usable']} "
                     f"other={st['other']} applied={st['applied']}")
        if st["usable"] == 0:
            ok = False
            lines.append("tweaks FAILED: 当前系统一项可用微调都没有，os 标注有问题")
        if not svcs or not starts:
            ok = False
            lines.append("optimize FAILED")

        # 变更记录：三份备份折叠成时间线（只读）
        from app.core import changes as chg
        crow, csum = chg.collect()
        lines.append(f"changes ok: total={csum.total} "
                     f"active={csum.active_total} restored={csum.restored} "
                     f"(startup={csum.active_startup} service={csum.active_service} "
                     f"tweak={csum.active_tweak})")
        if csum.total != len(crow) or csum.active_total + csum.restored != csum.total:
            ok = False
            lines.append("changes FAILED: 统计与明细不一致")

        # 网络诊断：只做不依赖外网的部分（网卡 / hosts / 代理）
        from app.core import netdiag
        nics = netdiag.adapters()
        pri = netdiag.primary(nics)
        nlines, nent, nlevel, _note = netdiag.hosts_entries()
        prx = netdiag.proxy()
        lst = netdiag.listening(limit=200)
        lines.append(f"net ok: nics={len(nics)} primary={pri.title[:28] if pri else '-'} "
                     f"ip={pri.ip if pri else '-'} listening={len(lst)} "
                     f"hosts={len(nent)}/{nlines} level={nlevel or 'ok'} "
                     f"proxy={prx['enabled']}")
        if not nics:
            ok = False
            lines.append("net FAILED: 一块网卡都没读到")
        if win.page(5) is None or not hasattr(win.page(5), "diagnose"):
            ok = False
            lines.append("net FAILED: 第六工作台没接上")

        # 宽带测速：只验节点配置与延迟采样，不真跑一次
        # （真跑会吃掉几百 MB 流量，打包验证不该顺手烧掉用户的流量）
        from app.core import netspeed
        ns_ok, ns_msg = netspeed.quick_check()
        lines.append(f"netspeed {'ok' if ns_ok else 'FAILED'}: {ns_msg}")
        page = win.page(5)
        if not ns_ok or page is None or not hasattr(page, "start_speed"):
            ok = False
            lines.append("netspeed FAILED: 测速页签没接上")

        # 系统体检：设备 / 升级条件 / 实时采样 / 安全基线
        from app.core import devices as devmod, perf, security
        from app.core import component, procs
        from app.core import upgrade as upmod
        from app.core import hwevent, smart as smartm
        devs = devmod.devices()
        drvs = devmod.drivers()
        devmod.link_devices(devs, drvs)
        dst = devmod.stats(devs, drvs)
        lines.append(f"devices ok: {dst.devices} devices / {dst.drivers} drivers "
                     f"problems={dst.problems} disabled={dst.disabled} "
                     f"removed={dst.removed} third={dst.third_party}")
        if not devs or not drvs:
            ok = False
            lines.append("devices FAILED: 设备或驱动清单为空")
        urep = upmod.check()
        lines.append(f"upgrade ok: ready={urep.ready} "
                     f"blockers={len(urep.blockers)} cautions={len(urep.cautions)} "
                     f"cpu={urep.cpu_gen or '-'} ram={urep.ram_gb:.1f}GB "
                     f"fw={urep.firmware or '-'} tpm={urep.tpm}")
        if not urep.items:
            ok = False
            lines.append("upgrade FAILED: 体检项为空")
        _t0 = time.perf_counter()
        psam = perf.Sampler()
        _dt = (time.perf_counter() - _t0) * 1000
        psam.prime()
        time.sleep(0.3)
        ppt = psam.sample()
        dready = psam.wait_disk(2.5)
        psam.close()
        lines.append(f"perf ok: cpu={ppt.cpu:.1f}% mem={ppt.mem:.1f}% "
                     f"disk={ppt.disk:.1f}% net={ppt.net_total:.0f}KB/s "
                     f"ctor={_dt:.0f}ms disk_ready={dready}")
        if not (0 <= ppt.cpu <= 100 and 0 <= ppt.mem <= 100):
            ok = False
            lines.append("perf FAILED: CPU/内存百分比越界")
        if _dt > 200:
            # 磁盘计数器又回到同步建了 —— 体检页首次打开会卡一下
            ok = False
            lines.append(f"perf FAILED: 采样器构造阻塞 {_dt:.0f}ms（应 <200ms）")
        srep = security.scan()
        lines.append(f"security ok: score={srep.score} "
                     f"risks={len(srep.risks)} warns={len(srep.warns)} "
                     f"rows={len(srep.rows)}")
        if not srep.rows:
            ok = False
            lines.append("security FAILED: 基线检查为空")
        crep = component.scan(with_dism=False)
        lines.append(f"component ok: dism={crep.dism_state or 'skip'} "
                     f"cbs={crep.cbs_size_mb:.1f}MB bad={crep.cbs_bad} "
                     f"noise={crep.cbs_noise} pending={len(crep.pending)} "
                     f"ubr={crep.ubr or '-'} rows={len(crep.rows)}")
        if not crep.rows:
            ok = False
            lines.append("component FAILED: 组件检查为空")
        psm = procs.ProcSampler()
        psm.sample()
        time.sleep(0.4)
        prp = psm.sample()
        lines.append(f"procs ok: total={prp.total} denied={prp.denied} "
                     f"cpu_sum={sum(p.cpu for p in prp.rows):.1f}% "
                     f"top={prp.sorted_by('cpu', 1)[0].name if prp.rows else '-'}")
        if not prp.rows or not prp.warm:
            ok = False
            lines.append("procs FAILED: 进程采样为空或未取得基准")
        if sum(p.cpu for p in prp.rows) > 105:
            ok = False
            lines.append("procs FAILED: CPU 合计越界（口径应归一为全机 100）")
        srep2 = smartm.scan()
        d0 = srep2.disks[0] if srep2.disks else None
        lines.append(f"smart ok: disks={len(srep2.disks)} "
                     f"headline={srep2.headline} "
                     f"io={srep2.io_total} "
                     + (f"life={d0.log.life_left}% temp={d0.log.temp_c}C "
                        f"write={d0.log.write_gb:.0f}GB "
                        f"per_day={d0.per_day_gb}GB samples={d0.samples}"
                        if d0 and d0.log else "no-nvme-log"))
        if srep2.disks and d0 is not None and d0.log is None:
            # 有盘但一个字段都没解析出来，说明解析口径和本机对不上
            ok = False
            lines.append("smart FAILED: 有磁盘记录但健康日志解析为空")
        hrep = hwevent.scan()
        lines.append(f"hwevent ok: fatal={hrep.fatal} corrected={hrep.corrected} "
                     f"total={hrep.total} parts={hrep.parts} "
                     f"info={sum(hrep.info_ids.values())} unknown={hrep.unknown} "
                     f"bugcheck={len(hrep.bugchecks)} memdiag={len(hrep.memdiag)}")
        if hrep.records and hrep.unknown >= len(hrep.records):
            # 全部记录都判不出格式，等于这段功能是哑的
            ok = False
            lines.append("hwevent FAILED: CPER 记录全部未被识别")
        if win.page(6) is None or not hasattr(win.page(6), "run_all"):
            ok = False
            lines.append("health FAILED: 第七工作台没接上")
        elif not hasattr(win.page(6), "tab_file"):
            ok = False
            lines.append("health FAILED: 系统文件页签没接上")

        # 崩溃转储配置体检（v1.13.0）：只读，实测物理内存 / 页面文件 / 转储文件
        from app.core import dumpcfg
        dsnap = dumpcfg.read(with_events=False)
        dfind = dumpcfg.evaluate(dsnap)
        dneed, dnote = dsnap.need_pagefile()
        lines.append(f"dumpcfg ok: type={dsnap.type_text} "
                     f"pf={dsnap.pagefile_bytes // 1048576}MB "
                     f"ram={dsnap.ram // 1073741824}GB "
                     f"kernel={dsnap.kernel_total // 1048576}MB "
                     f"need={dneed // 1048576}MB dumps={len(dsnap.dumps)} "
                     f"findings={len(dfind)}")
        if not dsnap.ram or not dsnap.pagefiles:
            # 这两项读不到，容量校验就是瞎的，必须红
            ok = False
            lines.append("dumpcfg FAILED: 物理内存或页面文件读不到")
        if win.page(6) is None or not hasattr(win.page(6), "tab_dc"):
            ok = False
            lines.append("dumpcfg FAILED: 崩溃转储页签没接上")

        # 本机蓝屏记录（v1.17.0）：转储头取码 + 事件日志对齐
        from app.core import bugchecks
        bscan = bugchecks.collect(with_events=True)
        lines.append(f"bugchecks ok: records={len(bscan.records)} "
                     f"with_code={len(bscan.with_code)} "
                     f"cost={bscan.cost_ms}ms "
                     f"codes={[c.code_text for c in bscan.with_code][:4]}")
        if win.page(1) is None or not hasattr(win.page(1), "bc_model"):
            ok = False
            lines.append("bugchecks FAILED: 本机蓝屏记录页签没接上")
        # 有码就必须能显示名称与中文说明 —— 知识库回填断了会在这里露出来
        for _c in bscan.with_code:
            if not _c.name_text or not _c.cn_text:
                ok = False
                lines.append(f"bugchecks FAILED: {_c.code_text} 名称/说明为空")

        # 环境变量体检（v1.14.0）：PATH 条目解析 + 关键变量
        from app.core import pathenv
        prep = pathenv.audit()
        lines.append(f"pathenv ok: entries={prep.total} "
                     f"machine={prep.machine_count} user={prep.user_count} "
                     f"missing={prep.missing} dup={prep.dups} "
                     f"len={len(prep.env.get('PATH', '') or prep.machine_raw)} "
                     f"findings={len(prep.findings)}")
        if prep.total == 0:
            ok = False
            lines.append("pathenv FAILED: 一条 PATH 都没读到")
        if win.page(6) is None or not hasattr(win.page(6), "tab_env"):
            ok = False
            lines.append("pathenv FAILED: 环境变量页签没接上")

        # 签名校验（v1.14.0）：必须走通内嵌 + 目录两条路
        from app.core import sign as signmod
        exe_self = sys.executable
        sinfo = signmod.verify(exe_self)
        lines.append(f"sign ok: {os.path.basename(exe_self)} -> "
                     f"{sinfo.level_text}/{sinfo.method_text} "
                     f"{sinfo.elapsed_ms:.0f}ms rc={sinfo.rc_text}")
        if sinfo.kind == "error":
            ok = False
            lines.append("sign FAILED: 校验自身可执行文件时出错")
        note_pad = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"),
                                "System32", "notepad.exe")
        if os.path.isfile(note_pad):
            ninfo = signmod.verify(note_pad)
            lines.append(f"sign-catalog ok: notepad -> {ninfo.level_text}/"
                         f"{ninfo.method_text} cat={ninfo.catalog[:40] or '-'}")
            if not ninfo.ok:
                # notepad 是目录签名，这条挂了说明 catalog 补判失效
                ok = False
                lines.append("sign FAILED: notepad 没通过（目录签名补判失效）")

        # 文件占用（v1.14.0）：只查不存在的路径，不碰真实文件
        from app.core import locks
        lrep = locks.query(os.path.join(os.environ.get("TEMP", "."),
                                        "cls_selftest_not_exist.bin"))
        lines.append(f"locks ok: files={lrep.total} locked={lrep.locked_count} "
                     f"verdict={lrep.files[0].verdict if lrep.files else '-'} "
                     f"{lrep.elapsed_ms:.0f}ms")
        if lrep.total != 1 or lrep.files[0].verdict != "路径不存在":
            ok = False
            lines.append("locks FAILED: 不存在路径没被判成「路径不存在」")
        if win.page(3) is None or not hasattr(win.page(3), "tab_lock"):
            ok = False
            lines.append("locks FAILED: 文件占用页签没接上")

        # 驱动体检（v1.15.0）：包数据库 + 已加载模块 + 一次真签名校验
        from app.core import drivers as drvmod
        drep = drvmod.audit(want_sign=False)
        dthird = len(drep.third_packages)
        lines.append(f"drivers ok: packages={len(drep.packages)} "
                     f"third={dthird} services={len(drep.services)} "
                     f"files={len(drep.files)} modules={drep.modules} "
                     f"image_less={drep.image_less}")
        if not drep.packages or not drep.services:
            ok = False
            lines.append("drivers FAILED: 驱动包或驱动服务读不到")
        if drep.modules <= 0:
            # EnumDeviceDrivers 拿不到模块 → 「是否已加载」这一列全是错的
            ok = False
            lines.append("drivers FAILED: EnumDeviceDrivers 没拿到任何内核模块")
        # 签名并行链路：拿两个文件走 scan_signatures（走缓存，不重扫全盘）
        drv_dir = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"),
                               "System32", "drivers")
        sample = [os.path.join(drv_dir, n) for n in
                  ("tcpip.sys", "beep.sys", "null.sys")
                  if os.path.isfile(os.path.join(drv_dir, n))]
        if sample:
            sinfos, shits, scut = drvmod.scan_signatures(sample)
            good = sum(1 for i in sinfos if i.ok)
            lines.append(f"drivers-sign ok: n={len(sinfos)} ok={good} "
                         f"cache={shits} cut={scut} "
                         f"{sinfos[0].level_text if sinfos else '-'}")
            if len(sinfos) != len(sample) or good != len(sample):
                # 这三个都是系统内置驱动，正常应当全部通过（内嵌或目录签名）
                ok = False
                lines.append("drivers FAILED: 系统内置驱动没通过签名校验")
        if win.page(6) is None or not hasattr(win.page(6), "tab_drv"):
            ok = False
            lines.append("drivers FAILED: 驱动体检页签没接上")

        # 第八工作台：只校验盘枚举与分级口径，不写盘（自检不该在用户盘上造文件）
        from app.core import usbperf
        uts = usbperf.scan()
        probe = usbperf.Report(
            target=uts[0] if uts else usbperf.Target(),
            sample=usbperf.Sample(read_mbs=329.0, write_mbs=59.0))
        lines.append(f"usbperf ok: targets={len(uts)} "
                     f"usb={sum(1 for t in uts if t.is_usb)} "
                     f"grade={probe.read_grade[0]}/{probe.write_grade[0]}/"
                     f"{probe.ratio_grade[0]}")
        if uts and not all(t.letter.endswith(":") for t in uts):
            ok = False
            lines.append("usbperf FAILED: 卷标识不规范")
        if probe.ratio_grade[0] != "写入偏慢":
            # 读 329 / 写 59 是本机实测的 USB 盘数据，分级口径变了这里会红
            ok = False
            lines.append("usbperf FAILED: 分级阈值与实测口径不一致")
        if win.page(7) is None or not hasattr(win.page(7), "start"):
            ok = False
            lines.append("usb FAILED: 第八工作台没接上")

        cand = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tests", "fixtures")
        base = getattr(sys, "_MEIPASS", BASE)
        for d in (cand, os.path.join(base, "tests", "fixtures")):
            if os.path.isdir(d):
                dumps = [f for f in os.listdir(d) if f.endswith(".dmp")]
                if dumps:
                    rep = minidump.parse(os.path.join(d, dumps[0]))
                    dana = ana.analyze_dump(rep)
                    lines.append(f"dump ok: {rep.name} -> {dana.headline}")
                    break
        # 全面体检编排层：只跑最快的一项，验证归一结构与 JSON 可序列化
        import json as _json

        from app.core import health as hscan
        hrep = hscan.run(keys=["pathenv"], fast=True)
        _json.dumps(hrep.to_dict(), ensure_ascii=False)
        lines.append(f"healthscan ok: sections={len(hrep.sections)} "
                     f"level={hrep.level} score={hrep.score} "
                     f"cost={hrep.cost_ms}ms")
        if hrep.section("pathenv") is None:
            ok = False
            lines.append("healthscan FAILED: 环境变量板块缺失")
        if len(hscan.KEYS) != 9:
            ok = False
            lines.append(f"healthscan FAILED: 体检项应为 9 个，实际 {len(hscan.KEYS)}")

        # 报告是交付物：光有结论导不出来等于没用，这里真写一次盘
        import tempfile as _tf

        from app.core import report as _rep
        _dir = _tf.mkdtemp(prefix="cls_selftest_")
        _h = _rep.export_health_html(os.path.join(_dir, "r.html"), hrep)
        _x = _rep.export_health_xlsx(os.path.join(_dir, "r.xlsx"), hrep)
        _hs, _xs = os.path.getsize(_h), os.path.getsize(_x)
        lines.append(f"healthreport ok: html={_hs}B xlsx={_xs}B")
        if _hs < 1000 or _xs < 1000:
            ok = False
            lines.append("healthreport FAILED: 报告文件过小，内容大概没写进去")

        # 版本资源：spec 里 version=None 时 exe 属性一片空白，程序自己毫无察觉。
        # 打包版必须能读回自己的文件版本，且与代码里的 VERSION 一致。
        from app.core import verinfo
        _vi = verinfo.read(sys.executable)
        if getattr(sys, "frozen", False):
            lines.append("verinfo ok: " + verinfo.describe(sys.executable))
            _fv = ".".join(str(x) for x in _vi.get("filevers", ()))
            if _fv != ".".join(str(x) for x in _ver.version_tuple()):
                ok = False
                lines.append("verinfo FAILED: 版本资源 %r 与 VERSION %r 不一致"
                             % (_fv, VERSION))
            if _vi.get("CompanyName") != COMPANY:
                ok = False
                lines.append("verinfo FAILED: 公司名 %r"
                             % _vi.get("CompanyName"))
        else:
            lines.append("verinfo skip: 源码运行，版本资源只在打包后存在")

        lines.append("theme ok: " + theme.current()["name"]
                     + ("" if len(theme.THEMES) == 1 else
                        " FAILED: 只剩深色皮肤，THEMES 却还有 %d 套" % len(theme.THEMES)))
        if theme.current() is not theme.DARK:
            ok = False
            lines.append("theme FAILED: 当前令牌不是 DARK")
    except Exception as exc:  # noqa: BLE001
        ok = False
        import traceback
        lines.append("SELFTEST FAILED: " + repr(exc))
        lines.append(traceback.format_exc())
    lines.append("RESULT: " + ("OK" if ok else "FAIL"))
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    if ok:
        print("\n".join(lines))
    return 0 if ok else 1


def _scan_text(rep) -> str:
    """给人看的版本。只列「需要处理」的项，正常项不占篇幅。"""
    from app.core import health

    lines = ["系统万能工具箱 SystemToolbox v%s · 全面体检" % VERSION,
             "耗时 %.1f 秒 · 健康分 %d/100 · 结论：%s"
             % (rep.cost_ms / 1000.0, rep.score, rep.headline),
             "=" * 64]
    for s in rep.sections:
        lines.append("[%s] %s（%d ms）%s"
                     % (health.LEVEL_CN.get(s.level, s.level), s.title,
                        s.cost_ms, s.headline))
        if s.error:
            lines.append("    检查失败：%s" % s.error)
            continue
        if s.counts:
            lines.append("    " + " · ".join("%s %s" % (k, v)
                                             for k, v in s.counts.items()))
        for it in s.items:
            if it.severity not in ("crit", "warn"):
                continue
            lines.append("    - %s：%s" % (it.title, it.detail))
            for a in it.advice:
                lines.append("      建议：%s" % a)
        lines.append("")
    probs = rep.problems()
    lines.append("=" * 64)
    if probs:
        lines.append("需要处理 %d 项（严重 %d · 注意 %d）"
                     % (len(probs), rep.crit, rep.warn))
    else:
        lines.append("没有需要处理的项")
    return "\n".join(lines)


def _say(text: str = "", end: str = "\n") -> None:
    """往控制台写。打包版挂在 GBK 控制台上，`›`(U+203A) 这类字符会抛
    UnicodeEncodeError —— 控制台只是顺手看一眼，落盘的 UTF-8 才是正本，
    不能因为控制台编码把整份体检结果丢了。"""
    out = sys.stdout
    if out is None:                 # windowed 构建根本没有控制台
        return
    try:
        out.write(text + end)
    except UnicodeEncodeError:
        enc = getattr(out, "encoding", None) or "gbk"
        out.write(text.encode(enc, "replace").decode(enc, "replace") + end)
    try:
        out.flush()
    except (OSError, ValueError):
        pass


def _scan(argv) -> int:
    """全面体检的命令行出口。打包版没有控制台，所以结果必须落盘。"""
    import json
    import tempfile

    from app.core import health

    full = "--full" in argv
    as_json = "--json" in argv
    keys = None
    out = ""
    for a in argv:
        if a.startswith("--only="):
            keys = [k.strip() for k in a.split("=", 1)[1].split(",") if k.strip()]
        elif a.startswith("--out="):
            out = a.split("=", 1)[1].strip().strip('"').strip("'")
    if not out:
        out = os.path.join(tempfile.gettempdir(),
                           "SystemToolbox_scan" + (".json" if as_json else ".txt"))

    def tick(done, total, msg):
        if msg:
            _say("\r[%d/%d] %-38s" % (done, total, msg[:38]), end="")

    try:
        rep = health.run(keys=keys, tick=tick, fast=not full)
    except Exception as exc:                    # noqa: BLE001
        _say("体检失败：%r" % (exc,))
        return 3
    if sys.stdout:
        _say()                  # 进度行是 \r 结尾的，换一行再打正文

    text = (json.dumps(rep.to_dict(), ensure_ascii=False, indent=2)
            if as_json else _scan_text(rep))
    try:
        with open(out, "w", encoding="utf-8") as f:
            f.write(text)
    except OSError as exc:
        _say("写文件失败：%r" % (exc,))
        return 3
    _say(text)
    _say("结果已写入：%s" % out)
    return 1 if rep.crit else 0


def main() -> int:
    if "--version" in sys.argv:
        print(f"系统万能工具箱 SystemToolbox v{VERSION}")
        return 0
    if "--scan" in sys.argv:
        return _scan(sys.argv[1:])
    if "--selftest" in sys.argv:
        return _selftest()

    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    app = _make_app(sys.argv)
    win = MainWindow()
    win.show()

    if args:
        target = args[0]
        low = target.lower()
        if os.path.isfile(target):
            if low.endswith(".evtx"):
                win.btn_log.setChecked(True)
                win._switch(0)
                win.page(0).set_file(target)
            elif low.endswith((".dmp", ".mdmp", ".hdmp")):
                win.btn_dump.setChecked(True)
                win._switch(1)
                win.page(1).load_file(target)
    return app.exec_()


if __name__ == "__main__":
    sys.exit(main())
