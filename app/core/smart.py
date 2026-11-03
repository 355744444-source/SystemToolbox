"""磁盘 SMART 深度读取（NVMe 健康日志 / 寿命 / 温度 / 累计写入）。

## 为什么不直读 IOCTL

本机（Win11 26300、stornvme）实测：
- `IOCTL_STORAGE_QUERY_PROPERTY` + `STORAGE_PROTOCOL_SPECIFIC_DATA`：
  PropertyId 试了 19 / 20 / 49 / 50 / 31，ProtocolDataOffset 试了「相对输出缓冲」
  与「置 0」，句柄试了只读与读写两种打开方式 —— **全部返回错误码 1
  （ERROR_INVALID_FUNCTION）**，stornvme 根本不支持这条查询路径。
- `IOCTL_STORAGE_PREDICT_FAILURE` 同样返回 1。
- `IOCTL_ATA_PASS_THROUGH`：本机两块盘是 NVMe + USB 桥，没有 SATA 盘可测，
  所以**不写这段未经验证的代码**——宁可显示「该盘不支持」也不输出可能错的属性表。

真正能拿到数据的是 `Microsoft-Windows-Storage-Storport/Health` 的**事件 512**：
它把整页 NVMe SMART/Health Log 以十六进制塞在 `NvmeHealthLog` 里，
还附带序列号、固件版本、是否启动盘。本机 400 条记录跨 14 个月，
解析出的温度与 `Get-PhysicalDisk` 一致，已用寿命与写入量随时间单调递增。

## 也不用 Get-StorageReliabilityCounter

实测 3.9~8.2 秒，且只给 `Wear`（整数 0）和 `Temperature` 两项，
`PowerOnHours` / `ReadErrorsTotal` / `WriteErrorsUncorrected` 全是 null。
拿 8 秒换两个字段不划算，只保留 `Get-PhysicalDisk` 作为清单与降级来源。

## 一个必须提醒用户的细节

事件里的 `SystemUptime_s` 恒为 660，说明健康日志是**开机约 11 分钟时**写的一条快照，
不是「此刻」的温度。寿命与写入量这类慢变量无所谓，温度会略有滞后，
所以 UI 上要显示记录时间，不能写「当前温度」。
"""
from __future__ import annotations

import os
import re
import struct
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from . import evtx

CHANNEL = "Microsoft-Windows-Storage-Storport/Health"
EVENT_HEALTH = 512
MAX_READ = 600                      # 本机 400 条；多盘机器留余量

# NVMe Critical Warning 的 8 个位（NVMe Base Spec 5.2.12.1.1）
CRIT_BITS: list[tuple[int, str, str]] = [
    (0x01, "可用备用空间低于阈值", "备件块快用完，写放大已经失控，尽快更换"),
    (0x02, "温度超过或低于临界值", "检查散热与风道，笔记本注意底部进灰"),
    (0x04, "可靠性已降级", "控制器自身报可靠性下降，属于硬件告示"),
    (0x08, "介质已置为只读", "盘已进入保护态，只能读，立刻备份并更换"),
    (0x10, "易失内存备份失败", "掉电保护失效，异常断电可能丢缓存数据"),
    (0x20, "持久内存区域已只读", "PMR 区域不可写"),
    (0x40, "持久内存区域可靠性降级", "PMR 可靠性下降"),
    (0x80, "保留", "保留位，正常不会置位"),
]

# System 日志里与磁盘 IO 直接相关的 (Provider, 事件ID)。
#
# **必须连 Provider 一起判**：本机实测 System 日志里的 ID 7 来自
# `Microsoft-Windows-Hyper-V-VmSwitch`，内容是「Miniport NIC ... successfully
# initialized」（虚拟网卡初始化），和磁盘毫无关系；只有 `disk` provider 的
# ID 7 才是「有坏块」。只按 ID 判定会把一台健康的机器直接标成坏盘。
IO_EVENT_IDS: dict[tuple[str, int], tuple[str, str, str]] = {
    ("disk", 7): ("磁盘有坏块", "crit", "介质层面已确认坏块，先备份再谈修复"),
    ("disk", 51): ("分页操作时磁盘错误", "crit", "换页都报错，运行中随时可能卡死"),
    ("disk", 153): ("IO 操作重试", "warn", "重试说明请求失败过一次，密度高时是盘在求救"),
    ("disk", 157): ("磁盘意外移除", "warn", "热插拔或供电瞬断"),
}

