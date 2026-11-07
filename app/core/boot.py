# -*- coding: utf-8 -*-
"""开机与登录诊断：把 Windows 自己量好的开机耗时拆开看，并点名拖时间的那个。

## 数据从哪来

`Microsoft-Windows-Diagnostics-Performance/Operational`。这个通道从 Vista 起
就在跑，Win10 / Win11 一直没变，字段名也一直是英文（本地化只影响消息文本，
`EventData` 的 `Name` 属性不受语言影响）—— 所以按字段名取值是可靠的。

| ID | 含义 | 本模块怎么用 |
|---|---|---|
| 100 | 一次开机的汇总（40+ 个计时字段） | 一次开机一条 `BootRecord`，拆成八个阶段 |
| 101 | 应用启动超时 | 慢项：应用 |
| 102 | 驱动初始化超时 | 慢项：驱动 |
| 103 | 服务启动超时 | 慢项：服务 |
| 104~108 | 核心系统 / 前台预取 / 后台预取 / 计算机策略 / 用户策略 | 慢项：阶段 |
| 109 | 设备初始化超时 | 慢项：设备 |
| 110 | 会话管理器初始化超时 | 慢项：会话管理器 |
| 200~203 | 关机汇总与拖时间的应用 / 设备 / 服务 | 关机表 |
| 300~303 | 睡眠恢复汇总与拖时间的对象 | 睡眠表 |

## 为什么要拆分阶段，而不是只报一个总时长

「开机 78 秒」这句话对现场没用 —— 该关启动项还是该换硬盘，取决于这 78 秒
花在哪一段。事件 100 把 `BootTime` 切成两半：

- `MainPathBootTime`：Windows 徽标出现 → 桌面 / 登录框出现
- `BootPostBootTime`：桌面出现 → 系统真正闲下来（达到 80% 空闲）

前者慢是「还没进系统就卡住了」（引导、驱动、服务、配置文件），
后者慢是「进去了但还在忙」（启动项、杀毒、同步盘）。
这两个方向的处理手法完全不同，所以必须先分。

## 阶段字段能覆盖多少

`MainPath` 里能被字段归因的只有 10 个（内核 / 驱动 / 设备 / 预取 / 自检 /
SMSS / 关键服务 / 用户配置 / 计算机配置 / Explorer）。实测本机样本
（`MainPathBootTime=30263`）这 10 项加起来 29126 ms，剩下约 1.1 秒归不进任何字段；
另一台机器样本 `MainPath=64858` 时残差有 34 秒（大头是 Session Init 与
Winlogon 等待）。所以本模块**不假装能全解释**：残差单列成
「会话与登录（未细分）」，占比大时结论就直接说「Windows 没细分这段，建议按
组策略 / 登录脚本 / 网络等待的方向查」。

## 为什么用「相对基线」而不是硬阈值

同一台机器冷启动 22 秒很正常，另一台 45 秒也很正常。拿固定阈值判「慢」
会把一半的正常机器标红。所以：**每段都跟本机自己最近若干次的中位数比**，
超过 1.6 倍且多出 1 秒以上才算这次这段异常；硬阈值（45 秒 / 90 秒）
只在记录太少、没有基线时兜底。

## 只读

全程只读事件日志与一个注册表值（`HiberbootEnabled`，用来判断是不是
「快速启动」口径）。不改电源计划、不改启动项、不动注册表 —— 建议动作只写
「该去哪儿关什么」。
"""
from __future__ import annotations

import glob
import os
import statistics
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from . import evtx
from .analysis import Finding

CHANNEL = "Microsoft-Windows-Diagnostics-Performance/Operational"
LOG_GLOB = "Microsoft-Windows-Diagnostics-Performance*Operational.evtx"

BOOT_ID = 100
SHUTDOWN_ID = 200
SLEEP_ID = 300

# 慢项事件：ID -> (类别, 说明)
SLOW_KINDS = {
    101: ("应用", "应用启动超时"),
    102: ("驱动", "驱动初始化超时"),
    103: ("服务", "服务启动超时"),
    104: ("阶段", "核心系统初始化超时"),
    105: ("阶段", "前台预取超时"),
    106: ("阶段", "后台预取超时"),
    107: ("阶段", "计算机策略应用超时"),
    108: ("阶段", "用户策略应用超时"),
    109: ("设备", "设备初始化超时"),
    110: ("阶段", "会话管理器初始化超时"),
}
MISC_KINDS = {
    200: ("关机", 0), 201: ("关机", 201), 202: ("关机", 202), 203: ("关机", 203),
    300: ("睡眠", 0), 301: ("睡眠", 301), 302: ("睡眠", 302), 303: ("睡眠", 303),
}
# 关机 / 睡眠里的小类（0 = 汇总事件本身）
MISC_SUB = {
    201: "应用拖时间", 202: "设备拖时间", 203: "服务拖时间",
    301: "应用拖时间", 302: "设备拖时间", 303: "服务拖时间",
}

