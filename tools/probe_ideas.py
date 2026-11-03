"""候选功能可行性探针：文件占用 / 开机耗时 / PATH 体检 / 驱动回溯。

只读，不改任何设置。结果写 tools/probe_ideas.log。
"""
import ctypes
import ctypes.wintypes as wt
import os
import sys
import time
import subprocess

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "probe_ideas.log")
log = []


def p(s=""):
    log.append(str(s))


# ---------------------------------------------------------------- A. Restart Manager
k32 = ctypes.WinDLL("kernel32", use_last_error=True)
rm = ctypes.WinDLL("rstrtmgr", use_last_error=True)

CCH_RM_SESSION_KEY = 32
CCH_RM_MAX_APP_NAME = 255
CCH_RM_MAX_SVC_NAME = 63
RmRebootReasonNone = 0


class FILETIME(ctypes.Structure):
    _fields_ = [("dwLowDateTime", wt.DWORD), ("dwHighDateTime", wt.DWORD)]


class RM_UNIQUE_PROCESS(ctypes.Structure):
    _fields_ = [("dwProcessId", wt.DWORD), ("ProcessStartTime", FILETIME)]


class RM_PROCESS_INFO(ctypes.Structure):
    _fields_ = [
        ("Process", RM_UNIQUE_PROCESS),
        ("strAppName", ctypes.c_wchar * (CCH_RM_MAX_APP_NAME + 1)),
        ("strServiceShortName", ctypes.c_wchar * (CCH_RM_MAX_SVC_NAME + 1)),
        ("ApplicationType", ctypes.c_uint),
        ("AppStatus", wt.ULONG),
        ("TSSessionId", wt.DWORD),
        ("bRestartable", wt.BOOL),
    ]


