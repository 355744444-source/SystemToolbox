# -*- coding: utf-8 -*-
"""进程级占用采样（只读，纯 ctypes）。

为什么不用 tasklist / wmic / PowerShell：
- `tasklist` 只给内存，不给 CPU 时间、不给私有工作集、不给句柄数
- `wmic process` 已在新系统上被标记弃用，且要起 WMI 服务，1~3 秒
- 这里一轮 190 个进程只要 **10 毫秒**，可以每秒采一次而不影响被测机器

CPU 占用口径（与任务管理器一致）：
    cpu% = Δ(进程内核+用户时间) / Δ(系统内核+用户时间) * 100
注意 `GetSystemTimes` 返回的内核时间**已经包含空闲时间**，所以
`kern + user` 就是这台机器这段时间的全部 CPU 容量（本机实测 16 核 1 秒 = 16 秒）。

**不要**再除以核数：除以核数会变成「满一个核 = 100%」（Process Explorer 口径），
16 核机器上一个吃满单核的进程会显示 100%，用户看着像占满整机，而任务管理器里
它只有 6.25%。售后现场都是对着任务管理器看的，口径必须一致。
代价是数值很小（0.1% 级），所以 UI 显示保留两位小数。

首次采样没有历史，CPU 一律报 0 并置 `warm=False`，UI 侧据此显示「采集中…」而不是
一排 0.0%（否则用户会以为工具坏了）。
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import os
import time
from dataclasses import dataclass, field
from datetime import datetime

from . import winutil

#: Toolhelp 的进程条目结构现在由 `winutil` 统一持有（`process_name_map` 也用同一份）
PROCESSENTRY32W = winutil.PROCESSENTRY32W

# ---------------- Win32 常量 ----------------
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
TH32CS_SNAPPROCESS = 0x00000002
MAX_PATH = 260

k32 = ctypes.WinDLL("kernel32", use_last_error=True)
psapi = ctypes.WinDLL("psapi", use_last_error=True)

k32.CreateToolhelp32Snapshot.restype = wt.HANDLE
k32.CreateToolhelp32Snapshot.argtypes = [wt.DWORD, wt.DWORD]
k32.OpenProcess.restype = wt.HANDLE
k32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
k32.CloseHandle.argtypes = [wt.HANDLE]
k32.GetSystemTimes.argtypes = [ctypes.POINTER(wt.FILETIME)] * 3


class PROCESS_MEMORY_COUNTERS_EX(ctypes.Structure):
    _fields_ = [("cb", wt.DWORD), ("PageFaultCount", wt.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
                ("PrivateUsage", ctypes.c_size_t)]


psapi.GetProcessMemoryInfo.argtypes = [
    wt.HANDLE, ctypes.POINTER(PROCESS_MEMORY_COUNTERS_EX), wt.DWORD]

# 句柄入参一律显式声明 HANDLE：ctypes 默认按 c_int 传，64 位句柄会被截断，
# OpenProcess 拿到的句柄就是残的（本项目在 advapi32 上踩过同样的坑）。
k32.GetProcessTimes.argtypes = [wt.HANDLE] + [ctypes.POINTER(wt.FILETIME)] * 4
k32.GetProcessHandleCount.argtypes = [wt.HANDLE, ctypes.POINTER(wt.DWORD)]
k32.Process32FirstW.argtypes = [wt.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
k32.Process32NextW.argtypes = [wt.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]


def _ft(ft: wt.FILETIME) -> int:
    return (ft.dwHighDateTime << 32) | ft.dwLowDateTime


def _dt(ft: wt.FILETIME) -> datetime | None:
    v = _ft(ft)
    if not v:
        return None
    try:
        # FILETIME 是 1601-01-01 起的 100ns，转 Unix 秒再本地化
        return datetime.fromtimestamp(v / 10_000_000 - 11644473600)
    except (ValueError, OSError, OverflowError):
        return None


def _sys_cpu_ticks() -> int:
    """系统累计 CPU 时间（内核 + 用户，100ns）。内核里已含空闲时间。"""
    idle, kern, user = wt.FILETIME(), wt.FILETIME(), wt.FILETIME()
    if not k32.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kern),
                              ctypes.byref(user)):
        return 0
    return _ft(kern) + _ft(user)


@dataclass
class Proc:
    """一条进程快照。内存单位 MB。"""
    pid: int
    name: str
    cpu: float = 0.0                      # 百分比，满一个核 = 100
    mem_mb: float = 0.0                   # 工作集（任务管理器「内存」列同源）
    private_mb: float = 0.0               # 私有工作集（排查泄漏看这个）
    handles: int = 0
    threads: int = 0
    started: datetime | None = None
    path: str = ""
    system: bool = False                  # 镜像在 System32 下

    @property
    def uptime_text(self) -> str:
        if not self.started:
            return "—"
        secs = (datetime.now() - self.started).total_seconds()
        if secs < 60:
            return "%d 秒" % max(int(secs), 0)
        if secs < 3600:
            return "%d 分" % (secs // 60)
        if secs < 86400:
            return "%.1f 小时" % (secs / 3600)
        return "%.1f 天" % (secs / 86400)

    @property
    def started_text(self) -> str:
        return self.started.strftime("%m-%d %H:%M") if self.started else "—"


@dataclass
class ProcReport:
    rows: list[Proc] = field(default_factory=list)
    warm: bool = False                    # 是否已有两次采样（CPU 才有意义）
    total: int = 0
    ncpu: int = 1
    elapsed: float = 0.0
    denied: int = 0                       # 打不开的进程数（权限不足）

    def sorted_by(self, key: str = "cpu", limit: int = 40) -> list[Proc]:
        rows = list(self.rows)
        if key == "mem":
            rows.sort(key=lambda p: p.private_mb, reverse=True)
        elif key == "handles":
            rows.sort(key=lambda p: p.handles, reverse=True)
        else:
            rows.sort(key=lambda p: p.cpu, reverse=True)
        return rows[:limit]


class ProcSampler:
    """两次采样做差分，可反复调用；线程内独占，不共享给 UI。"""

    def __init__(self) -> None:
        self._prev: dict[int, int] = {}       # pid -> 累计 CPU 时间
        self._prev_sys = 0
        self._prev_ts = 0.0
        self.ncpu = os.cpu_count() or 1

    def reset(self) -> None:
        self._prev.clear()
        self._prev_sys = 0
        self._prev_ts = 0.0

    def sample(self) -> ProcReport:
        t0 = time.perf_counter()
        rep = ProcReport(ncpu=self.ncpu, warm=bool(self._prev))
        cur: dict[int, int] = {}
        sys_now = _sys_cpu_ticks()
        d_sys = sys_now - self._prev_sys if self._prev_sys else 0
        # 分母：这段时间整机的 CPU 容量（100ns）。整机满载 = 100%
        denom = float(d_sys) if d_sys > 0 else 0.0

        snap = k32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
        if snap in (None, wt.HANDLE(-1).value):
            rep.elapsed = time.perf_counter() - t0
            return rep
        pe = PROCESSENTRY32W()
        pe.dwSize = ctypes.sizeof(pe)
        try:
            ok = k32.Process32FirstW(snap, ctypes.byref(pe))
            while ok:
                pid = pe.th32ProcessID
                p = Proc(pid=pid, name=pe.szExeFile, threads=pe.cntThreads)
                h = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
                if not h:
                    rep.denied += 1
                    rep.rows.append(p)
                else:
                    try:
                        pmc = PROCESS_MEMORY_COUNTERS_EX()
                        pmc.cb = ctypes.sizeof(pmc)
                        if psapi.GetProcessMemoryInfo(h, ctypes.byref(pmc), pmc.cb):
                            p.mem_mb = pmc.WorkingSetSize / 1048576.0
                            p.private_mb = pmc.PrivateUsage / 1048576.0
                        hc = wt.DWORD()
                        if k32.GetProcessHandleCount(h, ctypes.byref(hc)):
                            p.handles = int(hc.value)
                        c, e, kk, u = (wt.FILETIME(), wt.FILETIME(),
                                       wt.FILETIME(), wt.FILETIME())
                        if k32.GetProcessTimes(h, ctypes.byref(c), ctypes.byref(e),
                                               ctypes.byref(kk), ctypes.byref(u)):
                            total = _ft(kk) + _ft(u)
                            cur[pid] = total
                            prev = self._prev.get(pid)
                            if prev is not None and denom > 0 and total >= prev:
                                p.cpu = (total - prev) / denom * 100.0
                            p.started = _dt(c)
                        # 路径查询统一走 winutil（本项目原来有三份实现）；
                        # 句柄已经打开，直接给它，别再 OpenProcess 一次
                        p.path = winutil.image_path_from_handle(h)
                        if p.path:
                            low = p.path.lower()
                            p.system = ("\\windows\\system32\\" in low
                                        or "\\windows\\syswow64\\" in low)
                    finally:
                        k32.CloseHandle(h)
                    rep.rows.append(p)
                ok = k32.Process32NextW(snap, ctypes.byref(pe))
        finally:
            k32.CloseHandle(snap)

        self._prev = cur
        self._prev_sys = sys_now
        self._prev_ts = time.perf_counter()
        rep.total = len(rep.rows)
        rep.elapsed = time.perf_counter() - t0
        # 已退出的进程不该一直占着分母，直接由 cur 覆盖已实现
        return rep


def snapshot() -> ProcReport:
    """一次性快照（CPU 全 0，只用于「不想等一秒」的场合）。"""
    s = ProcSampler()
    return s.sample()


def top(key: str = "cpu", limit: int = 10) -> list[Proc]:
    """给自检用：采样两次，返回 CPU TOP。"""
    s = ProcSampler()
    s.sample()
    time.sleep(0.4)
    return s.sample().sorted_by(key, limit)
