"""单独确认 pagefile.sys / 目录 / 独占文件的 RmRegisterResources 返回码是否稳定。"""
import ctypes
import ctypes.wintypes as wt
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.core import locks

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "probe_v1140b.log")
log = []


def p(s=""):
    log.append(str(s))


for target in (r"C:\pagefile.sys", r"C:\hiberfil.sys", r"C:\Windows\System32\drivers",
               r"C:\Windows\System32\config\SYSTEM", r"C:\Windows\explorer.exe"):
    p("target=%s exists=%s" % (target, os.path.exists(target)))
    for i in range(3):
        rc, procs = locks._query_batch([target])
        p("   #%d rc=%-3d procs=%d  %s" % (i, rc, len(procs),
                                           [x.label for x in procs]))
    rep = locks.query(target)
    p("   query(): files=%d locked=%d notes=%s" % (rep.total, rep.locked_count, rep.notes))
    for f in rep.files[:3]:
        p("      %s rc=%d %s %s" % (f.name, f.rc, f.verdict, f.error))
    p()

# 会话句柄的取值范围
p("RmStartSession 句柄样本：")
for i in range(6):
    h, rc = locks._new_session()
    p("   rc=%d handle=%r" % (rc, h))

with open(OUT, "w", encoding="utf-8") as fh:
    fh.write("\n".join(log) + "\n")
print("ok", len(log))
