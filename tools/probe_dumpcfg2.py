"""探针 v1.13-b：页面文件实测大小 + 内核内存占用的取法。"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import os

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "probe_dumpcfg2.log")
lines: list[str] = []


def log(s: str = "") -> None:
    lines.append(s)


def probe_pagefile() -> None:
    log("=== pagefile.sys 能否用 os.stat 取大小 ===")
    for p in (r"C:\pagefile.sys", r"D:\pagefile.sys"):
        try:
            st = os.stat(p)
            log("  %s  size=%.2f MB  attrib=0x%X" % (p, st.st_size / 2 ** 20, st.st_file_attributes))
        except OSError as exc:
            log("  %s  失败: %s" % (p, exc))

    log()
    log("=== GetCompressedFileSizeW ===")
    k32 = ctypes.windll.kernel32
    k32.GetCompressedFileSizeW.argtypes = [wt.LPCWSTR, ctypes.POINTER(wt.DWORD)]
    k32.GetCompressedFileSizeW.restype = wt.DWORD
    hi = wt.DWORD(0)
    lo = k32.GetCompressedFileSizeW(r"C:\pagefile.sys", ctypes.byref(hi))
    size = (hi.value << 32) | lo
    log("  C:\\pagefile.sys = %.2f MB (err=%d)" % (size / 2 ** 20, k32.GetLastError()))


class PERFORMANCE_INFORMATION(ctypes.Structure):
    _fields_ = [("cb", wt.DWORD), ("CommitTotal", ctypes.c_size_t),
                ("CommitLimit", ctypes.c_size_t), ("CommitPeak", ctypes.c_size_t),
                ("PhysicalTotal", ctypes.c_size_t), ("PhysicalAvailable", ctypes.c_size_t),
                ("SystemCache", ctypes.c_size_t), ("KernelTotal", ctypes.c_size_t),
                ("KernelPaged", ctypes.c_size_t), ("KernelNonpaged", ctypes.c_size_t),
                ("PageSize", ctypes.c_size_t), ("HandleCount", wt.DWORD),
                ("ProcessCount", wt.DWORD), ("ThreadCount", wt.DWORD)]


def probe_kernel() -> None:
    log()
    log("=== 内核内存占用（psapi.GetPerformanceInfo）===")
    pi = PERFORMANCE_INFORMATION()
    pi.cb = ctypes.sizeof(pi)
    psapi = ctypes.windll.psapi
    ok = psapi.GetPerformanceInfo(ctypes.byref(pi), pi.cb)
    log("  ok=%s page_size=%d" % (ok, pi.PageSize))
    ps = pi.PageSize
    log("  内核总计   %.1f MB" % (pi.KernelTotal * ps / 2 ** 20))
    log("  分页池     %.1f MB" % (pi.KernelPaged * ps / 2 ** 20))
    log("  非分页池   %.1f MB" % (pi.KernelNonpaged * ps / 2 ** 20))
    log("  提交总量   %.1f GB" % (pi.CommitTotal * ps / 2 ** 30))
    log("  提交上限   %.1f GB" % (pi.CommitLimit * ps / 2 ** 30))
    log("  物理内存   %.1f GB" % (pi.PhysicalTotal * ps / 2 ** 30))

    ms_mod = ctypes.Structure
    log()
    log("=== 与 GlobalMemoryStatusEx 交叉 ===")
    class M(ms_mod):
        _fields_ = [("dwLength", wt.DWORD), ("dwMemoryLoad", wt.DWORD),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
    m = M()
    m.dwLength = ctypes.sizeof(m)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
    log("  物理 %.2f GB  提交上限 %.2f GB  差值 %.2f GB"
        % (m.ullTotalPhys / 2 ** 30, m.ullTotalPageFile / 2 ** 30,
           (m.ullTotalPageFile - m.ullTotalPhys) / 2 ** 30))


def main() -> None:
    probe_pagefile()
    probe_kernel()
    with open(OUT, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
