# -*- coding: utf-8 -*-
"""性能采样：CPU / 内存 / 磁盘占用 / 网络吞吐，每秒一个点。**全只读**。

取数一律走系统 API，不起外部进程（`typeperf` 那种要 1 秒以上才出第一个点，
而且输出是本地化文本）：

| 指标 | 用什么 | 为什么不用别的 |
|---|---|---|
| CPU 总占用 | `GetSystemTimes` 两次差值 | 比 WMI `Win32_PerfFormattedData` 快两个数量级 |
| 内存 | `GlobalMemoryStatusEx` | 一次结构体调用，含已用/总量/提交 |
| 磁盘占用 | PDH `\\PhysicalDisk(_Total)\\% Disk Time` | 这是任务管理器同源计数器；`PdhAddEnglishCounterW` 在中文系统上也能用英文路径 |
| 网络吞吐 | `GetIfTable` 的 `dwInOctets/dwOutOctets` 差值 | 不用 PDH 的 `Network Interface` —— 那一族计数器在断链/虚拟网卡上经常整体失效 |

**第一次 sample 一定返回 0**：所有「差值型」指标都要一个基准点，
所以调用方应当立刻丢弃第一个点（`Sampler` 内部已经处理）。

**磁盘计数器必须后台建**：`PdhAddEnglishCounterW` 在中文系统上要先把英文
计数器名表映射出来，实测阻塞 690ms（同机 `PdhAddCounterW` 走本地化路径更快，
但路径名随语言变，不可移植）。这 690ms 不能压在 UI 线程上 —— 用户点开
「系统体检」就是一片白。所以 `Sampler` 默认把 DiskCounter 丢到守护线程里建，
建好之前 `disk` 一律 0.0，曲线晚 1 秒出磁盘线，比卡住一秒好。
"""
from __future__ import annotations

import ctypes
import os
import threading
import time
from ctypes import wintypes
from dataclasses import dataclass

IS_WIN = os.name == "nt"


@dataclass
class Point:
    ts: float = 0.0
    cpu: float = 0.0            # 0~100，多核合计
    mem: float = 0.0            # 0~100
    disk: float = 0.0           # 0~100（可超过 100，展示时钳制）
    net_in: float = 0.0         # KB/s
    net_out: float = 0.0        # KB/s
    mem_used_gb: float = 0.0
    mem_total_gb: float = 0.0

    @property
    def net_total(self) -> float:
        return self.net_in + self.net_out


# ------------------------------------------------------------------ CPU / 内存
class _FILETIME(ctypes.Structure):
    _fields_ = [("dwLowDateTime", wintypes.DWORD),
                ("dwHighDateTime", wintypes.DWORD)]

    @property
    def value(self) -> int:
        return (self.dwHighDateTime << 32) | self.dwLowDateTime


def _ft() -> tuple[int, int, int]:
    k32 = ctypes.windll.kernel32
    idle, kern, user = _FILETIME(), _FILETIME(), _FILETIME()
    if not k32.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kern),
                              ctypes.byref(user)):
        return (0, 0, 0)
    # kernel 时间里含 idle，所以 busy = (kernel + user) - idle
    return (idle.value, kern.value, user.value)


class _MEMORYSTATUSEX(ctypes.Structure):
    _fields_ = [("dwLength", wintypes.DWORD),
                ("dwMemoryLoad", wintypes.DWORD),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]


def memory() -> tuple[float, float, float]:
    """(已用百分比, 已用 GB, 总量 GB)"""
    st = _MEMORYSTATUSEX()
    st.dwLength = ctypes.sizeof(st)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st)):
        return (0.0, 0.0, 0.0)
    total = st.ullTotalPhys / (1024 ** 3)
    used = (st.ullTotalPhys - st.ullAvailPhys) / (1024 ** 3)
    pct = (used / total * 100.0) if total else 0.0
    return (pct, used, total)


# ------------------------------------------------------------------ 磁盘（PDH）
class _PDH_FMT_COUNTERVALUE(ctypes.Structure):
    _fields_ = [("CStatus", wintypes.DWORD),
                ("doubleValue", ctypes.c_double)]


_pdh = None
_pdh_failed = False


def _pdh_dll():
    global _pdh, _pdh_failed
    if _pdh is not None or _pdh_failed:
        return _pdh
    try:
        d = ctypes.WinDLL("pdh", use_last_error=True)
        d.PdhOpenQueryW.argtypes = [wintypes.LPCWSTR, ctypes.c_size_t,
                                    ctypes.POINTER(ctypes.c_void_p)]
        d.PdhOpenQueryW.restype = ctypes.c_long
        d.PdhAddEnglishCounterW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR,
                                           ctypes.c_size_t,
                                           ctypes.POINTER(ctypes.c_void_p)]
        d.PdhAddEnglishCounterW.restype = ctypes.c_long
        d.PdhCollectQueryData.argtypes = [ctypes.c_void_p]
        d.PdhCollectQueryData.restype = ctypes.c_long
        d.PdhGetFormattedCounterValue.argtypes = [
            ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
            ctypes.POINTER(_PDH_FMT_COUNTERVALUE)]
        d.PdhGetFormattedCounterValue.restype = ctypes.c_long
        d.PdhCloseQuery.argtypes = [ctypes.c_void_p]
        _pdh = d
    except Exception:                        # noqa: BLE001
        _pdh_failed = True
        _pdh = None
    return _pdh


