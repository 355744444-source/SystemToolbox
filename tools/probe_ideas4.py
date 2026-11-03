"""看 Kernel-Boot 通道能否算出开机时长；并看 component.patches 的用量。"""
import os
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "probe_ideas4.log")
log = []


def p(s=""):
    log.append(str(s))


from app.core import evtx as ev

CH = "Microsoft-Windows-Kernel-Boot/Operational"
evs, st = ev.query_events(CH, False, "*", limit=3000, message_mode="none")
p("Kernel-Boot 读取 %d 条 error=%r" % (len(evs), st.error))
c = Counter(e.event_id for e in evs)
p("ID 分布: %s" % dict(sorted(c.items())))

by_id = defaultdict(list)
for e in evs:
    by_id[e.event_id].append(e)

for eid in sorted(by_id)[:24]:
    e = by_id[eid][0]
    keys = list(e.data.items())[:6]
    p("  id=%-4d n=%-4d 样本 %s  %s" % (eid, len(by_id[eid]), e.time_text, keys))

p()
p("== 每次开机的 18/20/27/32 配对（最近 6 次）==")
# 按时间聚簇：相邻事件间隔 > 10 分钟算新一次开机
boots = [e for e in evs if e.event_id in (18, 20, 27, 30, 32, 12, 13, 14)]
boots.sort(key=lambda e: e.time or 0)
clusters, cur, last = [], [], None
for e in boots:
    if last is not None and e.time and (e.time - last).total_seconds() > 600:
        clusters.append(cur)
        cur = []
    cur.append(e)
    last = e.time or last
if cur:
    clusters.append(cur)
p("聚簇数=%d" % len(clusters))
for cl in clusters[-6:]:
    t0, t1 = cl[0].time, cl[-1].time
    span = (t1 - t0).total_seconds() if t0 and t1 else 0
    ids = ["%d@%s" % (e.event_id, e.time.strftime("%H:%M:%S.%f")[:-3] if e.time else "?")
           for e in cl]
    p("  %s 跨度 %.1fs  %s" % (t0.strftime("%Y-%m-%d %H:%M:%S") if t0 else "?",
                              span, ids[:8]))

p()
p("== 单次开机内各 ID 的时间差 ==")
for cl in clusters[-4:]:
    d = {}
    for e in cl:
        d.setdefault(e.event_id, e.time)
    if 18 in d and 20 in d:
        p("  BootStart(18)→BootEnd(20) = %.2f s   (%s)"
          % ((d[20] - d[18]).total_seconds(), d[18].strftime("%m-%d %H:%M:%S")))
    for k in (12, 13, 14, 27, 30, 32):
        if k in d:
            p("    id=%-3d %s  data=%s" % (k, d[k].strftime("%H:%M:%S.%f")[:-3],
                                           dict(list([e for e in cl if e.event_id == k][0]
                                                     .data.items())[:5])))

p()
p("== 备选：从 LastBootUpTime 与 6013 反推开机到现在的时长 ==")
try:
    import ctypes
    ntdll = ctypes.WinDLL("ntdll")
    # 用 GetTickCount64 反推
    k32 = ctypes.WinDLL("kernel32")
    k32.GetTickCount64.restype = ctypes.c_ulonglong
    up = k32.GetTickCount64() / 1000.0
    p("  GetTickCount64 = %.0f s（%.2f 小时）" % (up, up / 3600))
except Exception as exc:  # noqa: BLE001
    p("  EXC %s" % exc)

p()
p("== component.patches 是否已在 UI 用 ==")
try:
    from app.core import component as comp
    ps = comp.patches(limit=8)
    p("  patches -> %d 条" % len(ps))
    for x in ps[:8]:
        p("    %s" % (x,))
except Exception as exc:  # noqa: BLE001
    p("  EXC %s" % exc)

with open(OUT, "w", encoding="utf-8") as fh:
    fh.write("\n".join(log) + "\n")
print("ok", len(log))
