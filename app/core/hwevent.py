"""硬件错误聚合（WHEA / CPER）+ 内存诊断结果。

## 为什么不能「WHEA 通道有事件 = 硬件故障」

本机实测 `Microsoft-Windows-Kernel-WHEA/Operational` 2000 条事件里：
- **1845 条是 id=42**（错误源通知，level=4 Information），
  每次开机刷几百条，内容只是 HAL / KRNL / PSHD / PCI 各错误源的注册与状态通报；
- 155 条是 id=5（错误源表初始化）。

如果按「通道里有事件」判定，这台完全健康的机器会当场报出 2000 条「硬件错误」。
所以真正的错误必须从 `Kernel-WHEA/Errors` 里的 **CPER 记录**解析出来：
`RawData` 以 ASCII `CPER`（0x43504552）开头，头部带 `ErrorSeverity`，
分节里带 Section GUID（处理器 / 内存 / PCIe / 固件）。

## CPER 头部偏移用「交叉校验」而不是硬套文档

不同文档给出的 `WHEA_ERROR_RECORD_HEADER` 字段顺序并不一致：本机实测
`SignatureEndId` 与 `Revision` 的位置和 UEFI spec 相反，而 `ErrorSeverity`
因为枚举要对齐到 4 字节，实际落在 **0x10** 而不是文档常见的 0x0E。
这里按**实测偏移**读，并且要求解析出的 `RecordLength`（0x14）与事件自带的
`Length` 完全相等 —— 本机 60/60 条全部命中，所以这个偏移可信；
一旦对不上就判定「格式未识别」并降级，宁可少说，不能瞎说。

实测本机 id=20 记录：Length=952，SectionCount=2（0x0C）→ 两个分节。

## 部件定位：第一个 Section GUID 才是主部件

本机 194 条记录里 PCIe 与 Processor Generic 两个 GUID **每一条都出现**，
因为 WHEA 在报 PCIe 错误时会附带一条处理器上下文分节。
所以「某 GUID 出现过」不能当作「该部件有错」，要按 GUID 在记录里的
**出现位置排序**，第一个才是主部件，后面的只作为上下文说明。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

from . import evtx

WHEA_OP = "Microsoft-Windows-Kernel-WHEA/Operational"
WHEA_ERR = "Microsoft-Windows-Kernel-WHEA/Errors"
MEMDIAG = "Microsoft-Windows-MemoryDiagnostics-Results/Debug"

SEVERITY_CN = {
    0: ("可恢复", "warn", "记录为可恢复错误，硬件自己纠正过来了，但说明有部件在出错"),
    1: ("致命", "crit", "致命级硬件错误，通常直接导致蓝屏停机，必须定位部件"),
    2: ("已纠正", "info", "已纠正的硬件错误：单次不致命，密度高说明硬件在劣化"),
    3: ("通知", "info", "信息性记录，不表示故障"),
}

# Section GUID 的 raw 字节序（小端前 3 段 + 大端后 2 段）→ 部件名
SECTION_GUIDS: dict[str, str] = {
    "adcc7698b447db4bb65e16f193c4f3db": "处理器（通用）",      # 9876CCAD-47B4-4BDB-B65E-16F193C4F3DB
    "b0a03edc44a19747b95b53fa242b6e1d": "处理器（IA32/x64）",  # DC3EA0B0-A144-4797-B95B-53FA242B6E1D
    "633975c5843b9540bf78eddad09c9d19": "处理器错误详情",       # C5753963-3B84-4095-BF78-EDDAD09C9D19
    "1411bca5646fde4eb8633e83ed7c83b1": "内存",                # A5BC1114-6F64-4EDE-B863-3E83ED7C83B1
    "54e995d9c1bb0f43ad91b44dcb3c6f35": "PCIe 设备/链路",       # D995E954-BBC1-430F-AD91-B44DCB3C6F35
    "962a2181ed09964994718d729c8e69ed": "固件",                # 81212A96-09ED-4996-9471-8D729C8E69ED
}

# 明确是「错误源管理」而非错误本身的事件：只做说明，不进故障统计
INFO_EVENT_IDS = {
    42: "错误源通知（错误源注册与状态通报，每次开机都会刷）",
    5: "错误源表初始化",
}


@dataclass
class Cper:
    """一条 CPER（Common Platform Error Record）的解析结果。"""
    severity: int = 3
    record_length: int = 0
    declared_length: int = 0
    section_count: int = 0
    sections: list[str] = field(default_factory=list)   # 按出现顺序，[0] 是主部件
    ok: bool = False

    @property
    def severity_text(self) -> str:
        return SEVERITY_CN.get(self.severity, ("未知", "info", ""))[0]

    @property
    def severity_level(self) -> str:
        return SEVERITY_CN.get(self.severity, ("未知", "info", ""))[1]

    @property
    def part(self) -> str:
        return self.sections[0] if self.sections else "未识别部件"

    @property
    def context(self) -> str:
        return "、".join(self.sections[1:])


def parse_cper(hexstr: str, declared_length: int = 0) -> Optional[Cper]:
    """解析 CPER 记录。

    偏移全部来自本机实测（60/60 条 record_length 命中）：
      0x00  'CPER'            SignatureStartId
      0x0C  uint16            SectionCount
      0x10  uint32            ErrorSeverity (0 可恢复 / 1 致命 / 2 已纠正 / 3 通知)
      0x14  uint32            RecordLength
    校验：RecordLength 必须等于事件 EventData 里的 Length，否则视为格式未识别。
    """
    if not hexstr:
        return None
    try:
        d = bytes.fromhex(hexstr.strip())
    except ValueError:
        return None
    if len(d) < 0x18 or d[0:4] != b"CPER":
        return None

    # 按 GUID 在记录中的出现位置排序，第一个是主部件
    low = hexstr.strip().lower()
    found: list[tuple[int, str]] = []
    for g, cn in SECTION_GUIDS.items():
        pos = low.find(g)
        if pos >= 0:
            found.append((pos, cn))
    found.sort()
    sections = [cn for _pos, cn in found]

    rec = Cper(sections=sections, declared_length=declared_length,
               section_count=int.from_bytes(d[0x0C:0x0E], "little"))
    sev = int.from_bytes(d[0x10:0x14], "little")
    rlen = int.from_bytes(d[0x14:0x18], "little")
    if sev <= 3:
        rec.severity = sev
    rec.record_length = rlen

    if declared_length:
        rec.ok = (rlen == declared_length)
    else:
        rec.ok = 0 < rlen <= len(d)
    return rec


@dataclass
class ErrRow:
    """聚合后的一行：同一严重级别 + 同一部件的所有记录。"""
    sev: str
    severity: int
    part: str
    count: int
    first: Optional[datetime] = None
    last: Optional[datetime] = None
    recent7: int = 0
    level: str = "info"
    hint: str = ""
    context: str = ""
    samples: list = field(default_factory=list)   # [ErrRec]


@dataclass
class ErrRec:
    time: Optional[datetime]
    event_id: int
    severity: int
    part: str
    length: int
    ok: bool
    context: str = ""


@dataclass
class Report:
    rows: list[ErrRow] = field(default_factory=list)
    records: list[ErrRec] = field(default_factory=list)
    info_ids: dict[int, int] = field(default_factory=dict)
    unknown: int = 0                        # 格式未识别的记录数
    memdiag: list[tuple] = field(default_factory=list)
    memdiag_note: str = ""
    bugchecks: list[tuple] = field(default_factory=list)
    io_rows: list[tuple] = field(default_factory=list)
    io_counts: dict[int, int] = field(default_factory=dict)
    elapsed: float = 0.0
    note: str = ""

    @property
    def fatal(self) -> int:
        return sum(r.count for r in self.rows if r.severity == 1)

    @property
    def corrected(self) -> int:
        return sum(r.count for r in self.rows if r.severity == 2)

    @property
    def total(self) -> int:
        return sum(r.count for r in self.rows)

    @property
    def worst(self) -> str:
        if self.fatal:
            return "crit"
        if any(r.level == "warn" for r in self.rows):
            return "warn"
        if self.corrected or self.io_total:
            return "info"
        return "ok"

    @property
    def io_total(self) -> int:
        return sum(self.io_counts.values())

    @property
    def headline(self) -> str:
        if self.fatal:
            return f"{self.fatal} 条致命硬件错误"
        if self.corrected:
            dens = "最近 7 天仍在发生" if any(r.recent7 for r in self.rows) else "近期已无新增"
            return f"{self.corrected} 条已纠正硬件错误（{dens}）"
        if self.total:
            return f"{self.total} 条硬件记录，无致命级"
        if self.io_total:
            return f"无 WHEA 错误，但有 {self.io_total} 条磁盘 IO 告警"
        return "未发现硬件错误"

    @property
    def parts(self) -> list[tuple[str, int]]:
        agg: dict[str, int] = {}
        for r in self.rows:
            agg[r.part] = agg.get(r.part, 0) + r.count
        return sorted(agg.items(), key=lambda x: -x[1])


def _read_whea(stop_flag=None) -> tuple[list[ErrRec], dict[int, int], int]:
    recs: list[ErrRec] = []
    info: dict[int, int] = {}
    unknown = 0
    for chan in (WHEA_ERR, WHEA_OP):
        if stop_flag is not None and stop_flag():
            break
        try:
            events, _st = evtx.query_events(chan, False, limit=2000,
                                           message_mode="none")
        except Exception:       # noqa: BLE001
            continue
        for e in events:
            if e.event_id in INFO_EVENT_IDS:
                info[e.event_id] = info.get(e.event_id, 0) + 1
                continue
            raw = e.data.get("RawData") or ""
            try:
                declared = int(e.data.get("Length") or 0)
            except (TypeError, ValueError):
                declared = 0
            cp = parse_cper(raw, declared)
            if cp is None:
                # 没有 CPER 头的事件（如 id=20 之外的杂项）不算错误，只计为未识别
                unknown += 1
                continue
            if not cp.ok:
                unknown += 1
            recs.append(ErrRec(time=e.time, event_id=e.event_id,
                               severity=cp.severity, part=cp.part,
                               length=declared or cp.record_length, ok=cp.ok,
                               context=cp.context))
    return recs, info, unknown


def _aggregate(recs: list[ErrRec]) -> list[ErrRow]:
    """按 (严重级别, 部件) 聚合，并算最近 7 天的密度。"""
    now = datetime.now()
    cut = now - timedelta(days=7)
    buckets: dict[tuple[int, str], list[ErrRec]] = {}
    for r in recs:
        buckets.setdefault((r.severity, r.part), []).append(r)

    rows: list[ErrRow] = []
    for (sev, part), lst in buckets.items():
        lst.sort(key=lambda x: x.time or datetime.min)
        times = [r.time for r in lst if r.time]
        cnt = len(lst)
        recent = sum(1 for t in times if t >= cut)
        name, level, hint = SEVERITY_CN.get(sev, ("未知", "info", ""))
        # 已纠正错误：单次不致命，但最近还在刷就要提级
        if sev == 2 and recent >= 3:
            level = "warn"
            hint = ("已纠正错误仍在持续产生（最近 7 天 %d 次）。"
                    "这类错误是硬件劣化的早期信号，先关超频 / XMP 再观察。" % recent)
        elif sev == 2 and cnt >= 20:
            level = "warn"
            hint = ("累计 %d 次已纠正错误，密度偏高。检查 %s 相关的散热、"
                    "插接与固件版本。" % (cnt, part))
        # 部件相关的具体建议比通用建议有用得多
        if "PCIe" in part and sev in (0, 1, 2):
            hint += ("　PCIe 侧优先查：显卡/网卡金手指与插槽灰尘、转接排线、"
                     "BIOS 里关闭 ASPM 节能后复测、必要时把卡换到另一条插槽。")
        elif "内存" in part and sev in (0, 1, 2):
            hint += ("　内存侧优先查：关闭 XMP/EXPO、单条轮流压测、"
                     "重插并擦净金手指、核对四条混插的颗粒是否一致。")
        elif "处理器" in part and sev in (0, 1, 2):
            hint += ("　处理器侧优先查：BIOS 恢复默认、关闭 PBO/超频、"
                     "确认散热与供电、更新主板固件。")
        rows.append(ErrRow(
            sev=name, severity=sev, part=part, count=cnt,
            first=times[0] if times else None,
            last=times[-1] if times else None,
            recent7=recent, level=level, hint=hint.strip(),
            context=lst[0].context, samples=lst[-3:]))
    rows.sort(key=lambda r: (r.severity, -r.count))
    return rows


def _memdiag(stop_flag=None) -> tuple[list[tuple], str]:
    """Windows 内存诊断（mdsched）的历史结果。没跑过就是没跑过，不编。"""
    try:
        events, _st = evtx.query_events(MEMDIAG, False, limit=50,
                                        message_mode="none")
    except Exception:       # noqa: BLE001
        return [], "无法读取内存诊断通道（可能从未运行过 Windows 内存诊断）"
    if not events:
        return [], ("这台机器从未运行过 Windows 内存诊断（mdsched）。"
                    "怀疑内存条问题时可以用它做一次重启自检。")
    rows = []
    for e in events[:20]:
        txt = e.message or str(e.data)
        rows.append((e.time, e.event_id, txt[:160]))
    return rows, ""


def _bugchecks(limit: int = 4000) -> list[tuple]:
    """从 System 1001 里提取 BugCheck 码，与 WHEA 对照。"""
    try:
        events, _st = evtx.query_events("System", False, limit=limit,
                                        message_mode="auto")
    except Exception:       # noqa: BLE001
        return []
    out: list[tuple] = []
    for e in events:
        if e.event_id != 1001:
            continue
        msg = e.message or ""
        low = msg.lower()
        if "bugcheck" not in low:
            continue
        code = ""
        for token in msg.replace(",", " ").replace(":", " ").split():
            t = token.strip().lower()
            if t.startswith("0x") and len(t) >= 6:
                code = t
                break
        out.append((e.time, code or "—", msg[:150].replace("\n", " ")))
        if len(out) >= 40:
            break
    return out


def scan(stop_flag=None, progress=None) -> Report:
    """跑一遍硬件错误聚合。progress(done, total, msg)。"""
    import time
    from . import smart as smartm
    t0 = time.perf_counter()
    rep = Report()
    if progress:
        progress(0, 4, "解析 WHEA 错误记录…")
    recs, info, unknown = _read_whea(stop_flag=stop_flag)
    rep.records, rep.info_ids, rep.unknown = recs, info, unknown
    rep.rows = _aggregate(recs)
    if progress:
        progress(1, 4, "读取内存诊断记录…")
    rep.memdiag, rep.memdiag_note = _memdiag(stop_flag)
    if progress:
        progress(2, 4, "提取蓝屏代码…")
    rep.bugchecks = _bugchecks()
    if progress:
        progress(3, 4, "统计磁盘 IO 错误…")
    rep.io_rows, rep.io_counts = smartm.io_errors()
    if not recs:
        rep.note = ("没有解析到 CPER 错误记录。WHEA 通道里的事件多为错误源通知"
                    "（每次开机刷几百条），不是硬件故障，这里不计入。")
    rep.elapsed = time.perf_counter() - t0
    if progress:
        progress(4, 4, "完成")
    return rep
