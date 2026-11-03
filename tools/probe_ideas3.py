"""修正 RmRegisterResources 参数位置 + 查开机事件通道为何为空 + pnputil 解析。"""
import ctypes
import ctypes.wintypes as wt
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "probe_ideas3.log")
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


rm.RmStartSession.argtypes = [ctypes.POINTER(wt.DWORD), ctypes.POINTER(wt.DWORD),
                              ctypes.c_wchar_p]
rm.RmStartSession.restype = wt.ULONG
rm.RmRegisterResources.argtypes = [wt.DWORD, wt.UINT, ctypes.POINTER(ctypes.c_wchar_p),
                                   wt.UINT, ctypes.POINTER(RM_UNIQUE_PROCESS),
                                   wt.UINT, ctypes.POINTER(ctypes.c_wchar_p)]
rm.RmRegisterResources.restype = wt.ULONG
rm.RmGetList.argtypes = [wt.DWORD, ctypes.POINTER(wt.UINT), ctypes.POINTER(wt.UINT),
                         ctypes.POINTER(RM_PROCESS_INFO), ctypes.POINTER(wt.ULONG)]
rm.RmGetList.restype = wt.ULONG
rm.RmEndSession.argtypes = [wt.DWORD]
rm.RmEndSession.restype = wt.ULONG

APP_TYPE = {0: "未知", 1: "窗口程序", 2: "其他", 3: "服务", 4: "资源管理器"}
STATUS = {0: "正常", 1: "挂起", 2: "出错", 3: "验证中", 4: "已停止", 5: "服务停止"}


def who_locks(paths):
    """返回 (rc, 耗时ms, [(pid, app, svc, type, status, restartable)])"""
    import time
    t0 = time.perf_counter()
    h = wt.DWORD(0)
    key = ctypes.create_unicode_buffer(33)
    if rm.RmStartSession(ctypes.byref(h), None, key) != 0:
        return -1, 0.0, []
    try:
        arr = (ctypes.c_wchar_p * len(paths))(*paths)
        rc = rm.RmRegisterResources(h.value, len(paths), arr, 0, None, 0, None)
        if rc != 0:
            return rc, (time.perf_counter() - t0) * 1000, []
        need, cnt, reason = wt.UINT(0), wt.UINT(0), wt.ULONG(0)
        rc = rm.RmGetList(h.value, ctypes.byref(need), ctypes.byref(cnt), None,
                          ctypes.byref(reason))
        if rc not in (0, 234):
            return rc, (time.perf_counter() - t0) * 1000, []
        if not need.value:
            return 0, (time.perf_counter() - t0) * 1000, []
        buf = (RM_PROCESS_INFO * need.value)()
        cnt = wt.UINT(need.value)
        rc = rm.RmGetList(h.value, ctypes.byref(need), ctypes.byref(cnt), buf,
                          ctypes.byref(reason))
        rows = [(buf[i].Process.dwProcessId, buf[i].strAppName,
                 buf[i].strServiceShortName, buf[i].ApplicationType,
                 buf[i].AppStatus, bool(buf[i].bRestartable))
                for i in range(min(cnt.value, need.value))]
        return 0, (time.perf_counter() - t0) * 1000, rows
    finally:
        rm.RmEndSession(h.value)


p("=" * 68)
p("A. Restart Manager（参数位置修正后）")
p("=" * 68)
tmp = os.path.join(os.environ["TEMP"], "cls_rm_probe3.bin")
with open(tmp, "wb") as fh:
    fh.write(b"x" * 4096)
GENERIC_READ = 0x80000000
h1 = k32.CreateFileW(tmp, GENERIC_READ, 1, None, 3, 0, None)   # deny write
p("自测文件本方 pid=%d handle=%r" % (os.getpid(), h1))
cases = [
    ("自己占着（独占写）", [tmp]),
    ("不存在 D:\\nope.bin", [r"D:\definitely_nope.bin"]),
    ("pagefile.sys", [r"C:\pagefile.sys"]),
    ("hosts", [r"C:\Windows\System32\drivers\etc\hosts"]),
    ("目录 System32\\drivers", [r"C:\Windows\System32\drivers"]),
    ("多个文件混合", [tmp, r"C:\Windows\System32\drivers\etc\hosts",
                      r"D:\definitely_nope.bin"]),
]
for label, paths in cases:
    rc, ms, rows = who_locks(paths)
    p("  %-22s rc=%-3d %6.1f ms  进程数=%d" % (label, rc, ms, len(rows)))
    for r in rows[:6]:
        p("      pid=%-6d type=%-5s status=%-5s restart=%-5s app=%r svc=%r"
          % (r[0], APP_TYPE.get(r[3], r[3]), STATUS.get(r[4], r[4]), r[5],
             r[1][:36], r[2][:24]))
k32.CloseHandle(h1)
try:
    os.remove(tmp)
except OSError:
    pass

