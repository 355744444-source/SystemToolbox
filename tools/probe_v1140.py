"""v1.14.0 三个新模块的实测：sign / locks / pathenv。"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "probe_v1140.log")
log = []


def p(s=""):
    log.append(str(s))


from app.core import locks, pathenv, sign

p("=" * 70)
p("A. sign.verify")
p("=" * 70)
for f in [r"C:\Windows\System32\notepad.exe",
          r"C:\Windows\System32\drivers\tcpip.sys",
          r"C:\Windows\System32\drivers\cdfs.sys",
          r"C:\Windows\System32\drivers\PROCEXP152.SYS",
          r"C:\Users\MSI\WorkBuddy\2026-10-21-09-08-23\CrashLogSuite\dist\SystemToolbox.exe",
          r"C:\nope\nothing.exe"]:
    info = sign.verify(f)
    p("  %-9s %-6s %s  %6.1f ms  cat=%s"
      % (info.level_text, info.method_text, os.path.basename(f),
         info.elapsed_ms, info.catalog[:40] or "-"))
    p("      note=%s" % info.note)

p()
p("  批量 40 个 System32\\drivers *.sys：")
sysdir = os.path.join(os.environ["WINDIR"], "System32", "drivers")
files = [os.path.join(sysdir, n) for n in sorted(os.listdir(sysdir))
         if n.lower().endswith(".sys")][:40]
res = sign.verify_many(files)
import time
t0 = time.perf_counter()
res = sign.verify_many(files)
dt = time.perf_counter() - t0
bad = [r for r in res if r.suspicious]
p("    %d 个 用时 %.2f s（%.1f ms/个），可疑 %d 个：%s"
  % (len(res), dt, dt * 1000 / len(res), len(bad),
     [os.path.basename(r.path) for r in bad][:6]))

p()
p("=" * 70)
p("B. locks.query")
p("=" * 70)
tmpdir = tempfile.mkdtemp(prefix="cls_lock_")
f1 = os.path.join(tmpdir, "held.bin")
open(f1, "wb").write(b"x" * 4096)
import ctypes
import ctypes.wintypes as wt
k32 = ctypes.WinDLL("kernel32")
k32.CreateFileW.restype = wt.HANDLE
h = k32.CreateFileW(f1, 0x80000000, 1, None, 3, 0, None)   # deny write
open(os.path.join(tmpdir, "free.bin"), "wb").write(b"y" * 128)
p("置一个占用句柄，本进程 pid=%d" % os.getpid())

for target, label in [(f1, "单文件（被占）"),
                      (os.path.join(tmpdir, "free.bin"), "单文件（空闲）"),
                      (tmpdir, "整个目录"),
                      (r"D:\nope.bin", "不存在"),
                      (r"C:\pagefile.sys", "系统独占")]:
    rep = locks.query(target)
    p("  %-14s files=%-2d locked=%-2d %.0f ms  → %s"
      % (label, rep.total, rep.locked_count, rep.elapsed_ms, rep.headline))
    for f in rep.files[:4]:
        p("      %-14s %-6s rc=%-3d %s" % (f.name, f.verdict, f.rc,
                                           f.error if f.rc else ""))
        for pr in f.processes[:3]:
            p("         pid=%-6d %-18s %-8s %-8s %s"
              % (pr.pid, pr.label, pr.type_text, pr.status_text, pr.exe[-46:]))
    for n in rep.notes:
        p("      note: %s" % n)

k32.CloseHandle(h)

p()
p("=" * 70)
p("C. pathenv.audit")
p("=" * 70)
rep = pathenv.audit()
p("  系统 %d 项（值类型 %d）/ 用户 %d 项（%d）  %.0f ms"
  % (rep.machine_count, rep.machine_kind, rep.user_count, rep.user_kind, rep.elapsed * 1000))
p("  headline: %s" % rep.headline)
p("  level=%s" % rep.level)
for e in rep.entries:
    if e.kind != "正常":
        p("    [%s] %-10s %s" % (e.scope_text, e.kind, e.show))
p("  findings:")
for f in rep.findings:
    p("    %-5s ×%-2d %s" % (f.severity, f.count, f.title))
    for ev in f.evidence[:3]:
        p("           · %s" % ev)
p("  verdict: %s" % (pathenv.verdict(rep.findings),))

with open(OUT, "w", encoding="utf-8") as fh:
    fh.write("\n".join(log) + "\n")
print("ok", len(log))
