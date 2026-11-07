# -*- coding: utf-8 -*-
"""关联分析引擎。

单个事件 ID 只能说明「发生了什么」，真正有用的是把事件串起来：
BugCheck(1001) + Kernel-Power(41) 同时出现 = 蓝屏后自动重启；
41 出现但没有 1001 = 直接掉电，方向完全不同；
再加上磁盘 153/129 与 WHEA 17/18 的密度，就能给出硬件怀疑排序。
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from . import knowledge as kb
from .evtx import Event, ReadStats
from .minidump import DumpReport

SEV_ORDER = {"临界": 0, "高": 1, "中": 2, "提示": 3}
SEV_DEDUCT = {"临界": 30, "高": 12, "中": 5, "提示": 0}


@dataclass
class Finding:
    severity: str
    topic: str
    title: str
    detail: str = ""
    advice: list[str] = field(default_factory=list)
    count: int = 0
    first: Optional[datetime] = None
    last: Optional[datetime] = None
    evidence: list[str] = field(default_factory=list)
    ref_event_id: int = 0

    @property
    def time_text(self) -> str:
        if self.first and self.last and self.first != self.last:
            return f"{self.first:%m-%d %H:%M} ~ {self.last:%m-%d %H:%M}"
        if self.first:
            return f"{self.first:%Y-%m-%d %H:%M:%S}"
        return "-"


@dataclass
class LogAnalysis:
    findings: list[Finding] = field(default_factory=list)
    level_counts: Counter = field(default_factory=Counter)
    topic_counts: Counter = field(default_factory=Counter)
    boot_times: list = field(default_factory=list)
    crash_times: list = field(default_factory=list)
    bugchecks: list = field(default_factory=list)
    first: Optional[datetime] = None
    last: Optional[datetime] = None
    health: int = 100
    headline: str = "未分析"
    total: int = 0
    truncated: bool = False
    notes: list[str] = field(default_factory=list)

    @property
    def span_text(self) -> str:
        if not self.first or not self.last:
            return "-"
        delta = self.last - self.first
        days = delta.days
        hours = delta.seconds // 3600
        if days:
            return f"{self.first:%Y-%m-%d %H:%M} ~ {self.last:%Y-%m-%d %H:%M}（{days} 天 {hours} 小时）"
        return f"{self.first:%Y-%m-%d %H:%M} ~ {self.last:%H:%M}（{hours} 小时）"

    @property
    def span_short(self) -> str:
        if not self.first or not self.last:
            return "-"
        days = (self.last - self.first).days
        if days >= 1:
            return f"{self.first:%m-%d} ~ {self.last:%m-%d} · {days + 1} 天"
        return f"{self.first:%m-%d %H:%M} ~ {self.last:%H:%M}"


def _num(text: str) -> Optional[int]:
    """把事件里的数字字符串转成整数：兼容 '209' / '0xD1' / 'd1' / '00000000000000d1'。"""
    if text is None:
        return None
    s = str(text).strip().replace("#", "")
    if not s:
        return None
    try:
        if s.lower().startswith("0x"):
            return int(s, 16)
        if re.fullmatch(r"[0-9]+", s):
            v = int(s, 10)
            return v
        if re.fullmatch(r"[0-9a-fA-F]+", s):
            return int(s, 16)
    except ValueError:
        pass
    return None


def _pick(data: dict, *names) -> str:
    """按候选键名取值，兼容中英文/paramN 两类模板。"""
    lowered = {str(k).lower(): v for k, v in data.items()}
    for n in names:
        if n in data:
            return str(data[n])
        if n.lower() in lowered:
            return str(lowered[n.lower()])
    return ""


def _ev_line(e: Event) -> str:
    text = e.summary[:110]
    return f"{e.time_text}  [{e.level_name}]  {e.provider} / {e.event_id}  {text}"


class _Ctx:
    def __init__(self, events: list[Event], stats: Optional[ReadStats]):
        self.events = events
        self.stats = stats
        self.by_id: dict[int, list[Event]] = defaultdict(list)
        for e in events:
            self.by_id[e.event_id].append(e)

    def get(self, *ids: int) -> list[Event]:
        out: list[Event] = []
        for i in ids:
            out.extend(self.by_id.get(i, []))
        out.sort(key=lambda e: e.time or datetime.min)
        return out


def analyze(events: list[Event], stats: Optional[ReadStats] = None) -> LogAnalysis:
    ctx = _Ctx(events, stats)
    res = LogAnalysis(total=len(events))
    if stats is not None:
        res.truncated = stats.truncated
        if stats.error:
            res.notes.append(stats.error)

    times = [e.time for e in events if e.time]
    if times:
        res.first, res.last = min(times), max(times)
    res.level_counts = Counter(e.level for e in events)

    _rule_bugcheck(ctx, res)
    _rule_power(ctx, res)
    _rule_disk(ctx, res)
    _rule_whea(ctx, res)
    _rule_app(ctx, res)
    _rule_service(ctx, res)
    _rule_driver(ctx, res)
    _rule_security(ctx, res)
    _rule_reboot(ctx, res)
    _rule_time(ctx, res)
    _rule_dcom(ctx, res)

    order = {"临界": 0, "高": 1, "中": 2, "提示": 3}
    res.findings.sort(key=lambda f: (order.get(f.severity, 9), -(f.count or 0)))
    # 健康分：同类问题递减计分，避免「问题一多就 0 分」失去区分度
    score = 100.0
    for sev, base in (("临界", 26.0), ("高", 10.0), ("中", 4.0)):
        items = [f for f in res.findings if f.severity == sev]
        for i, _f in enumerate(items):
            score -= base if i == 0 else base * 0.6
    res.health = max(5, min(100, int(round(score))))
    for f in res.findings:
        res.topic_counts[f.topic] += max(f.count, 1)

    res.headline = _headline(res)
    return res


def _headline(res: LogAnalysis) -> str:
    # 「没发现问题」这句话只有读全了才敢说。读到一半中断（错误/取消/被 limit
    # 截断）时，页面上原来照样显示「日志干净」—— 这是只读取证工具最不能出的
    # 结论：用户会拿它当「这台机器没问题」的证据。
    incomplete = bool(res.truncated or any("失败" in n or "取消" in n
                                          for n in res.notes))
    if not res.total:
        return "没有读取到任何事件"
    crit = [f for f in res.findings if f.severity == "临界"]
    high = [f for f in res.findings if f.severity == "高"]
    if crit:
        top = crit[0]
        extra = f"（另有 {len(crit) - 1} 项临界问题）" if len(crit) > 1 else ""
        return f"{top.title}{extra}"
    if high:
        top = high[0]
        extra = f"（另有 {len(high) - 1} 项高风险问题）" if len(high) > 1 else ""
        return f"{top.title}{extra}"
    if res.findings:
        return f"未发现崩溃或硬件错误，仅有 {len(res.findings)} 项提示：" + res.findings[0].title
    if incomplete:
        return ("只读到 %d 条事件（未读完）：以下结论只覆盖已读部分，"
                "不能当作「这台机器没问题」" % res.total)
    return "日志干净：窗口内没有崩溃、掉电、磁盘或硬件错误事件"


# ------------------------------------------------------------------ 规则实现
def _rule_bugcheck(ctx: _Ctx, res: LogAnalysis) -> None:
    events = ctx.get(1001)
    # 只保留真正的 BugCheck 记录（1001 也被 Windows 更新等复用）
    real = []
    for e in events:
        code = _num(_pick(e.data, "param1", "BugcheckCode", "BugCheckCode"))
        if code is None:
            continue
        blk = kb.bugcheck_info(code)
        if blk is None and code > 0x0000FFFF:
            continue
        real.append((e, code))
    if not real:
        return

    grouped: dict[int, list[Event]] = defaultdict(list)
    for e, code in real:
        grouped[code].append(e)
    res.crash_times = sorted(e.time for e, _ in real if e.time)

    for code, evs in grouped.items():
        info = kb.bugcheck_info(code)
        params = []
        first = evs[0]
        for i in range(2, 6):
            raw = _pick(first.data, f"param{i}")
            if raw:
                v = _num(raw)
                if v is not None:
                    params.append(v)
        if info:
            title = f"蓝屏 0x{code:08X} · {info.name_cn}"
            detail = f"{info.name}（{info.name_cn}），共 {len(evs)} 次。"
            if info.culprit:
                detail += f"常见原因方向：{info.culprit}。"
            if params:
                detail += " 参数：" + " / ".join(f"0x{p:X}" for p in params[:4])
            advice = list(info.advice)
            if info.params_note:
                advice.append("参数含义：" + "；".join(info.params_note))
        else:
            title = f"蓝屏 0x{code:08X} · 未收录的代码"
            detail = (f"共 {len(evs)} 次。该蓝屏码不在内置知识库中，"
                      f"可把 0x{code:08X} 连同 dmp 一起用转储工作台分析。")
            advice = ["打开 C:\\Windows\\Minidump 下同名 dmp 查看故障模块",
                      "更新最近安装的驱动与系统补丁"]
        res.bugchecks.append((code, len(evs), info.name_cn if info else ""))
        res.findings.append(Finding(
            severity="临界", topic="蓝屏", title=title, detail=detail,
            advice=advice, count=len(evs), first=evs[0].time, last=evs[-1].time,
            evidence=[_ev_line(e) for e in evs[:6]], ref_event_id=1001))


def _rule_power(ctx: _Ctx, res: LogAnalysis) -> None:
    p41 = ctx.get(41)
    crashes = [t for t in res.crash_times]
    near_crash = 0
    standalone: list[Event] = []
    for e in p41:
        hit = False
        if e.time:
            for t in crashes:
                if abs((e.time - t).total_seconds()) <= 300:
                    hit = True
                    break
        if hit:
            near_crash += 1
        else:
            standalone.append(e)

    if near_crash:
        res.notes.append(f"其中 {near_crash} 次 41 事件与蓝屏同时发生，属于蓝屏后的重启，不单独计为掉电")

    if standalone:
        evs = standalone
        res.findings.append(Finding(
            severity="高", topic="掉电",
            title=f"非正常关机 {len(evs)} 次（无蓝屏记录）",
            detail=("Kernel-Power 41 出现但没有对应的 BugCheck(1001)，"
                    "说明系统是「直接断掉」而不是先蓝屏：优先怀疑供电、主板、过温保护、"
                    "以及强行断电/长按电源键。"),
            advice=["检查电源功率是否够（高功耗显卡机型尤甚）与供电线路",
                    "查看主板/CPU 温度与风扇转速记录",
                    "确认现场是否有长按电源键、拔电、UPS 切换",
                    "若为笔记本，检查电池健康与适配器"],
            count=len(evs), first=evs[0].time, last=evs[-1].time,
            evidence=[_ev_line(e) for e in evs[:6]], ref_event_id=41))

    p6008 = ctx.get(6008)
    if p6008 and not standalone:
        res.findings.append(Finding(
            severity="中", topic="掉电",
            title=f"意外关机记录 {len(p6008)} 次",
            detail="事件日志服务记录到上次关机非正常完成，可配合 41 事件判断是蓝屏还是掉电。",
            count=len(p6008), first=p6008[0].time, last=p6008[-1].time,
            evidence=[_ev_line(e) for e in p6008[:4]], ref_event_id=6008))


def _rule_disk(ctx: _Ctx, res: LogAnalysis) -> None:
    groups = {
        "坏块": (ctx.get(7), "高", "磁盘出现坏块错误",
                 "读写重试后仍失败，通常意味着物理坏道或闪存单元劣化。",
                 ["立即备份重要数据", "查 SMART 的 05/C5/C6 计数", "评估更换硬盘"]),
        "分页失败": (ctx.get(51), "高", "页文件所在磁盘读写失败",
                     "这类错误会直接导致 0x77/0x7A 蓝屏。",
                     ["chkdsk /r 全盘检查", "检查 SMART 与数据线"]),
        "IO重试": (ctx.get(153), "中", "磁盘 IO 超时重试",
                   "链路不稳（线材/固件/供电/节能），尚未致命但会拖慢系统并可能演变为蓝屏。",
                   ["更新 SSD/NVMe 固件", "更换 SATA 线或 M.2 插槽", "关闭硬盘 LPM 节能与快速启动"]),
        "复位": (ctx.get(129, 134), "高", "磁盘设备复位 / NVMe 超时",
                 "设备被控制器复位，常见于 NVMe 过热、供电不足或固件缺陷。",
                 ["检查 NVMe 散热片与风道", "更新固件", "BIOS 中关闭激进节能"]),
        "文件系统": (ctx.get(55, 98, 130), "高", "文件系统/卷健康异常",
                     "NTFS 结构或卷状态异常，可能是坏道或掉电导致。",
                     ["chkdsk /f 修复", "检查磁盘健康", "修复后再次导出日志确认不再复现"]),
        "控制器": (ctx.get(11, 157), "高", "磁盘控制器错误 / 设备被移除",
                   "控制器层错误或接触不良。", ["更换线材/插槽", "更新主板芯片组与存储驱动"]),
    }
    for topic_name, (evs, sev, title, detail, advice) in groups.items():
        if not evs:
            continue
        count = len(evs)
        if topic_name == "IO重试" and count < 3:
            sev = "提示"
        res.findings.append(Finding(
            severity=sev, topic="磁盘", title=f"{title}（{count} 条）",
            detail=detail,
            advice=advice,
            count=count, first=evs[0].time, last=evs[-1].time,
            evidence=[_ev_line(e) for e in evs[:5]], ref_event_id=evs[0].event_id))


def _rule_whea(ctx: _Ctx, res: LogAnalysis) -> None:
    uncorrected = ctx.get(18, 19)
    corrected = ctx.get(17, 47)
    mem = ctx.get(46)
    if uncorrected:
        res.findings.append(Finding(
            severity="临界", topic="硬件",
            title=f"WHEA 硬件错误 {len(uncorrected)} 次（不可纠正级）",
            detail=("WHEA 报告了不可纠正错误，这类事件几乎必然伴随 0x124 蓝屏，"
                    "指向 CPU / 内存 / PCIe / 主板供电等真实硬件问题，而不是软件。"),
            advice=["查看每条 WHEA 事件里的“错误来源”，区分 Processor / Memory / PCIe",
                    "BIOS 恢复默认，关闭超频与 XMP/EXPO",
                    "重插内存与 PCIe 设备，清理金手指",
                    "单条内存轮流测试，必要时更换"],
            count=len(uncorrected), first=uncorrected[0].time, last=uncorrected[-1].time,
            evidence=[_ev_line(e) for e in uncorrected[:5]], ref_event_id=18))
    if mem:
        res.findings.append(Finding(
            severity="高", topic="硬件",
            title=f"WHEA 内存错误 {len(mem)} 次",
            detail="内存条或内存控制器报错，XMP/EXPO 超频会显著放大此类错误。",
            advice=["关闭 XMP/EXPO 后复测", "单条内存逐一压力测试", "检查内存插槽与金手指"],
            count=len(mem), first=mem[0].time, last=mem[-1].time,
            evidence=[_ev_line(e) for e in mem[:5]], ref_event_id=46))
    if corrected:
        sev = "高" if len(corrected) >= 10 else "中"
        res.findings.append(Finding(
            severity=sev, topic="硬件",
            title=f"已纠正的硬件错误 {len(corrected)} 次",
            detail=("可纠正错误，单独看不一定致命，但密度高说明硬件在劣化，"
                    "是蓝屏前的早期信号。"),
            advice=["统计出现密度与来源部件", "关闭超频并更新 BIOS", "持续观察是否出现 18/19"],
            count=len(corrected), first=corrected[0].time, last=corrected[-1].time,
            evidence=[_ev_line(e) for e in corrected[:5]], ref_event_id=17))


APP_KEYS_NAME = ("AppName", "ApplicationName", "param1", "名称", "应用程序名")
APP_KEYS_MOD = ("ModuleName", "FaultingModuleName", "param3", "错误模块名称", "模块名")
APP_KEYS_CODE = ("ExceptionCode", "param5", "异常代码")


def _rule_app(ctx: _Ctx, res: LogAnalysis) -> None:
    evs = ctx.get(1000)
    if evs:
        mods: Counter = Counter()
        apps: Counter = Counter()
        for e in evs:
            mod = _pick(e.data, *APP_KEYS_MOD)
            app = _pick(e.data, *APP_KEYS_NAME)
            if mod:
                mods[mod] += 1
            if app:
                apps[app] += 1
        top_mod = mods.most_common(1)
        detail = f"应用程序崩溃 {len(evs)} 次。"
        if apps:
            detail += "涉及程序：" + "、".join(f"{a}({c})" for a, c in apps.most_common(4)) + "。"
        if top_mod:
            detail += f"出现最多的故障模块：{top_mod[0][0]}（{top_mod[0][1]} 次）。"
        advice = ["结合故障模块判断：系统 DLL 多为入参/内存问题，第三方 DLL 优先更新版本",
                  "有对应 dmp 时用转储工作台看异常码与调用栈",
                  "若集中在同一程序，收集版本与复现步骤给研发"]
        res.findings.append(Finding(
            severity="高" if len(evs) >= 3 else "中", topic="应用",
            title=f"应用程序崩溃 {len(evs)} 次",
            detail=detail, advice=advice, count=len(evs),
            first=evs[0].time, last=evs[-1].time,
            evidence=[_ev_line(e) for e in evs[:6]], ref_event_id=1000))

    hangs = ctx.get(1002)
    if hangs:
        res.findings.append(Finding(
            severity="中", topic="应用",
            title=f"应用程序无响应 {len(hangs)} 次",
            detail="界面线程阻塞或等待 IO 超时，常与同步调用、死锁、大文件/设备访问有关。",
            advice=["挂起时抓进程转储，看主线程调用栈", "排查同步 IO 与 UI 线程阻塞"],
            count=len(hangs), first=hangs[0].time, last=hangs[-1].time,
            evidence=[_ev_line(e) for e in hangs[:5]], ref_event_id=1002))

    dotnet = ctx.get(1026)
    if dotnet:
        res.findings.append(Finding(
            severity="高", topic="应用",
            title=f".NET 未处理异常 {len(dotnet)} 次",
            detail="托管代码异常导致进程退出，事件里通常带异常类型与堆栈。",
            advice=["查看事件详细内容中的异常类型", "补充 try/catch 与全局异常处理"],
            count=len(dotnet), first=dotnet[0].time, last=dotnet[-1].time,
            evidence=[_ev_line(e) for e in dotnet[:5]], ref_event_id=1026))


def _rule_service(ctx: _Ctx, res: LogAnalysis) -> None:
    crash = ctx.get(7031, 7034)
    if crash:
        names: Counter = Counter()
        for e in crash:
            nm = _pick(e.data, "param1", "ServiceName", "服务名")
            if nm:
                names[nm] += 1
        detail = f"服务异常终止 {len(crash)} 次。"
        if names:
            detail += "涉及服务：" + "、".join(f"{n}({c})" for n, c in names.most_common(5)) + "。"
        res.findings.append(Finding(
            severity="高", topic="服务", title=f"服务意外终止 {len(crash)} 次",
            detail=detail, advice=["更新对应服务的程序版本", "查看服务自身日志与其依赖"],
            count=len(crash), first=crash[0].time, last=crash[-1].time,
            evidence=[_ev_line(e) for e in crash[:5]], ref_event_id=7031))

    fail = ctx.get(7000, 7009, 7011)
    if fail:
        res.findings.append(Finding(
            severity="中", topic="服务", title=f"服务启动失败/超时 {len(fail)} 次",
            detail="服务未能在超时内进入运行状态，常见于依赖服务未就绪、权限不足、程序损坏。",
            advice=["检查服务账户与依赖服务", "确认程序文件完整", "必要时延长服务启动超时"],
            count=len(fail), first=fail[0].time, last=fail[-1].time,
            evidence=[_ev_line(e) for e in fail[:5]], ref_event_id=7000))


def _rule_driver(ctx: _Ctx, res: LogAnalysis) -> None:
    evs = ctx.get(219, 108)
    if evs:
        res.findings.append(Finding(
            severity="高", topic="驱动", title=f"驱动加载失败/超时 {len(evs)} 次",
            detail="驱动未能加载，通常是签名、兼容性或文件缺失导致；也可能与蓝屏同源。",
            advice=["确认驱动数字签名有效", "更新到厂商最新版本", "检查是否有驱动残留冲突"],
            count=len(evs), first=evs[0].time, last=evs[-1].time,
            evidence=[_ev_line(e) for e in evs[:5]], ref_event_id=219))


def _rule_security(ctx: _Ctx, res: LogAnalysis) -> None:
    cleared = ctx.get(1102, 104)
    if cleared:
        res.findings.append(Finding(
            severity="临界", topic="安全", title=f"事件日志被清空 {len(cleared)} 次",
            detail="日志清空会破坏证据链，需确认是否为授权运维操作。",
            advice=["核对操作人与操作时间", "开启日志转发或集中收集", "检查是否有异常账户活动"],
            count=len(cleared), first=cleared[0].time, last=cleared[-1].time,
            evidence=[_ev_line(e) for e in cleared[:5]], ref_event_id=1102))

    locked = ctx.get(4740)
    fails = ctx.get(4625)
    if locked or len(fails) >= 10:
        res.findings.append(Finding(
            severity="中", topic="安全",
            title=f"登录失败 {len(fails)} 次 / 账户锁定 {len(locked)} 次",
            detail="密集失败登录可能是凭据过期、服务账户配置错误或暴力破解。",
            advice=["确认失败来源 IP 与账户", "更新服务账户凭据", "必要时启用账户锁定策略告警"],
            count=max(len(fails), len(locked)),
            first=(fails[0].time if fails else locked[0].time),
            last=(fails[-1].time if fails else locked[-1].time),
            evidence=[_ev_line(e) for e in (fails[:4] or locked[:4])], ref_event_id=4625))


def _rule_reboot(ctx: _Ctx, res: LogAnalysis) -> None:
    boots = ctx.get(12, 6005)
    res.boot_times = sorted({e.time for e in boots if e.time})
    if len(res.boot_times) >= 4:
        res.findings.append(Finding(
            severity="中", topic="开关机",
            title=f"窗口内 {len(res.boot_times)} 次开机",
            detail="重启频繁会放大故障影响面，需区分是人为重启、更新重启还是崩溃重启。",
            advice=["对照 1074 事件确认重启发起者", "区分更新重启与异常重启"],
            count=len(res.boot_times),
            first=res.boot_times[0], last=res.boot_times[-1],
            evidence=[], ref_event_id=12))

    forced = ctx.get(1074)
    if forced:
        names: Counter = Counter()
        for e in forced:
            nm = _pick(e.data, "param1", "param2")
            if nm:
                names[nm] += 1
        res.notes.append("重启发起者： " + "、".join(f"{n}({c})" for n, c in names.most_common(4)))


def _rule_time(ctx: _Ctx, res: LogAnalysis) -> None:
    evs = ctx.get(1, 50, 36)
    if not evs:
        return
    res.findings.append(Finding(
        severity="提示", topic="时间",
        title=f"时间同步相关记录 {len(evs)} 条",
        detail="时间跳变会让多台机器的日志无法对齐，排查跨设备问题时先校准时间。",
        advice=["检查 NTP 源可达性", "统一内网时间服务器"],
        count=len(evs), first=evs[0].time, last=evs[-1].time,
        evidence=[_ev_line(e) for e in evs[:3]], ref_event_id=1))


def _rule_dcom(ctx: _Ctx, res: LogAnalysis) -> None:
    evs = ctx.get(10016)
    if len(evs) < 20:
        return
    res.findings.append(Finding(
        severity="提示", topic="权限",
        title=f"DCOM 10016 权限告警 {len(evs)} 条",
        detail="权限不足但系统会自动回退到可用权限，绝大多数场景无实际影响，不建议为此改注册表。",
        advice=["一般可忽略", "若确有功能异常，再按 CLSID/APPID 定向授权"],
        count=len(evs), first=evs[0].time, last=evs[-1].time,
        evidence=[_ev_line(e) for e in evs[:3]], ref_event_id=10016))


# ------------------------------------------------------------------ 转储分析
@dataclass
class DumpAnalysis:
    findings: list[Finding] = field(default_factory=list)
    headline: str = ""
    health: int = 100


def cdb_finding(res) -> Optional[Finding]:
    """把 cdb !analyze -v 的结构化结论变成一条诊断发现（界面卡片与报告共用）。"""
    if res is None or not getattr(res, "ok", False):
        return None
    k = dict(getattr(res, "keys", {}) or {})
    mod = k.get("MODULE_NAME", "")
    img = k.get("IMAGE_NAME", "") or (mod + ".sys" if mod else "")
    ver = k.get("IMAGE_VERSION", "")
    proc = k.get("PROCESS_NAME", "")
    bucket = k.get("FAILURE_BUCKET_ID", "")
    sym = k.get("SYMBOL_NAME", "")
    blamed = getattr(res, "blamed", "") or img
    title = f"调试器定位：{blamed}" if blamed else "调试器已取到符号堆栈"

    bits = []
    if mod:
        bits.append(f"模块 {mod}" + (f"（{img} {ver}）" if img and ver else ""))
    if proc:
        bits.append(f"触发进程 {proc}")
    if bucket:
        bits.append(f"失败桶 {bucket}")
    if sym:
        bits.append(f"符号 {sym}")
    detail = "；".join(bits)
    stack = list(getattr(res, "stack", []) or [])[:6]
    if stack:
        detail += "\n调用栈：\n" + "\n".join("  " + s for s in stack)

    advice = []
    if img:
        advice.append(f"优先排查 {img}" + (f"（版本 {ver}）" if ver else "")
                      + "：升级 / 回滚驱动后复测")
    if bucket:
        advice.append(f"用失败桶 {bucket} 检索微软符号库与驱动厂商问题库的同类案例")
    advice.append("把该转储和这份结论一并交给研发，可按符号直接下断点复现")
    return Finding("高", "调试器", title, detail, advice, evidence=stack[:3])


def analyze_dump(rep: DumpReport) -> DumpAnalysis:
    out = DumpAnalysis()
    if rep.kind == "kernel":
        info = kb.bugcheck_info(rep.bugcheck.code)
        if info:  # 回填中文语义，供 KPI 卡与标题使用
            rep.bugcheck.name = info.name
            rep.bugcheck.name_cn = info.name_cn
            rep.bugcheck.advice = list(info.advice)
            rep.bugcheck.culprit_hint = info.culprit
        if not rep.bugcheck.code:
            out.headline = "未能从内核转储中提取蓝屏码"
            out.health = 40
        else:
            title = f"蓝屏 {rep.bugcheck.code_text}" + (f" · {info.name_cn}" if info else " · 未收录代码")
            detail = ""
            if info:
                detail = f"{info.name}（{info.name_cn}）。常见原因方向：{info.culprit}。"
            params = rep.bugcheck.params
            if params:
                detail += " 参数：" + " / ".join(f"0x{p:X}" for p in params)
            advice = list(info.advice) if info else ["把 dmp 交给研发或用 WinDbg/cdb 做符号化分析"]
            if info and info.params_note:
                advice.append("参数含义：" + "；".join(info.params_note))
            out.findings.append(Finding(
                severity="临界", topic="蓝屏", title=title, detail=detail, advice=advice,
                count=1, first=rep.created, last=rep.created,
                evidence=[f"{rep.name}  {rep.kind_text}  {rep.size_text}",
                          f"OS {rep.system.os_name} / {rep.system.arch_name} / {rep.system.cpus} 逻辑处理器"],
                ref_event_id=rep.bugcheck.code))
            out.health = 20
        out.headline = title if rep.bugcheck.code else out.headline
        out.findings.append(Finding(
            severity="中", topic="内核转储",
            title="内核转储不含模块与堆栈信息",
            detail=("内核转储按物理页组织，导出驱动列表与调用栈需要 cdb/WinDbg 与符号服务器。"
                    "蓝屏码 + 参数已经足以确定排查方向；如需函数级定位，安装 Windows SDK 的"
                    "调试工具后本工具会自动调用 cdb。"),
            advice=["安装 Windows SDK → Debugging Tools，或用 Microsoft Store 的 WinDbg",
                    "配置 _NT_SYMBOL_PATH=srv*C:\\Symbols*https://msdl.microsoft.com/download/symbols",
                    "重新分析本文件即可获得符号化堆栈"],
            count=1, first=rep.created, last=rep.created))
        return out

    # 用户模式
    exc_code = rep.exception_code
    info = kb.exception_info(exc_code)
    if info:
        rep.bugcheck.name = info.name
        rep.bugcheck.name_cn = info.name_cn
        rep.bugcheck.advice = list(info.advice)
    fault = rep.faulting_module
    if exc_code:
        title = f"异常 0x{exc_code:08X}" + (f" · {info.name_cn}" if info else " · 未收录异常码")
        detail = ""
        if info:
            detail = f"{info.name}（{info.name_cn}）。"
        if fault:
            off = rep.exception_address - fault.base
            detail += f"故障模块：{fault.short_name} {fault.version}，地址 0x{rep.exception_address:X}（模块内偏移 +0x{off:X}）。"
            if fault.name.lower().startswith(r"c:\windows"):
                detail += " 该模块属于系统组件，通常是调用方传入了非法参数或内存已被破坏。"
            else:
                detail += " 该模块为第三方组件，优先核对其版本与已知缺陷。"
        else:
            detail += f"异常地址 0x{rep.exception_address:X} 不在任何已记录模块范围内。"
        advice = list(info.advice) if info else ["用 WinDbg 加载符号查看调用栈"]
        if rep.faulting_module and not rep.faulting_module.name.lower().startswith(r"c:\windows"):
            advice.insert(0, f"先更新/回滚 {rep.faulting_module.short_name} 到最新稳定版本")
        out.findings.append(Finding(
            severity="临界" if exc_code in (0xC0000005, 0xC0000374, 0xC0000409) else "高",
            topic="应用崩溃", title=title, detail=detail, advice=advice,
            count=1, first=rep.created, last=rep.created,
            evidence=[f"{rep.name}  {rep.kind_text}  {rep.size_text}",
                      f"进程 PID {rep.process_id} / 线程 {rep.faulting_thread} / 模块 {len(rep.modules)} 个",
                      f"OS {rep.system.os_name}（{rep.system.version_text}）{rep.system.arch_name}"],
            ref_event_id=exc_code))
        out.health = 30
        out.headline = title
    else:
        out.headline = "转储中没有异常记录（可能是手动创建或挂起转储）"
        out.health = 70

    # 参数提示
    if info and info.advice:
        out.findings.append(Finding(
            severity="提示", topic="异常参数",
            title=f"{info.name} 排查要点",
            detail="；".join(info.advice),
            advice=[], count=1, first=rep.created, last=rep.created))

    # 第三方模块提示
    third = [m for m in rep.modules if not m.name.lower().startswith(r"c:\windows")
             and not m.is_driver]
    if third:
        out.findings.append(Finding(
            severity="提示", topic="模块清单",
            title=f"加载了 {len(third)} 个第三方模块",
            detail="第三方模块越多，崩溃面越大；若故障模块为空，可在这些模块里找可疑项（版本老旧、同名多份）。",
            advice=[f"{m.short_name} {m.version}".strip() for m in third[:8]],
            count=len(third), first=rep.created, last=rep.created))
    if rep.warnings:
        out.findings.append(Finding(
            severity="中", topic="文件完整性", title="转储文件存在异常",
            detail="；".join(rep.warnings), count=1, first=rep.created, last=rep.created))
    order = {"临界": 0, "高": 1, "中": 2, "提示": 3}
    out.findings.sort(key=lambda f: order.get(f.severity, 9))
    return out
