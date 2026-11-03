"""探针 v1.13-c：用真实数据跑 dumpcfg.read() / evaluate()，看结论是否站得住。"""
from __future__ import annotations

import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app.core import dumpcfg  # noqa: E402

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "probe_dumpcfg3.log")
lines: list[str] = []


def log(s: str = "") -> None:
    lines.append(s)


def main() -> None:
    t0 = time.perf_counter()
    snap = dumpcfg.read(with_events=False)
    t1 = time.perf_counter()
    snap2 = dumpcfg.read(with_events=True)
    t2 = time.perf_counter()
    log("采集耗时：无事件 %.1f ms / 含事件 %.1f ms"
        % ((t1 - t0) * 1000, (t2 - t1) * 1000))
    log()

    s = snap2
    log("=== 事实 ===")
    log("  转储类型      %s (CrashDumpEnabled=%d FilterPages=%d)"
        % (s.type_text, s.enabled, s.filter_pages))
    log("  DumpFile      %s" % s.dump_file_path)
    log("  MinidumpDir   %s" % s.minidump_path)
    log("  自动重启      %d   覆盖 %d   写日志 %d   保留数 %d"
        % (s.auto_reboot, s.overwrite, s.log_event, s.minidumps_count))
    log("  DumpFilters   %s" % (s.dump_filters,))
    log("  PagingFiles   %s" % (s.paging_reg,))
    log("  页面文件      %s" % ([(p, dumpcfg.human(sz)) for p, sz in s.pagefiles],))
    log("  物理内存      %s" % dumpcfg.human(s.ram))
    log("  内核占用      %s" % dumpcfg.human(s.kernel_total))
    log("  提交上限      %s" % dumpcfg.human(s.commit_limit))
    log("  系统盘/剩余   %s / %s" % (s.system_drive, dumpcfg.human(s.free_system)))
    log("  页面文件在系统盘 %s" % s.pagefile_on_system)
    need, note = s.need_pagefile()
    log("  页面文件需求  %s （%s）" % (dumpcfg.human(need), note))
    log("  现有转储      %d 个" % len(s.dumps))
    for d in s.dumps[:5]:
        log("    %s  %s  %s  %s" % (d.name, d.size_text, d.time_text, d.kind_text))
    log("  活动内核报告  %d 个 %s" % (len(s.live_reports),
                                   [os.path.basename(p) for p in s.live_reports[:3]]))
    log("  崩溃事件      %d 条（蓝屏 %d / 掉电 %d）"
        % (len(s.crashes), len(s.bugchecks), len(s.power_losses)))
    for c in s.crashes[:6]:
        log("    %s  %-34s id=%-5d %-14s code=%s | %s"
            % (c.time_text, c.provider[:34], c.event_id, c.kind_text,
               c.code_text, c.explain))

    log()
    log("=== 结论 ===")
    level, text = dumpcfg.verdict(dumpcfg.evaluate(s))
    log("  [%s] %s" % (level, text))
    log()
    for f in dumpcfg.evaluate(s):
        log("  (%s) %-28s %s" % (f.severity, f.topic, f.title))
        if f.detail:
            log("      %s" % f.detail.replace("\n", " "))
        for a in f.advice:
            log("      · %s" % a)

    # 模拟：把本机改成「完整转储」，页面文件 2GB → 应该报 crit
    log()
    log("=== 反事实：改成完整内存转储（页面文件仍 2 GB）===")
    bad = dumpcfg.Snap(enabled=1, ram=32 * 1024 ** 3, kernel_total=s.kernel_total,
                       pagefiles=[(r"C:\pagefile.sys", 2 * 1024 ** 3)],
                       system_drive="C:", free_system=128 * 1024 ** 3)
    bad.crashes = s.crashes
    for f in dumpcfg.evaluate(bad):
        if f.severity in ("crit", "warn"):
            log("  (%s) %s" % (f.severity, f.title))
            log("      %s" % f.detail)

    log()
    log("=== 反事实：转储关闭 + 有蓝屏记录 ===")
    off = dumpcfg.Snap(enabled=0, ram=32 * 1024 ** 3,
                       pagefiles=[(r"C:\pagefile.sys", 2 * 1024 ** 3)])
    from app.core.dumpcfg import CrashEvent
    from datetime import datetime
    off.crashes = [CrashEvent(datetime(2026, 10, 1, 3, 0), "BugCheck", 1001,
                              "bugcheck", 0x0000009F)]
    for f in dumpcfg.evaluate(off):
        if f.severity in ("crit", "warn"):
            log("  (%s) %s" % (f.severity, f.title))
            log("      %s" % f.detail)

    with open(OUT, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
