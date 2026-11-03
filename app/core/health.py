# -*- coding: utf-8 -*-
"""全面体检编排：把九个只读检查归一成一份结论。

为什么要这一层：`HealthPage.run_all()` 只是并发点火九个 `load_xxx()`，
每个函数自己算 KPI、自己 `status.emit`，**没有任何地方持有「全机结论」**。
于是「导出一份体检报告」「批量跑一遍出 JSON」都做不到，只能重跑。

这里把编排和归一化搬到 core（本模块零 PyQt 依赖），界面、命令行、报告
三方共用同一份 `HealthReport`。

结构分两半：
  - `_scan_xxx(ctx)` 只负责取数，返回模块自己的原始报告；
  - `_sec_xxx(raw, fast)` 只做纯变换，把原始报告折成 `Section`。

拆开的意义：界面上的九个 `load_xxx()` 已经并发跑完并留住了原始报告，
`fold()` 可以直接拿来归一 —— 不必为了出报告再扫一遍（那是十几秒）。

只读，不改任何系统状态 —— 和体检页面同一条红线。
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

LEVELS = ("crit", "warn", "info", "ok")
RANK = {name: i for i, name in enumerate(LEVELS)}
LEVEL_CN = {"crit": "严重", "warn": "注意", "info": "提示", "ok": "正常"}
# 健康分按板块扣：一个板块再糟也只影响自己那一份
DEDUCT = {"crit": 40, "warn": 15, "info": 0, "ok": 0}


def worst(levels) -> str:
    """取最严重的等级。空集合当「正常」。"""
    best = "ok"
    for lv in levels:
        if RANK.get(lv, 3) < RANK[best]:
            best = lv
    return best


@dataclass
class Item:
    """一条结论。字段刻意与 `analysis.Finding` 对齐，报告层好复用。"""
    severity: str = "info"
    title: str = ""
    detail: str = ""
    advice: list = field(default_factory=list)
    source: str = ""

    def to_dict(self) -> dict:
        return {"severity": self.severity, "title": self.title,
                "detail": self.detail, "advice": list(self.advice),
                "source": self.source}


@dataclass
class Section:
    """一个体检板块的结论。`counts` 是给概览看的短数字，不进结论卡。"""
    key: str = ""
    title: str = ""
    headline: str = ""
    counts: dict = field(default_factory=dict)
    items: list = field(default_factory=list)
    cost_ms: int = 0
    error: str = ""
    lv: str = ""            # 模块自己的判定；空的才由 items 推

    @property
    def level(self) -> str:
        if self.error:
            return "warn"           # 跑失败本身要让人看见，但不能报成「机器坏了」
        if self.lv in RANK:
            return self.lv          # 模块有权威判定就用它，别让折叠过程改语义
        return worst(i.severity for i in self.items)

    def count(self, severity: str) -> int:
        return sum(1 for i in self.items if i.severity == severity)

    @property
    def crit(self) -> int:
        return self.count("crit")

    @property
    def warn(self) -> int:
        return self.count("warn")

    def to_dict(self) -> dict:
        return {"key": self.key, "title": self.title, "level": self.level,
                "headline": self.headline, "counts": dict(self.counts),
                "crit": self.crit, "warn": self.warn,
                "cost_ms": self.cost_ms, "error": self.error,
                "items": [i.to_dict() for i in self.items]}


@dataclass
class HealthReport:
    sections: list = field(default_factory=list)
    cost_ms: int = 0
    fast: bool = False

    @property
    def level(self) -> str:
        return worst(s.level for s in self.sections)

    @property
    def crit(self) -> int:
        return sum(s.crit for s in self.sections)

    @property
    def warn(self) -> int:
        return sum(s.warn for s in self.sections)

    @property
    def score(self) -> int:
        """粗算健康分（0~100），按板块平均。

        一个板块再糟也只扣自己那一份 —— 若按「问题条数」线性扣分，
        十几个 warn 会把一台整体尚可的机器算成不及格。这不是精确评级。
        """
        if not self.sections:
            return 0
        total = sum(100 - DEDUCT.get(s.level, 0) for s in self.sections)
        return int(round(total / len(self.sections)))

    @property
    def headline(self) -> str:
        # 按严重度挑，不能按板块顺序挑 —— 否则「1 台设备被禁用」会盖住
        # 排在前面的位置、却真正严重的板块（安全基线 crit）
        bad = sorted((s for s in self.sections if s.level in ("crit", "warn")),
                     key=lambda s: RANK.get(s.level, 3))
        if not bad:
            return "九项检查全部通过"
        return bad[0].headline or ("发现 %d 项问题" % (self.crit + self.warn))

    def problems(self, limit: int = 0) -> list:
        """按严重度排序的结论清单，`limit=0` 表示全给。"""
        out = [i for s in self.sections for i in s.items
               if i.severity in ("crit", "warn")]
        out.sort(key=lambda i: RANK.get(i.severity, 3))
        return out[:limit] if limit else out

    def section(self, key: str):
        for s in self.sections:
            if s.key == key:
                return s
        return None

    def to_dict(self) -> dict:
        from .report import VERSION
        return {"app": "SystemToolbox", "version": VERSION,
                "fast": self.fast, "cost_ms": self.cost_ms,
                "level": self.level, "score": self.score,
                "headline": self.headline,
                "crit": self.crit, "warn": self.warn,
                "sections": [s.to_dict() for s in self.sections]}


class Ctx:
    """探针的运行上下文：进度、停止标志、是否快速模式。"""

    def __init__(self, tick=None, stop_flag=None, fast: bool = False):
        self._tick = tick
        self._stop = stop_flag
        self.fast = fast

    def tick(self, done: int, total: int, msg: str = "") -> None:
        if self._tick:
            self._tick(done, total, msg)

    def stopped(self) -> bool:
        return bool(self._stop and self._stop())


def _advice(raw) -> list:
    """`advice` 各模块有 list 也有 str，统一成 list[str]。"""
    if not raw:
        return []
    if isinstance(raw, str):
        return [raw]
    return [str(x) for x in raw]


def _item(f, source: str) -> Item:
    """把一个别的模块的结论折成 Item。

    各模块的结论类字段名不统一，这里只做字段名适配、不动语义：
      - 等级：多数叫 `severity`，`security` 叫 `level`，`upgrade` 叫 `verdict`
      - 标题：多数叫 `title`，`security` 叫 `name`
      - 建议：多数叫 `advice`，`security` 叫 `where`
    """
    sev = (getattr(f, "severity", None) or getattr(f, "level", None)
           or getattr(f, "verdict", None) or "info")
    if sev not in RANK:
        sev = "info"
    return Item(severity=sev,
                title=str(getattr(f, "title", None)
                          or getattr(f, "name", "") or ""),
                detail=str(getattr(f, "detail", "") or ""),
                advice=_advice(getattr(f, "advice", None)
                               or getattr(f, "where", None)),
                source=source)


# -------------------------------------------------------------- 界面复用的原料
@dataclass
class DevParts:
    """设备板块的原始材料：三样都得一起给，`stats` 是关联后的汇总。"""
    devices: list = field(default_factory=list)
    drivers: list = field(default_factory=list)
    stats: object = None


@dataclass
class DumpParts:
    """转储板块的原始材料：快照 + 已经算好的结论。"""
    snap: object = None
    findings: list = field(default_factory=list)


# ---------------------------------------------------------------- 取数（不含判断）
def _scan_devices(ctx: Ctx) -> DevParts:
    from . import devices as dv
    devs = dv.devices()
    ctx.tick(0, 3, "设备枚举完成")
    drvs = dv.drivers()
    ctx.tick(1, 3, "驱动清单完成")
    dv.link_devices(devs, drvs)
    st = dv.stats(devs, drvs)
    ctx.tick(2, 3, "关联完成")
    return DevParts(devs, drvs, st)


def _scan_upgrade(ctx: Ctx):
    from . import upgrade as up
    return up.check()


def _scan_security(ctx: Ctx):
    from . import security as sec
    return sec.scan()


def _scan_component(ctx: Ctx):
    from . import component as comp
    # DISM 要管理员且要几分钟；快速模式下不跑，但结论里必须写明没跑
    return comp.scan(with_dism=not ctx.fast)


def _scan_smart(ctx: Ctx):
    from . import smart as sm
    return sm.scan(stop_flag=lambda: ctx.stopped(),
                   progress=lambda d, t, m: ctx.tick(d, t, m))


def _scan_hw(ctx: Ctx):
    from . import hwevent as hw
    return hw.scan(stop_flag=lambda: ctx.stopped(),
                   progress=lambda d, t, m: ctx.tick(d, t, m))


def _scan_dumpcfg(ctx: Ctx) -> DumpParts:
    from . import dumpcfg as dc
    # 先不读事件（省 200ms），再把蓝屏记录挂上去 —— 与体检页顺序一致。
    # 蓝屏记录只花约 90ms，却是这台机器上最值钱的信息，不跟着 fast 跳过。
    snap = dc.read(with_events=False)
    snap.crashes = dc.scan_crashes()
    return DumpParts(snap, dc.evaluate(snap))


def _scan_pathenv(ctx: Ctx):
    from . import pathenv as pe
    return pe.audit()


def _scan_drivers(ctx: Ctx):
    from . import drivers as drv
    return drv.audit(stop_flag=lambda: ctx.stopped(),
                     progress=lambda d, t, m: ctx.tick(d, t, m))


# ---------------------------------------------------------------- 归一（纯变换）
def _sec_devices(raw: DevParts, fast: bool) -> Section:
    st = raw.stats
    out = []
    for d in raw.devices:
        if not d.problem:
            continue
        name = d.name or "未知设备"
        out.append(Item(
            severity=d.level if d.level in RANK else "warn",
            title="%s %s" % (name, d.status or "异常"),
            detail=" · ".join(x for x in (d.cls, d.hint) if x),
            advice=["在设备管理器里查看该设备，重新安装对应驱动"],
            source="devices"))
    if st.removed:
        out.append(Item(
            severity="info", title="%d 个已移除设备的注册表残留" % st.removed,
            detail="设备换过位置或拔掉后，注册表还留着旧节点。",
            advice=["不影响使用，可忽略；强迫症可用「残留治理」清理"],
            source="devices"))
    return Section(
        key="devices", title="设备与驱动",
        headline="%d 台设备异常" % st.problems if st.problems
        else ("%d 台设备被禁用" % st.disabled if st.disabled
              else "%d 台设备全部正常" % st.devices),
        counts={"设备": st.devices, "异常": st.problems,
                "已禁用": st.disabled, "驱动": st.drivers,
                "第三方驱动": st.third_party},
        items=out)


def _sec_upgrade(rep, fast: bool) -> Section:
    out = []
    for it in rep.items:
        # upgrade 的条目字段是 name / current / required / verdict，
        # 与别处的 Finding 不通用，这里显式转一次
        if it.verdict not in ("crit", "warn"):
            continue
        detail = "本机 %s · 要求 %s" % (it.current or "—", it.required or "—")
        if it.note:
            detail += "。" + it.note
        out.append(Item(
            severity=it.verdict, title=it.name, detail=detail,
            advice=["对照「Win11 升级」页签确认是否升级对应硬件"]
            if it.verdict == "crit" else [],
            source="upgrade"))
    return Section(
        key="upgrade", title="Win11 升级条件",
        headline=getattr(rep, "headline", "") or "",
        counts={"可升级": "是" if rep.ready else "否",
                "硬性不满足": len(rep.blockers),
                "需注意": len(rep.cautions),
                "处理器": getattr(rep, "cpu_gen", "") or getattr(rep, "cpu", "")},
        items=out)


def _sec_security(rep, fast: bool) -> Section:
    out = [_item(f, "security") for f in rep.risks]
    out += [_item(f, "security") for f in rep.warns]
    return Section(
        key="security", title="安全基线",
        headline=rep.headline,
        counts={"评分": rep.score, "风险": len(rep.risks),
                "关注": len(rep.warns)},
        items=out)


def _sec_component(rep, fast: bool) -> Section:
    out = []
    if rep.problems:
        out.append(Item(
            severity="crit", title="系统文件完整性检查发现 %d 个问题" % rep.problems,
            detail="CBS 日志里有无法修复的组件损坏。",
            advice=["管理员命令行执行 sfc /scannow",
                    "仍不通过再执行 DISM /Online /Cleanup-Image /RestoreHealth"],
            source="component"))
    if rep.warns:
        out.append(Item(
            severity="warn", title="CBS 日志有 %d 处警告" % rep.warns,
            detail="多数是「文件已存在」这类正常记录，不必逐条处理。",
            advice=["只关注同一组件反复出现的条目"], source="component"))
    for e in (getattr(rep, "errors", None) or [])[:5]:
        out.append(Item(severity="warn", title=str(e)[:120],
                        detail="来自组件存储的错误记录", source="component"))
    return Section(
        key="component", title="系统文件",
        headline=rep.headline,
        counts={"问题": rep.problems, "警告": rep.warns,
                "DISM": getattr(rep, "dism_text", "") or
                        ("未运行（快速模式）" if fast else "—")},
        items=out)


def _sec_smart(rep, fast: bool) -> Section:
    out = []
    for d in rep.risks:
        out.append(Item(severity="crit", title="%s 需要处理" % d.title,
                        detail=d.life_tip if hasattr(d, "life_tip") else "",
                        advice=["尽快备份数据，准备更换该磁盘"],
                        source="smart"))
    for d in rep.warns:
        out.append(Item(severity="warn", title="%s 需关注" % d.title,
                        detail=d.life_tip if hasattr(d, "life_tip") else "",
                        advice=["关注寿命与温度变化，先备份重要数据"],
                        source="smart"))
    # 盘本身健康但 IO 错误狂刷也是问题 —— 体检页的 KPI 就是这么判的，这里保持一致
    if getattr(rep, "io_level", "ok") in ("crit", "warn") and rep.io_total:
        out.append(Item(
            severity=rep.io_level,
            title="磁盘 IO 告警 %d 条" % rep.io_total,
            detail="不是坏块，是读写请求出错重试（ID 51 多为分页文件 IO 失败）。",
            advice=["先查内存与页面文件所在盘，再查数据线 / 接口",
                    "ID 153 / 129 大量出现通常指向磁盘或线材"],
            source="smart"))
    return Section(
        key="smart", title="磁盘 SMART",
        headline=rep.headline,
        counts={"磁盘": len(rep.disks), "需处理": len(rep.risks),
                "IO 告警": rep.io_total},
        items=out)


def _sec_hw(rep, fast: bool) -> Section:
    out = []
    if rep.fatal:
        out.append(Item(
            severity="crit", title="%d 条致命硬件错误（WHEA）" % rep.fatal,
            detail="CPU / 内存 / PCIe 上报的不可纠正错误，通常表现为随机重启或蓝屏。",
            advice=["先更新主板 BIOS 与芯片组驱动",
                    "再排查内存（MemTest）与超频设置"], source="hw"))
    if rep.corrected:
        out.append(Item(
            severity="warn", title="%d 条已纠正硬件错误" % rep.corrected,
            detail="硬件自己修好了，但反复出现说明在临界状态。",
            advice=["留意是否集中在同一部件（见「硬件错误」页签）"],
            source="hw"))
    return Section(
        key="hw", title="硬件错误",
        headline=rep.headline,
        counts={"致命": rep.fatal, "已纠正": rep.corrected,
                "记录": rep.total},
        items=out)


def _sec_dumpcfg(raw: DumpParts, fast: bool) -> Section:
    from . import dumpcfg as dc
    snap = raw.snap
    findings = list(raw.findings or [])
    if not findings:
        findings = dc.evaluate(snap)        # 界面没算过就现算，纯函数、很便宜
    lv, text = dc.verdict(findings)
    out = [_item(f, "dumpcfg") for f in findings]
    return Section(
        key="dumpcfg", title="崩溃转储",
        headline=text, lv=lv,
        counts={"转储类型": snap.type_text,
                "异常关机": len(getattr(snap, "crashes", None) or [])},
        items=out)


def _sec_pathenv(rep, fast: bool) -> Section:
    out = [_item(f, "pathenv") for f in rep.findings]
    return Section(
        key="pathenv", title="环境变量",
        headline=rep.headline,
        counts={"条目": rep.total, "失效": rep.missing, "重复": rep.dups},
        items=out)


def _sec_drivers(rep, fast: bool) -> Section:
    from . import drivers as drv
    lv, text = drv.verdict(rep)
    out = [_item(f, "drivers") for f in drv.build_findings(rep)]
    return Section(
        key="drivers", title="驱动体检",
        headline=text, lv=lv,
        counts={"驱动包": len(rep.packages),
                "未签名": len(rep.unsigned),
                "文件缺失": len(rep.missing_files)},
        items=out)


# 顺序与体检页的页签一致，报告里也跟着这个顺序
PROBES = (
    ("devices", "设备与驱动", _scan_devices, _sec_devices),
    ("upgrade", "Win11 升级", _scan_upgrade, _sec_upgrade),
    ("security", "安全基线", _scan_security, _sec_security),
    ("component", "系统文件", _scan_component, _sec_component),
    ("smart", "磁盘 SMART", _scan_smart, _sec_smart),
    ("hw", "硬件错误", _scan_hw, _sec_hw),
    ("dumpcfg", "崩溃转储", _scan_dumpcfg, _sec_dumpcfg),
    ("pathenv", "环境变量", _scan_pathenv, _sec_pathenv),
    ("drivers", "驱动体检", _scan_drivers, _sec_drivers),
)

KEYS = tuple(k for k, _t, _s, _f in PROBES)
FOLD = {k: f for k, _t, _s, f in PROBES}
TITLES = {k: t for k, t, _s, _f in PROBES}


def fold(raws: dict, fast: bool = False, cost_ms: int = 0) -> HealthReport:
    """把已经拿到的各模块原始报告折成一份结论，**不重新扫描**。

    界面上的九个 `load_xxx()` 是并发跑的，跑完再调 `run()` 等于全部重扫一遍
    （十几秒白花）。这里只做纯变换，成本是毫秒级。

    `raws` 里缺哪一项，那一项就记成「本次未运行」—— 不能假装它正常。
    """
    sections = []
    for key, title, _scan, sec_fn in PROBES:
        raw = (raws or {}).get(key)
        if raw is None:
            sections.append(Section(key=key, title=title, error="本次未运行"))
            continue
        try:
            sections.append(sec_fn(raw, fast))
        except Exception as exc:            # noqa: BLE001
            sections.append(Section(key=key, title=title,
                                    error="%s: %s" % (type(exc).__name__, exc)))
    return HealthReport(sections=sections, cost_ms=cost_ms, fast=fast)


def run(keys=None, tick=None, stop_flag=None, fast: bool = False) -> HealthReport:
    """跑一遍体检。串行执行 —— 串行才好算单项耗时、好中途停。

    `keys` 为空表示九项全跑；单项失败不中断整体，写进 `Section.error`。
    `fast=True` 跳过 DISM（要管理员且要几分钟），并在结论里写明没跑。
    """
    want = list(keys) if keys else list(KEYS)
    unknown = [k for k in want if k not in KEYS]
    if unknown:
        raise ValueError("未知的体检项：%s" % "、".join(unknown))

    ctx = Ctx(tick=tick, stop_flag=stop_flag, fast=fast)
    t0 = time.perf_counter()
    sections = []
    total = len(want)
    for idx, (key, title, scan_fn, sec_fn) in enumerate(
            p for p in PROBES if p[0] in want):
        if ctx.stopped():
            sections.append(Section(key=key, title=title,
                                    error="已取消，未执行"))
            continue
        ctx.tick(idx, total, "正在检查：%s" % title)
        s0 = time.perf_counter()
        try:
            sec = sec_fn(scan_fn(ctx), ctx.fast)
        except Exception as exc:            # noqa: BLE001
            # 单项炸掉不能让整份报告消失：写清哪项失败，其余照常
            sec = Section(key=key, title=title,
                          error="%s: %s" % (type(exc).__name__, exc))
        sec.cost_ms = int((time.perf_counter() - s0) * 1000)
        sections.append(sec)
        ctx.tick(idx + 1, total, "%s 完成（%d ms）" % (title, sec.cost_ms))

    return HealthReport(sections=sections,
                        cost_ms=int((time.perf_counter() - t0) * 1000),
                        fast=ctx.fast)