# 129「控制器复位」的 Provider 随驱动不同而不同，用集合匹配。
# 本机 31 次复位全部来自 UASPStor —— 也就是那块 USB 硬盘盒，而不是内置 NVMe。
RESET_PROVIDERS = ("uaspstor", "storahci", "stornvme", "stornvme", "iastor",
                   "iastorav", "iastorv", "megasr", "nvraid", "msdsm")
RESET_INFO = ("磁盘复位（控制器重置）", "warn",
              "链路不稳或供电不足，常见于 USB 桥与劣质数据线")


def io_kind(provider: str, event_id: int) -> Optional[tuple[str, str, str]]:
    """判断一条日志是不是磁盘 IO 事件；不是就返回 None。"""
    prov = (provider or "").strip().lower()
    if event_id == 129 and prov in RESET_PROVIDERS:
        return RESET_INFO
    return IO_EVENT_IDS.get((prov, event_id))


def _dev_label(data: dict) -> str:
    """从 EventData 里挖出「哪块盘」。153/51 用 #0 的硬链路名，129 用 RaidPort。"""
    raw = ""
    for key in ("#0", "DeviceName", "param1", "param2"):
        v = data.get(key)
        if isinstance(v, str) and v.strip():
            raw = v.strip()
            break
    if not raw:
        return ""
    m = re.search(r"Harddisk(\d+)", raw, re.I)
    if m:
        return "磁盘 %s" % m.group(1)
    m = re.search(r"RaidPort(\d+)", raw, re.I)
    if m:
        return "RAID 端口 %s" % m.group(1)
    return raw.split("\\")[-1][:24]


def _run_ps(script: str, timeout: int = 25) -> str:
    """跑 PowerShell，失败返回空串。与 disk.py 同一套降级约定。"""
    import subprocess
    if os.name != "nt":
        return ""
    try:
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        p = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive",
             "-ExecutionPolicy", "Bypass", "-Command", script],
            capture_output=True, timeout=timeout,
            creationflags=0x08000000, startupinfo=si)
        return (p.stdout or b"").decode("utf-8", "replace")
    except Exception:       # noqa: BLE001
        return ""


# ---------------------------------------------------------------- NVMe 日志解析

@dataclass
class NvmeLog:
    """NVMe SMART / Health Information Log Page（前 216 字节有用的部分）。"""
    critical_warning: int = 0
    temp_c: int = 0
    sensors_c: list[int] = field(default_factory=list)
    avail_spare: int = 0
    spare_threshold: int = 0
    percent_used: int = 0
    read_gb: float = 0.0
    write_gb: float = 0.0
    hours: int = 0
    cycles: int = 0
    unsafe_shutdown: int = 0
    media_err: int = 0
    err_log_entries: int = 0

    @property
    def crit_names(self) -> list[str]:
        return [name for bit, name, _hint in CRIT_BITS
                if self.critical_warning & bit]

    @property
    def life_left(self) -> int:
        """剩余寿命百分比（NVMe 的 percentage_used 是"已用寿命"，0~100 或更高）。"""
        return max(0, 100 - self.percent_used)


def parse_nvme_log(hexstr: str) -> Optional[NvmeLog]:
    """把 NvmeHealthLog 的十六进制串解析成结构化数据。

    字段偏移来自 NVMe Base Spec 图 205（SMART/Health Information Log）：
      0      Critical Warning (1)
      1..2   Composite Temperature (Kelvin, uint16)
      3      Available Spare (%)
      4      Available Spare Threshold (%)
      5      Percentage Used
      32..47 Data Units Read  (uint128, 1 单位 = 1000 × 512 字节)
      48..63 Data Units Written
      128..143 Power On Hours
      144..159 Power Cycle count
      160..175 Unsafe Shutdowns
      176..191 Media and Data Integrity Errors
      192..199 Number of Error Information Log Entries
      200..215 温度传感器 1~8
    """
    if not hexstr:
        return None
    try:
        d = bytes.fromhex(hexstr.strip())
    except ValueError:
        return None
    if len(d) < 200:
        return None
    temp_k = struct.unpack_from("<H", d, 1)[0]
    sensors = []
    if len(d) >= 216:
        for i in range(8):
            k = struct.unpack_from("<H", d, 200 + 2 * i)[0]
            if k:
                sensors.append(k - 273)
    return NvmeLog(
        critical_warning=d[0],
        temp_c=(temp_k - 273) if temp_k else 0,
        sensors_c=sensors,
        avail_spare=d[3],
        spare_threshold=d[4],
        percent_used=d[5],
        read_gb=round(int.from_bytes(d[32:48], "little") * 512 * 1000 / 1e9, 1),
        write_gb=round(int.from_bytes(d[48:64], "little") * 512 * 1000 / 1e9, 1),
        hours=int.from_bytes(d[128:144], "little"),
        cycles=int.from_bytes(d[144:160], "little"),
        unsafe_shutdown=int.from_bytes(d[160:176], "little"),
        media_err=int.from_bytes(d[176:192], "little"),
        err_log_entries=int.from_bytes(d[192:200], "little") if len(d) >= 200 else 0,
    )


