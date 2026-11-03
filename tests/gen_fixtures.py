# -*- coding: utf-8 -*-
"""生成合成转储文件，用于单元测试与回归。

真实环境里凑不齐「蓝屏 + 应用崩溃」两类样本，所以按微软公开的
MINIDUMP / DUMP_HEADER 结构手工构造，字段偏移与真机一致。
运行：python tests/gen_fixtures.py <输出目录>
"""
from __future__ import annotations

import os
import struct
import sys


class Builder:
    def __init__(self) -> None:
        self.buf = bytearray(32)  # 预留 MINIDUMP_HEADER

    def alloc(self, size: int) -> int:
        rva = len(self.buf)
        self.buf.extend(b"\x00" * size)
        return rva

    def put(self, rva: int, data: bytes) -> None:
        self.buf[rva:rva + len(data)] = data

    def put_u16(self, rva: int, v: int) -> None:
        self.put(rva, struct.pack("<H", v))

    def put_u32(self, rva: int, v: int) -> None:
        self.put(rva, struct.pack("<I", v))

    def put_u64(self, rva: int, v: int) -> None:
        self.put(rva, struct.pack("<Q", v))

    def put_wstr(self, rva: int, text: str) -> None:
        self.put(rva, text.encode("utf-16-le") + b"\x00\x00")


def _add_string(b: Builder, text: str) -> int:
    rva = b.alloc(len(text) * 2 + 2)
    b.put_wstr(rva, text)
    return rva


def build_minidump(path: str,
                   modules: list[tuple[str, int, int, str]],
                   exc_code: int,
                   exc_addr: int,
                   exc_params: list[int],
                   threads: int = 3,
                   build: int = 19045,
                   comment: str = "This dump file has an exception of interest stored in it.",
                   process_id: int = 4321) -> None:
    """modules: [(模块路径, 基址, 大小, 版本字符串)]"""
    b = Builder()

    si = b.alloc(0x38)
    b.put_u16(si, 9)          # AMD64
    b.put_u16(si + 2, 6)      # ProcessorLevel
    b.put_u16(si + 4, 0xA500)
    b.buf[si + 6] = 8         # 8 核
    b.buf[si + 7] = 1         # 工作站
    b.put_u32(si + 8, 10)
    b.put_u32(si + 12, 0)
    b.put_u32(si + 16, build)
    b.put_u32(si + 20, 2)
    b.put_u32(si + 24, 0)
    b.put_u16(si + 28, 0x100)
    b.put_u16(si + 30, 0)

    ml_rva = b.alloc(4 + 108 * len(modules))
    b.put_u32(ml_rva, len(modules))
    for i, (name, base, size, ver) in enumerate(modules):
        off = ml_rva + 4 + i * 108
        name_rva = _add_string(b, name)
        b.put_u64(off, base)
        b.put_u32(off + 8, size)
        b.put_u32(off + 12, 0)
        b.put_u32(off + 16, 0x5F000000)
        b.put_u32(off + 20, name_rva)
        parts = [int(x) for x in ver.split(".")] + [0, 0, 0, 0]
        b.put_u32(off + 32, (parts[0] << 16) | parts[1])
        b.put_u32(off + 36, (parts[2] << 16) | parts[3])

    ex_rva = b.alloc(168)
    b.put_u32(ex_rva, 0x1A2C)       # 故障线程
    b.put_u32(ex_rva + 8, exc_code)
    b.put_u32(ex_rva + 12, 0)
    b.put_u64(ex_rva + 16, 0)
    b.put_u64(ex_rva + 24, exc_addr)
    b.put_u32(ex_rva + 32, len(exc_params))
    b.put_u32(ex_rva + 36, 0)
    for i, p in enumerate(exc_params[:15]):
        b.put_u64(ex_rva + 40 + i * 8, p)

    tl_rva = b.alloc(4 + 48 * threads)
    b.put_u32(tl_rva, threads)
    for i in range(threads):
        off = tl_rva + 4 + i * 48
        b.put_u32(off, 0x1A2C - i * 4)
        b.put_u32(off + 4, 0)
        b.put_u32(off + 8, 0x20)
        b.put_u32(off + 12, 8)
        b.put_u64(off + 16, 0x7FF000 + i * 0x1000)
        b.put_u64(off + 24, 0x1000)   # Stack.StartOfMemoryRange
        b.put_u32(off + 32, 0x400)    # Stack.DataSize
        b.put_u32(off + 36, 0)
        b.put_u32(off + 40, 0x4D0)    # ThreadContext.DataSize

    misc = b.alloc(40)
    b.put_u32(misc, 40)               # SizeOfInfo
    b.put_u32(misc + 4, 3)            # Flags1
    b.put_u32(misc + 8, process_id)
    b.put_u32(misc + 12, 1750000000)  # ProcessCreateTime
    b.put_u32(misc + 16, 1200)
    b.put_u32(misc + 20, 800)
    b.put_u32(misc + 24, 3600)
    b.put_u32(misc + 28, 3400)
    b.put_u32(misc + 32, 3600)

    cmt_rva = _add_string(b, comment)

    # ---- 流目录
    streams = [(7, 0x38, si), (4, 4 + 108 * len(modules), ml_rva),
               (6, 168, ex_rva), (3, 4 + 48 * threads, tl_rva),
               (15, 40, misc), (11, len(comment) * 2 + 2, cmt_rva)]
    dir_rva = b.alloc(12 * len(streams))
    for i, (st, size, rva) in enumerate(streams):
        b.put_u32(dir_rva + i * 12, st)
        b.put_u32(dir_rva + i * 12 + 4, size)
        b.put_u32(dir_rva + i * 12 + 8, rva)

    b.put(0, b"MDMP")
    b.put_u32(4, 0xA793)
    b.put_u32(8, len(streams))
    b.put_u32(12, dir_rva)
    b.put_u32(16, 0)
    b.put_u32(20, 1750000000)
    b.put_u64(24, 0x0020)

    with open(path, "wb") as f:
        f.write(bytes(b.buf))


