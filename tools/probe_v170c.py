"""v1.7.0 探针 C：两个数据源的真实字段边界。

已知：
- Storage-Storport/Health id=512 带 NvmeHealthLog（十六进制原串）
- WHEA 通道 2000 条里 1845 条是 id=42、level=4

要确认：
1. id=512 的完整字段列表 —— 有没有 SATA 属性表字段？有没有 NVMe 之外的总线？
2. WHEA id=20 / 42 / 5 的完整 XML —— 哪些 ID 才是真错误记录
3. NvmeHealthLog 的字节布局能不能对上（对照 PowerShell 的 temp=38、wear=0）
4. 记录频率：这些健康事件多久写一次，能不能算日均写入量
"""
from __future__ import annotations

import json
import os
import struct
import sys
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

LOG = os.path.join(ROOT, "probe_v170c.log")
_lines: list[str] = []


def w(s: str = "") -> None:
    _lines.append(str(s))
    with open(LOG, "w", encoding="utf-8") as f:
        f.write("\n".join(_lines) + "\n")


def nvme_parse(hx: str) -> dict:
    """NVMe SMART / Health Information Log Page（512 字节，事件里通常 216）。"""
    d = bytes.fromhex(hx)
    if len(d) < 200:
        return {"err": "len=%d" % len(d)}
    temp_k = struct.unpack_from("<H", d, 1)[0]
    # 温度传感器 1~8，Kelvin，0 表示不可用
    sensors = [struct.unpack_from("<H", d, 200 + 2 * i)[0] for i in range(8)] \
        if len(d) >= 216 else []
    return {
        "len": len(d),
        "critical_warning": d[0],
        "temp_c": temp_k - 273 if temp_k else 0,
        "sensors_c": [s - 273 for s in sensors if s],
        "avail_spare": d[3],
        "spare_threshold": d[4],
        "percent_used": d[5],
        "read_units": int.from_bytes(d[32:48], "little"),
        "write_units": int.from_bytes(d[48:64], "little"),
        "read_gb": round(int.from_bytes(d[32:48], "little") * 512 * 1000 / 1e9, 1),
        "write_gb": round(int.from_bytes(d[48:64], "little") * 512 * 1000 / 1e9, 1),
        "hours": int.from_bytes(d[128:144], "little"),
        "cycles": int.from_bytes(d[144:160], "little"),
        "unsafe": int.from_bytes(d[160:176], "little"),
        "media_err": int.from_bytes(d[176:192], "little"),
        "err_log_entries": int.from_bytes(d[192:200], "little") if len(d) >= 200 else -1,
    }


