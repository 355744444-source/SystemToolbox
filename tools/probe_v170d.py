"""v1.7.0 探针 D：验证两个新 core 模块的真实输出。"""
from __future__ import annotations

import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

LOG = os.path.join(ROOT, "probe_v170d.log")
_lines: list[str] = []


def w(s: str = "") -> None:
    _lines.append(str(s))
    with open(LOG, "w", encoding="utf-8") as f:
        f.write("\n".join(_lines) + "\n")


def main() -> None:
    from app.core import hwevent, smart

    w("======== smart.scan ========")
    t0 = time.perf_counter()
    steps = []
    rep = smart.scan(progress=lambda d, t, m: steps.append("%d/%d %s" % (d, t, m)))
    w("耗时 %.2fs，进度 %d 步" % (time.perf_counter() - t0, len(steps)))
    w("headline = %s" % rep.headline)
    w("worst = %s  risks=%d warns=%d" % (
        rep.worst, len(rep.risks), len(rep.warns)))
    for d in rep.disks:
        w()
        w("盘：%s  [%s]  %s  序列号 %s  固件 %s" % (
            d.title, d.bus, d.size_gb, d.serial, d.firmware))
        w("  状态 %s / %s" % (d.level, d.status))
        w("  %s  %s  读 %s 写 %s  通电 %d 小时 通电次数 %d 异常掉电 %d 介质错误 %d" % (
            d.life_text, d.temp_text, d.read_text, d.write_text,
            d.log.hours if d.log else -1, d.log.cycles if d.log else -1,
            d.log.unsafe_shutdown if d.log else -1, d.log.media_err if d.log else -1))
        w("  样本 %d 条 / 跨度 %.1f 天 / 日均写入 %.1f GB / 预估 %s" % (
            d.samples, d.span_days, d.per_day_gb, d.remain_text))
        w("  记录时间 %s  boot=%s  source=%s" % (d.when_text, d.boot, d.source))
        w("  提示 %s" % d.hint)
    w()
    w("磁盘 IO 错误计数 %s（共 %d 条样本）" % (rep.io_counts, len(rep.io_rows)))
    for r in rep.io_rows[:6]:
        w("   %s id=%-3d %s  %s" % (r[0], r[1], r[2], r[3][:40]))
    if rep.note:
        w("note: %s" % rep.note)

    w()
    w("======== hwevent.scan ========")
    t0 = time.perf_counter()
    steps = []
    hr = hwevent.scan(progress=lambda d, t, m: steps.append("%d/%d %s" % (d, t, m)))
    w("耗时 %.2fs，进度 %d 步" % (time.perf_counter() - t0, len(steps)))
    w("headline = %s" % hr.headline)
    w("worst=%s fatal=%d corrected=%d total=%d io_total=%d unknown=%d" % (
        hr.worst, hr.fatal, hr.corrected, hr.total, hr.io_total, hr.unknown))
    w("info_ids = %s" % hr.info_ids)
    w("parts = %s" % hr.parts)
    for r in hr.rows:
        w()
        w("聚合行：%s / %s  共 %d 次  最近7天 %d 次  level=%s" % (
            r.sev, r.part, r.count, r.recent7, r.level))
        w("  时间 %s ~ %s" % (r.first, r.last))
        w("  提示 %s" % r.hint)
        for s in r.samples:
            w("    样本 id=%d sev=%d len=%d ok=%s %s" % (
                s.event_id, s.severity, s.length, s.ok, s.time))
    w()
    w("内存诊断：%d 条  note=%s" % (len(hr.memdiag), hr.memdiag_note))
    w("蓝屏代码：%d 条" % len(hr.bugchecks))
    for b in hr.bugchecks[:4]:
        w("   %s %s %s" % (b[0], b[1], b[2][:80]))
    if hr.note:
        w("note: %s" % hr.note)

    w()
    w("======== 边界：空串 / 坏数据 ========")
    w("parse_nvme_log('') = %s" % smart.parse_nvme_log(""))
    w("parse_nvme_log('zz') = %s" % smart.parse_nvme_log("zz"))
    w("parse_nvme_log 短串 = %s" % smart.parse_nvme_log("00" * 50))
    w("parse_cper('') = %s" % hwevent.parse_cper(""))
    w("parse_cper 非 CPER = %s" % hwevent.parse_cper("0011223344"))
    # 构造一条最小 CPER：头 0x0E 处 severity=1，0x12 处 record_length=64
    head = bytearray(64)
    head[0x0C:0x0E] = (2).to_bytes(2, "little")
    head[0:4] = b"CPER"
    head[0x10:0x14] = (1).to_bytes(4, "little")
    head[0x14:0x18] = (64).to_bytes(4, "little")
    cp = hwevent.parse_cper(head.hex(), 64)
    w("构造致命 CPER -> ok=%s sev=%s(%s) len=%d" % (
        cp.ok if cp else None, cp.severity if cp else None,
        cp.severity_text if cp else None, cp.record_length if cp else -1))
    cp2 = hwevent.parse_cper(head.hex(), 999)
    w("长度对不上 -> ok=%s（应 False，表示格式未识别）" % (cp2.ok if cp2 else None))

    w()
    w("DONE")


if __name__ == "__main__":
    main()
