# -*- coding: utf-8 -*-
"""临时：设备状态分布 + 定位失败样例。"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.core import devices as dv

items = dv.devices()
lines = []
p = lines.append
cnt = {}
for d in items:
    cnt[d.status] = cnt.get(d.status, 0) + 1
p("status dist: %r" % cnt)
p("level dist: %r" % {k: sum(1 for d in items if d.level == k)
                      for k in ("ok", "info", "warn", "crit")})

unknown = [d for d in items if d.status == "状态未知"]
p("unknown=%d" % len(unknown))
for d in unknown[:12]:
    p("  %-40s | %s" % (d.instance[:40], d.name[:30]))

# 再探一次：把 STORAGE\Volume 的实例换成把 _??_ 还原成 \
for d in unknown[:6]:
    alt = d.instance.replace("_??_", "\\")
    p("  retry %s -> %r" % (alt[:52], dv.node_status(alt)))
    # 换 PHANTOM 也不行的话，试 AC 前缀等
open("probe_dev_status.log", "w", encoding="utf-8").write("\n".join(lines))
print("\n".join(lines))
