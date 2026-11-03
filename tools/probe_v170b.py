"""v1.7.0 探针 B：把 NVMe 健康日志打通。

第一次用 PropertyId=49 得到 err=87（参数错），改完结构长度后变 err=1（函数不支持）。
真正的原因是 STORAGE_PROPERTY_ID 枚举里
  StorageAdapterProtocolSpecificProperty = 19
  StorageDeviceProtocolSpecificProperty  = 20
根本没有 49。这里把 19 / 20 × 打开方式 都试一遍，找出本机可用的组合。
"""
from __future__ import annotations

import ctypes
import json
import os
import struct
import subprocess
import sys
import time
from ctypes import wintypes as wt

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
LOG = os.path.join(ROOT, "probe_v170b.log")
_lines: list[str] = []


def w(s: str = "") -> None:
    _lines.append(str(s))
    with open(LOG, "w", encoding="utf-8") as f:
        f.write("\n".join(_lines) + "\n")


k32 = ctypes.WinDLL("kernel32", use_last_error=True)
k32.CreateFileW.restype = wt.HANDLE
k32.CreateFileW.argtypes = [wt.LPCWSTR, wt.DWORD, wt.DWORD, wt.LPVOID,
                            wt.DWORD, wt.DWORD, wt.HANDLE]
k32.DeviceIoControl.restype = wt.BOOL
k32.DeviceIoControl.argtypes = [wt.HANDLE, wt.DWORD, wt.LPVOID, wt.DWORD,
                                wt.LPVOID, wt.DWORD, ctypes.POINTER(wt.DWORD),
                                wt.LPVOID]
k32.CloseHandle.argtypes = [wt.HANDLE]

IOCTL_STORAGE_QUERY_PROPERTY = 0x2D1400
IOCTL_STORAGE_PREDICT_FAILURE = 0x2D1100


def open_drive(n: int, write: bool = False):
    acc = 0x80000000 | (0x40000000 if write else 0)
    h = k32.CreateFileW("\\\\.\\PhysicalDrive%d" % n, acc, 0x1 | 0x2, None, 3, 0, None)
    if h in (None, 0, -1) or h == wt.HANDLE(-1).value:
        return None, ctypes.get_last_error()
    return h, 0


def ioctl(h, code, in_buf, in_len, out_len):
    out = ctypes.create_string_buffer(out_len)
    ret = wt.DWORD(0)
    ok = k32.DeviceIoControl(h, code, in_buf, in_len, out, out_len,
                             ctypes.byref(ret), None)
    return bool(ok), out.raw, ret.value, ctypes.get_last_error()


def nvme_query(h, prop_id: int, off_mode: str) -> dict:
    """off_mode: 'rel' = 相对整个输出缓冲（MSDN 口径）; 'zero' = 填 0"""
    spec_len = 40
    hdr = 8
    off = (hdr + spec_len) if off_mode == "rel" else 0
    q = struct.pack("<II", prop_id, 0)
    spec = struct.pack("<IIIIIIIIII", 3, 2, 0x02, 0, off, 512, 0, 0, 0, 0)
    in_buf = q + spec
    ok, raw, n, err = ioctl(h, IOCTL_STORAGE_QUERY_PROPERTY, in_buf, len(in_buf),
                            hdr + spec_len + 512 + 128)
    if not ok:
        return {"ok": False, "err": err, "n": n}
    d = raw[off:off + 512] if off else raw[-512:]
    if len(d) < 512:
        return {"ok": False, "err": "short", "len": len(d)}
    # NVMe 全零说明驱动没回填
    if not any(d):
        return {"ok": False, "err": "all-zero"}
    temp_k = struct.unpack_from("<H", d, 1)[0]
    return {"ok": True, "crit": d[0], "temp_c": (temp_k - 273) if temp_k else 0,
            "spare": d[3], "spare_thr": d[4], "used_pct": d[5],
            "read_gb": round(int.from_bytes(d[32:48], "little") * 512 * 1000 / 1e9, 1),
            "written_gb": round(int.from_bytes(d[48:64], "little") * 512 * 1000 / 1e9, 1),
            "hours": int.from_bytes(d[128:144], "little"),
            "cycles": int.from_bytes(d[144:160], "little"),
            "unsafe_shutdown": int.from_bytes(d[160:176], "little"),
            "media_err": int.from_bytes(d[176:192], "little"),
            "head": d[:16].hex()}