# 真实场景：exe 正在被运行时会怎样（用 python 自己模拟不可行，改测 pagefile）
p("  注：pagefile.sys 的占用者应为 System(4) / 内核，用它验证「系统进程」显示")

p()
p("=" * 68)
p("B. 开机事件通道：文件里到底有没有数据")
p("=" * 68)
logs = r"C:\Windows\System32\winevt\Logs"
for fn in ("Microsoft-Windows-Diagnostics-Performance%4Operational.evtx",
           "Microsoft-Windows-Kernel-Boot%4Operational.evtx",
           "System.evtx"):
    fp = os.path.join(logs, fn)
    if os.path.isfile(fp):
        stt = os.stat(fp)
        p("  %-56s %9d 字节  修改 %s"
          % (fn, stt.st_size,
             __import__("datetime").datetime.fromtimestamp(stt.st_mtime)
             .strftime("%Y-%m-%d %H:%M")))
    else:
        p("  %-56s 不存在" % fn)

from app.core import evtx as ev
for ch in ("Microsoft-Windows-Diagnostics-Performance/Operational",
           "Microsoft-Windows-Kernel-Boot/Operational"):
    try:
        evs, st = ev.query_events(ch, False, "*", limit=3, message_mode="none")
        p("  通道 %s → 条数=%d error=%r" % (ch.split("/")[0], len(evs), st.error))
    except Exception as exc:  # noqa: BLE001
        p("  通道 %s EXC %s" % (ch, exc))

# 直接读文件（通道可能被过滤）
fp = os.path.join(logs, "Microsoft-Windows-Diagnostics-Performance%4Operational.evtx")
if os.path.isfile(fp):
    try:
        evs, st = ev.query_events(fp, True, "*", limit=2000, message_mode="none")
        p("  直接读文件 → 条数=%d error=%r" % (len(evs), st.error))
        from collections import Counter
        p("  ID 分布: %s" % dict(sorted(Counter(e.event_id for e in evs).items())))
        boots = [e for e in evs if e.event_id == 100]
        for e in boots[:5]:
            d = e.data
            p("     %s boot=%s main=%s post=%s degr=%s"
              % (e.time_text, d.get("BootTime"), d.get("MainPathBootTime"),
                 d.get("BootPostBootTime"), d.get("BootDegradationTime")))
        slow = [e for e in evs if e.event_id in (101, 102, 103, 106, 109, 110)]
        p("  拖慢项 %d 条，样本:" % len(slow))
        for e in slow[:5]:
            p("     id=%-4d %s %s" % (e.event_id, e.time_text,
                                      dict(list(e.data.items())[:4])))
    except Exception as exc:  # noqa: BLE001
        p("  读文件 EXC %s" % exc)

p()
p("=" * 68)
p("C. 关机耗时 / 启动类型（备选口径）")
p("=" * 68)
try:
    evs, st = ev.query_events("System", False,
                              "*[System[(EventID=6005 or EventID=6013 or EventID=12 or EventID=13)]]",
                              limit=200, message_mode="none")
    p("  相关事件 %d 条" % len(evs))
    for e in evs[:6]:
        p("     id=%-5d %s %s" % (e.event_id, e.time_text,
                                  dict(list(e.data.items())[:5])))
except Exception as exc:  # noqa: BLE001
    p("  EXC %s" % exc)

p()
p("=" * 68)
p("D. pnputil 解析（按空行切块）")
p("=" * 68)
r = subprocess.run(["pnputil.exe", "/enum-drivers"], capture_output=True, text=True,
                   encoding="oem", errors="replace", creationflags=0x08000000)
txt = (r.stdout or "")
blocks = [b for b in re.split(r"\n\s*\n", txt) if "发布名称" in b]
p("  块数=%d" % len(blocks))
prov, dates, klass = {}, [], {}
for b in blocks:
    m_pv = re.search(r"提供程序名称:\s*(.+)", b)
    m_v = re.search(r"驱动程序版本:\s*(\d{2}/\d{2}/\d{4})", b)
    m_c = re.search(r"类名:\s*(.+)", b)
    if m_pv:
        prov[m_pv.group(1).strip()] = prov.get(m_pv.group(1).strip(), 0) + 1
    if m_v:
        dates.append(m_v.group(1))
    if m_c:
        klass[m_c.group(1).strip()] = klass.get(m_c.group(1).strip(), 0) + 1
p("  提供程序 TOP: %s" % sorted(prov.items(), key=lambda kv: -kv[1])[:6])
p("  类名 TOP: %s" % sorted(klass.items(), key=lambda kv: -kv[1])[:6])
p("  日期范围: %s ~ %s" % (min(dates) if dates else "-", max(dates) if dates else "-"))
old = [d for d in dates if d.endswith(("/2019", "/2020", "/2021", "/2022", "/2023"))]
p("  2023 及更早的驱动: %d 个" % len(old))

with open(OUT, "w", encoding="utf-8") as fh:
    fh.write("\n".join(log) + "\n")
print("ok", len(log))
