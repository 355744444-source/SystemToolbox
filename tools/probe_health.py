# -*- coding: utf-8 -*-
"""性能采样 + 安全基线探针。结果写 probe_health.log"""
from __future__ import annotations

import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app.core import perf, security                          # noqa: E402

OUT = os.path.join(ROOT, "probe_health.log")


def main() -> int:
    lines = []
    p = lines.append
    try:
        # ---------------- 性能
        s = perf.Sampler()
        s.prime()
        pts = []
        for _ in range(4):
            time.sleep(0.6)
            pts.append(s.sample())
        s.close()
        p("perf points=%d" % len(pts))
        for pt in pts:
            p("  cpu=%5.1f%% mem=%5.1f%% (%.1f/%.1f GB) disk=%5.1f%% "
              "net=%.1f KB/s (in %.1f / out %.1f)"
              % (pt.cpu, pt.mem, pt.mem_used_gb, pt.mem_total_gb, pt.disk,
                 pt.net_total, pt.net_in, pt.net_out))
        sm = perf.summarize(pts)
        p("  summarize=%r" % {k: (round(v, 1) if isinstance(v, float) else v)
                              for k, v in sm.items()})
        rx, tx = perf.if_octets()
        p("if_octets rx=%.1f MB tx=%.1f MB" % (rx / 1048576, tx / 1048576))
        snap = perf.snapshot()
        p("snapshot cpu=%.1f mem=%.1f disk=%.1f" % (snap.cpu, snap.mem, snap.disk))

        # ---------------- 安全基线
        t0 = time.time()
        rep = security.scan()
        p("security elapsed=%.2fs score=%d risks=%d warns=%d"
          % (time.time() - t0, rep.score, len(rep.risks), len(rep.warns)))
        p("  headline: " + rep.headline)
        for r in rep.rows:
            p("  [%-4s %s] %-16s | %-34s | %s"
              % (r.level, r.level_text, r.name, r.value[:34],
                 (r.detail or r.where)[:44]))
        p("RESULT: OK")
    except Exception as exc:                    # noqa: BLE001
        import traceback
        p("FAILED: " + repr(exc))
        p(traceback.format_exc())
    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
