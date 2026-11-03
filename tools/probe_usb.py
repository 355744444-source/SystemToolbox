"""探针：验证「绕过系统缓存的顺序/随机读写测速」是否可行。

关键点：
1. FILE_FLAG_NO_BUFFERING 要求 缓冲区地址、长度、文件偏移 三者都按扇区对齐，
   否则 WriteFile 直接 ERROR_INVALID_PARAMETER(87)。
2. 顺序写要配 FILE_FLAG_WRITE_THROUGH，否则数据还在缓存里就返回，测出假速度。
3. 随机 4K 要用 SetFilePointerEx 定位到 4K 对齐偏移。

只在临时文件里读写，结束即删。
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import os
import random
import sys
import time

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "probe_usb.log")
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
k32.ReadFile.argtypes = [wt.HANDLE, ctypes.c_void_p, wt.DWORD,
                         ctypes.POINTER(wt.DWORD), ctypes.c_void_p]
k32.ReadFile.restype = wt.BOOL
k32.SetFilePointerEx.argtypes = [wt.HANDLE, ctypes.c_longlong,
                                 ctypes.POINTER(ctypes.c_longlong), wt.DWORD]
k32.SetFilePointerEx.restype = wt.BOOL
k32.CloseHandle.argtypes = [wt.HANDLE]
k32.VirtualAlloc.restype = ctypes.c_void_p
k32.VirtualAlloc.argtypes = [ctypes.c_void_p, ctypes.c_size_t, wt.DWORD, wt.DWORD]
k32.VirtualFree.argtypes = [ctypes.c_void_p, ctypes.c_size_t, wt.DWORD]

GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
CREATE_ALWAYS = 2
FILE_ATTRIBUTE_TEMPORARY = 0x00000100
FILE_FLAG_WRITE_THROUGH = 0x80000000
FILE_FLAG_NO_BUFFERING = 0x20000000
FILE_BEGIN = 0
MEM_COMMIT_RESERVE = 0x3000
MEM_RELEASE = 0x8000
PAGE_READWRITE = 0x04


class Buf:
    """按 4096 对齐的裸缓冲区。"""

    def __init__(self, size: int):
        self.want = size
        self._raw = k32.VirtualAlloc(None, size + 4096, MEM_COMMIT_RESERVE,
                                     PAGE_READWRITE)
        if not self._raw:
            raise OSError("VirtualAlloc 失败")
        self.addr = (self._raw + 4095) & ~4095

    def fill(self, byte: int) -> None:
        ctypes.memset(self.addr, byte, self.want)

    def free(self) -> None:
        if self._raw:
            k32.VirtualFree(self._raw, 0, MEM_RELEASE)
            self._raw = None


def open_raw(path: str, reading: bool, write_through: bool,
             existing: bool = False) -> wt.HANDLE:
    access = (GENERIC_READ | GENERIC_WRITE) if not reading else GENERIC_READ
    flags = FILE_FLAG_NO_BUFFERING
    if write_through:
        flags |= FILE_FLAG_WRITE_THROUGH
    disp = 3 if (reading or existing) else CREATE_ALWAYS
    h = k32.CreateFileW(path, access, 0, None, disp,
                        flags | FILE_ATTRIBUTE_TEMPORARY, None)
    if h == wt.HANDLE(-1).value or h == -1:
        err = ctypes.get_last_error()
        raise OSError("CreateFileW 失败 err=%d" % err)
    return h


def seq_write(path: str, size: int, chunk: int = 4 << 20) -> float:
    buf = Buf(chunk)
    buf.fill(0x5A)
    h = open_raw(path, reading=False, write_through=True)
    done = 0
    written = wt.DWORD(0)
    t0 = time.perf_counter()
    try:
        while done < size:
            n = min(chunk, size - done)
            if not k32.WriteFile(h, buf.addr, n, ctypes.byref(written), None):
                raise OSError("WriteFile err=%d" % ctypes.get_last_error())
            done += n
    finally:
        k32.CloseHandle(h)
        buf.free()
    return time.perf_counter() - t0


def seq_read(path: str, size: int, chunk: int = 4 << 20) -> float:
    buf = Buf(chunk)
    h = open_raw(path, reading=True, write_through=False)
    got = wt.DWORD(0)
    t0 = time.perf_counter()
    total = 0
    try:
        while total < size:
            n = min(chunk, size - total)
            if not k32.ReadFile(h, buf.addr, n, ctypes.byref(got), None):
                raise OSError("ReadFile err=%d" % ctypes.get_last_error())
            if got.value == 0:
                break
            total += got.value
    finally:
        k32.CloseHandle(h)
        buf.free()
    return time.perf_counter() - t0


def rand_read(path: str, size: int, count: int = 4000,
              block: int = 4096) -> tuple[float, int]:
    """4K 随机读，返回 (耗时, 成功次数)。"""
    buf = Buf(block)
    h = open_raw(path, reading=True, write_through=False)
    got = wt.DWORD(0)
    slots = max(1, size // block)
    pos = ctypes.c_longlong(0)
    ok = 0
    rnd = random.Random(12345)
    t0 = time.perf_counter()
    try:
        for _ in range(count):
            off = rnd.randrange(slots) * block
            if not k32.SetFilePointerEx(h, off, ctypes.byref(pos), FILE_BEGIN):
                break
            if not k32.ReadFile(h, buf.addr, block, ctypes.byref(got), None):
                break
            if got.value != block:
                break
            ok += 1
    finally:
        k32.CloseHandle(h)
        buf.free()
    return time.perf_counter() - t0, ok


def rand_write(path: str, size: int, count: int = 2000,
               block: int = 4096) -> tuple[float, int]:
    buf = Buf(block)
    buf.fill(0xA5)
    # 随机写必须带 WRITE_THROUGH：否则 4K 全落在卷缓存里，
    # NVMe 上能测出 14000 IOPS 这种假数（U 盘更容易虚高 10 倍以上）。
    # 用 OPEN_EXISTING 而不是重建：截断后再随机 seek 写会混进簇分配开销。
    h = open_raw(path, reading=False, write_through=True, existing=True)
    written = wt.DWORD(0)
    slots = max(1, size // block)
    pos = ctypes.c_longlong(0)
    ok = 0
    rnd = random.Random(54321)
    t0 = time.perf_counter()
    try:
        for _ in range(count):
            off = rnd.randrange(slots) * block
            if not k32.SetFilePointerEx(h, off, ctypes.byref(pos), FILE_BEGIN):
                break
            if not k32.WriteFile(h, buf.addr, block, ctypes.byref(written), None):
                break
            if written.value != block:
                break
            ok += 1
    finally:
        k32.CloseHandle(h)
        buf.free()
    return time.perf_counter() - t0, ok


def free_space(path: str) -> int:
    free = ctypes.c_ulonglong(0)
    total = ctypes.c_ulonglong(0)
    avail = ctypes.c_ulonglong(0)
    k32.GetDiskFreeSpaceExW(ctypes.c_wchar_p(path), ctypes.byref(avail),
                            ctypes.byref(total), ctypes.byref(free))
    return free.value


def run(root: str, size_mb: int) -> None:
    size = size_mb << 20
    log("=== 测试位置 %s  文件 %d MB  剩余 %.1f GB ==="
        % (root, size_mb, free_space(root) / 2**30))
    if free_space(root) < size * 3:
        log("  剩余空间不足，跳过")
        return
    path = os.path.join(root, "_cls_usb_probe.bin")
    try:
        # 先测普通写入 vs 绕过缓存写入，看差距是否合理
        t = seq_write(path, 64 << 20)
        log("  顺序写 64MB（NO_BUFFERING+WRITE_THROUGH）: %.0f ms → %.0f MB/s"
            % (t * 1000, 64 / t))
        t = seq_write(path, size)
        log("  顺序写 %dMB                              : %.0f ms → %.0f MB/s"
            % (size_mb, t * 1000, size_mb / t))

        t = seq_read(path, size)
        log("  顺序读 %dMB                              : %.0f ms → %.0f MB/s"
            % (size_mb, t * 1000, size_mb / t))

        t, ok = rand_read(path, size, 4000)
        log("  4K 随机读 %d 次                          : %.0f ms → %.0f IOPS"
            % (ok, t * 1000, ok / t if t else 0))

        t, ok = rand_write(path, size, 2000)
        log("  4K 随机写 %d 次                          : %.0f ms → %.0f IOPS"
            % (ok, t * 1000, ok / t if t else 0))
    except OSError as exc:
        log("  失败: %s" % exc)
    finally:
        try:
            os.remove(path)
            log("  临时文件已删除: %s" % (not os.path.exists(path)))
        except OSError as exc:
            log("  删除失败: %s" % exc)


def main() -> None:
    root = sys.argv[1] if len(sys.argv) > 1 else "D:\\"
    # GetDiskFreeSpaceExW 只认 "X:\"，正斜杠会返回 0，看起来像"空间不足"
    root = root.replace("/", "\\").rstrip("\\") + "\\"
    size_mb = int(sys.argv[2]) if len(sys.argv) > 2 else 256
    run(root, size_mb)
    with open(OUT, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
