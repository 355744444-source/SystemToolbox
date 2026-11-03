# -*- coding: utf-8 -*-
"""设备与驱动体检探针：验证问题码、驱动清单、统计。全只读。

结果写 probe_devices.log
"""
from __future__ import annotations

import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app.core import devices as dv                          # noqa: E402

OUT = os.path.join(ROOT, "probe_devices.log")
log = []


def w(s=""):
    log.append(str(s))


def main() -> int:
    try:
        t0 = time.time()
        items = dv.devices()
        el = time.time() - t0
        w(f"devices={len(items)} elapsed={el:.2f}s")

        lv = {}
        for d in items:
            lv[d.level] = lv.get(d.level, 0) + 1
        w(f"levels={lv}")
        absent = [d for d in items if not d.present]
        w(f"absent={len(absent)} 样例={[d.name[:26] for d in absent[:4]]}")

        probs = dv.problem_devices(items)
        w(f"problems={len(probs)}")
        for d in probs[:12]:
            w(f"  [{d.level}] {d.name[:34]} | {d.cls} | {d.status} "
              f"| problem={d.problem} | {d.hint[:36]}")

        # 已知设备应能取到类别与问题码；抽查前 3 台
        for d in items[:3]:
            w(f"sample: {d.name[:30]} cls={d.cls!r} vendor={d.vendor[:18]!r} "
              f"driver={d.driver!r} problem={d.problem} level={d.level}")

        t1 = time.time()
        drvs = dv.drivers()
        w(f"drivers={len(drvs)} elapsed={time.time() - t1:.2f}s")
        for d in drvs[:8]:
            w(f"  drv {d.date:>10} {d.version:>18} {d.desc[:32]:<32} "
              f"| {d.provider[:20]} | {d.inf}")
        third = [d for d in drvs if d.third_party]
        w(f"third_party={len(third)} "
          f"样例={[d.desc[:20] for d in third[:4]]}")
        undated = [d for d in drvs if not d.ts]
        w(f"无法解析日期={len(undated)} 样例={[(d.desc[:18], d.date) for d in undated[:3]]}")

        dv.link_devices(items, drvs)
        linked = [d for d in drvs if d.devices]
        w(f"关联到设备的驱动={len(linked)} "
          f"top={[(d.desc[:16], d.devices) for d in sorted(drvs, key=lambda x: -x.devices)[:5]]}")

        st = dv.stats(items, drvs)
        w(f"stats devices={st.devices} problems={st.problems} "
          f"disabled={st.disabled} absent={st.absent} drivers={st.drivers} "
          f"third={st.third_party} old={st.old_drivers}")
        for r in st.rows:
            w("  " + r)

        # 问题码映射要覆盖 1..52 的常见项
        w(f"PROBLEM_CN keys={len(dv.PROBLEM_CN)}")
        assert 43 in dv.PROBLEM_CN and 28 in dv.PROBLEM_CN
        w("RESULT: OK")
    except Exception as exc:                     # noqa: BLE001
        import traceback
        w("FAILED: " + repr(exc))
        w(traceback.format_exc())
    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(log))
    return 0


if __name__ == "__main__":
    sys.exit(main())