# 阶段定义：(key, 中文名, 组成字段, 一句话解释)
PHASES = (
    ("loader", "引导加载", ("OSLoaderDuration",),
     "启动管理器与 winload，发生在 Windows 徽标之前；这段时间不计入 BootTime"),
    ("kernel", "内核初始化", ("BootKernelInitTime",), "内核自身初始化"),
    ("driver", "驱动与设备", ("BootDriverInitTime", "BootDevicesInitTime"),
     "加载启动驱动 + PnP 枚举设备"),
    ("prefetch", "预取与自检", ("BootPrefetchInitTime", "BootAutoChkTime"),
     "Superfetch 预读与磁盘自检"),
    ("session", "会话与服务", ("BootSmssInitTime", "BootCriticalServicesInitTime"),
     "SMSS 建会话 + 关键服务启动"),
    ("desk", "用户配置与桌面",
     ("BootUserProfileProcessingTime", "BootMachineProfileProcessingTime",
      "BootExplorerInitTime"),
     "加载用户/计算机配置 + 起 Explorer"),
)

# 判定口径（写死在这里，页面与 --scan 共用一套）
SLOW_TOTAL_MS = 45000        # 总时长超过这个数算「偏慢」
VERY_SLOW_MS = 90000         # 超过这个数算「很慢」，直接进严重
MAINPATH_LIMIT_MS = 45000    # 进系统之前就花了这么久，现场可感知
POSTBOOT_LIMIT_MS = 30000    # 桌面出来了还要忙半分钟
PHASE_RATIO = 1.6            # 某一段超过本机中位数的 1.6 倍算这次异常
PHASE_FLOOR_MS = 1500        # 但绝对值太小就不提（1.5 秒以内属抖动）
PHASE_MIN_SAMPLES = 3        # 少于三次记录不建基线，改用硬阈值
ATTACH_SEC = 180             # 慢项事件与 100 事件的配对窗口（秒）
REPEAT_DEGRADE_MS = 5000     # 重复出现且单次超标 5 秒以上才升级为「真问题」
REPEAT_CRIT_MS = 20000       # 重复出现且平均多花 20 秒以上才进「很慢」


def dur_text(ms) -> str:
    """毫秒转人话。开机耗时多是两位数秒，不要一律显示成 0.0 分钟。"""
    ms = max(0, int(ms or 0))
    if ms < 1000:
        return "%d 毫秒" % ms
    if ms < 60000:
        return "%.1f 秒" % (ms / 1000.0)
    m, s = divmod(int(round(ms / 1000.0)), 60)
    return "%d 分 %d 秒" % (m, s) if s else "%d 分" % m


def sec_text(ms) -> str:
    """表格里用的短格式：统一「秒」，不换算成分。"""
    return "%.1f 秒" % (max(0, int(ms or 0)) / 1000.0)


def _int(data: dict, key: str) -> int:
    """字段缺失 / 非数字一律给 0 —— 老系统上部分字段确实不存在。"""
    try:
        return max(0, int(float(str(data.get(key, "") or 0))))
    except (TypeError, ValueError):
        return 0


def _bool(data: dict, key: str) -> bool:
    return str(data.get(key, "")).strip().lower() in ("true", "1", "yes")


def _time(text: str) -> Optional[datetime]:
    """`2019-08-29T10:51:02.707926000Z` → 本地 naive datetime。"""
    if not text:
        return None
    try:
        s = str(text).replace("Z", "+00:00")
        if "." in s:
            head, tail = s.split(".", 1)
            frac, rest = "", ""
            for i, ch in enumerate(tail):
                if ch.isdigit():
                    frac += ch
                else:
                    rest = tail[i:]
                    break
            s = "%s.%s%s" % (head, frac[:6].ljust(6, "0"), rest)
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is not None:
            dt = dt.astimezone().replace(tzinfo=None)
        return dt
    except Exception:                     # noqa: BLE001
        return None


# ------------------------------------------------------------------ 数据模型
@dataclass
class BootPhase:
    """一段耗时。"""

    key: str
    label: str
    ms: int = 0
    hint: str = ""
    # 两层归属要分开：BootTime = MainPath + PostBoot，
    # 而「引导加载」连 BootTime 都不算（发生在 Windows 徽标之前）。
    in_boot: bool = True          # 计入总时长 BootTime
    in_main: bool = True          # 计入 MainPath（进系统前那一段）

    @property
    def text(self) -> str:
        return dur_text(self.ms)