class DiskCounter:
    """`\\PhysicalDisk(_Total)\\% Disk Time` 的 PDH 采集器。"""

    PDH_FMT_DOUBLE = 0x00000200

    def __init__(self):
        self.query = None
        self.counter = None
        self._dll = _pdh_dll()
        if self._dll is None:
            return
        q = ctypes.c_void_p()
        if self._dll.PdhOpenQueryW(None, 0, ctypes.byref(q)) != 0:
            return
        c = ctypes.c_void_p()
        path = r"\PhysicalDisk(_Total)\% Disk Time"
        if self._dll.PdhAddEnglishCounterW(q, path, 0, ctypes.byref(c)) != 0:
            self._dll.PdhCloseQuery(q)
            return
        self.query, self.counter = q, c
        self._dll.PdhCollectQueryData(q)     # 第一次采集只建立基准

    def read(self) -> float:
        if self.query is None:
            return 0.0
        if self._dll.PdhCollectQueryData(self.query) != 0:
            return 0.0
        val = _PDH_FMT_COUNTERVALUE()
        if self._dll.PdhGetFormattedCounterValue(
                self.counter, self.PDH_FMT_DOUBLE, None, ctypes.byref(val)) != 0:
            return 0.0
        if val.CStatus != 0:
            return 0.0
        return max(0.0, min(100.0, float(val.doubleValue)))

    def close(self) -> None:
        if self.query is not None and self._dll is not None:
            try:
                self._dll.PdhCloseQuery(self.query)
            except Exception:                # noqa: BLE001
                pass
        self.query = None


# ------------------------------------------------------------------ 网络
class _MIB_IFROW(ctypes.Structure):
    _fields_ = [("wszName", wintypes.WCHAR * 256),
                ("dwIndex", wintypes.DWORD),
                ("dwType", wintypes.DWORD),
                ("dwMtu", wintypes.DWORD),
                ("dwSpeed", wintypes.DWORD),
                ("dwPhysAddrLen", wintypes.DWORD),
                ("bPhysAddr", ctypes.c_ubyte * 8),
                ("dwAdminStatus", wintypes.DWORD),
                ("dwOperStatus", wintypes.DWORD),
                ("dwLastChange", wintypes.DWORD),
                ("dwInOctets", wintypes.DWORD),
                ("dwInUcastPkts", wintypes.DWORD),
                ("dwInNUcastPkts", wintypes.DWORD),
                ("dwInDiscards", wintypes.DWORD),
                ("dwInErrors", wintypes.DWORD),
                ("dwInUnknownProtos", wintypes.DWORD),
                ("dwOutOctets", wintypes.DWORD),
                ("dwOutUcastPkts", wintypes.DWORD),
                ("dwOutNUcastPkts", wintypes.DWORD),
                ("dwOutDiscards", wintypes.DWORD),
                ("dwOutErrors", wintypes.DWORD),
                ("dwOutQLen", wintypes.DWORD),
                ("dwDescrLen", wintypes.DWORD),
                ("bDescr", ctypes.c_ubyte * 256)]


class _MIB_IFTABLE(ctypes.Structure):
    _fields_ = [("dwNumEntries", wintypes.DWORD),
                ("table", _MIB_IFROW * 1)]


def if_octets() -> tuple[int, int]:
    """所有「已连接且非环回」网卡的 (累计入字节, 累计出字节)。"""
    if not IS_WIN:
        return (0, 0)
    try:
        iphlp = ctypes.WinDLL("iphlpapi", use_last_error=True)
        size = wintypes.DWORD(0)
        iphlp.GetIfTable(None, ctypes.byref(size), False)
        if size.value <= 0:
            return (0, 0)
        buf = ctypes.create_string_buffer(size.value)
        if iphlp.GetIfTable(buf, ctypes.byref(size), False) != 0:
            return (0, 0)
        table = ctypes.cast(buf, ctypes.POINTER(_MIB_IFTABLE)).contents
        rows = ctypes.cast(ctypes.byref(table.table),
                           ctypes.POINTER(_MIB_IFROW))
        rx = tx = 0
        for i in range(int(table.dwNumEntries)):
            r = rows[i]
            if int(r.dwType) == 24:          # 环回不算吞吐
                continue
            if int(r.dwOperStatus) < 4:      # 未连接的不算
                continue
            rx += int(r.dwInOctets)
            tx += int(r.dwOutOctets)
        return (rx, tx)
    except Exception:                        # noqa: BLE001
        return (0, 0)


