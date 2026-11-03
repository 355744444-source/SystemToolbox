# -*- coding: utf-8 -*-
"""本机蓝屏记录：把散落在三个地方的蓝屏码汇总成一张表。

## 三个来源，可信度不同

1. **转储文件头**（最准）——`DUMP_HEADER64` 的 `0x38` 就是 `BugCheckCode`，
   `0x40` 起是 4 个参数。网上有资料把蓝屏码写在 `0x400`，实测是错的：
   本机 `ResourceTimeout-20261020-1655.dmp` 的 `0x38` 是 `0x1CC`，
   而 `0x400` 读出来是 `0xB80`（别的位置）。只读前 0x60 字节，2 GB 的文件也是毫秒级。
2. **System 通道 `BugCheck` 1001**（有码，但只保留最近若干条，日志滚动就没了）。
3. **System 通道 `Kernel-Power` 41**（掉电 / 硬关机，`BugcheckCode` 通常是 0）。

## 为什么必须读转储头

本机的真实情况：驱动挂起时系统**不写** Minidump，只在
`C:\\Windows\\LiveKernelReports` 留一个 GB 级 dmp；同时事件日志里的 41 是 `code=0`。
也就是说**事件日志里一个蓝屏码都没有**，真正的码只在转储头里。
只信事件日志的「蓝屏历史」在这种机器上会显示成「从未蓝屏」，是假的。

## 关联规则

转储（蓝屏瞬间）在前、41（重启后补写）在后，间隔 ≤ 15 分钟视为同一次故障的后续硬关机，
合并成一行并注明「蓝屏后 N 秒硬关机」。1001 与转储按 ±3 分钟双向匹配。
"""
from __future__ import annotations

import os
import struct
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from . import dumpcfg
from . import knowledge as kb
from .analysis import Finding

HEADER_PROBE = 0x60        # DUMP_HEADER64 前 0x60 字节够取蓝屏码与 4 个参数
MAX_CODE = 0xFFFF          # 内核蓝屏码都是小整数，超出必是读错位置
REBOOT_GAP_MIN = 15        # 转储 → 41 的关联窗口（分钟）
EVENT_GAP_SEC = 180        # 1001 ↔ 转储的关联窗口（秒）
MAX_MINIDUMPS = 200
MAX_LIVE = 40

# 来源文案：origin -> (短标签, 长说明)
ORIGIN_CN = {
    "minidump": ("小型转储", "Minidump 目录里的 .dmp，蓝屏瞬间写入"),
    "kernel": ("内核转储", "MEMORY.DMP，完整/内核级别的转储"),
    "live": ("实况内核报告", "LiveKernelReports，驱动挂起时写入，系统未必真蓝屏"),
    "event": ("事件日志", "仅事件日志有记录，转储文件已不在（被清理或未开启转储）"),
}


@dataclass
class HeaderCode:
    """从转储头里抠出来的东西。"""

    code: int = 0
    params: tuple = ()
    is64: bool = True
    origin: str = "kernel"
    image_type: int = 0
    cpus: int = 0
    bad_code: int = 0          # 读到的越界值，用于说明「头部不像蓝屏转储」

    @property
    def has_code(self) -> bool:
        return 0 < self.code <= MAX_CODE


