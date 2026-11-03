# -*- coding: utf-8 -*-
"""探针：看本机蓝屏记录合并后的每一条到底是什么。"""
import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app.core import bugchecks, dumpcfg  # noqa: E402

out = []
s = bugchecks.collect(with_events=True)
out.append("records=%d with_code=%d cost=%dms" % (len(s.records), len(s.with_code), s.cost_ms))
for c in s.records:
    out.append("- %s | %s | %s | %s | %s" % (
        c.time_text, c.code_text, c.origin_text, c.note_text,
        c.reboot_time.strftime("%Y-%m-%d %H:%M:%S") if c.reboot_time else "无后续"))
out.append("problems=%s" % s.problems)
out.append("event_error=%r" % s.event_error)

evs = dumpcfg.scan_crashes(limit=200)
out.append("--- 原始事件 %d 条" % len(evs))
for e in evs:
    out.append("  %s id=%d kind=%s code=0x%X" % (
        e.time.strftime("%Y-%m-%d %H:%M:%S") if e.time else "—",
        e.event_id, e.kind, e.code))

io.open(os.path.join(ROOT, "tools", "probe_bc3.txt"), "w", encoding="utf-8").write(
    "\n".join(out))
print("done")