@dataclass
class SlowItem:
    """一条「谁拖慢了启动」的原始记录。"""

    kind: str = ""                # 应用 / 驱动 / 服务 / 设备 / 阶段
    name: str = ""                # 文件名 / 服务名 / 阶段名
    friendly: str = ""
    version: str = ""
    path: str = ""
    company: str = ""
    total_ms: int = 0             # 这次花了多久
    degrade_ms: int = 0           # 比「正常」多花了多久
    event_id: int = 0
    when: Optional[datetime] = None
    # 聚合后才有意义
    count: int = 1
    worst_ms: int = 0
    total_degrade: int = 0

    @property
    def key(self) -> tuple:
        return (self.kind, (self.name or self.friendly).lower())

    @property
    def title(self) -> str:
        return self.friendly or self.name or "（未命名）"

    @property
    def sub(self) -> str:
        """表格里的「是谁」：有友好名就补上文件名，否则给路径 basename。"""
        if self.friendly and self.name and self.friendly != self.name:
            return self.name
        if self.path:
            return os.path.basename(self.path.replace("\\", "/")) or self.path
        return self.version or ""

    @property
    def total_text(self) -> str:
        return sec_text(self.worst_ms or self.total_ms)

    @property
    def degrade_text(self) -> str:
        return sec_text(self.total_degrade or self.degrade_ms)

    @property
    def repeat_text(self) -> str:
        return "%d 次" % self.count if self.count > 1 else "1 次"

    @property
    def level(self) -> str:
        """着色用令牌名。反复出现且平均多花 20 秒以上才算严重，否则只是线索。"""
        if self.count >= 2 and (self.total_degrade / max(1, self.count)) >= \
                REPEAT_CRIT_MS:
            return "crit"
        if (self.total_degrade or self.degrade_ms) >= REPEAT_DEGRADE_MS:
            return "warn"
        return "dim"

    @property
    def verdict(self) -> str:
        if self.count >= 3:
            return "反复拖慢"
        if self.count == 2:
            return "两次都慢"
        return "单次记录"

    @property
    def verdict_token(self) -> str:
        """「判定」列的颜色只看出现次数：单次记录是线索，不是问题 ——
        给它上黄字会让一屏里全是「警告」，真正的重复项反而看不出来。"""
        if self.count >= 3:
            return "crit"
        if self.count == 2:
            return "warn"
        return "dim"


@dataclass
class BootRecord:
    """一次开机（事件 100）。"""

    when: Optional[datetime] = None
    boot_ms: int = 0
    main_ms: int = 0
    post_ms: int = 0
    apps: int = 0
    degraded: bool = False
    degrade_delta: int = 0
    fields: dict = field(default_factory=dict)

    @property
    def time_text(self) -> str:
        return self.when.strftime("%Y-%m-%d %H:%M:%S") if self.when else "—"

    @property
    def short_time(self) -> str:
        return self.when.strftime("%m-%d %H:%M") if self.when else "—"

    @property
    def boot_text(self) -> str:
        return dur_text(self.boot_ms)

    @property
    def main_text(self) -> str:
        return dur_text(self.main_ms)

    @property
    def post_text(self) -> str:
        return dur_text(self.post_ms)

    @property
    def main_ratio(self) -> float:
        return (self.main_ms / self.boot_ms) if self.boot_ms else 0.0

    @property
    def attributed_ms(self) -> int:
        """被字段解释掉的 MainPath 时间。"""
        total = 0
        for _k, _label, keys, _hint in PHASES:
            if _k == "loader":
                continue
            total += sum(_int(self.fields, f) for f in keys)
        return total

    @property
    def unexplained_ms(self) -> int:
        """MainPath 里没有字段对应的部分（会话初始化、Winlogon 等待等）。"""
        return max(0, self.main_ms - self.attributed_ms)

    @property
    def phases(self) -> list:
        out = []
        for key, label, keys, hint in PHASES:
            ms = sum(_int(self.fields, f) for f in keys)
            out.append(BootPhase(key=key, label=label, ms=ms, hint=hint,
                                 in_boot=(key != "loader")))
        out.append(BootPhase(
            key="rest", label="会话与登录（未细分）", ms=self.unexplained_ms,
            hint="MainPath 里没有被任何字段单独计时的部分：会话初始化、"
                 "Winlogon 等待、组策略与登录脚本多数落在这里"))
        out.append(BootPhase(
            key="post", label="登录后后台", ms=self.post_ms,
            in_boot=True, in_main=False,
            hint="桌面已经出来、系统还没闲下来：启动项、杀毒扫描、同步盘"))
        return out

    @property
    def phase_max(self) -> int:
        return max([p.ms for p in self.phases] + [1])


@dataclass
class MiscEvent:
    """关机 / 睡眠恢复。"""

    kind: str = "关机"
    sub: str = ""
    when: Optional[datetime] = None
    ms: int = 0
    degraded: bool = False
    detail: str = ""

    @property
    def time_text(self) -> str:
        return self.when.strftime("%m-%d %H:%M:%S") if self.when else "—"

    @property
    def ms_text(self) -> str:
        return dur_text(self.ms)


