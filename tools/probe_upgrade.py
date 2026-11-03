# -*- coding: utf-8 -*-
"""Win11 升级体检探针。结果写 probe_upgrade.log"""
from __future__ import annotations

import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app.core import upgrade as up                          # noqa: E402

OUT = os.path.join(ROOT, "probe_upgrade.log")


def main() -> int:
    lines = []
    p = lines.append
    try:
        t0 = time.time()
        rep = up.check()
        p("elapsed=%.2fs ready=%s blockers=%d cautions=%d"
          % (time.time() - t0, rep.ready, len(rep.blockers), len(rep.cautions)))
        p("cpu=%r gen=%r ram=%.1fGB disk=%.0f/%.0fGB fw=%r tpm=%r sb=%r"
          % (rep.cpu, rep.cpu_gen, rep.ram_gb, rep.disk_total_gb,
             rep.disk_free_gb, rep.firmware, rep.tpm, rep.secure_boot))
        p("headline: " + rep.headline)
        for i in rep.items:
            p("  [%-4s %s] %-8s | 现状 %-52s | 要求 %-38s | %s"
              % (i.verdict, i.verdict_text, i.name, i.current[:52],
                 i.required[:38], i.note[:46]))

        # CPU 代际解析的单测素材
        for name in ("12th Gen Intel(R) Core(TM) i7-12700K",
                     "Intel(R) Core(TM) i5-8250U CPU @ 1.60GHz",
                     "Intel(R) Core(TM) i7-9700 CPU @ 3.00GHz",
                     "Intel(R) Core(TM) i5-10210U CPU @ 1.60GHz",
                     "AMD Ryzen 5 3600 6-Core Processor",
                     "AMD Ryzen 7 5800X 8-Core Processor",
                     "AMD Ryzen 5 2600 Six-Core Processor",
                     "AMD Ryzen 9 7945HX with Radeon Graphics",
                     "Intel(R) Xeon(R) Silver 4210R CPU @ 2.40GHz",
                     "AMD Athlon 3000G"):
            p("  gen(%s) = %r" % (name[:44], up.cpu_generation(name)))
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