def main() -> None:
    w("admin=%s" % ctypes.windll.shell32.IsUserAnAdmin())
    w()
    w("==== A) NVMe 健康日志：枚举 ID 与偏移组合 ====")
    for write in (False, True):
        h, err = open_drive(0, write)
        if h is None:
            w("打开 drive0（write=%s）失败 err=%d" % (write, err))
            continue
        for pid in (19, 20, 49, 50, 31):
            for mode in ("rel", "zero"):
                t0 = time.perf_counter()
                r = nvme_query(h, pid, mode)
                w("  write=%-5s pid=%-3d off=%-4s %4.0fms -> %s" % (
                    write, pid, mode, (time.perf_counter() - t0) * 1000,
                    json.dumps(r, ensure_ascii=False)))
                if r.get("ok"):
                    w("  ^^^ 可用组合：pid=%d off=%s write=%s" % (pid, mode, write))
        ok, raw, n, err = ioctl(h, IOCTL_STORAGE_PREDICT_FAILURE, b"", 0, 528)
        w("  PREDICT_FAILURE ok=%s err=%d" % (ok, err))
        if ok:
            fail = struct.unpack_from("<I", raw, 0)[0]
            w("    FailurePredicted=%d" % fail)
        k32.CloseHandle(h)
        break        # 只测只读打开，写打开留到下面单独测

    w()
    w("==== B) 写打开单独验证 ====")
    h, err = open_drive(0, True)
    if h is None:
        w("写打开失败 err=%d" % err)
    else:
        for pid in (19, 20):
            r = nvme_query(h, pid, "rel")
            w("  pid=%-3d -> %s" % (pid, json.dumps(r, ensure_ascii=False)))
        k32.CloseHandle(h)

    w()
    w("==== C) NVMe 温度传感器的备用路径（StorageDeviceTemperatureProperty=22）====")
    h, err = open_drive(0)
    if h:
        for pid in (22, 21, 25, 16):
            q = struct.pack("<II", pid, 0) + b"\x00" * 8
            ok, raw, n, e = ioctl(h, IOCTL_STORAGE_QUERY_PROPERTY, q, len(q), 1024)
            w("  pid=%-3d ok=%s err=%-4d len=%-4d head=%s" % (
                pid, ok, e, n, raw[:40].hex() if ok else ""))
        k32.CloseHandle(h)

    w()
    w("==== D) Storage-Storport/Health 事件字段（id=512 是不是磁盘健康）====")
    try:
        from app.core import evtx
        evs, st = evtx.query_events("Microsoft-Windows-Storage-Storport/Health",
                                    False, limit=120, message_mode="none")
        w("事件数 %d" % len(evs))
        for e in evs[:4]:
            w("  id=%d %s lvl=%d prov=%s" % (e.event_id, e.time, e.level, e.provider))
            w("    data=%s" % json.dumps(e.data, ensure_ascii=False)[:600])
            w("    msg=%s" % (e.message or "")[:200].replace("\n", " "))
    except Exception as e:      # noqa: BLE001
        w("异常 %s" % e)

    w()
    w("==== E) 磁盘相关通道可读性全景 ====")
    for ch in ("Microsoft-Windows-Storage-Storport/Health",
               "Microsoft-Windows-Storage-Storport/Operational",
               "Microsoft-Windows-StorPort/Health",
               "Microsoft-Windows-StorageSpaces-Driver/Operational",
               "Microsoft-Windows-Kernel-WHEA/Errors",
               "Microsoft-Windows-DeviceGuard/Operational"):
        try:
            evs, st = evtx.query_events(ch, False, limit=50, message_mode="none")
            ids: dict[int, int] = {}
            for e in evs:
                ids[e.event_id] = ids.get(e.event_id, 0) + 1
            w("%-52s events=%-4d ids=%s" % (ch, len(evs),
                                            sorted(ids.items(), key=lambda x: -x[1])[:5]))
        except Exception as e:      # noqa: BLE001
            w("%-52s <不可读 %s>" % (ch, str(e)[:70]))

    w()
    w("==== F) WHEA 通道全量 ID 分布（判断哪些 ID 才是真错误）====")
    for ch in ("Microsoft-Windows-Kernel-WHEA/Operational",
               "Microsoft-Windows-Kernel-WHEA/Errors"):
        try:
            evs, st = evtx.query_events(ch, False, limit=2000, message_mode="none")
            per: dict[tuple[int, int], int] = {}
            for e in evs:
                per[(e.event_id, e.level)] = per.get((e.event_id, e.level), 0) + 1
            w("%-46s total=%d" % (ch, len(evs)))
            for (eid, lvl), c in sorted(per.items(), key=lambda x: -x[1]):
                w("    id=%-4d level=%-2d count=%d" % (eid, lvl, c))
        except Exception as e:      # noqa: BLE001
            w("%-46s <不可读 %s>" % (ch, str(e)[:60]))

    w()
    w("DONE")


if __name__ == "__main__":
    main()