@dataclass
class Crash:
    """一次蓝屏 / 异常关机记录。"""

    time: Optional[datetime] = None
    code: int = 0
    params: tuple = ()
    origin: str = "event"          # minidump / kernel / live / event
    dump_path: str = ""
    dump_size: int = 0
    event_id: int = 0
    provider: str = ""
    reboot_time: Optional[datetime] = None    # 关联到的 41 事件时间
    note: str = ""
    # 知识库回填
    name: str = ""
    name_cn: str = ""
    advice: list = field(default_factory=list)

    # ---- 派生
    @property
    def has_code(self) -> bool:
        # 上限和 HeaderCode 用同一个 MAX_CODE：`0xC0000005` 这类状态码不能
        # 被当成蓝屏码显示（`_valid_code` 已经清过一遍，这里再兜一次）。
        return 0 < self.code <= MAX_CODE

    @property
    def code_text(self) -> str:
        return "0x%08X" % self.code if self.has_code else "—"

    @property
    def time_text(self) -> str:
        return self.time.strftime("%Y-%m-%d %H:%M:%S") if self.time else "—"

    @property
    def time_short(self) -> str:
        return self.time.strftime("%m-%d %H:%M:%S") if self.time else "—"

    @property
    def origin_text(self) -> str:
        return ORIGIN_CN.get(self.origin, ("—", ""))[0]

    @property
    def origin_tip(self) -> str:
        return ORIGIN_CN.get(self.origin, ("", ""))[1]

    @property
    def dump_text(self) -> str:
        if not self.dump_path:
            return "无"
        return dumpcfg.human(self.dump_size)

    @property
    def name_text(self) -> str:
        if not self.has_code:
            return "—"
        if self.name:
            return self.name
        return "知识库未收录"

    @property
    def cn_text(self) -> str:
        if not self.has_code:
            return "没有蓝屏码（掉电 / 硬关机）"
        return self.name_cn or "未收录的蓝屏码，建议按代码查微软文档"

    @property
    def params_text(self) -> str:
        if not self.params:
            return "—"
        return "  ".join("0x%X" % v for v in self.params)

    @property
    def reboot_text(self) -> str:
        """关联到的 41 事件：蓝屏/挂起之后多久被强行关机。"""
        if not self.reboot_time:
            return ""
        secs = int((self.reboot_time - self.time).total_seconds()) if self.time else 0
        if secs >= 60:
            return "→ %d分%d秒后硬关机" % (secs // 60, secs % 60)
        return "→ %d秒后硬关机" % secs

    @property
    def note_text(self) -> str:
        """表格「说明」列：一句话讲清这条记录到底是怎么回事。

        列宽只有约 200px，所以尽量短；完整解释在 note / path_tip 里。
        """
        if self.has_code:
            if self.origin == "live":
                base = "驱动挂起"
            elif self.origin == "event":
                base = "转储已不在"
            else:
                base = "有转储可分析"
        else:
            base = "掉电/硬关机"
        tail = self.reboot_text
        return (base + "  " + tail).strip() if tail else base

    @property
    def path_tip(self) -> str:
        lines = [f"{self.time_text} · {self.origin_text}"]
        if self.has_code:
            lines.append(f"蓝屏码 {self.code_text}  {self.name_text}")
            lines.append(f"中文说明：{self.cn_text}")
            if self.params:
                lines.append("参数：" + self.params_text)
        if self.dump_path:
            lines.append(f"转储：{self.dump_path}（{self.dump_text}）")
        if self.event_id:
            lines.append(f"事件：{self.provider} ID={self.event_id}")
        if self.reboot_time:
            lines.append("随后硬关机：%s %s" % (
                self.reboot_time.strftime("%Y-%m-%d %H:%M:%S"), self.reboot_text))
        if self.note:
            lines.append(self.note)
        return "\n".join(lines)

    @property
    def level(self) -> str:
        """表格着色用的主题 token 名。"""
        if self.has_code:
            return "crit" if self.origin != "live" else "warn"
        return "dim"


@dataclass
class LocalScan:
    """一次本机蓝屏扫描的全部结果。"""

    records: list = field(default_factory=list)     # list[Crash]
    scanned_at: Optional[datetime] = None
    problems: list = field(default_factory=list)    # 扫不到的原因
    cost_ms: int = 0
    event_error: str = ""

    @property
    def with_code(self) -> list:
        return [c for c in self.records if c.has_code]

    @property
    def power_only(self) -> list:
        return [c for c in self.records if not c.has_code]

    @property
    def latest(self) -> Optional[Crash]:
        return self.records[0] if self.records else None

    @property
    def latest_with_code(self) -> Optional[Crash]:
        return self.with_code[0] if self.with_code else None

    @property
    def code_counts(self) -> dict:
        out: dict = {}
        for c in self.with_code:
            out[c.code] = out.get(c.code, 0) + 1
        return out

    @property
    def headline(self) -> str:
        if not self.records:
            return "本机没有找到蓝屏或异常关机记录"
        n, m = len(self.records), len(self.with_code)
        head = "本机共 %d 次异常记录，其中 %d 次有蓝屏码" % (n, m)
        last = self.latest_with_code or self.latest
        if last and last.time:
            head += "；最近一次 %s" % last.time.strftime("%Y-%m-%d %H:%M:%S")
            if last.has_code:
                head += " · %s %s" % (last.code_text, last.name_text)
            else:
                head += " · 掉电/硬关机"
        return head

    @property
    def short_line(self) -> str:
        """提示条只有一行位置，用这个；完整结论放页面里。"""
        if not self.records:
            return "本机没有找到蓝屏记录"
        n, m = len(self.records), len(self.with_code)
        last = self.latest_with_code or self.latest
        if last and last.has_code:
            when = last.time.strftime("%m-%d %H:%M") if last.time else "—"
            return "本机 %d 条记录 · %d 条有蓝屏码 · 最近 %s %s" % (
                n, m, when, last.code_text)
        return "本机 %d 条记录，都没有蓝屏码（以掉电/硬关机为主）" % n


# ------------------------------------------------------------------ 采集
def read_header(path: str) -> Optional[HeaderCode]:
    """只读转储文件头，取出蓝屏码与参数。读不到 / 不是内核转储 → None。"""
    try:
        if os.path.getsize(path) < HEADER_PROBE:
            return None
        with open(path, "rb") as f:
            h = f.read(HEADER_PROBE)
    except OSError:
        return None
    if len(h) < HEADER_PROBE:
        return None
    if h[:4] == b"MDMP":
        return None                       # 用户模式迷你转储，没有内核蓝屏码
    if h[:4] != b"PAGE":
        return None
    is64 = h[4:8] == b"DU64"
    try:
        if is64:
            code = struct.unpack_from("<I", h, 0x38)[0]
            params = tuple(struct.unpack_from("<Q", h, 0x40 + i * 8)[0]
                           for i in range(4))
            image_type = struct.unpack_from("<I", h, 0x30)[0]
            cpus = struct.unpack_from("<I", h, 0x34)[0]
        else:
            code = struct.unpack_from("<I", h, 0x28)[0]
            params = tuple(struct.unpack_from("<I", h, 0x2C + i * 4)[0]
                           for i in range(4))
            image_type = struct.unpack_from("<I", h, 0x20)[0]
            cpus = struct.unpack_from("<I", h, 0x24)[0]
    except struct.error:
        return None
    out = HeaderCode(is64=is64, params=params, image_type=image_type, cpus=cpus)
    if 0 < code <= MAX_CODE:
        out.code = code
    elif code:
        out.bad_code = code               # 位置像是对的，但值不合理
    return out


def scan_paths() -> tuple[list, list]:
    """返回 ([(路径, origin)], [说明])。"""
    win = os.environ.get("SystemRoot", r"C:\Windows")
    items: list = []
    notes: list = []

    md = os.path.join(win, "Minidump")
    if os.path.isdir(md):
        try:
            names = [n for n in os.listdir(md) if n.lower().endswith((".dmp", ".mdmp"))]
        except OSError:
            names = []
            notes.append("Minidump 目录读不到（可能被权限拦了）")
        if not names:
            notes.append("Minidump 目录是空的 —— 说明系统没在这里写过小型转储"
                         "（转储被关闭、或蓝屏类型是「不转储」、或从未真正蓝屏）")
        for n in names[:MAX_MINIDUMPS]:
            items.append((os.path.join(md, n), "minidump"))
    else:
        notes.append("没有 %s 目录（系统从未生成过小型转储）" % md)

    main = os.path.join(win, "MEMORY.DMP")
    if os.path.isfile(main):
        items.append((main, "kernel"))

    live = os.path.join(win, "LiveKernelReports")
    if os.path.isdir(live):
        found = []
        try:
            for cur, _dirs, files in os.walk(live):
                for f in files:
                    if f.lower().endswith(".dmp"):
                        found.append(os.path.join(cur, f))
        except OSError:
            found = []
        for p in found[:MAX_LIVE]:
            items.append((p, "live"))
    return items, notes


def _mk_from_header(path: str, origin: str) -> Optional[Crash]:
    h = read_header(path)
    if h is None:
        return None
    try:
        st = os.stat(path)
        when = datetime.fromtimestamp(st.st_mtime)
        size = st.st_size
    except OSError:
        when, size = None, 0
    c = Crash(time=when, code=h.code, params=h.params, origin=origin,
              dump_path=path, dump_size=size)
    if origin == "live":
        c.note = ("实况内核报告：驱动挂起时写入，系统不一定真的蓝屏。"
                  "时间取自文件修改时间。")
    if h.bad_code and not h.has_code:
        c.note = "转储头里的蓝屏码是 0x%X，不像是有效值，可能不是内核转储" % h.bad_code
    return c


def _fill_kb(c: Crash) -> None:
    if not c.has_code:
        return
    info = kb.bugcheck_info(c.code)
    if info:
        c.name = info.name
        c.name_cn = info.name_cn
        c.advice = list(info.advice)


def _nearest_dump(when: Optional[datetime], dumps: list,
                  used: set) -> Optional[int]:
    """给事件找时间最接近的、还没被认领的转储下标。"""
    if when is None:
        return None
    best, best_gap = None, None
    for i, d in enumerate(dumps):
        if i in used or not d.time:
            continue
        gap = abs((d.time - when).total_seconds())
        if gap <= EVENT_GAP_SEC and (best_gap is None or gap < best_gap):
            best, best_gap = i, gap
    return best


def collect(with_events: bool = True, events: Optional[list] = None,
            paths: Optional[list] = None,
            now: Optional[datetime] = None) -> LocalScan:
    """扫全机。`events` / `paths` 可注入，方便单测。"""
    t0 = time.perf_counter()
    s = LocalScan(scanned_at=now or datetime.now())
    if paths is None:
        paths, notes = scan_paths()
        s.problems.extend(notes)
    dumps: list = []
    skipped = 0
    for path, origin in paths:
        c = _mk_from_header(path, origin)
        if c is None:
            skipped += 1
            continue
        _fill_kb(c)
        dumps.append(c)
    if skipped:
        # 别静默丢弃：目录里有 3 个 dmp、表里只有 2 行，用户会以为工具漏读了
        s.problems.append(
            "%d 个 .dmp 不是内核转储（用户模式 MDMP 或文件头异常），未列入蓝屏记录"
            % skipped)
    dumps.sort(key=lambda d: d.time or datetime.min, reverse=True)

    if with_events:
        if events is None:
            try:
                events = dumpcfg.scan_crashes(limit=200)
            except Exception as exc:            # noqa: BLE001
                events = []
                s.event_error = "读 System 事件日志失败：%s" % exc
        _merge_events(s, dumps, events)
    else:
        s.records = dumps

    s.records.sort(key=lambda c: c.time or datetime.min, reverse=True)
    s.cost_ms = int((time.perf_counter() - t0) * 1000)
    return s


def _valid_code(code) -> int:
    """蓝屏码都是 `0 < code ≤ 0xFFFF` 的小整数。

    `BugCheck` 1001 的 param1 在不同系统/语言版本上字段顺序未必一致，
    取错字段会拿到 `0xC0000005` 这类异常码或状态码。把它当蓝屏码显示出去
    就是误报（用户会照着「0xC0000005」去查蓝屏资料，越查越偏），
    所以越界一律按「没有码」处理。
    """
    try:
        code = int(code) & 0xFFFFFFFF
    except (TypeError, ValueError):
        return 0
    return code if 0 < code <= MAX_CODE else 0


def _merge_events(s: LocalScan, dumps: list, events: list) -> None:
    """把事件按时间贴到转储上。同一件事只能出现一行，不同的事不能被吞掉。

    分两遍过，结果**与事件顺序无关**（事件日志是倒序读出来的；
    单遍处理会让「先读到的那条」抢走转储，最后的结论就看事件顺序了）。
    第一遍：1001 认领最近的转储；第二遍：无码 41 认领它前面最近的那个转储。
    两边都是「一个转储只被认领一次」，多出来的事件各自成行 ——
    它们代表另一次蓝屏 / 另一次非正常关机，不该被前一次吞掉。
    """
    rest: list = []

    # ---- 第一遍：BugCheck 1001（±3 分钟，双向）
    claimed: set = set()
    for ev in events:
        if ev.kind != "bugcheck":
            continue
        code = _valid_code(ev.code)
        if not code:
            continue                            # 1001 但没有有效码，没信息量
        idx = _nearest_dump(ev.time, dumps, claimed)
        if idx is None:
            # 同一次蓝屏被 WER 补写第二条 1001 时，最近的转储已经被认领了。
            # 码相同就是同一件事，不能再占一行；码不同才当成另一次故障。
            j = _nearest_dump(ev.time, dumps, set())
            if j is not None and dumps[j].code == code:
                continue
            c = Crash(time=ev.time, code=code,
                      params=tuple(ev.params or ()), origin="event",
                      event_id=ev.event_id, provider=ev.provider)
            c.note = "转储文件已不在（被清理、或蓝屏时未开启转储）"
            _fill_kb(c)
            rest.append(c)
            continue
        d = dumps[idx]
        claimed.add(idx)
        d.event_id = ev.event_id
        d.provider = ev.provider
        if not d.has_code:
            d.code = code
            d.params = tuple(ev.params or ())
            _fill_kb(d)
        if not d.time:
            d.time = ev.time

    # ---- 第二遍：无码 Kernel-Power 41 = 转储之后的「后续硬关机」
    # 先算出每对(事件, 转储)的距离，再按距离从近到远配对 —— 一个转储只配一条，
    # 每条 41 也只配一个转储，剩下的原样落成独立记录。
    pairs: list = []            # (距离秒, 事件下标, 转储下标)
    for k, ev in enumerate(events):
        if ev.kind == "bugcheck" or _valid_code(ev.code) or not ev.time:
            continue                            # 带码的 41 走下面的独立记录
        for i, d in enumerate(dumps):
            if not d.time:
                continue
            gap = (ev.time - d.time).total_seconds()
            if 0 <= gap <= REBOOT_GAP_MIN * 60:
                # dumps 按时间降序，第一个落入窗口的就是最近的，不用再往后找
                pairs.append((gap, k, i))
                break
    pairs.sort(key=lambda p: p[0])
    linked: set = set()
    owned: set = set()
    for _gap, k, i in pairs:
        if k in linked or i in owned:
            continue
        dumps[i].reboot_time = events[k].time
        linked.add(k)
        owned.add(i)

    # ---- 剩下的 41：带码的当独立蓝屏记录，无码的当一次掉电/硬关机
    for k, ev in enumerate(events):
        if ev.kind == "bugcheck" or k in linked:
            continue
        c = Crash(time=ev.time, code=_valid_code(ev.code), origin="event",
                  event_id=ev.event_id, provider=ev.provider)
        if c.has_code:
            c.note = "从 Kernel-Power 41 事件里读出的蓝屏码，转储文件已不在"
            _fill_kb(c)
        else:
            c.note = ("事件日志只记到「非正常关机」，没有蓝屏码。"
                      "这类机器多是掉电、死机或强制关机，"
                      "方向是电源/散热/超频，不是驱动蓝屏。")
        rest.append(c)

    s.records = dumps + rest


# ------------------------------------------------------------------ 结论
def findings(s: LocalScan) -> list:
    """把扫描结果翻成诊断卡（复用 analysis.Finding）。"""
    out: list = []
    if not s.records:
        out.append(Finding(
            "提示", "蓝屏", "本机没有蓝屏记录",
            "三个来源都没找到：Minidump 目录、MEMORY.DMP、"
            "以及 System 日志里的 BugCheck 1001 / Kernel-Power 41。",
            ["如果确实蓝屏过但这里没有，检查「写入调试信息」是否被设为「无」，"
             "以及 C:\\Windows\\Minidump 是否被清理脚本删过"], count=0))
        return out

    withc = s.with_code
    if withc:
        last = withc[0]
        out.append(Finding(
            "临界" if last.origin in ("minidump", "kernel") else "高",
            "蓝屏", "最近一次蓝屏：%s %s" % (last.code_text, last.name_text),
            "%s · 来源 %s。%s" % (last.time_text, last.origin_text, last.cn_text),
            list(last.advice) or ["按代码到微软「Bug Check Code Reference」查参数含义"],
            count=len(withc), first=last.time, last=last.time,
            evidence=[last.path_tip]))

    counts = s.code_counts
    repeated = [(code, n) for code, n in counts.items() if n >= 2]
    repeated.sort(key=lambda x: -x[1])
    for code, n in repeated[:3]:
        info = kb.bugcheck_info(code)
        out.append(Finding(
            "高", "蓝屏",
            "0x%08X 重复出现 %d 次" % (code, n),
            "同一个蓝屏码反复出现，通常说明那个方向的驱动或硬件问题一直没解决，"
            "而不是偶发抖动。" + ("（%s）" % info.name_cn if info else ""),
            list(info.advice) if info else
            ["把每次蓝屏的转储都收集起来，比对是否有共同驱动",
             "对可疑驱动开 Driver Verifier（先在测试机验证）"],
            count=n))

    no_dump = [c for c in withc if not c.dump_path]
    if no_dump:
        out.append(Finding(
            "中", "蓝屏",
            "%d 次有蓝屏码但没有转储文件" % len(no_dump),
            "事件日志记下了蓝屏码，但 C:\\Windows\\Minidump 里没有对应的 dmp，"
            "所以拿不到出错模块和调用栈。",
            ["系统属性 → 高级 → 启动和故障恢复 → 写入调试信息选「小内存转储」",
             "确认 C:\\Windows\\Minidump 目录存在且没被清理脚本删掉",
             "确认系统盘页面文件不小于 2 MB（小型转储的硬要求）"],
            count=len(no_dump)))

    lives = [c for c in s.records if c.origin == "live"]
    if lives:
        out.append(Finding(
            "中", "蓝屏",
            "%d 个实况内核报告（驱动挂起）" % len(lives),
            "LiveKernelReports 里的转储是系统「失去响应」时写的，"
            "它不一定是蓝屏，但通常意味着某个驱动把内核线程卡住了。"
            "这类报告最容易在磁盘/存储驱动、USB 重定向、虚拟网卡上出现。",
            ["打开转储目录看文件名前缀，前缀就是挂起类型（如 ResourceTimeout 是资源超时）",
             "对最近安装的驱动做回滚验证",
             "用 cdb 分析该 dmp 能看到具体卡住的组件"],
            count=len(lives)))

    powers = s.power_only
    if powers:
        out.append(Finding(
            "提示", "蓝屏",
            "%d 次非正常关机没有蓝屏码" % len(powers),
            "这些是 Kernel-Power 41（掉电 / 死机 / 强制关机），"
            "不是驱动蓝屏，所以不会有蓝屏码 —— 没有码不代表记录有问题。",
            ["检查电源与供电（尤其高负载时）", "看散热与温度墙、是否超频/开了 XMP",
             "笔记本查电池与适配器"],
            count=len(powers)))

    unknown = [c for c in withc if not c.name]
    if unknown:
        out.append(Finding(
            "提示", "蓝屏",
            "%d 个蓝屏码知识库未收录" % len(unknown),
            "代码本身是有效的，只是本工具的知识库只收了常见的那批。"
            "按代码去微软的 Bug Check Code Reference 查即可。",
            ["收集该转储，用 cdb !analyze -v 定位模块"], count=len(unknown)))
    return out
