"""追查 Restart Manager 结构体与 Diagnostics-Performance 通道状态。"""
import ctypes
import ctypes.wintypes as wt
import os
import sys
import subprocess

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "probe_ideas2.log")
log = []


def p(s=""):
    log.append(str(s))


k32 = ctypes.WinDLL("kernel32", use_last_error=True)
rm = ctypes.WinDLL("rstrtmgr", use_last_error=True)


class FILETIME(ctypes.Structure):
    _fields_ = [("dwLowDateTime", wt.DWORD), ("dwHighDateTime", wt.DWORD)]


class RM_UNIQUE_PROCESS(ctypes.Structure):
    _fields_ = [("dwProcessId", wt.DWORD), ("ProcessStartTime", FILETIME)]


class RM_PROCESS_INFO(ctypes.Structure):
    _fields_ = [
        ("Process", RM_UNIQUE_PROCESS),
        ("strAppName", ctypes.c_wchar * 256),
        ("strServiceShortName", ctypes.c_wchar * 64),
        ("ApplicationType", ctypes.c_uint),
        ("AppStatus", wt.ULONG),
        ("TSSessionId", wt.DWORD),
        ("bRestartable", wt.BOOL),
    ]


p("sizeof: FILETIME=%d RM_UNIQUE_PROCESS=%d RM_PROCESS_INFO=%d （期望 8 / 12 / 668）"
  % (ctypes.sizeof(FILETIME), ctypes.sizeof(RM_UNIQUE_PROCESS),
     ctypes.sizeof(RM_PROCESS_INFO)))
p("offsetof ProcessStartTime=%d strAppName=%d ApplicationType=%d bRestartable=%d"
  % (RM_PROCESS_INFO.Process.offset + RM_UNIQUE_PROCESS.ProcessStartTime.offset,
     RM_PROCESS_INFO.strAppName.offset, RM_PROCESS_INFO.ApplicationType.offset,
     RM_PROCESS_INFO.bRestartable.offset))

rm.RmStartSession.argtypes = [ctypes.POINTER(wt.DWORD), ctypes.POINTER(wt.DWORD),
                              ctypes.c_wchar_p]
rm.RmStartSession.restype = wt.ULONG
rm.RmRegisterResources.argtypes = [wt.DWORD, wt.UINT, ctypes.POINTER(ctypes.c_wchar_p),
                                   wt.UINT, ctypes.POINTER(ctypes.c_wchar_p),
                                   wt.UINT, ctypes.POINTER(ctypes.c_wchar_p)]
rm.RmRegisterResources.restype = wt.ULONG
rm.RmGetList.argtypes = [wt.DWORD, ctypes.POINTER(wt.UINT), ctypes.POINTER(wt.UINT),
                         ctypes.POINTER(RM_PROCESS_INFO), ctypes.POINTER(wt.ULONG)]
rm.RmGetList.restype = wt.ULONG
rm.RmEndSession.argtypes = [wt.DWORD]
rm.RmEndSession.restype = wt.ULONG

tmp = os.path.join(os.environ["TEMP"], "cls_rm_probe2.bin")
with open(tmp, "wb") as fh:
    fh.write(b"x" * 4096)
h = k32.CreateFileW(tmp, 0x80000000, 1, None, 3, 0, None)
p("handle=%r 我方 pid=%d 文件=%s" % (h, os.getpid(), tmp))