def build_kernel_dump(path: str, bugcheck: int, params: list[int],
                      is64: bool = True, size: int = 0x40000,
                      build: int = 19045) -> None:
    """真机内核转储的 MinorVersion 字段装的是 build 号（Win11 24H2 = 26100）。"""
    buf = bytearray(size)
    buf[0:4] = b"PAGE"
    buf[4:8] = b"DU64" if is64 else b"DUMP"
    struct.pack_into("<I", buf, 0x08, 15 if build >= 22000 else 10)
    struct.pack_into("<I", buf, 0x0C, build)
    if is64:
        struct.pack_into("<Q", buf, 0x10, 0x1A0000000)
        struct.pack_into("<Q", buf, 0x18, 0x1A0000000)
        struct.pack_into("<Q", buf, 0x20, 0xFFFFF80000000000)
        struct.pack_into("<Q", buf, 0x28, 0xFFFFF80000000000)
        struct.pack_into("<I", buf, 0x30, 0x8664)
        struct.pack_into("<I", buf, 0x34, 16)
        struct.pack_into("<I", buf, 0x38, bugcheck)
        for i, p in enumerate(params[:4]):
            struct.pack_into("<Q", buf, 0x40 + i * 8, p)
    else:
        struct.pack_into("<I", buf, 0x10, 0x1000000)
        struct.pack_into("<I", buf, 0x14, 0x1000000)
        struct.pack_into("<I", buf, 0x18, 0x80000000)
        struct.pack_into("<I", buf, 0x1C, 0x80000000)
        struct.pack_into("<I", buf, 0x20, 0x014C)
        struct.pack_into("<I", buf, 0x24, 4)
        struct.pack_into("<I", buf, 0x28, bugcheck)
        for i, p in enumerate(params[:4]):
            struct.pack_into("<I", buf, 0x2C + i * 4, p)
    with open(path, "wb") as f:
        f.write(bytes(buf))


def main(out_dir: str) -> None:
    os.makedirs(out_dir, exist_ok=True)
    mods = [
        (r"C:\Program Files\SH.IV\VI.UI.exe", 0x7FF6A0000000, 0x002F0000, "5.2.1.0"),
        (r"C:\Windows\System32\ntdll.dll", 0x7FFB12000000, 0x001F0000, "10.0.19041.3636"),
        (r"C:\Windows\System32\KERNELBASE.dll", 0x7FFB0F000000, 0x000C0000, "10.0.19041.3636"),
        (r"C:\Windows\System32\HALCON.dll", 0x7FFAF0000000, 0x03A00000, "20.11.0.0"),
    ]
    # 访问违例：读 0x0（典型空指针），地址落在 VI.UI.exe 内
    build_minidump(os.path.join(out_dir, "user_appcrash_av.dmp"),
                   mods, 0xC0000005, 0x7FF6A0173C58, [0, 0, 0, 0])
    # 堆破坏
    build_minidump(os.path.join(out_dir, "user_appcrash_heap.dmp"),
                   mods, 0xC0000374, 0x7FFB120A1E20, [0], threads=2)

    # 模拟真机：Win11 24H2 / Win10 22H2 / 32 位 Win10
    build_kernel_dump(os.path.join(out_dir, "kernel_whea_124.dmp"),
                      0x00000124, [0, 0xFFFFF80012345678, 0xBA000000, 0x0000000000000800],
                      build=26100)
    build_kernel_dump(os.path.join(out_dir, "kernel_dpc_watchdog_133.dmp"),
                      0x00000133, [0, 1, 0x1E00, 0], build=22631)
    build_kernel_dump(os.path.join(out_dir, "kernel_x86_7e.dmp"),
                      0x0000007E, [0xC0000005, 0x804E1A20, 0x8B2C1C00, 0x8B2C1800],
                      is64=False, size=0x20000, build=19045)
    print("fixtures ->", out_dir)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(__file__), "fixtures"))
