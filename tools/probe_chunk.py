# -*- coding: utf-8 -*-
"""量化 Restart Manager 的会话成本：批大小 vs 耗时，用来定分块。"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
LOG = os.path.join(ROOT, "probe_chunk.log")


def main():
    from app.core import locks
    d = os.path.join(os.environ["SystemRoot"], "System32", "winevt", "Logs")
    names = [os.path.join(d, n) for n in sorted(os.listdir(d))]
    names = [p for p in names if os.path.isfile(p)]
    out = ["winevt\\Logs 共 %d 个文件" % len(names)]
    for size in (446, 128, 64, 32, 16, 8, 1):
        batch = names[:size]
        t0 = __import__("time").perf_counter()
        rc, procs = locks._query_batch(batch)
        cost = (__import__("time").perf_counter() - t0) * 1000
        out.append("  单会话 %3d 个文件：%7.0fms  rc=%d 进程=%d"
                   % (size, cost, rc, len(procs)))
    # 分块 + 二分：整块跑完要多久
    for size in (16, 32, 64):
        t0 = __import__("time").perf_counter()
        rep = locks.query_files(names[:size], budget_s=600)
        cost = (__import__("time").perf_counter() - t0) * 1000
        out.append("  query_files(%2d 个，预算 600s)：%7.0fms locked=%d timeout=%d"
                   % (size, cost, rep.locked_count, rep.timeout_count))
    with open(LOG, "w", encoding="utf-8") as fh:
        fh.write("\n".join(out))


if __name__ == "__main__":
    main()
    os._exit(0)
