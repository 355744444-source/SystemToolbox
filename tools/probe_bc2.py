# -*- coding: utf-8 -*-
"""探针：本机蓝屏记录模块的真实输出 + 耗时。"""
import io
import os
import sys
import time
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

LOG = os.path.join(ROOT, "probe_bc.log")
_buf = []


def p(*a):
    _buf.append(" ".join(str(x) for x in a))


try:
    from app.core import bugchecks

    t0 = time.perf_counter()
    s = bugchecks.collect(with_events=True)
    dt = (time.perf_counter() - t0) * 1000

    p("== collect 耗时 %.0f ms ==" % dt)
    p("记录 %d 条（有码 %d / 无码 %d）"
      % (len(s.records), len(s.with_code), len(s.power_only)))
    p("headline:", s.headline)
    p("")
    p("-- problems --")
    for x in s.problems:
        p("  ·", x)
    if s.event_error:
        p("  ! event_error:", s.event_error)
    p("")
    p("-- 记录明细 --")
    for c in s.records:
        p("  %s | %-9s | %-8s | %s | %s"
          % (c.time_text, c.origin_text, c.code_text, c.name_text, c.dump_text))
        if c.note:
            p("      note:", c.note)
    p("")
    p("-- findings --")
    for f in bugchecks.findings(s):
        p("  [%s] %s <%s>" % (f.severity, f.title, f.topic))
        p("      %s" % f.detail[:70])
        p("      advice=%d 条" % len(f.advice))

    p("")
    p("-- 单文件头读取（同一文件连读 20 次）--")
    live = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"),
                        "LiveKernelReports", "ResourceTimeout-20261020-1655.dmp")
    if os.path.isfile(live):
        t1 = time.perf_counter()
        for _ in range(20):
            bugchecks.read_header(live)
        p("  2.2GB 文件 20 次 = %.1f ms（单次 %.2f ms）"
          % ((time.perf_counter() - t1) * 1000, (time.perf_counter() - t1) * 50))

    p("")
    p("-- 错误输入不崩 --")
    for bad in ("", "C:\\不存在.dmp", "C:\\Windows",
                os.path.join(ROOT, "README.md")):
        try:
            p("  %r -> %r" % (bad, bugchecks.read_header(bad)))
        except Exception as exc:  # noqa: BLE001
            p("  %r -> 抛异常 %s" % (bad, exc))
except Exception:
    p(traceback.format_exc())
finally:
    io.open(LOG, "w", encoding="utf-8").write("\n".join(_buf) + "\n")