@dataclass
class BootScan:
    """一次扫描的全部结果。"""

    records: list = field(default_factory=list)      # list[BootRecord]，新→旧
    slow: list = field(default_factory=list)         # list[SlowItem]，聚合后
    misc: list = field(default_factory=list)         # list[MiscEvent]
    error: str = ""
    admin: bool = True
    problems: list = field(default_factory=list)
    fast_startup: Optional[bool] = None
    orphan_slow: int = 0                             # 没配到开机的慢项条数
    event_total: int = 0
    cost_ms: int = 0

    # ---- 统计
    @property
    def count(self) -> int:
        return len(self.records)

    @property
    def latest(self) -> Optional[BootRecord]:
        return self.records[0] if self.records else None

    @property
    def avg_ms(self) -> int:
        return int(sum(r.boot_ms for r in self.records) / len(self.records)) \
            if self.records else 0

    @property
    def median_ms(self) -> int:
        return int(statistics.median([r.boot_ms for r in self.records])) \
            if self.records else 0

    @property
    def baseline_ms(self) -> int:
        """除最近一次以外的中位数 —— 拿它判断「这次是不是反常」。"""
        if len(self.records) >= PHASE_MIN_SAMPLES:
            return int(statistics.median([r.boot_ms for r in self.records[1:]]))
        return self.median_ms

    @property
    def slowest(self) -> Optional[BootRecord]:
        return max(self.records, key=lambda r: r.boot_ms) if self.records else None

    @property
    def degraded_count(self) -> int:
        return sum(1 for r in self.records if r.degraded)

    @property
    def deltas(self) -> list:
        """每次开机相对基线的差值，给曲线下面的说明用。"""
        base = self.baseline_ms
        return [r.boot_ms - base for r in self.records] if base else []

    # ---- 结论
    @property
    def level(self) -> str:
        """整页结论等级。

        只出现一两次的慢项不能把整机判成「很慢」：杀毒软件在两次开机里各多花
        十几秒很常见，这么判会让一半的正常机器进红区。所以「很慢」只给两种情况：
        总时长确实过 90 秒，或者某个组件反复出现且平均多花 20 秒以上。
        """
        if not self.records:
            return "none"
        last = self.latest
        if last.boot_ms >= VERY_SLOW_MS:
            return "crit"
        repeats = [s for s in self.slow if s.count >= 2]
        if (last.boot_ms >= SLOW_TOTAL_MS and
                any(s.total_degrade / max(1, s.count) >= REPEAT_CRIT_MS
                    for s in repeats)):
            return "crit"
        if (last.boot_ms >= SLOW_TOTAL_MS or last.main_ms >= MAINPATH_LIMIT_MS
                or last.post_ms >= POSTBOOT_LIMIT_MS or repeats):
            return "warn"
        return "ok"

    @property
    def headline(self) -> str:
        if self.error:
            return self.error
        if not self.records:
            return "这个通道里还没有开机记录 —— 重启一次后才会写进来"
        last = self.latest
        base = self.baseline_ms
        head = "最近一次开机 %s" % last.boot_text
        if base and last.boot_ms != base:
            diff = last.boot_ms - base
            head += "（%s本机基线 %s）" % (
                "快于" if diff < 0 else "慢于", dur_text(base))
        head += "，其中进系统前 %s、桌面出来后 %s" % (last.main_text, last.post_text)
        if last.when:
            head += "；记录时间 %s" % last.when.strftime("%m-%d %H:%M")
        return head

    @property
    def short_line(self) -> str:
        if self.error:
            return self.error
        if not self.records:
            return "没有开机记录"
        last = self.latest
        return "最近开机 %s · 进系统前 %s · 登录后 %s · 慢项 %d 个" % (
            last.boot_text, last.main_text, last.post_text, len(self.slow))

    @property
    def worst_phase(self) -> Optional[BootPhase]:
        """最该被点名的阶段：**又大又反常**的那一段。

        只看绝对值会把每次都是 25 秒的「会话与服务」永远顶在第一位，
        而这次才冒出来的异常段被埋掉；只看倍数则会把「从 40 ms 涨到 120 ms」
        这种三倍但毫无意义的抖动排到第一。所以两个都要：倍数 × 秒数的平方根。
        `sqrt` 是关键 —— 它让「同样反常」时大的那段胜出，
        又不会让大段永久压制真正暴涨的小段。
        """
        last = self.latest
        if last is None:
            return None
        med = self._phase_medians()
        best, best_score = None, -1.0
        for p in last.phases:
            if p.ms <= 0 or p.key == "loader":
                continue
            m = med.get(p.key) or 0
            ratio = (p.ms / m) if m >= 300 else (p.ms / 10000.0)
            if p.ms < PHASE_FLOOR_MS:
                ratio = min(ratio, 0.5)
            score = max(0.2, ratio) * (p.ms / 1000.0) ** 0.5
            if score > best_score:
                best, best_score = p, score
        return best

    def _phase_medians(self) -> dict:
        """每个阶段在本机历史上的中位数。"""
        out: dict = {}
        for key in [p[0] for p in PHASES] + ["rest", "post"]:
            vals = []
            for r in self.records[1:] if len(self.records) > 1 else []:
                for p in r.phases:
                    if p.key == key:
                        vals.append(p.ms)
            if len(vals) >= 2:
                out[key] = int(statistics.median(vals))
        return out

    # ---- 建议
    @property
    def advice(self) -> list:
        """给现场的三五条动作，按「这次最值得先做」排序。"""
        if not self.records:
            return []
        out: list = []
        last = self.latest
        ph = self.worst_phase
        tips = {
            "loader": "引导阶段慢先查固件：BIOS 里把不用的启动项（网络启动、"
                      "光驱、USB）删掉，并确认系统盘是第一启动项",
            "kernel": "内核初始化慢很少见：先看补丁级别与内存条（有过内存报错"
                      "的机器内核初始化会明显变长）",
            "driver": "驱动与设备段慢：拔掉不用的 USB / 读卡器 / 扩展坞再测一次；"
                      "仍慢就在设备管理器里按厂商更新或回滚启动驱动（显卡、存储、网卡）",
            "prefetch": "预取与自检段慢：确认没开着「每次启动都做磁盘检查」；"
                        "机械盘换 SSD 对这一段收益最大",
            "session": "会话与服务段慢：把非必要的自动服务改成「自动（延迟启动）」，"
                       "重点看第三方安全软件和虚拟网卡驱动装的服务",
            "desk": "用户配置与桌面段慢：桌面文件过多、漫游配置文件、"
                    "资源管理器扩展（网盘、右键菜单）都会拖这一段",
            "rest": "这段 Windows 没有细分：按组策略（登录脚本、映射盘、"
                    "打印机）与域控/网络等待的方向查；脱域机器可对比一下",
            "post": "登录后后台慢：这是启动项在忙 —— 用本工具「系统优化 › 启动项」"
                    "按记录关掉不用的，或在本页慢项表里看具体是谁",
        }
        if ph is not None and ph.ms >= PHASE_FLOOR_MS:
            out.append("%s花了 %s，是这次最突出的一段 —— %s"
                       % (ph.label, ph.text, tips.get(ph.key, "")))
        repeats = [s for s in self.slow if s.count >= 2][:3]
        for s in repeats:
            out.append("%s「%s」在 %d 次开机里重复拖慢（合计多花 %s）——%s"
                       % (s.kind, s.title, s.count,
                          dur_text(s.total_degrade),
                          _kind_advice(s.kind)))
        ones = [s for s in self.slow if s.count == 1][:1]
        for s in ones:
            if len(out) < 4:
                out.append("单次记录：%s「%s」这次多花了 %s，只在一次开机里出现，"
                           "先观察不必动手" % (s.kind, s.title,
                                              dur_text(s.degrade_ms)))
        if (last.post_ms >= POSTBOOT_LIMIT_MS and len(out) < 4
                and (ph is None or ph.key != "post")):
            out.append("桌面出来后又忙了 %s，启动项总数 %d 个 ——"
                       "关掉一半不影响使用的启动项，这一段会直接变短"
                       % (last.post_text, last.apps))
        if self.fast_startup and len(out) < 5:
            out.append("本机开着「快速启动」：关机再开机走的是休眠镜像，只有"
                       "「重启」才是完整冷启动 —— 对比数据请统一用重启")
        return out[:5]

    def findings(self) -> list:
        """诊断卡（复用 analysis.Finding，页面与报告同一套判断）。"""
        if self.error:
            return [Finding("高", "开机", "读不到开机诊断记录", self.error,
                            ["右键「以管理员身份运行」再进本页",
                             "若通道确实被关闭：事件查看器 → 应用程序和服务日志 → "
                             "Microsoft → Windows → Diagnostics-Performance → "
                             "启用「Operational」，然后重启一次"],
                            count=0)]
        if not self.records:
            return [Finding("提示", "开机", "通道里没有开机记录",
                            "事件 100 只在开机完成时写一条，通道刚启用时必然是空的。",
                            ["重启一次机器，再回到本页刷新"], count=0)]
        out: list = []
        last = self.latest
        ph = self.worst_phase
        sev = ("临界" if last.boot_ms >= VERY_SLOW_MS else
               "高" if last.boot_ms >= SLOW_TOTAL_MS else "中")
        detail = ("%s · 进系统前 %s（占 %d%%）· 桌面出来后 %s · 启动项 %d 个"
                  % (last.time_text, last.main_text,
                     int(round(last.main_ratio * 100)), last.post_text, last.apps))
        if self.baseline_ms:
            detail += "；本机基线 %s" % dur_text(self.baseline_ms)
        out.append(Finding(sev, "开机", "最近一次开机 %s" % last.boot_text,
                           detail, self.advice[:2],
                           count=self.count, last=last.when))

        if ph is not None and ph.ms >= PHASE_FLOOR_MS:
            out.append(Finding(
                "高" if ph.ms >= MAINPATH_LIMIT_MS / 2 else "中", "开机",
                "最突出的一段：%s %s" % (ph.label, ph.text),
                ph.hint, [a for a in self.advice if ph.label in a][:2],
                count=1, last=last.when))

        repeats = [s for s in self.slow if s.count >= 2][:3]
        for s in repeats:
            out.append(Finding(
                s.level == "crit" and "临界" or "高", "开机",
                "%s「%s」重复拖慢 %d 次" % (s.kind, s.title, s.count),
                "平均每次多花 %s，最多一次 %s。重复出现说明不是偶发抖动。"
                "（%s）" % (dur_text(s.total_degrade // max(1, s.count)),
                            sec_text(s.worst_ms), s.path or s.version or "—"),
                [_kind_advice(s.kind)], count=s.count, last=s.when,
                evidence=[s.path] if s.path else []))

        if last.post_ms >= POSTBOOT_LIMIT_MS:
            out.append(Finding(
                "中", "登录", "桌面出来后又忙了 %s" % last.post_text,
                "这段不是 Windows 在启动，而是启动项在抢磁盘和 CPU。"
                "慢项表里类别为「应用」的那几条通常就是它。",
                ["用「系统优化 › 启动项」关掉不用的自启（可随时复活）",
                 "把杀毒软件的全盘扫描放到空闲时间"],
                count=last.apps, last=last.when))

        slow_misc = [m for m in self.misc if m.ms >= 30000]
        if slow_misc:
            m = max(slow_misc, key=lambda x: x.ms)
            out.append(Finding(
                "提示", m.kind, "%s偏慢：%s" % (m.kind, m.ms_text),
                "共 %d 条%s记录超过 30 秒，最慢的一条是 %s。"
                % (len(slow_misc), m.kind, m.time_text),
                ["关机慢多半是某个应用不肯退（见下方%s表）" % m.kind,
                 "睡眠恢复慢先看显卡与网卡驱动"], count=len(slow_misc)))
        return out


def _kind_advice(kind: str) -> str:
    return {
        "应用": "在「系统优化 › 启动项」里把它关掉或设为延迟，或直接升级/卸载它",
        "驱动": "更新或回滚这个驱动（设备管理器 → 属性 → 驱动程序）",
        "服务": "把它的启动类型改成「自动（延迟启动）」，别直接禁用系统服务",
        "设备": "这个设备初始化超时：查线缆/接口，或在设备管理器里更新固件与驱动",
        "阶段": "这是 Windows 内部阶段，按提示里的方向查，不要试图关服务来压它",
    }.get(kind, "按提示排查")


# ------------------------------------------------------------------ 采集
def channel_file() -> str:
    """通道对应的 .evtx。通道被安全策略挡住时，同一份数据还能按文件读。"""
    root = os.environ.get("SystemRoot", r"C:\Windows")
    pattern = os.path.join(root, "System32", "Winevt", "Logs", LOG_GLOB)
    hits = sorted(glob.glob(pattern))
    return hits[0] if hits else ""


def _error_text(exc: Exception) -> str:
    txt = str(exc)
    if "拒绝" in txt:
        return ("读取 Diagnostics-Performance 通道需要管理员权限："
                "右键「以管理员身份运行」再进本页")
    if "不存在" in txt:
        return ("Diagnostics-Performance 通道未启用或不存在：在事件查看器里"
                "启用 Operational 后重启一次，才会有第一条记录")
    return "读取开机诊断记录失败：%s" % txt


def read_events(limit: int = 400, channel: str = CHANNEL) -> tuple:
    """读事件。返回 (事件列表, 错误文案)。通道读不到就退回磁盘文件。"""
    xpath = ("*[System[(EventID>=100 and EventID<=110) or "
             "(EventID>=200 and EventID<=203) or (EventID>=300 and EventID<=303)]]")
    err = ""
    try:
        events, _stats = evtx.query_events(channel, False, xpath=xpath,
                                          limit=limit, message_mode="none")
        return events, ""
    except Exception as exc:                  # noqa: BLE001
        err = _error_text(exc)
    path = channel_file()
    if path:
        try:
            events, _stats = evtx.query_events(path, True, xpath=xpath,
                                              limit=limit, message_mode="none")
            return events, ""
        except Exception:                     # noqa: BLE001
            pass                              # 文件也读不到，保留通道那条原因
    return [], err


def is_admin() -> bool:
    """当前进程是否提权。这个通道普通用户读出来是空的（`EvtQuery` 带
    `TolerateQueryErrors` 时不会报错，只会返回 0 条）—— 不区分这一点，
    页面就会把「没权限」说成「这台机器没有开机记录」。"""
    try:
        import ctypes
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:                     # noqa: BLE001
        return False


def fast_startup_enabled() -> Optional[bool]:
    """快速启动（休眠引导）是否开启。它会让「关机再开」不再等于冷启动。"""
    try:
        import winreg
    except ImportError:
        return None
    try:
        key = winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SYSTEM\CurrentControlSet\Control\Session Manager\Power")
    except OSError:
        return None
    try:
        val, _kind = winreg.QueryValueEx(key, "HiberbootEnabled")
        return bool(int(val))
    except (OSError, ValueError, TypeError):
        return None
    finally:
        try:
            key.Close()
        except OSError:
            pass


def _parse_boot(ev) -> Optional[BootRecord]:
    d = ev.data or {}
    rec = BootRecord(
        when=_time(d.get("BootEndTime", "")) or ev.time,
        boot_ms=_int(d, "BootTime"),
        main_ms=_int(d, "MainPathBootTime"),
        post_ms=_int(d, "BootPostBootTime"),
        apps=_int(d, "BootNumStartupApps"),
        degraded=_bool(d, "BootIsDegradation"),
        degrade_delta=_int(d, "BootDegradationDelta"),
        fields={k: v for k, v in d.items()},
    )
    if rec.boot_ms <= 0:
        # 没有总时长这条记录没法用（解析口径不对时就是这个表现）
        return None
    # 总时长与两段对不上时，以两段为准重建 —— 有些系统在快速启动下
    # BootTime 只记了 MainPath，直接显示会让「进系统前 + 登录后」超过总数
    if rec.main_ms + rec.post_ms > rec.boot_ms + 1000:
        rec.boot_ms = rec.main_ms + rec.post_ms
    return rec


def _parse_slow(ev) -> Optional[SlowItem]:
    d = ev.data or {}
    kind, _desc = SLOW_KINDS.get(ev.event_id, ("阶段", ""))
    name = (d.get("Name") or "").strip()
    friendly = (d.get("FriendlyName") or "").strip()
    path = (d.get("Path") or "").strip().strip('"')
    if not (name or friendly or path):
        return None
    return SlowItem(
        kind=kind, name=name, friendly=friendly,
        version=(d.get("Version") or "").strip(),
        path=path, company=(d.get("CompanyName") or "").strip(),
        total_ms=_int(d, "TotalTime"), degrade_ms=_int(d, "DegradationTime"),
        event_id=ev.event_id, when=_time(d.get("StartTime", "")) or ev.time,
    )


def _parse_misc(ev) -> Optional[MiscEvent]:
    d = ev.data or {}
    kind, sub_id = MISC_KINDS.get(ev.event_id, ("", 0))
    if not kind:
        return None
    if sub_id:
        item = _parse_slow(ev)
        if item is None:
            return None
        return MiscEvent(kind=kind, sub=MISC_SUB.get(sub_id, ""), when=item.when,
                         ms=item.total_ms, detail=item.title)
    if ev.event_id == SHUTDOWN_ID:
        ms = _int(d, "ShutdownTime")
        when = _time(d.get("ShutdownStartTime", "")) or ev.time
        degraded = _bool(d, "ShutdownIsDegradation")
        return MiscEvent(kind=kind, sub="合计", when=when, ms=ms,
                         degraded=degraded)
    # 睡眠有「挂起」和「恢复」两段，加起来是总时长；分开写清楚才知道慢在哪头
    suspend = _int(d, "StandbySuspendTotal")
    resume = _int(d, "StandbyResumeTotal")
    ms = suspend + resume
    when = _time(d.get("ResumeStartTime", "")) or ev.time
    degraded = _bool(d, "StandbyIsDegradation")
    if ms <= 0:
        return None
    return MiscEvent(kind=kind, sub="挂起+恢复", when=when, ms=ms,
                     degraded=degraded,
                     detail="挂起 %s / 恢复 %s" % (dur_text(suspend),
                                                  dur_text(resume)))


def _aggregate(items: list) -> list:
    """同名同类合并。只看「多花了多久」，不看「花了多久」——
    一个本来就慢但一直慢的组件不是这次的问题。"""
    buckets: dict = {}
    for it in items:
        key = it.key
        cur = buckets.get(key)
        if cur is None:
            it.count = 1
            it.worst_ms = it.total_ms
            it.total_degrade = it.degrade_ms
            buckets[key] = it
            continue
        cur.count += 1
        cur.worst_ms = max(cur.worst_ms, it.total_ms)
        cur.total_degrade += it.degrade_ms
        if it.when and (cur.when is None or it.when > cur.when):
            cur.when = it.when
    out = list(buckets.values())
    out.sort(key=lambda s: (-s.total_degrade, -s.count, -s.worst_ms))
    return out


def _attach(records: list, items: list) -> tuple:
    """把慢项配到「它属于哪次开机」，配不上的单独计数。

    慢项事件与 100 事件是**同一批写入**的（记录时间几乎相同），所以按事件
    时间找最近的 100 就够；`StartTime` 是这次启动里那个组件的开始时刻，
    拿它配会偏到上一次开机去。
    """
    if not records:
        return 0
    times = [(r.when, r) for r in records if r.when]
    orphan = 0
    for it in items:
        if it.when is None:
            orphan += 1
            continue
        best, gap = None, None
        for when, rec in times:
            d = abs((it.when - when).total_seconds())
            if gap is None or d < gap:
                best, gap = rec, d
        if best is None or (gap is not None and gap > ATTACH_SEC):
            orphan += 1
    return orphan


def scan(events: Optional[list] = None, limit: int = 400,
         with_registry: bool = True) -> BootScan:
    """扫一次。`events` 可注入，方便单测与离线分析。"""
    t0 = time.perf_counter()
    s = BootScan(admin=is_admin())
    if events is None:
        events, err = read_events(limit=limit)
        s.error = err
    s.event_total = len(events)

    boots: list = []
    slows: list = []
    for ev in events:
        if ev.event_id == BOOT_ID:
            rec = _parse_boot(ev)
            if rec is not None:
                boots.append(rec)
            continue
        if ev.event_id in SLOW_KINDS:
            item = _parse_slow(ev)
            if item is not None:
                slows.append(item)
            continue
        m = _parse_misc(ev)
        if m is not None:
            s.misc.append(m)

    boots.sort(key=lambda r: r.when or datetime.min, reverse=True)
    s.records = boots
    s.misc.sort(key=lambda m: m.when or datetime.min, reverse=True)
    s.orphan_slow = _attach(boots, slows)
    s.slow = _aggregate(slows)
    if s.orphan_slow:
        s.problems.append(
            "%d 条慢项记录配不到对应的开机事件（日志滚动掉了 100 事件），"
            "仍计入统计但不参与单次拆解" % s.orphan_slow)
    if with_registry:
        s.fast_startup = fast_startup_enabled()
    s.cost_ms = int((time.perf_counter() - t0) * 1000)
    if not events and not s.error:
        # 空结果有两种完全不同的原因，别混成一句
        if not s.admin:
            s.error = ("读不到开机诊断记录：当前不是管理员，"
                       "这个通道要提权才读得到（右键「以管理员身份运行」）")
        else:
            s.error = "通道里没有任何性能诊断事件"
    return s


# ------------------------------------------------------------------ 表格行
def boot_rows(s: BootScan) -> list:
    """开机历史表：一行一次开机。

    最后一列给「这次相对本机基线」的差值 —— 原来写死的说明句每行都一样，
    占着最宽的一列却什么也没说。
    """
    base = s.baseline_ms
    out = []
    for r in s.records:
        delta = (r.boot_ms - base) if base else 0
        if not base or abs(delta) < 500:
            cmp_text = "持平"
        elif delta > 0:
            cmp_text = "比基线慢 %s" % dur_text(delta)
        else:
            cmp_text = "比基线快 %s" % dur_text(-delta)
        out.append((r.short_time, r.boot_text, r.main_text, r.post_text,
                    "%d 个" % r.apps, "是" if r.degraded else "—",
                    "%.1f%%" % (r.main_ratio * 100), cmp_text, r))
    return out


def slow_rows(s: BootScan) -> list:
    """慢项表：一行一个「谁」。"""
    return [
        (it.kind, it.title, it.sub, it.repeat_text, it.degrade_text,
         it.total_text, it.verdict, it.path or it.version or "—", it)
        for it in s.slow
    ]


def phase_rows(s: BootScan) -> list:
    """最近一次开机的阶段拆解表。

    两列百分比别混：**占开机比**看「这段有多重」，**较基线**看「这次是不是反常」。
    只给一个百分比时现场会拿 47% 当异常，其实那 47% 每次都一样。
    """
    last = s.latest
    if last is None:
        return []
    med = s._phase_medians()
    out = []
    for p in last.phases:
        base = med.get(p.key)
        token = ""
        if base is not None and p.ms != base:
            diff = p.ms - base
            cmp_text = ("%+d 毫秒" % diff if abs(diff) < 1000
                        else "%+.1f 秒" % (diff / 1000.0))
            # 几十毫秒的差涂红涂绿都是噪声，只有超过 0.5 秒才值得着色
            if abs(diff) >= 500:
                token = "crit" if diff > 0 else "ok"
        elif base is not None:
            cmp_text = "持平"
        else:
            cmp_text = "—（样本不足）"
        share = ("—" if not p.in_boot
                 else "%.0f%%" % (100.0 * p.ms / max(1, last.boot_ms)))
        out.append((p.label, p.text, share, cmp_text, p.hint, token, p))
    return out


def misc_rows(s: BootScan) -> list:
    return [(m.kind, m.sub, m.time_text, m.ms_text,
             "是" if m.degraded else "—", m.detail or "—", m)
            for m in s.misc]
