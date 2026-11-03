# -*- coding: utf-8 -*-
"""验证 procs.py / component.py 的真实输出，写 probe_v160b.log。"""
from __future__ import annotations

import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
LOG = open("probe_v160b.log", "w", encoding="utf-8", buffering=1)


def w(s):
    LOG.write(str(s) + "\n")


from app.core import component, procs        # noqa: E402

w("==== procs ====")
s = procs.ProcSampler()
r1 = s.sample()
w("first: total=%d warm=%s denied=%d elapsed=%.1fms" % (
    r1.total, r1.warm, r1.denied, r1.elapsed * 1000))
time.sleep(1.0)
r2 = s.sample()
w("second: total=%d warm=%s denied=%d elapsed=%.1fms ncpu=%d" % (
    r2.total, r2.warm, r2.denied, r2.elapsed * 1000, r2.ncpu))
w("cpu 合计 %.1f%%（任务管理器口径：整机满载=100，应 <= 100）" % sum(p.cpu for p in r2.rows))
for p in r2.sorted_by("cpu", 8):
    w("  CPU %6.2f%%  %-28s pid=%-6d mem=%.0fMB priv=%.0fMB h=%d t=%d up=%s sys=%s" % (
        p.cpu, p.name[:28], p.pid, p.mem_mb, p.private_mb,
        p.handles, p.threads, p.started_text, p.system))
w("-- 按内存 --")
for p in r2.sorted_by("mem", 5):
    w("  MEM %8.1fMB priv=%8.1fMB  %-28s pid=%d" % (
        p.mem_mb, p.private_mb, p.name[:28], p.pid))
w("-- 按句柄 --")
for p in r2.sorted_by("handles", 5):
    w("  H %5d  %-28s pid=%d" % (p.handles, p.name[:28], p.pid))
w("path 示例: %s" % (next((p.path for p in r2.rows if p.path), "")))

w("")
w("==== component ====")
t0 = time.perf_counter()
rep = component.scan(with_dism=True)
w("elapsed=%.2fs admin=%s dism=%s pending=%s ubr=%s" % (
    rep.elapsed, rep.admin, rep.dism_state, rep.pending, rep.ubr))
w("cbs: %.2fMB mtime=%s lines=%d noise=%d bad=%d sr=%d" % (
    rep.cbs_size_mb, rep.cbs_mtime, rep.cbs_lines, rep.cbs_noise,
    rep.cbs_bad, rep.sr_lines))
w("patches=%s" % (rep.patches,))
w("headline=%s" % rep.headline)
for r in rep.rows:
    w("  [%s] %-12s | %s" % (r.level, r.name, r.value[:70]))
    if r.detail:
        w("        %s" % r.detail[:120])
    if r.where:
        w("        -> %s" % r.where[:90])

w("")
w("==== winsxs 限时 5s ====")
t0 = time.perf_counter()
b, n, cut = component.measure_winsxs(timeout=5.0,
                                     progress=lambda d, t, m: w("   " + m))
w("bytes=%.2fGB files=%d truncated=%s elapsed=%.1fs" % (
    b / 1073741824.0, n, cut, time.perf_counter() - t0))

w("")
w("DONE")
LOG.close()
print("done")