# ---------------------------------------------------------------- 磁盘对象

@dataclass
class Disk:
    product: str = "-"
    serial: str = ""
    firmware: str = ""
    bus: str = "-"
    size_gb: float = 0.0
    boot: bool = False
    when: Optional[datetime] = None
    log: Optional[NvmeLog] = None
    first: Optional[NvmeLog] = None      # 时间最早的一条，用来算日均
    samples: int = 0
    span_days: float = 0.0
    per_day_gb: float = 0.0              # 日均写入（按首末两条差分）
    source: str = "storport"
    level: str = "ok"
    status: str = "正常"
    hint: str = ""

    @property
    def title(self) -> str:
        return self.product.strip() or self.serial or "未知磁盘"

    @property
    def key(self) -> str:
        return self.serial or self.product

    @property
    def life_text(self) -> str:
        if not self.log:
            return "—"
        return f"{self.log.life_left}%"

    @property
    def life_tip(self) -> str:
        if not self.log:
            return "该盘没有上报 NVMe 健康日志"
        return ("剩余寿命 %d%%，已用 %d%%（NVMe 的 Percentage Used，"
                "按厂商预估的总写入量折算）" % (self.log.life_left,
                                              self.log.percent_used))

    @property
    def temp_text(self) -> str:
        if not self.log or not self.log.temp_c:
            return "—"
        return f"{self.log.temp_c}℃"

    @property
    def write_text(self) -> str:
        if not self.log:
            return "—"
        return _gb_text(self.log.write_gb)

    @property
    def read_text(self) -> str:
        if not self.log:
            return "—"
        return _gb_text(self.log.read_gb)

    @property
    def when_text(self) -> str:
        return self.when.strftime("%Y-%m-%d %H:%M") if self.when else "—"

    @property
    def remain_text(self) -> str:
        """按当前写入速度粗估还能用多久。样本不足就说算不出，不编。"""
        if not self.log or self.per_day_gb <= 0:
            return "—"
        used = self.log.percent_used
        if used <= 0:
            return "样本不足"
        total_gb = self.log.write_gb / used * 100        # 反推整盘设计写入量
        left_gb = max(0.0, total_gb - self.log.write_gb)
        days = left_gb / self.per_day_gb
        if days > 365 * 30:
            return "30 年以上"
        if days > 365:
            return f"约 {days / 365:.1f} 年"
        return f"约 {days:.0f} 天"


def _gb_text(gb: float) -> str:
    if gb >= 1024:
        return f"{gb / 1024:.2f} TB"
    return f"{gb:.0f} GB"


