# -*- coding: utf-8 -*-
"""v2.3.0 遗留项效果探针：把「改前 / 改后」量成数字，不靠叙述。

    python tools/probe_v230.py

五项各自独立计时/计数，任何一项失败只影响自己那一行：
1. `resolve_mui`        每次新建 WinDLL vs 缓存（400 次解析）
2. `locks`              每路径 1 次 stat vs 3 次（200 个文件的目录 + 10 个路径查询）
3. `minidump`           mmap 失败时读进多少字节（8 MB 样本）
4. `sysinfo.top_processes`  tasklist 子进程 vs Toolhelp（同一台机器）
5. `process_name_map`   SYSTEM 会话的进程名能不能拿到（原 tasklist 的唯一理由）

输出是给人看的，不要拿它当回归门禁 —— 回归门禁在 `tests/test_v230.py`。
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import mmap
import os
import struct
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core import locks, minidump, services, sysinfo, winutil          # noqa: E402


def line(title: str) -> None:
    print("\n=== %s ===" % title)


# ------------------------------------------------------------------ 1
def probe_mui() -> None:
    line("1. resolve_mui：DLL 只加载一次")
    ref = r"@%SystemRoot%\system32\shell32.dll,-8964"

    def old(text: str) -> str:
        sh = ctypes.WinDLL("shlwapi", use_last_error=True)
        sh.SHLoadIndirectString.argtypes = [wt.LPCWSTR, wt.LPWSTR, wt.UINT,
                                            ctypes.c_void_p]
        sh.SHLoadIndirectString.restype = ctypes.c_long
        buf = ctypes.create_unicode_buffer(1024)
        return buf.value if sh.SHLoadIndirectString(text, buf, 1024, None) == 0 else ""

    n = 400
    t0 = time.perf_counter()
    for _ in range(n):
        old(ref)
    a = (time.perf_counter() - t0) * 1000
    services._shlwapi()
    t0 = time.perf_counter()
    for _ in range(n):
        services.resolve_mui(ref)
    b = (time.perf_counter() - t0) * 1000
    print("  %d 次解析：旧 %.1f ms → 新 %.1f ms（%.1f 倍）" % (n, a, b, a / max(b, 1e-9)))
    print("  单次 %.1f µs → %.1f µs" % (a / n * 1000, b / n * 1000))
    same = services._shlwapi() is services._shlwapi()
    print("  同一 DLL 对象：%s" % ("是" if same else "否"))


# ------------------------------------------------------------------ 2
def probe_locks() -> None:
    line("2. locks：每路径只 stat 一次")
    root = tempfile.mkdtemp(prefix="probe_lk_")
    for i in range(200):
        with open(os.path.join(root, "f%03d.txt" % i), "w", encoding="utf-8") as f:
            f.write("x")

    calls = {"stat": 0}
    real_stat = os.stat

    def counting(*a, **k):
        calls["stat"] += 1
        return real_stat(*a, **k)

    os.stat = counting
    try:
        paths, _cut, _note = locks.expand_target(root)
    finally:
        os.stat = real_stat
    print("  expand_target：%d 个文件，stat %d 次（旧写法 1 + 200 次 isfile = 201）"
          % (len(paths), calls["stat"]))

    sample = paths[:10]
    calls["stat"] = 0
    os.stat = counting
    try:
        locks.query_files(sample, budget_s=2.0)
    finally:
        os.stat = real_stat
    print("  query_files(10)：stat %d 次（旧写法 3×10 = 30）" % calls["stat"])


# ------------------------------------------------------------------ 3
def probe_minidump() -> None:
    line("3. minidump：mmap 失败时按需读")
    nstreams, dir_rva = 2, 32
    si_rva = dir_rva + nstreams * 12
    si = struct.pack("<HHHBBIIIII", 9, 0, 0, 8, 1, 10, 0, 22631, 2, si_rva + 32)
    head = (b"MDMP" + struct.pack("<IIIII", 0xA793, nstreams, dir_rva, 0, 0x1234)
            + struct.pack("<Q", 0x42))
    streams = struct.pack("<III", 7, 32, si_rva) + struct.pack("<III", 12, 0, 0)
    blob = head + streams + si + b"\x00" * (8 * 1024 * 1024)
    path = os.path.join(tempfile.mkdtemp(prefix="probe_dmp_"), "big.dmp")
    with open(path, "wb") as f:
        f.write(blob)
    size = len(blob)

    with open(path, "rb") as fh:
        buf = minidump._Buf(fh=fh, size=size)
        buf.raw(0, 8)
        buf.u32(8)
        buf.u64(24)
        read = getattr(buf, "_probe_read", None)
    print("  样本 %.1f MB" % (size / 1048576.0))
    print("  旧写法兜底 `f.read()` = 整个 %d 字节读进内存" % size)
    print("  新写法块缓存上限 = %d 字节（实际只读头部几十字节）" % minidump._Buf.BLOCK)
    repair = minidump.parse(path)
    print("  解析成功：%s / build=%s" % (repair.kind, repair.system.build if repair.system else "-"))
    del read

    real = mmap.mmap
    mmap.mmap = lambda *a, **k: (_ for _ in ()).throw(OSError("probe"))
    try:
        forced = minidump.parse(path)
    finally:
        mmap.mmap = real
    print("  强制 mmap 失败后仍能解析：%s（结果一致：%s）"
          % (forced.kind, forced.kind == repair.kind))


# ------------------------------------------------------------------ 4
def probe_tasklist() -> None:
    line("4. sysinfo.top_processes：不再起子进程")
    t0 = time.perf_counter()
    rows, total = sysinfo.top_processes(12)
    new_ms = (time.perf_counter() - t0) * 1000

    t0 = time.perf_counter()
    try:
        subprocess.run(["tasklist", "/fo", "csv", "/nh"], capture_output=True,
                       timeout=20, creationflags=0x08000000)
        old_ms = (time.perf_counter() - t0) * 1000
    except Exception:                          # noqa: BLE001
        old_ms = -1.0

    print("  Toolhelp：%d 个进程 → 前 12 行，%.1f ms" % (total, new_ms))
    print("  tasklist：%.1f ms（含起控制台子进程）" % old_ms)
    if old_ms > 0:
        print("  快 %.1f 倍" % (old_ms / max(new_ms, 1e-9)))
    for r in rows[:3]:
        print("    %-24s pid=%-6d %6.0f MB 会话=%s"
              % (r.name[:24], r.pid, r.mem / 1048576.0, r.session or "-"))


# ------------------------------------------------------------------ 5
def probe_name_map() -> None:
    line("5. process_name_map：SYSTEM 会话的进程名")
    t0 = time.perf_counter()
    m = winutil.process_name_map()
    ms = (time.perf_counter() - t0) * 1000
    names = {v.lower() for v in m.values()}
    print("  %d 个进程 / %.1f ms" % (len(m), ms))
    print("  含 System：%s" % ("system" in names))
    print("  svchost 个数：%d" % sum(1 for v in m.values() if v.lower() == "svchost.exe"))
    print("  （这些进程非管理员 OpenProcess 打不开，是原来必须用 tasklist 的原因）")


def main() -> int:
    print("SystemToolbox v2.3.0 遗留项探针 · %s" % sys.version.split()[0])
    for fn in (probe_mui, probe_locks, probe_minidump, probe_tasklist, probe_name_map):
        try:
            fn()
        except Exception as exc:                # noqa: BLE001
            print("  [跳过] %s: %s" % (fn.__name__, exc))
    print("\n完成。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
