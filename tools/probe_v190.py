"""探针：验证「文件被谁占用」与「转储配置」两条能力是否真能拿到。

只读，不改任何东西。
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import os
import sys
import time
import winreg

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "probe_v190.log")
lines: list[str] = []


def log(s: str) -> None:
    lines.append(s)


# ---------------------------------------------------------------- Restart Manager
def rm_holders(path: str) -> list[tuple[str, int, str]]:
    """返回 [(进程名, PID, 应用类型)]。用 Restart Manager，不需要句柄遍历权限。"""
    try:
        rm = ctypes.WinDLL("rstrtmgr.dll", use_last_error=True)
    except OSError as exc:
        log("  rstrtmgr 加载失败: %s" % exc)
        return []

    class FILETIME(ctypes.Structure):
        _fields_ = [("dwLowDateTime", wt.DWORD), ("dwHighDateTime", wt.DWORD)]

    class RM_UNIQUE_PROCESS(ctypes.Structure):
        _fields_ = [("dwProcessId", wt.DWORD), ("ProcessStartTime", FILETIME)]

    MAX_APP = 255
    CCH_RM_SESSION = 32

    class RM_PROCESS_INFO(ctypes.Structure):
        _fields_ = [
            ("Process", RM_UNIQUE_PROCESS),
            ("strAppName", wt.WCHAR * (MAX_APP + 1)),
            ("strServiceShortName", wt.WCHAR * (MAX_APP + 1)),
            ("ApplicationType", ctypes.c_uint),
            ("AppStatus", wt.ULONG),
            ("TSSessionId", wt.DWORD),
            ("bRestartable", wt.BOOL),
        ]

    session = wt.WCHAR * (CCH_RM_SESSION + 1)
    key = session()
    h = wt.HANDLE()
    rc = rm.RmStartSession(ctypes.byref(h), ctypes.byref(wt.DWORD(0)), key)
    if rc != 0:
        log("  RmStartSession rc=%d" % rc)
        return []
    try:
        arr = (wt.LPCWSTR * 1)(path)
        rc = rm.RmRegisterResources(h, 1, arr, 0, None, 0, None)
        if rc != 0:
            log("  RmRegisterResources rc=%d" % rc)
            return []
        need = wt.UINT(0)
        reason = wt.DWORD(0)
        rc = rm.RmGetList(h, ctypes.byref(need), ctypes.byref(wt.UINT(0)),
                          None, ctypes.byref(reason))
        if need.value == 0:
            log("  RmGetList 无占用进程 rc=%d need=0" % rc)
            return []
        buf = (RM_PROCESS_INFO * need.value)()
        cnt = wt.UINT(need.value)
        rc = rm.RmGetList(h, ctypes.byref(cnt), ctypes.byref(cnt),
                          buf, ctypes.byref(reason))
        out = []
        for i in range(cnt.value):
            p = buf[i]
            out.append((p.strAppName, p.Process.dwProcessId,
                        p.strServiceShortName or ""))
        return out
    finally:
        rm.RmEndSession(h)


def probe_rm() -> None:
    log("=== Restart Manager 文件占用 ===")
    targets = [sys.executable, os.path.abspath(__file__)]
    # 找一个正在被占用的系统文件：本进程自己的 exe 必然被自己占着
    for t in targets:
        t0 = time.perf_counter()
        got = rm_holders(t)
        ms = (time.perf_counter() - t0) * 1000
        log("  %s -> %d 个占用者 (%.0f ms) %s"
            % (os.path.basename(t), len(got), ms, got[:4]))
    # 再试一个不存在的路径，看是否优雅失败
    log("  不存在路径 -> %s" % (rm_holders(r"C:\__no_such_file__.tmp"),))


# ---------------------------------------------------------------- 句柄枚举
def probe_handles() -> None:
    log("=== 句柄枚举（NtQuerySystemInformation 16） ===")
    ntdll = ctypes.WinDLL("ntdll", use_last_error=True)
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)

    class UNICODE_STRING(ctypes.Structure):
        _fields_ = [("Length", wt.USHORT), ("MaximumLength", wt.USHORT),
                    ("Buffer", wt.LPWSTR)]

    class SYSTEM_HANDLE_TABLE_ENTRY_INFO_EX(ctypes.Structure):
        _fields_ = [
            ("Object", ctypes.c_void_p),
            ("UniqueProcessId", ctypes.c_size_t),
            ("HandleValue", ctypes.c_size_t),
            ("GrantedAccess", wt.ULONG),
            ("CreatorBackTraceIndex", wt.USHORT),
            ("ObjectTypeIndex", wt.USHORT),
            ("HandleAttributes", wt.ULONG),
            ("Reserved", wt.ULONG),
        ]

    size = 1 << 22
    buf = ctypes.create_string_buffer(size)
    ret = wt.ULONG(0)
    ntdll.NtQuerySystemInformation.argtypes = [wt.ULONG, ctypes.c_void_p,
                                               wt.ULONG, ctypes.POINTER(wt.ULONG)]
    status = ntdll.NtQuerySystemInformation(16, buf, size, ctypes.byref(ret))
    log("  status=0x%08X 需要长度=%d" % (status & 0xFFFFFFFF, ret.value))
    if status != 0:
        return
    count = ctypes.cast(buf, ctypes.POINTER(ctypes.c_size_t))[0]
    log("  条目数=%d 每条=%d 字节"
        % (count, ctypes.sizeof(SYSTEM_HANDLE_TABLE_ENTRY_INFO_EX)))
    # 统计本进程的句柄，验证解析正确
    me = os.getpid()
    mine = 0
    arr = (SYSTEM_HANDLE_TABLE_ENTRY_INFO_EX * count).from_buffer(buf, 8)
    for i in range(min(count, 200000)):
        if arr[i].UniqueProcessId == me:
            mine += 1
    log("  本进程(PID=%d)句柄数按解析结果=%d" % (me, mine))
    k32.GetCurrentProcess.restype = wt.HANDLE
    k32.GetCurrentProcess.argtypes = []
    k32.GetProcessHandleCount.argtypes = [wt.HANDLE, ctypes.POINTER(wt.DWORD)]
    k32.GetProcessHandleCount.restype = wt.BOOL
    hproc = k32.GetCurrentProcess()
    n = wt.DWORD(0)
    k32.GetProcessHandleCount(hproc, ctypes.byref(n))
    log("  GetProcessHandleCount 实测=%d  （两者接近说明解析正确）" % n.value)


# ---------------------------------------------------------------- 转储配置
def probe_crash() -> None:
    log("=== 崩溃转储配置 (CrashControl) ===")
    key = r"SYSTEM\CurrentControlSet\Control\CrashControl"
    names = ["CrashDumpEnabled", "DumpFile", "MinidumpDir", "AutoReboot",
             "Overwrite", "AlwaysKeepMemoryDump", "LogEvent", "DumpFileSize",
             "MinidumpDirSize", "IgnorePagefileSize"]
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key) as k:
            for name in names:
                try:
                    v, typ = winreg.QueryValueEx(k, name)
                    log("  %-22s = %r (type=%d)" % (name, v, typ))
                except FileNotFoundError:
                    log("  %-22s = <不存在>" % name)
    except OSError as exc:
        log("  打不开 CrashControl: %s" % exc)

    log("--- 实际转储文件")
    for p in (r"%SystemRoot%\MEMORY.DMP", r"%SystemRoot%\Minidump"):
        real = os.path.expandvars(p)
        if os.path.isdir(real):
            files = os.listdir(real)
            total = sum(os.path.getsize(os.path.join(real, f))
                        for f in files if os.path.isfile(os.path.join(real, f)))
            log("  %s 目录 %d 个文件 %.2f GB" % (real, len(files), total / 2**30))
        elif os.path.isfile(real):
            log("  %s 存在 %.2f GB" % (real, os.path.getsize(real) / 2**30))
        else:
            log("  %s <不存在>" % real)

    log("--- 页面文件（内核转储需要足够大）")
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                            r"SYSTEM\CurrentControlSet\Control\Session Manager\Memory Management") as k:
            v, _ = winreg.QueryValueEx(k, "PagingFiles")
            log("  PagingFiles = %r" % (v,))
    except OSError as exc:
        log("  PagingFiles 读失败: %s" % exc)

    class MEMORYSTATUSEX(ctypes.Structure):
        _fields_ = [("dwLength", wt.DWORD), ("dwMemoryLoad", wt.DWORD),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

    ms = MEMORYSTATUSEX()
    ms.dwLength = ctypes.sizeof(ms)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(ms))
    log("  物理内存 %.1f GB / 页面文件总量 %.1f GB"
        % (ms.ullTotalPhys / 2**30, ms.ullTotalPageFile / 2**30))


def main() -> None:
    probe_rm()
    probe_handles()
    probe_crash()
    with open(OUT, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