# ------------------------------------------------------------------ 采样器
class Sampler:
    """连续采样。第一个点一定是 0（建立基准），用 `discard_first=True` 的
    `sample()` 会自动跳过它。

    `defer_disk=True`（默认）时磁盘计数器在守护线程里建，构造立即返回；
    在此之前 `sample().disk` 恒为 0.0。需要同步拿到磁盘值（自检 / 单测 /
    一次性快照）就传 `defer_disk=False`。
    """

    def __init__(self, defer_disk: bool = True):
        self._t0 = 0.0
        self._cpu0 = (0, 0, 0)
        self._net0 = (0, 0)
        self._disk: DiskCounter | None = None
        self._disk_lock = threading.Lock()
        self._disk_ready = threading.Event()
        self._closed = False
        self._primed = False
        _, used, total = memory()
        self._mem_used, self._mem_total = used, total
        if defer_disk:
            threading.Thread(target=self._build_disk, name="pdh-disk",
                             daemon=True).start()
        else:
            self._build_disk()

    def _build_disk(self) -> None:
        """（可能在子线程里）建 PDH 查询。中途被 close() 就立刻释放。"""
        try:
            dc = DiskCounter()
        except Exception:                     # noqa: BLE001
            dc = None
        with self._disk_lock:
            if self._closed:
                if dc is not None:
                    dc.close()
            else:
                self._disk = dc
        self._disk_ready.set()

    @property
    def disk_ready(self) -> bool:
        return self._disk is not None

    def wait_disk(self, timeout: float = 2.0) -> bool:
        """等磁盘计数器就绪（自检 / 单测用；UI 不要调，会把界面卡住）。"""
        return self._disk_ready.wait(timeout) and self._disk is not None

    def prime(self) -> None:
        """建立基准点。构造后立刻调用可以让第一个真实点在 1 秒内就有效。"""
        self._t0 = time.perf_counter()
        self._cpu0 = _ft()
        self._net0 = if_octets()
        self._primed = True

    def close(self) -> None:
        with self._disk_lock:
            self._closed = True
            dc, self._disk = self._disk, None
        if dc is not None:
            dc.close()

    def _read_disk(self) -> float:
        dc = self._disk
        if dc is None:
            return 0.0
        return dc.read()

    def sample(self) -> Point:
        if not self._primed:
            self.prime()
            return Point(ts=time.time())
        now = time.perf_counter()
        dt = max(1e-3, now - self._t0)
        cpu1 = _ft()
        net1 = if_octets()

        # ---- CPU：busy / total
        idle = cpu1[0] - self._cpu0[0]
        kern = cpu1[1] - self._cpu0[1]
        user = cpu1[2] - self._cpu0[2]
        total = kern + user
        cpu = 0.0 if total <= 0 else max(0.0, min(100.0, (total - idle) / total * 100.0))

        pct, used, mem_total = memory()
        dsk = self._read_disk()
        rx = max(0, net1[0] - self._net0[0]) / 1024.0 / dt
        tx = max(0, net1[1] - self._net0[1]) / 1024.0 / dt

        self._t0, self._cpu0, self._net0 = now, cpu1, net1
        return Point(ts=time.time(), cpu=cpu, mem=pct, disk=dsk,
                     net_in=rx, net_out=tx,
                     mem_used_gb=used, mem_total_gb=mem_total)


# ------------------------------------------------------------------ 概览
def snapshot() -> Point:
    """一次性快照（不建曲线），用于页面首次打开时先把 KPI 填上。

    这里要同步等磁盘计数器（否则快照里的磁盘占用恒为 0），所以显式
    `defer_disk=False` —— 调用方本来就接受一次性几百毫秒的开销。
    """
    s = Sampler(defer_disk=False)
    s.prime()
    time.sleep(0.35)
    p = s.sample()
    s.close()
    return p


def summarize(pts: list[Point]) -> dict:
    """采样结果的峰值/均值，给界面写「这段时间经历了什么」。"""
    if not pts:
        return {"n": 0, "cpu_avg": 0.0, "cpu_max": 0.0, "mem_avg": 0.0,
                "mem_max": 0.0, "disk_max": 0.0, "net_max": 0.0,
                "secs": 0.0}
    n = len(pts)
    return {
        "n": n,
        "cpu_avg": sum(p.cpu for p in pts) / n,
        "cpu_max": max(p.cpu for p in pts),
        "mem_avg": sum(p.mem for p in pts) / n,
        "mem_max": max(p.mem for p in pts),
        "disk_max": max(p.disk for p in pts),
        "net_max": max(p.net_total for p in pts),
        "secs": max(0.0, pts[-1].ts - pts[0].ts),
    }
