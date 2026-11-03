"""v1.7.0 前置探针：磁盘 SMART 与硬件错误聚合的事实核查。

要回答的问题（不能凭印象）：
1. 当前是不是管理员？SMART 直读需要管理员，若不是，实现里必须能优雅降级
2. NVMe 健康日志（IOCTL_STORAGE_QUERY_PROPERTY + ProtocolSpecific）在
   本机 Win11 26300 上到底能不能拿到百分百寿命与温度？
3. SATA 盘走 IOCTL_ATA_PASS_THROUGH 拿 SMART 属性表可行吗？
4. PowerShell 的 Get-StorageReliabilityCounter 能给哪些字段、耗时多少？
   如果它够用，就不必硬啃 ATA passthrough（少 200 行 ctypes）
5. WHEA 事件的 EventData 里到底有哪些字段能用来定位部件（Processor ID / Bank / APIC）
6. 内存诊断结果通道是否存在
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

LOG = os.path.join(ROOT, "probe_v170.log")
_lines: list[str] = []


def w(s: str = "") -> None:
    _lines.append(str(s))
    with open(LOG, "w", encoding="utf-8") as f:
        f.write("\n".join(_lines) + "\n")


def ps(script: str, timeout: int = 40) -> str:
    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    try:
        p = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive",
             "-ExecutionPolicy", "Bypass", "-Command", script],
            capture_output=True, timeout=timeout,
            creationflags=0x08000000, startupinfo=si)
        return (p.stdout or b"").decode("utf-8", "replace")
    except Exception as e:      # noqa: BLE001
        return f"<ERR {e}>"


k32 = ctypes.WinDLL("kernel32", use_last_error=True)
k32.CreateFileW.restype = wt.HANDLE
k32.CreateFileW.argtypes = [wt.LPCWSTR, wt.DWORD, wt.DWORD, wt.LPVOID,
                            wt.DWORD, wt.DWORD, wt.HANDLE]
k32.DeviceIoControl.restype = wt.BOOL
k32.DeviceIoControl.argtypes = [wt.HANDLE, wt.DWORD, wt.LPVOID, wt.DWORD,
                                wt.LPVOID, wt.DWORD, ctypes.POINTER(wt.DWORD),
                                wt.LPVOID]
k32.CloseHandle.argtypes = [wt.HANDLE]

GENERIC_READ = 0x80000000
FILE_SHARE_RW = 0x1 | 0x2
OPEN_EXISTING = 3

IOCTL_STORAGE_QUERY_PROPERTY = 0x2D1400
IOCTL_STORAGE_PREDICT_FAILURE = 0x2D1100
IOCTL_ATA_PASS_THROUGH = 0x4D02C

STORAGE_DEVICE_PROPERTY = 0
STORAGE_PROTOCOL_SPECIFIC_PROPERTY = 49

BUS_CN = {1: "SCSI", 2: "ATAPI", 3: "ATA", 4: "1394", 5: "SSA", 6: "光纤",
          7: "USB", 8: "RAID", 9: "iSCSI", 10: "SAS", 11: "SATA", 12: "SD",
          13: "MMC", 14: "虚拟", 15: "文件支持", 16: "Storage Spaces",
          17: "NVMe", 18: "SCM"}


def open_drive(n: int):
    h = k32.CreateFileW("\\\\.\\PhysicalDrive%d" % n, GENERIC_READ,
                        FILE_SHARE_RW, None, OPEN_EXISTING, 0, None)
    if h == wt.HANDLE(-1).value or h in (None, 0, -1):
        return None, ctypes.get_last_error()
    return h, 0


def close(h) -> None:
    try:
        k32.CloseHandle(h)
    except Exception:      # noqa: BLE001
        pass


def ioctl(h, code: int, in_buf, in_len: int, out_len: int):
    out = ctypes.create_string_buffer(out_len)
    ret = wt.DWORD(0)
    ok = k32.DeviceIoControl(h, code, in_buf, in_len, out, out_len,
                             ctypes.byref(ret), None)
    return (bool(ok), out.raw, ret.value, ctypes.get_last_error())


def _cstr(buf: bytes, off: int) -> str:
    if not off or off >= len(buf):
        return ""
    end = buf.find(b"\x00", off)
    if end < 0:
        end = len(buf)
    return buf[off:end].decode("mbcs", "replace").strip()


def device_desc(h) -> dict:
    """STORAGE_DEVICE_DESCRIPTOR：厂商 / 型号 / 序列号 / 总线类型。"""
    q = struct.pack("<II", STORAGE_DEVICE_PROPERTY, 0) + b"\x00" * 8
    ok, raw, n, err = ioctl(h, IOCTL_STORAGE_QUERY_PROPERTY, q, len(q), 1024)
    if not ok or len(raw) < 32:
        return {"ok": False, "err": err}
    ver, size, dtype, dmod, rem, cq = struct.unpack_from("<IIBBBB", raw, 0)
    (vid, pid, rev, sn, bus, rawlen) = struct.unpack_from("<IIIIII", raw, 12)
    return {"ok": True, "version": ver, "size": size, "bus": bus,
            "bus_cn": BUS_CN.get(bus, str(bus)),
            "vendor": _cstr(raw, vid), "product": _cstr(raw, pid),
            "rev": _cstr(raw, rev), "serial": _cstr(raw, sn)}


# ---- NVMe 健康日志
NVME_HEALTH_LOG_ID = 0x02
NVME_PROTOCOL_TYPE = 3
NVME_DATA_TYPE_LOG_PAGE = 2


def nvme_health(h) -> dict:
    """STORAGE_PROTOCOL_SPECIFIC_DATA + NVMe Get Log Page(SMART/Health)。

    结构偏移是以整个输出缓冲区（从 STORAGE_PROPERTY_QUERY 起）为基准的，
    这是本 IOCTL 最容易写错的地方：ProtocolDataOffset 不是相对 spec data 自己。
    """
    spec_len = 40                # 10 个 DWORD，少写一个就变成 36 → 偏移对不上 → err 87
    hdr = 8                      # STORAGE_PROPERTY_QUERY
    off = hdr + spec_len
    q = struct.pack("<II", STORAGE_PROTOCOL_SPECIFIC_PROPERTY, 0)
    spec = struct.pack("<IIIIIIIIII", NVME_PROTOCOL_TYPE, NVME_DATA_TYPE_LOG_PAGE,
                       NVME_HEALTH_LOG_ID, 0, off, 512, 0, 0, 0, 0)
    assert len(spec) == spec_len, (len(spec), spec_len)
    in_buf = q + spec
    ok, raw, n, err = ioctl(h, IOCTL_STORAGE_QUERY_PROPERTY, in_buf,
                            len(in_buf), off + 512 + 64)
    if not ok:
        return {"ok": False, "err": err, "n": n}
    if len(raw) < off + 512:
        return {"ok": False, "err": "short", "n": n, "len": len(raw)}
    d = raw[off:off + 512]
    crit = d[0]
    temp_k = struct.unpack_from("<H", d, 1)[0]
    spare = d[3]
    spare_thr = d[4]
    used = d[5]
    units_read = int.from_bytes(d[32:48], "little")
    units_written = int.from_bytes(d[48:64], "little")
    power_hours = int.from_bytes(d[128:144], "little")
    power_cycles = int.from_bytes(d[144:160], "little")
    unsafe = int.from_bytes(d[160:176], "little")
    media_err = int.from_bytes(d[176:192], "little")
    return {"ok": True, "crit": crit, "temp_c": temp_k - 273 if temp_k else 0,
            "spare": spare, "spare_thr": spare_thr, "used_pct": used,
            "read_gb": units_read * 512 * 1000 / 1e9,
            "written_gb": units_written * 512 * 1000 / 1e9,
            "hours": power_hours, "cycles": power_cycles,
            "unsafe_shutdown": unsafe, "media_err": media_err}


# ---- SATA SMART（ATA PASS THROUGH）
SMART_READ_DATA = 0xD0
SMART_CYL_LOW = 0x4F
SMART_CYL_HI = 0xC2


def sata_smart(h) -> dict:
    """IOCTL_ATA_PASS_THROUGH + SMART READ DATA，拿 30 个属性的原始值。"""
    class ATA_PT(ctypes.Structure):
        _pack_ = 1
        _fields_ = [("Length", wt.USHORT), ("AtaFlags", wt.USHORT),
                    ("PathId", wt.UCHAR), ("TargetId", wt.UCHAR),
                    ("Lun", wt.UCHAR), ("ReservedAsUchar", wt.UCHAR),
                    ("DataTransferLength", wt.ULONG),
                    ("TimeOutValue", wt.ULONG), ("ReservedAsUlong", wt.ULONG),
                    ("DataBufferOffset", ctypes.c_void_p),
                    ("PreviousTaskFile", wt.UCHAR * 8),
                    ("CurrentTaskFile", wt.UCHAR * 8)]

    class ATA_PT_WB(ctypes.Structure):
        _pack_ = 1
        _fields_ = [("apt", ATA_PT), ("data", wt.UCHAR * 512)]

    req = ATA_PT_WB()
    req.apt.Length = ctypes.sizeof(ATA_PT)
    req.apt.AtaFlags = 0x02 | 0x04           # 数据入 / 走 DRQ
    req.apt.DataTransferLength = 512
    req.apt.TimeOutValue = 10
    addr = ctypes.addressof(req) + ATA_PT.data.offset
    req.apt.DataBufferOffset = ctypes.c_void_p(addr)
    tf = req.apt.CurrentTaskFile
    tf[0] = 0x00
    tf[1] = 0x01
    tf[2] = 0x01
    tf[3] = 0x00
    tf[4] = SMART_CYL_LOW
    tf[5] = SMART_CYL_HI
    tf[6] = 0x00
    tf[7] = 0xA0
    ret = wt.DWORD(0)
    ok = k32.DeviceIoControl(h, IOCTL_ATA_PASS_THROUGH, ctypes.byref(req),
                             ctypes.sizeof(ATA_PT), ctypes.byref(req),
                             ctypes.sizeof(ATA_PT_WB), ctypes.byref(ret), None)
    if not ok:
        return {"ok": False, "err": ctypes.get_last_error()}
    data = bytes(req.data)
    if data[0] != 0x00 or data[1] != 0x5A:
        return {"ok": False, "err": "magic", "b0": data[0], "b1": data[1]}
    attrs = {}
    for i in range(30):
        o = 2 + i * 12
        aid = data[o]
        if aid == 0:
            continue
        attrs[aid] = {"value": data[o + 3], "worst": data[o + 4],
                      "raw": int.from_bytes(data[o + 5:o + 11], "little")}
    return {"ok": True, "attrs": attrs}


def main() -> None:
    try:
        admin = bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception as e:      # noqa: BLE001
        admin = f"<ERR {e}>"
    w("admin = %s" % admin)
    w("python = %s" % sys.version.split()[0])
    w()

    w("==== 1) 物理磁盘枚举 + SMART 直读 ====")
    seen = 0
    for n in range(8):
        h, err = open_drive(n)
        if h is None:
            w("drive%-2d 打不开 err=%d（%s）" % (
                n, err, {5: "ACCESS_DENIED 需要管理员", 2: "不存在"}.get(err, "")))
            continue
        seen += 1
        dd = device_desc(h)
        w("drive%-2d bus=%-8s vendor=%-8s product=%-28s serial=%s" % (
            n, dd.get("bus_cn"), dd.get("vendor", "")[:8],
            dd.get("product", "")[:28], dd.get("serial", "")[:20]))
        if dd.get("bus") == 17:
            t0 = time.perf_counter()
            nv = nvme_health(h)
            w("   NVMe 健康 %.0fms -> %s" % ((time.perf_counter() - t0) * 1000,
                                             json.dumps(nv, ensure_ascii=False)))
        elif dd.get("bus") in (3, 11):
            t0 = time.perf_counter()
            sm = sata_smart(h)
            if sm.get("ok"):
                a = sm["attrs"]
                w("   SATA SMART %.0fms 属性 %d 个：5(重映射)=%s 9(通电时)=%s "
                  "194(温度)=%s 197(待映射)=%s 199(CRC)=%s 231(寿命)=%s" % (
                      (time.perf_counter() - t0) * 1000, len(a),
                      a.get(5), a.get(9), a.get(194), a.get(197),
                      a.get(199), a.get(231)))
            else:
                w("   SATA SMART 失败：%s" % json.dumps(sm, ensure_ascii=False))
        ok, raw, cnt, err = ioctl(h, IOCTL_STORAGE_PREDICT_FAILURE, b"", 0, 528)
        w("   预测故障 ioctl ok=%s err=%d datalen=%d" % (ok, err, len(raw)))
        close(h)
    w("可打开磁盘数 = %d" % seen)
    w()

    w("==== 2) PowerShell 可靠性计数器（对比口径与耗时）====")
    t0 = time.perf_counter()
    out = ps("[Console]::OutputEncoding=[Text.Encoding]::UTF8;"
             "Get-PhysicalDisk | ForEach-Object { $d=$_; "
             "$r=$d | Get-StorageReliabilityCounter -ErrorAction SilentlyContinue; "
             "[pscustomobject]@{name=$d.FriendlyName; media=$d.MediaType; "
             "bus=$d.BusType; health=$d.HealthStatus; wear=$r.Wear; "
             "temp=$r.Temperature; hours=$r.PowerOnHours; "
             "readErr=$r.ReadErrorsTotal; writeErr=$r.WriteErrorsTotal; "
             "readUncorr=$r.ReadErrorsUncorrected; "
             "writeUncorr=$r.WriteErrorsUncorrected } } | ConvertTo-Json -Compress")
    w("耗时 %.0fms" % ((time.perf_counter() - t0) * 1000))
    w(out.strip()[:1600])
    w()

    w("==== 3) WMI 补充（MSStorageDriver_FailurePredictStatus）====")
    out = ps("[Console]::OutputEncoding=[Text.Encoding]::UTF8;"
             "Get-CimInstance -Namespace root\\wmi -ClassName "
             "MSStorageDriver_FailurePredictStatus -ErrorAction SilentlyContinue | "
             "Select-Object InstanceName,PredictFailure,Reason | ConvertTo-Json -Compress")
    w(out.strip()[:800] or "<空>")
    w()

    w("==== 4) WHEA 事件真实字段 ====")
    try:
        from app.core import evtx
        chans = ["Microsoft-Windows-Kernel-WHEA/Operational",
                 "Microsoft-Windows-Kernel-WHEA/Errors",
                 "System"]
        for ch in chans:
            t0 = time.perf_counter()
            evs, st = evtx.query_events(ch, False, limit=400, message_mode="none")
            ms = (time.perf_counter() - t0) * 1000
            whea = [e for e in evs if "WHEA" in (e.provider or "")
                    or "WHEA" in (e.channel or "")]
            ids = {}
            for e in whea:
                ids[e.event_id] = ids.get(e.event_id, 0) + 1
            w("%-46s events=%d whea=%d ids=%s %.0fms" % (
                ch, len(evs), len(whea), sorted(ids.items(), key=lambda x: -x[1])[:6], ms))
            for e in whea[:6]:
                w("   id=%-4d %s level=%d" % (e.event_id, e.time, e.level))
                w("      data=%s" % json.dumps(e.data, ensure_ascii=False)[:700])
                w("      msg=%s" % (e.message or "")[:160].replace("\n", " "))
            if whea:
                break
        else:
            w("三个通道都没有 WHEA 事件")
    except Exception as e:      # noqa: BLE001
        import traceback
        w("WHEA 读取异常：%s" % e)
        w(traceback.format_exc()[:1200])
    w()

    w("==== 5) 内存诊断 / StorPort / 磁盘通道 ====")
    for ch in ("Microsoft-Windows-MemoryDiagnostics-Results/Debug",
               "Microsoft-Windows-StorPort/Operational",
               "Microsoft-Windows-Storage-Storport/Health",
               "Microsoft-Windows-Kernel-Power/Thermal-Operational"):
        try:
            from app.core import evtx as _e
            evs, st = _e.query_events(ch, False, limit=60, message_mode="none")
            ids: dict[int, int] = {}
            for e in evs:
                ids[e.event_id] = ids.get(e.event_id, 0) + 1
            w("%-52s events=%-3d ids=%s" % (ch, len(evs),
                                            sorted(ids.items(), key=lambda x: -x[1])[:5]))
        except Exception as e:      # noqa: BLE001
            w("%-52s <不可读 %s>" % (ch, str(e)[:60]))
    w()

    w("==== 6) 内存诊断结果（mdsched 历史）====")
    out = ps("[Console]::OutputEncoding=[Text.Encoding]::UTF8;"
             "Get-CimInstance -ClassName Win32_ComputerSystem | "
             "Select-Object TotalPhysicalMemory | ConvertTo-Json -Compress")
    w(out.strip()[:300])
    try:
        from app.core import sysinfo
        info = sysinfo.collect() if hasattr(sysinfo, "collect") else None
        w("sysinfo.collect -> %s" % (str(info)[:200] if info else "无 collect"))
    except Exception as e:      # noqa: BLE001
        w("sysinfo 异常 %s" % e)

    w()
    w("DONE")


if __name__ == "__main__":
    main()