for label, paths in [("自己占着", [tmp]),
                     ("不存在", [r"D:\definitely_nope.bin"]),
                     ("系统文件", [r"C:\Windows\System32\config\SYSTEM"])]:
    handle = wt.DWORD(0)
    key = ctypes.create_unicode_buffer(33)
    rc0 = rm.RmStartSession(ctypes.byref(handle), None, key)
    p("---- %s  RmStartSession rc=%d session=%d key=%r" % (label, rc0, handle.value, key.value[:8]))
    if rc0 != 0:
        continue
    try:
        arr = (ctypes.c_wchar_p * len(paths))(*(ctypes.c_wchar_p(x) for x in paths))
        rc = rm.RmRegisterResources(handle.value, 0, None, len(paths), arr, 0, None)
        p("     register rc=%d err=%d" % (rc, ctypes.get_last_error()))
        need = wt.UINT(0)
        cnt = wt.UINT(0)
        reason = wt.ULONG(0)
        rc = rm.RmGetList(handle.value, ctypes.byref(need), ctypes.byref(cnt), None,
                          ctypes.byref(reason))
        p("     getlist#1 rc=%d need=%d cnt=%d reason=%d" % (rc, need.value, cnt.value,
                                                             reason.value))
        if need.value:
            buf = (RM_PROCESS_INFO * need.value)()
            cnt = wt.UINT(need.value)
            rc = rm.RmGetList(handle.value, ctypes.byref(need), ctypes.byref(cnt), buf,
                              ctypes.byref(reason))
            p("     getlist#2 rc=%d need=%d cnt=%d reason=%d" % (rc, need.value,
                                                                 cnt.value, reason.value))
            for i in range(min(cnt.value, need.value)):
                b = buf[i]
                p("       [%d] pid=%d app=%r svc=%r type=%d status=0x%X restart=%d"
                  % (i, b.Process.dwProcessId, b.strAppName[:40],
                     b.strServiceShortName[:20], b.ApplicationType, b.AppStatus,
                     b.bRestartable))
    finally:
        rm.RmEndSession(handle.value)

k32.CloseHandle(h)
try:
    os.remove(tmp)
except OSError:
    pass

p()
p("=" * 68)
p("B. Diagnostics-Performance 通道状态")
p("=" * 68)
for r in subprocess.run(["wevtutil.exe", "gl",
                         "Microsoft-Windows-Diagnostics-Performance/Operational"],
                        capture_output=True, text=True, encoding="oem",
                        errors="replace", creationflags=0x08000000).stdout.splitlines():
    p("  " + r)

try:
    from app.core import evtx as ev
    for xp, label in [("*", "全量前 5"),
                      ("*[System[(EventID=100)]]", "ID=100")]:
        try:
            evs, st = ev.query_events(
                "Microsoft-Windows-Diagnostics-Performance/Operational", False,
                xp, limit=5, message_mode="none")
            p("  %-10s 条数=%d error=%r" % (label, len(evs), st.error))
            for e in evs[:3]:
                p("      id=%d %s data=%s" % (e.event_id, e.time_text,
                                              dict(list(e.data.items())[:6])))
        except Exception as exc:  # noqa: BLE001
            p("  %-10s EXC %s" % (label, exc))
    # 备选：Kernel-Boot / 传统开机记录
    for ch in ("Microsoft-Windows-Kernel-Boot/Operational", "System"):
        try:
            evs, st = ev.query_events(ch, False,
                                      "*[System[(EventID=12 or EventID=13 or EventID=6005)]]",
                                      limit=5, message_mode="none")
            p("  %-42s 条数=%d error=%r" % (ch, len(evs), st.error))
        except Exception as exc:  # noqa: BLE001
            p("  %-42s EXC %s" % (ch, exc))
except Exception as exc:  # noqa: BLE001
    p("EXC %s" % exc)

p()
p("=" * 68)
p("D. pnputil 解析可行性")
p("=" * 68)
r = subprocess.run(["pnputil.exe", "/enum-drivers"], capture_output=True, text=True,
                   encoding="oem", errors="replace", creationflags=0x08000000)
blocks = [b for b in (r.stdout or "").split("\r\n\r\n") if "oem" in b]
p("  块数=%d  rc=%d" % (len(blocks), r.returncode))
import re
prov = {}
for b in blocks:
    m = re.search(r"原始名称:\s*(\S+)", b)
    v = re.search(r"驱动程序版本:\s*(\d{2}/\d{2}/\d{4})\s+(\S+)", b)
    pv = re.search(r"提供程序名称:\s*(.+)", b)
    if pv:
        prov[pv.group(1).strip()] = prov.get(pv.group(1).strip(), 0) + 1
p("  提供程序 TOP: %s" % sorted(prov.items(), key=lambda kv: -kv[1])[:6])
for b in blocks[:2]:
    p("  样本块: %s" % " | ".join(x.strip() for x in b.splitlines() if x.strip())[:220])

with open(OUT, "w", encoding="utf-8") as fh:
    fh.write("\n".join(log) + "\n")
print("ok", len(log))
