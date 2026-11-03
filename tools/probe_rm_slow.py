# -*- coding: utf-8 -*-
"""找出让 RmRegisterResources 卡住的文件：逐个单文件查询 + 3 秒看门狗。"""
import os
import sys
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
LOG = os.path.join(ROOT, "probe_rm_slow.log")
DIR = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
    os.environ["SystemRoot"], "System32", "winevt", "Logs")
CASE = 3.0


def w(msg):
    with open(LOG, "a", encoding="utf-8") as fh:
        fh.write(msg + "\n")


def main():
    from app.core import locks
    open(LOG, "w", encoding="utf-8").close()
    names = [os.path.join(DIR, n) for n in sorted(os.listdir(DIR))]
    names = [p for p in names if os.path.isfile(p)]
    w("dir=%s files=%d" % (DIR, len(names)))
    for i, p in enumerate(names):
        box = {}
        th = threading.Thread(target=lambda: box.update(z=locks._query_batch([p])),
                              daemon=True)
        t0 = time.perf_counter()
        th.start()
        th.join(CASE)
        cost = (time.perf_counter() - t0) * 1000
        if th.is_alive():
            w("HANG  %6.0fms %s" % (cost, p))
            os._exit(0)
        rc, procs = box.get("z", (-1, []))
        if cost > 200 or procs:
            w("slow  %6.0fms rc=%d procs=%s  %s"
              % (cost, rc, [(x.pid, x.label) for x in procs][:3],
                 os.path.basename(p)))
    w("ALL DONE: no file exceeded %.0fs" % CASE)
    os._exit(0)


if __name__ == "__main__":
    main()