rm.RmStartSession.argtypes = [ctypes.POINTER(wt.DWORD),
                               ctypes.POINTER(wt.DWORD),
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

APP_TYPE = {0: "未知", 1: "窗口程序", 2: "其他", 3: "服务", 4: "资源管理器"}


def who_locks(paths, label=""):
    t0 = time.perf_counter()
    handle = wt.DWORD(0)
    key = ctypes.create_unicode_buffer(CCH_RM_SESSION_KEY + 1)
    rc = rm.RmStartSession(ctypes.byref(handle), None, key)
    if rc != 0:
        return label, "RmStartSession rc=%d" % rc, 0.0, []
    try:
        arr = (ctypes.c_wchar_p * len(paths))(*paths)
        rc = rm.RmRegisterResources(handle, 0, None, 1,
                                     ctypes.cast(arr, ctypes.POINTER(ctypes.c_wchar_p)),
                                     0, None)
        # 逐个登记更稳
        rc = rm.RmRegisterResources(handle, 0, None, len(paths), arr, 0, None)
        if rc != 0:
            return label, "RmRegisterResources rc=%d" % rc, time.perf_counter() - t0, []
        need = wt.UINT(0)
        count = wt.UINT(0)
        reason = wt.ULONG(0)
        rc = rm.RmGetList(handle, ctypes.byref(need), ctypes.byref(count), None,
                           ctypes.byref(reason))
        if rc == 234:  # ERROR_MORE_DATA
            buf = (RM_PROCESS_INFO * max(need.value, 1))()
            count = wt.UINT(need.value)
            rc = rm.RmGetList(handle, ctypes.byref(need), ctypes.byref(count), buf,
                               ctypes.byref(reason))
            if rc != 0:
                return label, "RmGetList retry rc=%d" % rc, time.perf_counter() - t0, []
            rows = [(buf[i].Process.dwProcessId,
                     buf[i].strAppName,
                     buf[i].strServiceShortName,
                     APP_TYPE.get(buf[i].ApplicationType, "?"),
                     bool(buf[i].bRestartable)) for i in range(min(count.value, need.value))]
            return label, "ok need=%d" % need.value, time.perf_counter() - t0, rows
        if rc == 0:
            return label, "nobody", time.perf_counter() - t0, []
        return label, "RmGetList rc=%d" % rc, time.perf_counter() - t0, []
    finally:
        rm.RmEndSession(handle)


p("=" * 68)
p("A. Restart Manager（谁占着这个文件）")
p("=" * 68)

# 自造一个「被自己占着」的文件：deny write 表示别人改不了
tmp = os.path.join(os.environ["TEMP"], "cls_rm_probe.bin")
with open(tmp, "wb") as fh:
    fh.write(b"x" * 4096)
GENERIC_READ = 0x80000000
OPEN_EXISTING = 3
FILE_SHARE_READ = 1
h = k32.CreateFileW(tmp, GENERIC_READ, FILE_SHARE_READ, None, OPEN_EXISTING, 0, None)
h2 = k32.CreateFileW(tmp, GENERIC_READ, FILE_SHARE_READ, None, OPEN_EXISTING, 0, None)
p("自测文件 open handle=%r / %r（我方 python pid=%d）" % (h, h2, os.getpid()))
for name, paths in [
    ("单文件·自己占着", [tmp]),
    ("多文件·pagefile.sys + hosts", [r"C:\pagefile.sys",
                                     r"C:\Windows\System32\drivers\etc\hosts"]),
    ("目录·System32\\drivers", [r"C:\Windows\System32\drivers"]),
    ("不存在·D:\\nope.x", [r"D:\nope.x"]),
]:
    label, status, dt, rows = who_locks(paths, name)
    p("  %-28s %-22s %6.1f ms  rows=%d" % (label, status, dt * 1000, len(rows)))
    for r in rows[:6]:
        p("      pid=%-6d type=%s restartable=%s app=%r svc=%r"
          % (r[0], r[3], r[4], r[1][:40], r[2][:30]))

for hh in (h, h2):
    if hh and hh != ctypes.c_void_p(-1).value:
        k32.CloseHandle(hh)
try:
    os.remove(tmp)
except OSError:
    pass

# 真实场景：谁占着 exe 本身（用户在资源管理器里预览/被运行）
p("  真实样本 dist exe（未运行时应当没人占）:")
exe = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "dist", "SystemToolbox.exe")
if os.path.isfile(exe):
    label, status, dt, rows = who_locks([exe], "dist exe")
    p("      %-18s %-16s %6.1f ms rows=%d" % (label, status, dt * 1000, len(rows)))
else:
    p("      dist exe 不存在")

# ---------------------------------------------------------------- B. 开机耗时
p()
p("=" * 68)
p("B. 开机 / 关机耗时（Diagnostics-Performance，事件 100~199）")
p("=" * 68)
try:
    from app.core import evtx as ev
    t0 = time.perf_counter()
    ch = "Microsoft-Windows-Diagnostics-Performance/Operational"
    evs, st = ev.query_events(ch, False, "*[System[(EventID>=100 and EventID<=199)]]",
                              limit=6000, message_mode="none")
    dt = time.perf_counter() - t0
    p("读取 %d 条，用时 %.2f s，error=%r" % (len(evs), dt, st.error))
    from collections import Counter
    c = Counter(e.event_id for e in evs)
    p("ID 分布: %s" % dict(sorted(c.items())))
    boots = [e for e in evs if e.event_id == 100]
    p("开机记录 %d 条；最近 5 次:" % len(boots))
    for e in boots[:5]:
        d = e.data
        p("  %s boot=%s main=%s postboot=%s degr=%s"
          % (e.time_text, d.get("BootTime"), d.get("MainPathBootTime"),
             d.get("BootPostBootTime"), d.get("BootDegradationTime")))
    shuts = [e for e in evs if e.event_id in (200, 201, 202, 203)]
    p("关机记录 %d 条；最近 3 次:" % len(shuts))
    for e in shuts[:3]:
        p("  id=%d %s %s" % (e.event_id, e.time_text,
                             {k: v for k, v in list(e.data.items())[:5]}))
    slow = [e for e in evs if e.event_id in (101, 102, 103, 106, 109, 110)]
    p("拖慢项 (101应用/102驱动/103服务/106/109设备/110): %d 条" % len(slow))
    for e in slow[:6]:
        p("  id=%-4d %s %s" % (e.event_id, e.time_text,
                               {k: v for k, v in list(e.data.items())[:4]}))
