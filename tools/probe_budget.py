# -*- coding: utf-8 -*-
"""验证时间预算：winevt\\Logs（157 个 .evtx 被事件日志服务占着）与单文件。"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
LOG = os.path.join(ROOT, "probe_budget.log")


def main():
    from app.core import locks
    out = []
    cases = [
        (os.path.join(os.environ["SystemRoot"], "System32", "winevt", "Logs"), 12.0),
        (os.path.join(os.environ["SystemRoot"], "explorer.exe"), 12.0),
        (os.path.join(os.environ["SystemRoot"], "System32", "config"), 4.0),
    ]
    for target, budget in cases:
        seen = []
        rep = locks.query(target, budget_s=budget,
                          progress=lambda d, t, s: seen.append((d, t)))
        out.append("%s" % target)
        out.append("  budget=%.0fs  实际=%.1fs  total=%d locked=%d kernel=%d "
                   "timeout=%d skipped=%d"
                   % (budget, rep.elapsed_ms / 1000.0, rep.total,
                      rep.locked_count, rep.kernel_count, rep.timeout_count,
                      rep.skipped))
        out.append("  headline=%s" % rep.headline)
        for n in rep.notes:
            out.append("  note: " + n)
        out.append("  进度回调 %d 次，最后一次=%s"
                   % (len(seen), seen[-1] if seen else None))
        dist = {}
        for f in rep.files:
            dist[f.verdict] = dist.get(f.verdict, 0) + 1
        out.append("  判定分布=%s" % dist)
        for f in rep.files[:3]:
            out.append("    %-24s %-8s %s" % (f.name, f.verdict,
                                              (f.detail or "")[:70]))
    with open(LOG, "w", encoding="utf-8") as fh:
        fh.write("\n".join(out))


if __name__ == "__main__":
    main()
    os._exit(0)
