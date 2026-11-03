# -*- coding: utf-8 -*-
"""逐个目标增量写日志（带 flush + 单目标 watchdog），定位哪个目标会卡住。"""
import os
import sys
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
LOG = os.path.join(ROOT, "probe_doc_lock.log")
TARGET = sys.argv[1] if len(sys.argv) > 1 else ""


def w(msg):
    with open(LOG, "a", encoding="utf-8") as fh:
        fh.write(msg + "\n")


def main():
    from app.core import locks
    open(LOG, "w", encoding="utf-8").close()
    if TARGET == "list":
        cands = [os.path.join(os.environ["SystemRoot"], "System32", "config"),
                 os.path.join(os.environ["SystemRoot"], "System32", "drivers"),
                 os.path.join(os.environ["SystemRoot"], "System32", "winevt", "Logs"),
                 os.path.join(os.environ["SystemRoot"], "explorer.exe"),
                 os.path.join(os.environ["SystemRoot"], "System32", "ntoskrnl.exe")]
    else:
        cands = [TARGET]
    for t in cands:
        w("START %s" % t)
        t0 = time.time()
        guard = threading.Timer(20.0, lambda: w("TIMEOUT %s" % t))
        guard.daemon = True
        guard.start()
        try:
            rep = locks.query(t)
        finally:
            guard.cancel()
        w("DONE  %s total=%d locked=%d kernel=%d %.0fms  %s"
          % (t, rep.total, rep.locked_count, rep.kernel_count,
             rep.elapsed_ms, rep.headline))
        for f in rep.files[:5]:
            w("    %-22s %-8s %s" % (f.name, f.verdict, (f.detail or "")[:80]))
            for p in f.processes[:3]:
                w("        pid=%-6d %-30s %s" % (p.pid, p.label[:30], p.type_text))
        w("  cost %.1fs" % (time.time() - t0))
    w("ALL DONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