def _add_months_first(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d") if dt else "—"


# ---------------------------------------------------------------- 采集

def _from_events(stop_flag=None, progress=None) -> list[Disk]:
    """读 Storport 健康事件，按序列号归并成每块盘一条记录。"""
    try:
        events, _stats = evtx.query_events(CHANNEL, False, limit=MAX_READ,
                                          message_mode="none")
    except Exception:       # noqa: BLE001
        return []
    by_sn: dict[str, list] = {}
    for e in events:
        if e.event_id != EVENT_HEALTH:
            continue
        sn = (e.data.get("SerialNumber") or "").strip()
        if not sn:
            continue
        by_sn.setdefault(sn, []).append(e)

    out: list[Disk] = []
    for i, (sn, lst) in enumerate(by_sn.items()):
        if stop_flag is not None and stop_flag():
            break
        lst.sort(key=lambda x: x.time or datetime.min)
        latest = lst[-1]
        d = Disk(serial=sn, product=(latest.data.get("ProductId") or "").strip(),
                 firmware=(latest.data.get("FirmwareRevision") or "").strip(),
                 bus=_bus_from_miniport(latest.data.get("MiniportName", "")),
                 boot=(latest.data.get("BootDevice", "").lower() == "true"),
                 when=latest.time, samples=len(lst))
        d.log = parse_nvme_log(latest.data.get("NvmeHealthLog", ""))
        d.first = parse_nvme_log(lst[0].data.get("NvmeHealthLog", ""))
        if lst[0].time and latest.time:
            d.span_days = max(0.0, (latest.time - lst[0].time).total_seconds() / 86400)
        if d.log and d.first and d.span_days >= 1:
            delta = d.log.write_gb - d.first.write_gb
            if delta > 0:
                d.per_day_gb = round(delta / d.span_days, 1)
        _judge(d)
        out.append(d)
        if progress:
            progress(i + 1, len(by_sn), d.title)
    return out


def _bus_from_miniport(miniport: str) -> str:
    m = (miniport or "").lower()
    if "nvme" in m:
        return "NVMe"
    if "ahci" in m or "ata" in m:
        return "SATA"
    if "raid" in m or "megaraid" in m:
        return "RAID"
    if "usb" in m or "uasp" in m:
        return "USB"
    return miniport.strip() or "-"


def _judge(d: Disk) -> None:
    """按 NVMe 规范与实际经验分级。顺序很重要：先致命、再寿命、再温度。"""
    lg = d.log
    if lg is None:
        d.level, d.status = "info", "无 SMART 数据"
        d.hint = ("该盘没有上报 NVMe 健康日志（SATA / USB 桥通常不报）。"
                  "本工具不解析未经实测的 SATA 属性表，所以只显示系统健康状态。")
        return
    if lg.critical_warning:
        d.level = "crit"
        d.status = "；".join(lg.crit_names) or f"Critical Warning=0x{lg.critical_warning:02X}"
        d.hint = next((hint for bit, _n, hint in CRIT_BITS
                       if lg.critical_warning & bit), "")
        return
    if lg.media_err > 0:
        d.level, d.status = "crit", f"介质错误 {lg.media_err} 次"
        d.hint = "介质错误意味着已经读到坏块且 ECC 修不回来，先备份再谈更换。"
        return
    if lg.spare_threshold and lg.avail_spare <= lg.spare_threshold:
        d.level, d.status = "crit", f"备用空间 {lg.avail_spare}%（阈值 {lg.spare_threshold}%）"
        d.hint = "备用块快耗尽，写放大失控，盘进入寿命末期。"
        return
    if lg.percent_used >= 95:
        d.level, d.status = "crit", f"已用寿命 {lg.percent_used}%"
        d.hint = "寿命指标接近 100%，随时可能转为只读，立刻备份。"
        return
    if lg.percent_used >= 80:
        d.level, d.status = "warn", f"已用寿命 {lg.percent_used}%"
        d.hint = "已过 80% 寿命，建议纳入更换计划，至少保证有备份。"
        return
    if lg.temp_c >= 80:
        d.level, d.status = "crit", f"温度 {lg.temp_c}℃"
        d.hint = "超过 NVMe 的临界温度区间，主控会降速甚至掉盘，先解决散热。"
        return
    if lg.temp_c >= 70:
        d.level, d.status = "warn", f"温度 {lg.temp_c}℃"
        d.hint = "偏热，长期高温会加速电子迁移，检查散热片与风道。"
        return
    if lg.unsafe_shutdown >= 20 and lg.cycles and \
            lg.unsafe_shutdown >= max(5, lg.cycles // 2):
        d.level, d.status = "warn", f"异常掉电 {lg.unsafe_shutdown} / 通电 {lg.cycles} 次"
        d.hint = "异常掉电比例偏高，检查电源与关机流程；频繁掉电会伤映射表。"
        return
    if lg.percent_used >= 60:
        d.level, d.status = "info", f"已用寿命 {lg.percent_used}%"
        d.hint = "已过半寿命，属于正常消耗，可以开始留意备份策略。"
        return
    d.level, d.status = "ok", "正常"
    d.hint = "各项指标均在健康区间，无需处理"


def _attach_sizes(disks: list[Disk]) -> None:
    """容量只能从 Get-PhysicalDisk 拿，按型号名匹配，匹配不上就留空。"""
    from . import disk as diskm
    try:
        phys = diskm.physical_disks()
    except Exception:       # noqa: BLE001
        phys = []
    for d in disks:
        want = d.product.strip().lower()
        for p in phys:
            if p.model.strip().lower() == want:
                d.size_gb = round(p.size / (1024 ** 3), 0)
                if d.bus in ("-", ""):
                    d.bus = p.bus
                break


def io_errors(limit: int = 4000) -> tuple[list[tuple], dict[int, int]]:
    """System 日志里的磁盘 IO 错误，按事件 ID 计数并给出样本。

    返回 (样本行, {事件ID: 次数})。样本行是 tuple，方便直接喂给 RowModel：
    `(时间, 事件ID, 现象, 设备, 级别, 处置, 次数)`

    **同一秒内同一种事件会合并成一行并计数**：本机实测一次控制器复位会连带
    刷出四五条 ID 153，逐条列出来只会把表格刷成一片同样的文字，看不出重点。
    """
    try:
        events, _st = evtx.query_events("System", False, limit=limit,
                                        message_mode="none")
    except Exception:       # noqa: BLE001
        return [], {}
    counts: dict[int, int] = {}
    buckets: dict[tuple[str, int], list] = {}
    for e in events:
        kind = io_kind(getattr(e, "provider", ""), e.event_id)
        if kind is None:
            continue
        counts[e.event_id] = counts.get(e.event_id, 0) + 1
        stamp = e.time.strftime("%Y-%m-%d %H:%M:%S") if e.time else ""
        key = (stamp, e.event_id)
        if key in buckets:
            buckets[key][6] += 1
            continue
        name, level, hint = kind
        buckets[key] = [e.time, e.event_id, name, _dev_label(e.data),
                        level, hint, 1]
    samples = sorted(buckets.values(),
                     key=lambda r: r[0] or datetime.min, reverse=True)
    return [tuple(r) for r in samples[:200]], counts


@dataclass
class Report:
    disks: list[Disk] = field(default_factory=list)
    io_rows: list[tuple] = field(default_factory=list)
    io_counts: dict[int, int] = field(default_factory=dict)
    elapsed: float = 0.0
    note: str = ""

    @property
    def worst(self) -> str:
        for lv in ("crit", "warn", "info", "ok"):
            if any(d.level == lv for d in self.disks):
                return lv
        return "info"

    @property
    def risks(self) -> list[Disk]:
        return [d for d in self.disks if d.level == "crit"]

    @property
    def warns(self) -> list[Disk]:
        return [d for d in self.disks if d.level == "warn"]

    @property
    def io_total(self) -> int:
        return sum(self.io_counts.values())

    @property
    def io_level(self) -> str:
        """IO 告警自身的严重度：SMART 全好但 IO 一直重试也是问题。"""
        if self.io_counts.get(7) or self.io_counts.get(51):
            return "crit"
        if self.io_total >= 20:
            return "warn"
        if self.io_total:
            return "info"
        return "ok"

    @property
    def headline(self) -> str:
        if not self.disks:
            return "没有读到磁盘健康记录"
        if self.risks:
            return f"{len(self.risks)} 块盘需要立即处理"
        if self.warns:
            return f"{len(self.warns)} 块盘需要关注"
        if self.io_level in ("crit", "warn"):
            bad = "、".join(f"ID {k}×{v}" for k, v in
                            sorted(self.io_counts.items(), key=lambda x: -x[1]))
            return (f"{len(self.disks)} 块盘 SMART 正常，但有 {self.io_total} 条"
                    f"磁盘 IO 告警（{bad}）")
        return f"{len(self.disks)} 块盘全部健康"


def scan(stop_flag=None, progress=None) -> Report:
    """磁盘 SMART 全文检查。progress(done, total, msg)。"""
    import time
    t0 = time.perf_counter()
    rep = Report()
    if progress:
        progress(0, 3, "读取 Storport 健康事件…")
    rep.disks = _from_events(stop_flag=stop_flag)
    if progress:
        progress(1, 3, "匹配磁盘容量…")
    _attach_sizes(rep.disks)
    if progress:
        progress(2, 3, "统计磁盘 IO 错误…")
    rep.io_rows, rep.io_counts = io_errors()
    if not rep.disks:
        rep.note = ("没有读到 Storport 健康事件。可能原因：系统关闭了存储健康上报，"
                    "或这台机器上没有 NVMe 盘。磁盘清单仍会照常显示，"
                    "但拿不到寿命与累计写入。")
    rep.elapsed = time.perf_counter() - t0
    if progress:
        progress(3, 3, "完成")
    return rep
