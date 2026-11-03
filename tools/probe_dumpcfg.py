"""探针 v1.13：转储配置体检的真实数据源。

只读。目标是把「为什么没生成 dump」这条链路上的每个事实量出来：
CrashControl 键值 / 页面文件 / 物理内存 / 转储文件现状 / 最近一次蓝屏事件。

Bash 工具里 stdout 常丢，结果一律写 probe_dumpcfg.log 再读。
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import os
import sys
import time
import winreg
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "probe_dumpcfg.log")
lines: list[str] = []


def log(s: str = "") -> None:
    lines.append(s)


def gb(n: float) -> float:
    return n / 2 ** 30


# ---------------------------------------------------------------- 注册表
def probe_registry() -> None:
    log("=== CrashControl 全量键值 ===")
    key = r"SYSTEM\CurrentControlSet\Control\CrashControl"
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key) as k:
            idx = 0
            while True:
                try:
                    name, val, typ = winreg.EnumValue(k, idx)
                except OSError:
                    break
                idx += 1
                log("  %-26s = %-40r type=%d" % (name, val, typ))
            log("  --- 共 %d 个值" % idx)
    except OSError as exc:
        log("  打不开 CrashControl: %s" % exc)

    log()
    log("=== 页面文件配置 ===")
    try:
        with winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"SYSTEM\CurrentControlSet\Control\Session Manager\Memory Management") as k:
            for nm in ("PagingFiles", "ExistingPageFiles", "TempPageFile"):
                try:
                    v, _t = winreg.QueryValueEx(k, nm)
                    log("  %-18s = %r" % (nm, v))
                except FileNotFoundError:
                    log("  %-18s = <不存在>" % nm)
    except OSError as exc:
        log("  Memory Management 打不开: %s" % exc)


# ---------------------------------------------------------------- 内存
class MEMORYSTATUSEX(ctypes.Structure):
    _fields_ = [("dwLength", wt.DWORD), ("dwMemoryLoad", wt.DWORD),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]


def probe_memory() -> None:
    log()
    log("=== 物理内存 / 页面文件 ===")
    ms = MEMORYSTATUSEX()
    ms.dwLength = ctypes.sizeof(ms)
    if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(ms)):
        log("  物理内存      %.2f GB" % gb(ms.ullTotalPhys))
        log("  提交上限      %.2f GB（物理+页面文件）" % gb(ms.ullTotalPageFile))
    else:
        log("  GlobalMemoryStatusEx 失败")

    log("  --- 各卷页面文件（GetPerformanceInfo 之外的 WMI 口径）")
    try:
        import subprocess
        cp = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-CimInstance Win32_PageFileUsage | "
             "Select-Object Name,AllocatedBaseSize,CurrentUsage | "
             "ForEach-Object { \"$($_.Name)|$($_.AllocatedBaseSize)|$($_.CurrentUsage)\" }"],
            capture_output=True, text=True, timeout=20,
            creationflags=0x08000000)
        log("  rc=%d out=%r err=%r" % (cp.returncode, cp.stdout.strip()[:400],
                                       cp.stderr.strip()[:200]))
    except Exception as exc:                       # noqa: BLE001
        log("  WMI 查询失败: %s" % exc)


# ---------------------------------------------------------------- 转储文件
def probe_files() -> None:
    log()
    log("=== 现有转储文件 ===")
    sysroot = os.environ.get("SystemRoot", r"C:\Windows")
    dump = os.path.join(sysroot, "MEMORY.DMP")
    if os.path.isfile(dump):
        st = os.stat(dump)
        log("  MEMORY.DMP  %.2f GB  %s" % (
            gb(st.st_size), datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M")))
    else:
        log("  MEMORY.DMP  <不存在>")

    mdir = os.path.join(sysroot, "Minidump")
    if os.path.isdir(mdir):
        files = [os.path.join(mdir, f) for f in os.listdir(mdir)]
        files = [f for f in files if os.path.isfile(f)]
        total = sum(os.path.getsize(f) for f in files)
        log("  Minidump 目录 %d 个文件 合计 %.2f MB" % (len(files), total / 2 ** 20))
        files.sort(key=os.path.getmtime, reverse=True)
        for f in files[:6]:
            st = os.stat(f)
            log("    %-24s %7.2f MB  %s" % (
                os.path.basename(f), st.st_size / 2 ** 20,
                datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M")))
    else:
        log("  Minidump 目录 <不存在>")

    for p in (r"%SystemRoot%\LiveKernelReports", r"%SystemRoot%\Temp"):
        real = os.path.expandvars(p)
        if os.path.isdir(real):
            log("  %s 存在（%d 项）" % (real, len(os.listdir(real))))
        else:
            log("  %s <不存在>" % real)

    log()
    log("=== 各卷剩余空间 ===")
    mask = ctypes.windll.kernel32.GetLogicalDrives()
    for i in range(26):
        if not (mask >> i) & 1:
            continue
        root = "%s:\\" % chr(65 + i)
        free = ctypes.c_ulonglong(0)
        total = ctypes.c_ulonglong(0)
        ok = ctypes.windll.kernel32.GetDiskFreeSpaceExW(
            ctypes.c_wchar_p(root), ctypes.byref(free), ctypes.byref(total), None)
        if ok:
            log("  %s 总 %7.2f GB 剩 %7.2f GB" % (root, gb(total.value), gb(free.value)))


# ---------------------------------------------------------------- 蓝屏事件
def probe_bugcheck() -> None:
    log()
    log("=== 最近一次蓝屏事件（System 通道）===")
    from app.core import evtx
    for xpath, label in (
            ("*[System[(EventID=1001)]]", "id=1001 全部"),
            ("*[System[(Provider[@Name='BugCheck'])]]", "provider=BugCheck"),
            ("*[System[(EventID=41)]]", "id=41 Kernel-Power 仅条数"),
    ):
        t0 = time.perf_counter()
        try:
            evs, st = evtx.query_events("System", False, xpath=xpath, limit=200,
                                        message_mode="none")
            ms = (time.perf_counter() - t0) * 1000
            log("  [%s] %d 条 / %.0f ms" % (label, len(evs), ms))
            for e in evs[:5]:
                log("    %s  %-36s id=%-5s %s | %s"
                    % (e.time_text, e.provider[:36], e.event_id,
                       e.level_name, e.data_text[:120]))
        except Exception as exc:                   # noqa: BLE001
            log("  [%s] 读取失败: %s" % (label, exc))

    log()
    log("=== System 通道里出现过的 Kernel-Power 41 / BugCheck 计数（限 2000 条 Recent）===")
    t0 = time.perf_counter()
    try:
        evs, st = evtx.query_events("System", False, limit=2000, message_mode="none")
        ms = (time.perf_counter() - t0) * 1000
        log("  读 %d 条用 %.0f ms" % (len(evs), ms))
        from collections import Counter
        c = Counter((e.provider, e.event_id) for e in evs)
        for (prov, eid), n in c.most_common(12):
            log("    %-44s id=%-5s x%d" % (prov[:44], eid, n))
        # 蓝屏相关
        for (prov, eid), n in c.items():
            if eid in (41, 1001) or "BugCheck" in prov:
                log("    >> 蓝屏相关 %s id=%s x%d" % (prov, eid, n))
    except Exception as exc:                       # noqa: BLE001
        log("  读取失败: %s" % exc)


# ---------------------------------------------------------------- 权限
def probe_perms() -> None:
    log()
    log("=== 各文件可写性（用 fopen('ab') 试探，不真写内容）===")
    sysroot = os.environ.get("SystemRoot", r"C:\Windows")
    for p in (os.path.join(sysroot, "Minidump"), sysroot):
        try:
            probe = os.path.join(p, "__cls_perm_probe__.tmp")
            fh = open(probe, "ab")
            fh.close()
            os.remove(probe)
            log("  %s 可写" % p)
        except OSError as exc:
            log("  %s 不可写: %s" % (p, exc))


def main() -> None:
    probe_registry()
    probe_memory()
    probe_files()
    probe_bugcheck()
    probe_perms()
    with open(OUT, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
