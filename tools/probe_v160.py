# -*- coding: utf-8 -*-
"""v1.6.0 前置探针：组件健康数据源 + 进程枚举权限。

结论写 probe_v160.log，只读，不改任何系统状态。
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
LOG = open("probe_v160.log", "w", encoding="utf-8", buffering=1)


def w(s):
    LOG.write(str(s) + "\n")


k32 = ctypes.WinDLL("kernel32", use_last_error=True)
psapi = ctypes.WinDLL("psapi", use_last_error=True)


def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


# ---------------- 1. CBS 日志 ----------------
w("==== 1. CBS / 组件日志 ====")
cbsdir = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "Logs", "CBS")
w("dir exists=%s  path=%s" % (os.path.isdir(cbsdir), cbsdir))
if os.path.isdir(cbsdir):
    try:
        names = sorted(os.listdir(cbsdir))
        w("files=%d  head=%s" % (len(names), names[:8]))
        for n in names:
            if not n.lower().startswith(("cbs", "dpx")):
                continue
            p = os.path.join(cbsdir, n)
            try:
                st = os.stat(p)
                w("  %-28s %10.2f MB  mtime=%s" % (
                    n, st.st_size / 1048576.0,
                    time.strftime("%Y-%m-%d %H:%M", time.localtime(st.st_mtime))))
            except OSError as e:
                w("  %-28s stat fail %s" % (n, e))
    except OSError as e:
        w("listdir fail: %s" % e)

# 尾部采样：[SR] 行与错误行
cbs = os.path.join(cbsdir, "CBS.log")
if os.path.isfile(cbs):
    t0 = time.perf_counter()
    tail_n = 0
    err_lines = []
    sr_lines = 0
    with open(cbs, "rb") as f:
        f.seek(0, os.SEEK_END)
        size = f.tell()
        back = min(size, 8 * 1024 * 1024)      # 只读最后 8MB
        f.seek(size - back)
        raw = f.read()
    txt = raw.decode("utf-8", "ignore")
    for line in txt.splitlines():
        tail_n += 1
        if "[SR]" in line:
            sr_lines += 1
        low = line.lower()
        if "cannot repair" in low or "failed to" in low or "error_" in low:
            if len(err_lines) < 6:
                err_lines.append(line[:150])
    w("tail: read %.1f MB in %.0f ms, lines=%d, [SR]=%d, err_samples=%d" % (
        back / 1048576.0, (time.perf_counter() - t0) * 1000, tail_n, sr_lines,
        len(err_lines)))
    for e in err_lines:
        w("   ERR| " + e)

# ---------------- 2. 注册表 CBS / 待重启 ----------------
w("")
w("==== 2. 注册表 CBS / 待重启 ====")
import winreg

CBS = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Component Based Servicing"
for name, path, val in (
    ("CBS\\Version", CBS, "Version"),
    ("CBS\\PackagesPending", CBS + r"\PackagesPending", None),
    ("CBS\\RebootPending", CBS + r"\RebootPending", None),
    ("CBS\\SessionsPending", CBS + r"\SessionsPending", None),
    ("CBS\\PackagesInstalled", CBS + r"\PackagesInstalled", None),
    ("WU\\RebootRequired", r"SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate\Auto Update\RebootRequired", None),
    ("PendingFileRename", r"SYSTEM\CurrentControlSet\Control\Session Manager", "PendingFileRenameOperations"),
    ("Setup\\Upgrade", r"SYSTEM\Setup", "Upgrade"),
):
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path, 0,
                            winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as k:
            if val:
                try:
                    v, _t = winreg.QueryValueEx(k, val)
                    w("  %-22s = %r" % (name, v))
                except OSError:
                    w("  %-22s (value missing)" % name)
            else:
                subs, vals, _ = winreg.QueryInfoKey(k)
                w("  %-22s exists  subkeys=%d values=%d" % (name, subs, vals))
    except OSError as e:
        w("  %-22s missing (%s)" % (name, e.errno or e))

pending = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "WinSxS", "pending.xml")
w("  pending.xml exists=%s size=%s" % (
    os.path.isfile(pending),
    os.path.getsize(pending) if os.path.isfile(pending) else "-"))

# ---------------- 3. WinSxS 体积 ----------------
w("")
w("==== 3. WinSxS 体积（限时 25s）====")
winsxs = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "WinSxS")
t0 = time.perf_counter()
total = files = 0
truncated = False
try:
    stack = [winsxs]
    while stack:
        d = stack.pop()
        try:
            with os.scandir(d) as it:
                for e in it:
                    try:
                        if e.is_dir(follow_symlinks=False):
                            stack.append(e.path)
                        elif e.is_file(follow_symlinks=False):
                            total += e.stat(follow_symlinks=False).st_size
                            files += 1
                    except OSError:
                        pass
        except OSError:
            pass
        if time.perf_counter() - t0 > 25:
            truncated = True
            break
except Exception as e:                                   # noqa: BLE001
    w("scan error %s" % e)
w("  dirs_left=%d files=%d size=%.2f GB elapsed=%.1fs truncated=%s" % (
    len(stack), files, total / 1073741824.0, time.perf_counter() - t0, truncated))

# ---------------- 4. DISM CheckHealth ----------------
w("")
w("==== 4. DISM /CheckHealth ====")
w("  is_admin=%s" % is_admin())
try:
    t0 = time.perf_counter()
    r = subprocess.run(
        ["dism.exe", "/Online", "/Cleanup-Image", "/CheckHealth"],
        capture_output=True, timeout=120,
        creationflags=0x08000000)                        # CREATE_NO_WINDOW
    out = (r.stdout or b"").decode("mbcs", "ignore")
    err = (r.stderr or b"").decode("mbcs", "ignore")
    w("  rc=%d elapsed=%.1fs out_len=%d err_len=%d" % (
        r.returncode, time.perf_counter() - t0, len(out), len(err)))
    for line in out.splitlines():
        if line.strip():
            w("   DISM| " + line[:160])
    for line in err.splitlines()[:4]:
        w("   DISM_ERR| " + line[:160])
except FileNotFoundError:
    w("  dism.exe 不存在")
except subprocess.TimeoutExpired:
    w("  dism 超时")
except Exception as e:                                   # noqa: BLE001
    w("  dism fail %s" % e)

# ---------------- 5. 最近安装的更新 ----------------
w("")
w("==== 5. 最近安装的更新（CBS Packages 里取最近 5 个）====")
try:
    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, CBS + r"\Packages", 0,
                        winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as k:
        subs, _vals, _ = winreg.QueryInfoKey(k)
        rows = []
        for i in range(subs):
            nm = winreg.EnumKey(k, i)
            if nm.startswith("Package_for") or nm.startswith("Package_"):
                rows.append(nm)
        w("  packages=%d  sample=%s" % (subs, rows[:3]))
except OSError as e:
    w("  packages missing (%s)" % e)

# ---------------- 6. 进程枚举能力 ----------------
w("")
w("==== 6. 进程枚举（非管理员能拿到多少）====")

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
TH32CS_SNAPPROCESS = 0x00000002
MAX_PATH = 260


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [("dwSize", wt.DWORD), ("cntUsage", wt.DWORD),
                ("th32ProcessID", wt.DWORD), ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
                ("th32ModuleID", wt.DWORD), ("cntThreads", wt.DWORD),
                ("th32ParentProcessID", wt.DWORD), ("pcPriClassBase", ctypes.c_long),
                ("dwFlags", wt.DWORD), ("szExeFile", wt.WCHAR * MAX_PATH)]


class PMC_EX(ctypes.Structure):
    _fields_ = [("cb", wt.DWORD), ("PageFaultCount", wt.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t),
                ("PrivateUsage", ctypes.c_size_t)]


k32.CreateToolhelp32Snapshot.restype = wt.HANDLE
k32.OpenProcess.restype = wt.HANDLE
psapi.GetProcessMemoryInfo.argtypes = [wt.HANDLE, ctypes.POINTER(PMC_EX), wt.DWORD]

t0 = time.perf_counter()
snap = k32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
pe = PROCESSENTRY32W()
pe.dwSize = ctypes.sizeof(pe)
total_p = opened = mem_ok = 0
ncpu = os.cpu_count() or 1
if k32.Process32FirstW(snap, ctypes.byref(pe)):
    while True:
        total_p += 1
        h = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pe.th32ProcessID)
        if h:
            opened += 1
            pmc = PMC_EX()
            pmc.cb = ctypes.sizeof(pmc)
            if psapi.GetProcessMemoryInfo(h, ctypes.byref(pmc), pmc.cb):
                mem_ok += 1
            k32.CloseHandle(h)
        if not k32.Process32NextW(snap, ctypes.byref(pe)):
            break
k32.CloseHandle(snap)
w("  processes=%d opened=%d mem_ok=%d elapsed=%.0f ms ncpu=%d" % (
    total_p, opened, mem_ok, (time.perf_counter() - t0) * 1000, ncpu))

# CPU 时间差分口径验证：拿自己当靶子
class FILETIME(ctypes.Structure):
    _fields_ = [("dwLowDateTime", wt.DWORD), ("dwHighDateTime", wt.DWORD)]


def ft2i(ft):
    return (ft.dwHighDateTime << 32) | ft.dwLowDateTime


k32.GetSystemTimes.argtypes = [ctypes.POINTER(FILETIME)] * 3


def sys_times():
    i, ke, us = FILETIME(), FILETIME(), FILETIME()
    k32.GetSystemTimes(ctypes.byref(i), ctypes.byref(ke), ctypes.byref(us))
    return ft2i(ke), ft2i(us), ft2i(i)


def proc_cpu(pid):
    h = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return None
    c, e, kk, u = FILETIME(), FILETIME(), FILETIME(), FILETIME()
    ok = k32.GetProcessTimes(h, ctypes.byref(c), ctypes.byref(e),
                             ctypes.byref(kk), ctypes.byref(us := u))
    k32.CloseHandle(h)
    if not ok:
        return None
    return ft2i(kk) + ft2i(u)


b0 = sys_times()
p0 = proc_cpu(os.getpid())
time.sleep(1.0)
b1 = sys_times()
p1 = proc_cpu(os.getpid())
dk = (b1[0] - b0[0]) + (b1[1] - b0[1])
du = (b1[2] - b0[2])
dp = (p1 or 0) - (p0 or 0)
w("  self: dkern=%d duser=%d didle=%d dproc=%d" % (b1[0] - b0[0], b1[1] - b0[1], du, dp))
w("  busy_total=%.1f%%   self_cpu(按 ncpu 归一)=%.2f%%" % (
    (dk - du) / dk * 100 if dk else 0, dp / (dk / ncpu) * 100 if dk else 0))

w("")
w("DONE")
LOG.close()
print("done")
