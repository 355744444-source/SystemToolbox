# -*- coding: utf-8 -*-
"""测 WinVerifyTrust 能否并行：200 个 .sys，1 / 2 / 4 / 8 线程对比。"""
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
LOG = os.path.join(ROOT, "probe_par.log")
DRV = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "drivers")


def main():
    from app.core import sign

    files = [os.path.join(DRV, n) for n in sorted(os.listdir(DRV))
             if n.lower().endswith(".sys")][:200]
    out = ["文件 %d 个，CPU %d 核" % (len(files), os.cpu_count() or 1)]
    for workers in (1, 2, 4, 8, 12):
        t0 = time.perf_counter()
        if workers == 1:
            n_ok = sum(1 for p in files if sign.verify(p).rc == 0)
        else:
            with ThreadPoolExecutor(max_workers=workers) as pool:
                infos = list(pool.map(sign.verify, files))
            n_ok = sum(1 for i in infos if i.rc == 0)
        cost = time.perf_counter() - t0
        out.append("  %2d 线程: %6.2fs（%.1fms/个）通过 %d"
                   % (workers, cost, cost * 1000 / len(files), n_ok))
    with open(LOG, "w", encoding="utf-8") as fh:
        fh.write("\n".join(out))


if __name__ == "__main__":
    main()
    os._exit(0)
