"""探针：4K 随机写为什么测出 5397 IOPS（U 盘不可能）。

对比三种口径：
A. FILE_FLAG_WRITE_THROUGH（当前）
B. WRITE_THROUGH + 每块 FlushFileBuffers
C. 无标志 + 全部写完再 FlushFileBuffers（一次性）
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import os
import random
import sys
import time

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "probe_flush.log")
lines: list[str] = []


def log(s: str) -> None:
    lines.append(s)


k32 = ctypes.WinDLL("kernel32", use_last_error=True)
k32.CreateFileW.restype = wt.HANDLE
k32.CreateFileW.argtypes = [wt.LPCWSTR, wt.DWORD, wt.DWORD, ctypes.c_void_p,
                            wt.DWORD, wt.DWORD, wt.HANDLE]
k32.WriteFile.argtypes = [wt.HANDLE, ctypes.c_void_p, wt.DWORD,
                          ctypes.POINTER(wt.DWORD), ctypes.c_void_p]
k32.WriteFile.restype = wt.BOOL
k32.SetFilePointerEx.argtypes = [wt.HANDLE, ctypes.c_longlong,
                                 ctypes.POINTER(ctypes.c_longlong), wt.DWORD]
k32.CloseHandle.argtypes = [wt.HANDLE]
k32.VirtualAlloc.restype = ctypes.c_void_p
k32.VirtualAlloc.argtypes = [ctypes.c_void_p, ctypes.c_size_t, wt.DWORD, wt.DWORD]
k32.VirtualFree.argtypes = [ctypes.c_void_p, ctypes.c_size_t, wt.DWORD]
k32.FlushFileBuffers.argtypes = [wt.HANDLE]
k32.FlushFileBuffers.restype = wt.BOOL

GENERIC_RW = 0xC0000000
FILE_FLAG_WRITE_THROUGH = 0x80000000
FILE_FLAG_NO_BUFFERING = 0x20000000
OPEN_EXISTING = 3
FILE_BEGIN = 0


def rand_write(path: str, size: int, count: int, block: int,
               write_through: bool, flush_each: bool,
               flush_at_end: bool) -> tuple[float, int]:
    raw = k32.VirtualAlloc(None, block + 4096, 0x3000, 0x04)
    addr = (raw + 4095) & ~4095
    ctypes.memset(addr, 0xA5, block)
    flags = FILE_FLAG_NO_BUFFERING
    if write_through:
        flags |= FILE_FLAG_WRITE_THROUGH
    h = k32.CreateFileW(path, GENERIC_RW, 0, None, OPEN_EXISTING, flags, None)
    if h == -1:
        log("  CreateFileW err=%d" % ctypes.get_last_error())
        return (0.0, 0)
    written = wt.DWORD(0)
    pos = ctypes.c_longlong(0)
    slots = max(1, size // block)
    rnd = random.Random(999)
    ok = 0
    t0 = time.perf_counter()
    try:
        for _ in range(count):
            off = rnd.randrange(slots) * block
            if not k32.SetFilePointerEx(h, off, ctypes.byref(pos), FILE_BEGIN):
                break
            if not k32.WriteFile(h, addr, block, ctypes.byref(written), None):
                break
            if flush_each:
                k32.FlushFileBuffers(h)
            ok += 1
        if flush_at_end:
            k32.FlushFileBuffers(h)
    finally:
        k32.CloseHandle(h)
        k32.VirtualFree(raw, 0, 0x8000)
    return (time.perf_counter() - t0, ok)


def main() -> None:
    root = (sys.argv[1] if len(sys.argv) > 1 else "E:").replace("/", "\\")
    root = root.rstrip("\\") + "\\"
    size = 128 << 20
    path = os.path.join(root, "_cls_flush_probe.bin")
    # 先铺一个 128MB 的文件
    with open(path, "wb") as fh:
        fh.write(b"\0" * size)
        fh.flush()
        os.fsync(fh.fileno())
    log("=== %s  target 128MB ===" % root)
    cases = [
        ("A 仅 WRITE_THROUGH（不 flush）", True, False, False, 2000),
        ("B WRITE_THROUGH + 每块 flush", True, True, False, 200),
        ("C 无标志 + 结尾 flush 一次", False, False, True, 2000),
    ]
    for name, wt_flag, fe, fae, n in cases:
        t, ok = rand_write(path, size, n, 4096, wt_flag, fe, fae)
        log("  %-30s n=%-5d %7.0f ms → %8.0f IOPS  (%.2f MB/s)"
            % (name, ok, t * 1000, ok / t if t else 0,
               ok * 4096 / t / 2**20 if t else 0))
    try:
        os.remove(path)
        log("  已删除临时文件")
    except OSError as exc:
        log("  删除失败 %s" % exc)
    with open(OUT, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
