"""v1.7.0 探针 E：用真实 CPER 数据反推头部偏移。

手算字节切分已经错了一次（severity 读成 3、record_length 对不上）。
这里不再猜：直接扫真实记录，找出「哪个偏移的 uint32 等于事件声明的 Length」，
以及每个 uint32 位置的取值范围，用数据说话。
"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

LOG = os.path.join(ROOT, "probe_v170e.log")
_lines: list[str] = []


def w(s: str = "") -> None:
    _lines.append(str(s))
    with open(LOG, "w", encoding="utf-8") as f:
        f.write("\n".join(_lines) + "\n")


def main() -> None:
    from app.core import evtx

    evs, st = evtx.query_events("Microsoft-Windows-Kernel-WHEA/Errors", False,
                                limit=60, message_mode="none")
    w("事件 %d 条" % len(evs))
    w()

    # 取一条最新记录做详细解剖
    e = evs[0]
    declared = int(e.data.get("Length") or 0)
    raw = e.data.get("RawData") or ""
    d = bytes.fromhex(raw)
    w("事件 id=%d time=%s declared Length=%d 实际字节数=%d" % (
        e.event_id, e.time, declared, len(d)))
    w("前 80 字节：")
    for i in range(0, 80, 16):
        seg = d[i:i + 16]
        w("  %04X  %-47s  %s" % (i, " ".join("%02X" % b for b in seg),
                                 "".join(chr(b) if 32 <= b < 127 else "." for b in seg)))
    w()

    w("-- 扫描 uint32：哪个偏移等于 declared Length（%d）--" % declared)
    for off in range(0, 48):
        v = int.from_bytes(d[off:off + 4], "little")
        if v == declared:
            w("   uint32 @ 0x%02X = %d  <== 命中" % (off, v))
    w()
    w("-- 扫描 uint16：哪个偏移等于 declared Length --")
    for off in range(0, 48):
        v = int.from_bytes(d[off:off + 2], "little")
        if v == declared:
            w("   uint16 @ 0x%02X = %d  <== 命中" % (off, v))
    w()

    w("-- 0x00~0x28 每个偏移的 uint32 值（找 0~3 的 severity 候选）--")
    for off in range(0, 0x28):
        v = int.from_bytes(d[off:off + 4], "little")
        mark = ""
        if v <= 3:
            mark = "  <<< 可能是 severity"
        elif v == declared:
            mark = "  <<< 可能是 record_length"
        w("   0x%02X = %-12d%s" % (off, v, mark))
    w()

    w("-- 全量统计：各记录里「等于 declared 的 uint32 偏移」分布 --")
    hits: dict[int, int] = {}
    sev_hits: dict[int, list[int]] = {}
    for e in evs:
        decl = int(e.data.get("Length") or 0)
        try:
            dd = bytes.fromhex(e.data.get("RawData") or "")
        except ValueError:
            continue
        if not dd or decl <= 0:
            continue
        for off in range(0, 0x20):
            if int.from_bytes(dd[off:off + 4], "little") == decl:
                hits[off] = hits.get(off, 0) + 1
        for off in range(0, 0x20):
            v = int.from_bytes(dd[off:off + 4], "little")
            if v <= 3:
                sev_hits.setdefault(off, []).append(v)
    w("   record_length 命中偏移：%s" % dict(sorted(hits.items())))
    w("   severity 候选偏移与取值：")
    for off, vals in sorted(sev_hits.items()):
        uniq: dict[int, int] = {}
        for v in vals:
            uniq[v] = uniq.get(v, 0) + 1
        w("     0x%02X -> %s" % (off, uniq))
    w()

    w("-- 交叉验证：这些记录里 Section GUID 命中情况 --")
    from app.core import hwevent
    for g, cn in hwevent.SECTION_GUIDS.items():
        cnt = sum(1 for e in evs if g in (e.data.get("RawData") or "").lower())
        if cnt:
            w("   %-34s %s -> %d/%d 条" % (cn, g, cnt, len(evs)))
    w()

    w("-- 这些记录的 level / Keywords（判断系统自己怎么定性）--")
    lv: dict[int, int] = {}
    kw: dict[str, int] = {}
    for e in evs:
        lv[e.level] = lv.get(e.level, 0) + 1
        x = (e.raw_xml or "")
        i = x.find("<Keywords>")
        k = x[i + 10:x.find("</Keywords>", i)] if i >= 0 else "?"
        kw[k] = kw.get(k, 0) + 1
    w("   level 分布：%s" % lv)
    w("   Keywords 分布：%s" % kw)

    w()
    w("-- 一条完整 header 视图（前 0xA0 字节）--")
    for i in range(0, min(0xA0, len(d)), 16):
        seg = d[i:i + 16]
        w("  %04X  %-47s  %s" % (i, " ".join("%02X" % b for b in seg),
                                 "".join(chr(b) if 32 <= b < 127 else "." for b in seg)))

    w()
    w("DONE")


if __name__ == "__main__":
    main()