except Exception as exc:  # noqa: BLE001
    import traceback
    p("EXC %s" % exc)
    p(traceback.format_exc())

# ---------------------------------------------------------------- C. PATH 体检
p()
p("=" * 68)
p("C. 环境变量 / PATH 体检")
p("=" * 68)
for scope in ("Machine", "User"):
    raw = ""
    try:
        if scope == "Machine":
            import winreg
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                                r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment") as k:
                raw = winreg.QueryValueEx(k, "Path")[0]
                p("%s Path 值类型=%s" % (scope, winreg.QueryValueEx(k, "Path")[1]))
        else:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Environment") as k:
                raw = winreg.QueryValueEx(k, "Path")[0]
    except FileNotFoundError:
        p("%s 无 Path" % scope)
        continue
    parts = [x for x in raw.split(";") if x.strip()]
    seen = {}
    dup = []
    for x in parts:
        key = os.path.expandvars(x).rstrip("\\").lower()
        if key in seen:
            dup.append(x)
        seen[key] = 1
    missing = [x for x in parts if not os.path.isdir(os.path.expandvars(x))]
    p("%s: %d 项 · 长度 %d 字符 · 重复 %d · 不存在 %d"
      % (scope, len(parts), len(raw), len(dup), len(missing)))
    for x in dup[:6]:
        p("    dup  %s" % x)
    for x in missing[:8]:
        p("    miss %s" % x)
p("合并后 PATH 长度（当前进程）= %d 字符" % len(os.environ.get("PATH", "")))
mx = 32767
p("上限 %d，余量 %d" % (mx, mx - len(os.environ.get("PATH", ""))))

# ---------------------------------------------------------------- D. 驱动回溯
p()
p("=" * 68)
p("D. 第三方驱动列表（DriverStore / pnputil）")
p("=" * 68)
t0 = time.perf_counter()
try:
    r = subprocess.run(["pnputil.exe", "/enum-drivers"], capture_output=True,
                       text=True, encoding="oem", errors="replace",
                       creationflags=0x08000000, timeout=30)
    p("pnputil rc=%d 用时 %.2f s 字节=%d" % (r.returncode, time.perf_counter() - t0,
                                            len(r.stdout or "")))
    p((r.stdout or "")[:600])
except Exception as exc:  # noqa: BLE001
    p("pnputil EXC %s" % exc)

# 注册表侧统计驱动服务
try:
    import winreg
    n_all = n_third = 0
    samples = []
    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                        r"SYSTEM\CurrentControlSet\Services") as root:
        i = 0
        while True:
            try:
                name = winreg.EnumKey(root, i)
            except OSError:
                break
            i += 1
            try:
                with winreg.OpenKey(root, name) as sk:
                    stype = winreg.QueryValueEx(sk, "Type")[0]
                    if stype not in (1, 2):
                        continue
                    n_all += 1
                    path = ""
                    try:
                        path = winreg.QueryValueEx(sk, "ImagePath")[0]
                    except OSError:
                        pass
                    low = str(path).lower()
                    if low and "\\windows\\" not in os.path.expandvars(low):
                        n_third += 1
                        samples.append((name, path))
            except OSError:
                continue
    p("驱动类服务 %d 项，其中 ImagePath 不在 Windows 目录下 %d 项" % (n_all, n_third))
    for s in samples[:8]:
        p("    %-32s %s" % s)
except Exception as exc:  # noqa: BLE001
    p("reg EXC %s" % exc)

with open(OUT, "w", encoding="utf-8") as fh:
    fh.write("\n".join(log) + "\n")
print("wrote", OUT, len(log), "lines")
