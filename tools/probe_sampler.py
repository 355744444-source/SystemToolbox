# -*- coding: utf-8 -*-
"""体检页首进阻塞点测量。"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "probe_sampler.log")
lines = []


def log(s):
    lines.append(s)
    print(s)


from app.core import perf, procs

a = time.perf_counter()
s = perf.Sampler()
b = time.perf_counter()
log("perf.Sampler()      %6.0f ms" % ((b - a) * 1000))
a = time.perf_counter()
s.prime()
b = time.perf_counter()
log("perf.prime()        %6.0f ms" % ((b - a) * 1000))
s.close()

a = time.perf_counter()
ps = procs.ProcSampler()
b = time.perf_counter()
log("ProcSampler()       %6.0f ms" % ((b - a) * 1000))
a = time.perf_counter()
r = ps.sample()
b = time.perf_counter()
log("ProcSampler.sample  %6.0f ms  rows=%d denied=%d" % ((b - a) * 1000, r.total, r.denied))

a = time.perf_counter()
r2 = ps.sample()
b = time.perf_counter()
log("ProcSampler.sample2 %6.0f ms" % ((b - a) * 1000))

with open(OUT, "w", encoding="utf-8") as f:
    f.write("\n".join(lines))