def main() -> None:
    from app.core import evtx

    w("==== 1) id=512 健康事件的完整字段 ====")
    evs, st = evtx.query_events("Microsoft-Windows-Storage-Storport/Health", False,
                                limit=400, message_mode="none")
    w("事件总数 %d，通道 %s" % (len(evs), st.channel if hasattr(st, "channel") else "?"))
    recs = [e for e in evs if e.event_id == 512]
    w("id=512 有 %d 条，时间范围 %s ~ %s" % (
        len(recs), recs[-1].time if recs else "-", recs[0].time if recs else "-"))
    if recs:
        e0 = recs[0]
        w("字段清单：%s" % ", ".join(e0.data.keys()))
        w()
        w("按序列号分组与记录频率：")
        by_sn: dict[str, list] = {}
        for e in recs:
            by_sn.setdefault(e.data.get("SerialNumber", "?"), []).append(e)
        for sn, lst in by_sn.items():
            w("  %s  记录 %d 次  %s ~ %s" % (
                sn, len(lst), lst[-1].time, lst[0].time))
            w("    Miniport=%s Vendor=%s Product=%s FW=%s Boot=%s" % (
                lst[0].data.get("MiniportName"), lst[0].data.get("VendorId"),
                lst[0].data.get("ProductId"), lst[0].data.get("FirmwareRevision"),
                lst[0].data.get("BootDevice")))
            for e in lst[:3]:
                hx = e.data.get("NvmeHealthLog", "")
                w("    %s  len=%s warn=%s parsed=%s" % (
                    e.time, e.data.get("NvmeHealthLogLength"),
                    e.data.get("CriticalWarning"),
                    json.dumps(nvme_parse(hx), ensure_ascii=False)))
        w()
        w("原始 XML（第一条，看有没有 SATA / 其他总线字段）：")
        w((e0.raw_xml or "")[:3000])
    w()

    w("==== 2) 有没有非 NVMe 的总线记录 ====")
    buses: dict[str, int] = {}
    for e in recs:
        buses[e.data.get("MiniportName", "?")] = buses.get(
            e.data.get("MiniportName", "?"), 0) + 1
    w("MiniportName 分布：%s" % buses)
    w()

    w("==== 3) WHEA 三类事件的完整字段 ====")
    for chan in ("Microsoft-Windows-Kernel-WHEA/Operational",
                 "Microsoft-Windows-Kernel-WHEA/Errors"):
        evs, st = evtx.query_events(chan, False, limit=1500, message_mode="none")
        w()
        w("---- %s（%d 条）----" % (chan, len(evs)))
        seen = set()
        for e in evs:
            key = (e.event_id, e.level)
            if key in seen:
                continue
            seen.add(key)
            w("id=%d level=%d 时间=%s" % (e.event_id, e.level, e.time))
            w("  字段：%s" % ", ".join(e.data.keys()))
            w("  data=%s" % json.dumps(e.data, ensure_ascii=False)[:500])
            w("  xml=%s" % (e.raw_xml or "")[:1400].replace("\n", " "))
            w()

    w("==== 4) id=42 的 RawData 头部分解（判断错误源类型）====")
    evs, _ = evtx.query_events("Microsoft-Windows-Kernel-WHEA/Operational", False,
                               limit=300, message_mode="none")
    tags: dict[str, int] = {}
    for e in evs:
        if e.event_id != 42:
            continue
        hx = e.data.get("RawData", "")
        try:
            d = bytes.fromhex(hx)
        except ValueError:
            continue
        # 尝试从第 16/24 字节起找 4 字节 ASCII 标记
        tag = ""
        for off in (16, 24, 32):
            if len(d) >= off + 4:
                seg = d[off:off + 4]
                if all(32 <= b < 127 for b in seg):
                    tag = seg.decode("ascii")
                    break
        tags[tag or "(非ASCII)"] = tags.get(tag or "(非ASCII)", 0) + 1
    w("id=42 的 ASCII 标记分布：%s" % sorted(tags.items(), key=lambda x: -x[1])[:10])
    w()

    w("==== 5) 内存诊断通道 ====")
    for ch in ("Microsoft-Windows-MemoryDiagnostics-Results/Debug",
               "Microsoft-Windows-MemoryDiagnostics-Results/Operational",
               "System"):
        try:
            evs, st = evtx.query_events(ch, False, limit=200, message_mode="none")
            hit = [e for e in evs if e.event_id in (1101, 1102, 1201, 1202)
                   or "MemoryDiagnostic" in (e.provider or "")]
            w("%-52s events=%-4d 内存诊断相关=%d" % (ch, len(evs), len(hit)))
            for e in hit[:3]:
                w("   id=%d %s %s" % (e.event_id, e.time,
                                      json.dumps(e.data, ensure_ascii=False)[:300]))
        except Exception as e:      # noqa: BLE001
            w("%-52s <不可读 %s>" % (ch, str(e)[:60]))

    w()
    w("==== 6) System 日志里的 BugCheck / 磁盘错误 ID ====")
    evs, _ = evtx.query_events("System", False, limit=1500, message_mode="none")
    ids: dict[int, int] = {}
    for e in evs:
        if e.event_id in (1001, 41, 6008, 7, 11, 51, 129, 153, 157):
            ids[e.event_id] = ids.get(e.event_id, 0) + 1
    w("System 最近 %d 条中崩溃/磁盘相关：%s" % (len(evs), sorted(ids.items())))
    for e in evs:
        if e.event_id == 1001 and "bugcheck" in (e.message or "").lower()[:200]:
            w("   1001 %s %s" % (e.time, (e.message or "")[:120].replace("\n", " ")))
            break

    w()
    w("DONE")


if __name__ == "__main__":
    main()
