# -*- coding: utf-8 -*-
"""崩溃转储配置体检：回答「蓝屏了为什么没有 dump」。

## 为什么需要这一页

本工具整天分析 .dmp，但用户最常问的其实是「我电脑蓝屏了，你们这没有文件」。
原因几乎总在配置侧：转储类型选的是完整转储、页面文件却只有 2 GB；
或者转储被关掉了；或者那根本不是蓝屏，是掉电。

这一页把结论前置：**先确认下次崩溃能不能留下证据**，再去分析文件。

## 判定依据（全部来自官方文档，不是估算）

`HKLM\\SYSTEM\\CurrentControlSet\\Control\\CrashControl` 的 `CrashDumpEnabled`
枚举（Microsoft KB254649）：

    0 = 无        1 = 完整内存转储   2 = 内核内存转储
    3 = 小型内存转储（256 KB）        4 = 同 3
    7 = 自动内存转储（Win8+ 默认）
    1 + FilterPages = 1 → 活动内存转储

各类转储对页面文件的最小要求（Microsoft Learn「如何确定 64 位版本的
Windows 的适当页面文件大小」表格）：

    小型内存转储        1 MB（老文档写 2 MB，取大值保守）
    内核内存转储        取决于内核虚拟内存使用量
    完整内存转储        1 × RAM + 257 MB（1 MB 头 + 256 MB 驱动数据）
    自动内存转储        取决于内核虚拟内存使用量（首次崩溃后按需扩到 RAM 或 32 GB）

内核/自动这两档没有硬指标，本模块用 `psapi.GetPerformanceInfo` 的
`KernelTotal`（内核分页池 + 非分页池的实际占用）作为基准，**留 30% 余量**
判「可能不足」。这个口径写进了结论文案里，不冒充官方硬指标。

## 实测校准（本机 Win11 26H2，2026-10-23）

- `os.stat(r"C:\\pagefile.sys")` 能直接拿到页面文件大小（2048 MB），
  与 WMI `Win32_PageFileUsage.AllocatedBaseSize` 一致；
  **`GetCompressedFileSizeW` 不能用于 pagefile.sys** —— 它返回 4096 MB 并置
  `ERROR_SHARING_VIOLATION(32)`，是错值，已弃用。
- `GlobalMemoryStatusEx` 的 `ullTotalPageFile - ullTotalPhys` 差值也等于 2.00 GB，
  可作为 `os.stat` 全部失败时的兜底口径。
- 读 System 通道用 `*[System[(EventID=41 or EventID=1001)]]` 过滤约 150~200 ms；
  不带过滤读 2000 条要 475 ms，所以必须带 xpath。
- **`Kernel-Power` id=41 的 `BugcheckCode=0` 表示「不是蓝屏」**：掉电、长按电源、
  硬件死机都会记 41，但只有 BugcheckCode 非 0 才是真的 bugcheck。
  本机 3 条 41 全部是 0，而 Minidump 目录是空的 —— 两者互相印证。
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import os
import re
import winreg
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from . import evtx
from .analysis import Finding

CRASH_KEY = r"SYSTEM\CurrentControlSet\Control\CrashControl"
MM_KEY = r"SYSTEM\CurrentControlSet\Control\Session Manager\Memory Management"

TYPE_CN = {
    0: "无（转储已关闭）",
    1: "完整内存转储",
    2: "内核内存转储",
    3: "小型内存转储",
    4: "小型内存转储",
    7: "自动内存转储",
}

# 官方枚举里真正会产转储的取值。0 与其它值都算「关」
DUMP_TYPES = (1, 2, 3, 4, 7)

BUGCHECK_BIT = 0x80000000      # System 日志里 1001 事件若是 BugCheck 来源
MB = 1024 * 1024
GB = 1024 * 1024 * 1024

_HEX_RE = re.compile(r"0x([0-9a-fA-F]{1,16})")


def human(n: float) -> str:
    """字节数转可读。页面文件/转储动辄几十 GB，用 GB 起步。"""
    if n <= 0:
        return "0 B"
    if n >= GB:
        return "%.2f GB" % (n / GB)
    if n >= MB:
        return "%.1f MB" % (n / MB)
    if n >= 1024:
        return "%.0f KB" % (n / 1024)
    return "%d B" % n


# ---------------------------------------------------------------- 数据结构
@dataclass
class DumpFile:
    path: str
    size: int = 0
    time: Optional[datetime] = None
    kind: str = "unknown"        # small / kernel / complete / unknown

    @property
    def name(self) -> str:
        return os.path.basename(self.path)

    @property
    def dir_name(self) -> str:
        return os.path.dirname(self.path)

    @property
    def size_text(self) -> str:
        return human(self.size)

    @property
    def time_text(self) -> str:
        return self.time.strftime("%Y-%m-%d %H:%M") if self.time else "—"

    @property
    def kind_text(self) -> str:
        return {"small": "小型转储", "kernel": "内核转储",
                "complete": "完整转储"}.get(self.kind, "未识别")


@dataclass
class CrashEvent:
    time: Optional[datetime] = None
    provider: str = ""
    event_id: int = 0
    kind: str = "bugcheck"       # bugcheck / power
    code: int = 0
    params: tuple = ()
    text: str = ""

    @property
    def time_text(self) -> str:
        return self.time.strftime("%Y-%m-%d %H:%M:%S") if self.time else "—"

    @property
    def time_short(self) -> str:
        """表格里用：去掉年份，秒保留（蓝屏时间点要精确到秒）。"""
        return self.time.strftime("%m-%d %H:%M:%S") if self.time else "—"

    @property
    def provider_short(self) -> str:
        """表格里用：`Microsoft-Windows-Kernel-Power` 太长，只留后半段。"""
        p = self.provider or ""
        for pre in ("Microsoft-Windows-", "Microsoft-"):
            if p.startswith(pre):
                return p[len(pre):]
        return p

    @property
    def code_text(self) -> str:
        if self.kind == "bugcheck":
            return "0x%08X" % self.code if self.code else "未记录"
        return "非蓝屏" if not self.code else "0x%08X" % self.code

    @property
    def kind_text(self) -> str:
        if self.kind == "bugcheck":
            return "蓝屏（BugCheck）"
        return "意外关机（非蓝屏）" if not self.code else "意外重启（含 BugCheck）"

    @property
    def explain(self) -> str:
        """表格「说明」列只有约 20 个汉字的位置，长解释留给卡片和 tooltip。"""
        if self.kind == "bugcheck":
            return "蓝屏事件，应有转储"
        if not self.code:
            return "掉电 / 死机，无转储"
        return "关机前有蓝屏码"


@dataclass
class Snap:
    """一次体检的全部事实。字段都可单独构造，方便单测。"""

    # 注册表
    enabled: int = 3
    filter_pages: int = 0
    dump_file: str = r"%SystemRoot%\MEMORY.DMP"
    minidump_dir: str = r"%SystemRoot%\Minidump"
    auto_reboot: int = 1
    overwrite: int = 1
    log_event: int = 1
    always_keep: int = 0
    minidumps_count: int = 0
    dump_filters: list = field(default_factory=list)
    # 页面文件：(展开后的真实路径, 字节数)
    pagefiles: list = field(default_factory=list)
    paging_reg: list = field(default_factory=list)
    # 硬件
    ram: int = 0
    kernel_total: int = 0
    commit_limit: int = 0
    # 现状
    dumps: list = field(default_factory=list)
    live_reports: list = field(default_factory=list)
    crashes: list = field(default_factory=list)
    system_drive: str = "C:"
    free_system: int = 0
    # 元信息
    error: str = ""
    scanned_at: Optional[datetime] = None

    # ---- 派生
    @property
    def is_active_dump(self) -> bool:
        return self.enabled == 1 and self.filter_pages == 1

    @property
    def is_complete(self) -> bool:
        """只有 1 是完整转储；4 与 3 等价，是小型转储。"""
        return self.enabled == 1

    @property
    def disabled(self) -> bool:
        """0 与任何非官方取值都视为「关」。注意 0 在 TYPE_CN 里（用于文案），
        所以不能用 `enabled not in TYPE_CN` 判断。"""
        return self.enabled not in DUMP_TYPES

    @property
    def type_text(self) -> str:
        if self.enabled not in DUMP_TYPES:
            return TYPE_CN.get(self.enabled, "未知（%d）" % self.enabled)
        if self.is_active_dump:
            return "活动内存转储"
        return TYPE_CN[self.enabled]

    @property
    def dump_file_path(self) -> str:
        return os.path.expandvars(self.dump_file or "")

    @property
    def minidump_path(self) -> str:
        return os.path.expandvars(self.minidump_dir or "")

    @property
    def pagefile_bytes(self) -> int:
        return sum(sz for _p, sz in self.pagefiles)

    @property
    def pagefile_on_system(self) -> bool:
        """完整/内核转储要求启动卷上有页面文件。"""
        drv = (self.system_drive or "C:").rstrip("\\").upper()
        for p, _sz in self.pagefiles:
            if p.upper().startswith(drv):
                return True
        return False

    @property
    def bugchecks(self) -> list:
        return [c for c in self.crashes if c.kind == "bugcheck"]

    @property
    def power_losses(self) -> list:
        return [c for c in self.crashes if c.kind == "power" and not c.code]

    @property
    def latest_dump(self) -> Optional[DumpFile]:
        if not self.dumps:
            return None
        return max(self.dumps, key=lambda d: (d.time or datetime.min))

    def need_pagefile(self) -> tuple[int, str]:
        """返回 (页面文件需求下限, 口径说明)。"""
        if self.disabled:
            return 0, "转储已关闭，无需求"
        if self.enabled == 1:
            need = self.ram + 257 * MB
            if self.is_active_dump:
                return need, "活动内存转储，保守按「RAM + 257 MB」校验"
            return need, "官方硬指标：1 × RAM + 257 MB"
        if self.enabled in (2, 7):
            base = max(self.kernel_total, 512 * MB)
            return int(base * 1.3), "按内核实际占用 %s 加 30%% 余量估算" % human(self.kernel_total)
        if self.enabled in (3, 4):
            return 2 * MB, "官方硬指标：启动卷上至少 2 MB"
        return 0, "未知转储类型"


# ---------------------------------------------------------------- 采集
def _reg_values(key: str) -> dict:
    out: dict = {}
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key) as k:
            idx = 0
            while True:
                try:
                    name, val, _typ = winreg.EnumValue(k, idx)
                except OSError:
                    break
                idx += 1
                out[name] = val
    except OSError:
        pass
    return out


class _MEMORYSTATUSEX(ctypes.Structure):
    _fields_ = [("dwLength", wt.DWORD), ("dwMemoryLoad", wt.DWORD),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]


class _PERFINFO(ctypes.Structure):
    _fields_ = [("cb", wt.DWORD), ("CommitTotal", ctypes.c_size_t),
                ("CommitLimit", ctypes.c_size_t), ("CommitPeak", ctypes.c_size_t),
                ("PhysicalTotal", ctypes.c_size_t), ("PhysicalAvailable", ctypes.c_size_t),
                ("SystemCache", ctypes.c_size_t), ("KernelTotal", ctypes.c_size_t),
                ("KernelPaged", ctypes.c_size_t), ("KernelNonpaged", ctypes.c_size_t),
                ("PageSize", ctypes.c_size_t), ("HandleCount", wt.DWORD),
                ("ProcessCount", wt.DWORD), ("ThreadCount", wt.DWORD)]


def memory_facts() -> tuple[int, int, int, int]:
    """返回 (物理内存, 提交上限, 内核占用, 页面文件总量兜底值)。"""
    ram = limit = kernel = 0
    ms = _MEMORYSTATUSEX()
    ms.dwLength = ctypes.sizeof(ms)
    try:
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(ms)):
            ram = ms.ullTotalPhys
            limit = ms.ullTotalPageFile
    except OSError:
        pass
    pi = _PERFINFO()
    pi.cb = ctypes.sizeof(pi)
    try:
        if ctypes.windll.psapi.GetPerformanceInfo(ctypes.byref(pi), pi.cb):
            kernel = pi.KernelTotal * pi.PageSize
    except OSError:
        pass
    fallback = max(0, limit - ram)
    return ram, limit, kernel, fallback


def _expand_nt(path: str) -> str:
    """`\\??\\C:\\pagefile.sys` → `C:\\pagefile.sys`。"""
    p = path.strip()
    for pre in ("\\??\\", "\\\\?\\"):
        if p.startswith(pre):
            p = p[len(pre):]
    return os.path.expandvars(p)


def pagefile_facts(paging_reg: list, existing: list,
                   fallback_total: int) -> list:
    """返回 [(路径, 字节)]。os.stat 优先，全部失败才用提交上限差值兜底。"""
    out: list = []
    for raw in existing or []:
        real = _expand_nt(str(raw))
        try:
            out.append((real, os.stat(real).st_size))
        except OSError:
            out.append((real, 0))
    if not out:
        for raw in paging_reg or []:
            txt = str(raw)
            parts = txt.split(",")
            head = parts[0].strip()
            if head.startswith("?"):          # ?:\pagefile.sys = 系统托管，盘符未知
                continue
            real = _expand_nt(head)
            try:
                size = os.stat(real).st_size
            except OSError:
                size = 0
            out.append((real, size))
    if out and all(sz == 0 for _p, sz in out) and fallback_total > 0:
        out = [(p, 0) for p, _sz in out]
        first = out[0][0]
        out[0] = (first, fallback_total)
    return out


def _free_space(drive: str) -> int:
    free = ctypes.c_ulonglong(0)
    total = ctypes.c_ulonglong(0)
    root = drive.rstrip("\\/") + "\\"
    try:
        ok = ctypes.windll.kernel32.GetDiskFreeSpaceExW(
            ctypes.c_wchar_p(root), ctypes.byref(free), ctypes.byref(total), None)
        return free.value if ok else 0
    except OSError:
        return 0


def _guess_dump_kind(size: int, name: str) -> str:
    if size >= 512 * MB:
        return "complete"
    if size >= 4 * MB:
        return "kernel"
    if size > 0:
        return "small"
    return "unknown"


def scan_dumps(snap: Snap) -> None:
    """收集 MEMORY.DMP 与 Minidump 目录里的文件。"""
    out: list = []
    main = snap.dump_file_path
    if main and os.path.isfile(main):
        try:
            st = os.stat(main)
            out.append(DumpFile(main, st.st_size,
                                datetime.fromtimestamp(st.st_mtime),
                                _guess_dump_kind(st.st_size, main)))
        except OSError:
            pass
    mdir = snap.minidump_path
    if mdir and os.path.isdir(mdir):
        try:
            names = os.listdir(mdir)
        except OSError:
            names = []
        for nm in names:
            if not nm.lower().endswith((".dmp", ".mdmp")):
                continue
            full = os.path.join(mdir, nm)
            try:
                st = os.stat(full)
            except OSError:
                continue
            out.append(DumpFile(full, st.st_size,
                                datetime.fromtimestamp(st.st_mtime),
                                _guess_dump_kind(st.st_size, nm)))
    out.sort(key=lambda d: d.time or datetime.min, reverse=True)
    snap.dumps = out


def scan_live_reports(snap: Snap) -> None:
    root = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "LiveKernelReports")
    if not os.path.isdir(root):
        snap.live_reports = []
        return
    found: list = []
    try:
        for cur, _dirs, files in os.walk(root):
            for f in files:
                if f.lower().endswith(".dmp"):
                    found.append(os.path.join(cur, f))
    except OSError:
        pass
    snap.live_reports = found[:20]


def scan_crashes(limit: int = 200) -> list:
    """读 System 通道里 41 / 1001，按 provider 区分「蓝屏」与「掉电」。"""
    xpath = "*[System[(EventID=41 or EventID=1001)]]"
    try:
        events, _st = evtx.query_events("System", False, xpath=xpath,
                                        limit=limit, message_mode="none")
    except Exception:                      # noqa: BLE001  通道读不到就当没有
        return []
    out: list = []
    for e in events:
        data = e.data or {}
        if e.event_id == 1001:
            prov = (e.provider or "").lower()
            if "bugcheck" not in prov:
                continue
            code = 0
            for key in ("param1", "bugcheckcode", "BugcheckCode"):
                raw = str(data.get(key, "") or "")
                m = _HEX_RE.search(raw)
                if m:
                    code = int(m.group(1), 16)
                    break
            if not code:
                # 兜底：消息正文里第一个 0x 十六进制串
                m = _HEX_RE.search(e.message or "")
                if m:
                    try:
                        code = int(m.group(1), 16)
                    except ValueError:
                        code = 0
            out.append(CrashEvent(e.time, e.provider, e.event_id, "bugcheck",
                                  code & 0xFFFFFFFF, (), e.data_text))
            continue
        # id=41
        raw = str(data.get("BugcheckCode", data.get("bugcheckCode", "0")) or "0")
        try:
            code = int(raw, 0)
        except (TypeError, ValueError):
            code = 0
        out.append(CrashEvent(e.time, e.provider, e.event_id, "power",
                              code & 0xFFFFFFFF, (), e.data_text))
    out.sort(key=lambda c: c.time or datetime.min, reverse=True)
    return out


def read(with_events: bool = True) -> Snap:
    """完整采集。with_events=False 跳过事件读取（省 ~200 ms）。"""
    s = Snap()
    s.scanned_at = datetime.now()
    cv = _reg_values(CRASH_KEY)
    mv = _reg_values(MM_KEY)

    def g(d: dict, k: str, default=0):
        v = d.get(k, default)
        return default if v is None else v

    try:
        s.enabled = int(g(cv, "CrashDumpEnabled", 3))
    except (TypeError, ValueError):
        s.enabled = 3
    try:
        s.filter_pages = int(g(cv, "FilterPages", 0))
    except (TypeError, ValueError):
        s.filter_pages = 0
    s.dump_file = str(g(cv, "DumpFile", r"%SystemRoot%\MEMORY.DMP"))
    s.minidump_dir = str(g(cv, "MinidumpDir", r"%SystemRoot%\Minidump"))
    s.auto_reboot = int(g(cv, "AutoReboot", 1) or 0)
    s.overwrite = int(g(cv, "Overwrite", 1) or 0)
    s.log_event = int(g(cv, "LogEvent", 1) or 0)
    s.always_keep = int(g(cv, "AlwaysKeepMemoryDump", 0) or 0)
    s.minidumps_count = int(g(cv, "MinidumpsCount", 0) or 0)
    filt = g(cv, "DumpFilters", [])
    s.dump_filters = list(filt) if isinstance(filt, (list, tuple)) else [str(filt)]

    paging_reg = mv.get("PagingFiles", []) or []
    existing = mv.get("ExistingPageFiles", []) or []
    if isinstance(paging_reg, str):
        paging_reg = [paging_reg]
    if isinstance(existing, str):
        existing = [existing]
    s.paging_reg = list(paging_reg)
    s.ram, s.commit_limit, s.kernel_total, fallback = memory_facts()
    s.pagefiles = pagefile_facts(paging_reg, existing, fallback)

    drive = os.environ.get("SystemDrive", "C:")
    s.system_drive = drive
    s.free_system = _free_space(drive)

    scan_dumps(s)
    scan_live_reports(s)
    if with_events:
        s.crashes = scan_crashes()
    return s


# ---------------------------------------------------------------- 判定
def _f(severity: str, topic: str, title: str, detail: str = "",
       advice=None, evidence=None, count: int = 0,
       first=None, last=None) -> Finding:
    return Finding(severity=severity, topic=topic, title=title, detail=detail,
                   advice=list(advice or []), count=count, first=first,
                   last=last, evidence=list(evidence or []))


def _mtime_text(path: str) -> str:
    try:
        return datetime.fromtimestamp(os.stat(path).st_mtime).strftime("%m-%d %H:%M")
    except OSError:
        return "—"


def nearby_live_reports(s: "Snap", event, minutes: int = 45) -> list:
    """活动内核报告的时间与意外关机贴合时，说明那次死机另有原因。"""
    if event is None or event.time is None:
        return []
    out: list = []
    for p in s.live_reports:
        try:
            mt = datetime.fromtimestamp(os.stat(p).st_mtime)
        except OSError:
            continue
        if abs((mt - event.time).total_seconds()) <= minutes * 60:
            out.append(os.path.basename(p))
    return out


def evaluate(s: Snap) -> list:
    """把事实翻译成诊断结论。只读，不改任何东西。"""
    out: list = []
    need, need_note = s.need_pagefile()
    pf = s.pagefile_bytes

    # ---- 1. 转储被关掉
    if s.enabled == 0:
        out.append(_f(
            "crit", "dump-off", "崩溃转储已关闭，蓝屏不会留下任何文件",
            "系统被配置为「不写入调试信息」。下次蓝屏只会重启，"
            "除事件日志里一条 BugCheck 记录外不会有 dump，"
            "任何分析工具都无法还原当时的调用栈。",
            ["系统属性 → 高级 → 启动和恢复 → 设置",
             "「写入调试信息」改为「自动内存转储」",
             "同一处确认「将事件写入系统日志」已勾选"],
            ["CrashControl\\CrashDumpEnabled = 0"]))
    elif s.disabled:
        out.append(_f(
            "warn", "dump-odd", "转储类型取值异常（%d），按关闭处理" % s.enabled,
            "CrashDumpEnabled 不在官方枚举（0/1/2/3/4/7）内，Windows 会当作"
            "「不记录」处理。可能被第三方优化工具改过。",
            ["把「写入调试信息」重新选一次并应用"],
            ["CrashControl\\CrashDumpEnabled = %d" % s.enabled]))

    # ---- 2. 有蓝屏记录却没有转储（最能解释"为什么没有文件"）
    if s.bugchecks:
        if not s.dumps:
            newest = s.bugchecks[0]
            out.append(_f(
                "crit", "no-dump", "有 %d 次蓝屏记录，但没找到任何转储文件"
                % len(s.bugchecks),
                "事件日志里有 BugCheck 记录（最近一次 %s，错误码 0x%08X），"
                "但 %s 与 %s 都没有文件。"
                "说明崩溃时转储写入失败 —— 常见原因是页面文件不够、"
                "转储目录所在盘已满，或转储类型被改过。"
                % (newest.time_text, newest.code, s.dump_file_path or "MEMORY.DMP",
                   s.minidump_path or "Minidump"),
                ["先看下面「容量校验」一条，确认页面文件是否够",
                 "确认 %s 所在磁盘剩余空间充足" % (s.system_drive or "C:"),
                 "把转储类型改成「自动内存转储」，系统会按需扩大页面文件"],
                ["最近蓝屏 %s code=0x%08X" % (newest.time_text, newest.code)],
                count=len(s.bugchecks), first=s.bugchecks[-1].time,
                last=newest.time))
        else:
            newest = s.bugchecks[0]
            out.append(_f(
                "info", "bugcheck-history", "最近 %d 次蓝屏留有记录"
                % len(s.bugchecks),
                "最近一次 %s，错误码 0x%08X。下方「转储文件」表里可挑对应文件分析。"
                % (newest.time_text, newest.code),
                [], ["BugCheck %s → 0x%08X" % (newest.time_text, newest.code)],
                count=len(s.bugchecks), first=s.bugchecks[-1].time,
                last=newest.time))

    # ---- 3. 容量校验
    if not s.disabled and need > 0:
        if s.enabled == 1:
            if not s.pagefile_on_system:
                out.append(_f(
                    "crit", "pf-drive", "完整内存转储要求在系统盘上有页面文件",
                    "当前页面文件只有：%s。完整内存转储的写入路径依赖启动卷上的"
                    "页面文件，放在其他盘时系统会在崩溃瞬间放弃写 dump。"
                    % ("、".join(p for p, _x in s.pagefiles) or "无"),
                    ["把 %s 的页面文件加回来（自定义大小，至少 %s）"
                     % (s.system_drive or "C:", human(need)),
                     "或者改用「自动内存转储」，对页面文件要求低得多"],
                    ["页面文件：%s" % ("、".join(
                        "%s %s" % (p, human(sz)) for p, sz in s.pagefiles) or "无")]))
            elif pf < need:
                out.append(_f(
                    "crit", "pf-size",
                    "页面文件太小，完整内存转储写不出来",
                    "物理内存 %s，完整内存转储需要页面文件至少 %s（%s），"
                    "实际只有 %s，差 %s。"
                    % (human(s.ram), human(need), need_note, human(pf),
                       human(need - pf)),
                    ["改成「自动内存转储」（推荐，开销小且够定位问题）",
                     "或把 %s 的页面文件设为「系统管理的大小」并重启"
                     % (s.system_drive or "C:"),
                     "完整转储只在需要提交给厂商深挖时才值得开"],
                    ["RAM %s / 页面文件 %s / 需要 %s"
                     % (human(s.ram), human(pf), human(need))]))
            else:
                out.append(_f(
                    "ok", "pf-size", "完整内存转储的页面文件容量足够",
                    "需要 %s，实际 %s（%s）。崩溃时可完整落盘。"
                    % (human(need), human(pf), need_note),
                    [], ["RAM %s / 页面文件 %s" % (human(s.ram), human(pf))]))
        elif s.enabled in (2, 7):
            if not s.pagefile_on_system:
                out.append(_f(
                    "warn", "pf-drive", "页面文件不在系统盘",
                    "内核/自动转储同样优先写入启动卷。当前页面文件：%s。"
                    % ("、".join(p for p, _x in s.pagefiles) or "无"),
                    ["在 %s 上保留一份页面文件，或改用小型内存转储"
                     % (s.system_drive or "C:")],
                    ["页面文件：%s" % ("、".join(p for p, _x in s.pagefiles) or "无")]))
            elif pf < need:
                out.append(_f(
                    "warn", "pf-size", "页面文件可能不足以写下内核转储",
                    "内核实际占用 %s，按 %s 估算，当前页面文件 %s。"
                    "内核分配量会随驱动与运行时间变化，崩溃时可能更多。"
                    % (human(s.kernel_total), need_note, human(pf)),
                    ["把页面文件改成「系统管理的大小」，交给系统按转储类型扩",
                     "或在「启动和恢复」里改用「小型内存转储」（256 KB，几乎无开销）"],
                    ["内核占用 %s / 页面文件 %s"
                     % (human(s.kernel_total), human(pf))]))
            else:
                out.append(_f(
                    "ok", "pf-size", "页面文件容量够写内核转储",
                    "内核占用 %s，页面文件 %s（%s）。"
                    % (human(s.kernel_total), human(pf), need_note),
                    [], ["内核占用 %s / 页面文件 %s"
                         % (human(s.kernel_total), human(pf))]))
        elif s.enabled in (3, 4):
            if pf < need:
                out.append(_f(
                    "crit", "pf-size", "没有页面文件，小型转储也写不出来",
                    "小型转储只需要启动卷上 %s 的页面文件，当前为 %s。"
                    % (human(need), human(pf)),
                    ["系统属性 → 高级 → 启动和恢复 → 设置，确认转储未关闭",
                     "在 %s 上启用页面文件（系统管理的大小）" % (s.system_drive or "C:")],
                    ["页面文件 %s" % human(pf)]))
            else:
                out.append(_f(
                    "ok", "pf-size", "页面文件满足小型转储要求",
                    "小型转储只需要 %s，当前页面文件 %s。"
                    % (human(need), human(pf)),
                    [], ["页面文件 %s" % human(pf)]))

    # ---- 4. 目标盘剩余空间
    if not s.disabled and s.free_system and need > 0:
        if s.enabled == 1 and s.free_system < need:
            out.append(_f(
                "warn", "disk-space", "系统盘剩余空间不够放完整转储",
                "完整转储要写出约 %s 的文件，%s 只剩 %s。"
                % (human(need), s.system_drive or "C:", human(s.free_system)),
                ["清理系统盘或改用「自动内存转储」"],
                ["剩余 %s / 需要 %s" % (human(s.free_system), human(need))]))
        elif s.free_system < 2 * GB and s.enabled in (2, 7):
            out.append(_f(
                "warn", "disk-space", "系统盘剩余空间偏少，转储可能被跳过",
                "%s 只剩 %s。内核转储通常 0.5~3 GB，空间不足时系统会放弃写入。"
                % (s.system_drive or "C:", human(s.free_system)),
                ["给系统盘腾出至少 5 GB"],
                ["剩余 %s" % human(s.free_system)]))

    # ---- 5. 「不是蓝屏」这一类
    if s.power_losses and not s.bugchecks:
        newest = s.power_losses[0]
        near = nearby_live_reports(s, newest)
        detail = ("Kernel-Power 41 记录到 %d 次意外关机，但 BugcheckCode 全为 0 —— "
                  "这类是掉电、长按电源、硬件死机或死循环，系统来不及也无需写 dump。"
                  "如果现象是「突然黑屏重启、没有蓝色画面」，方向应转到供电与硬件，"
                  "而不是找崩溃文件。" % len(s.power_losses))
        advice = ["检查电源、内存条接触与散热（可配合「硬件错误」页看 WHEA）",
                  "确认不是长按电源或插排断电导致的",
                  "若确实见过蓝屏画面，用「日志分析」页查 BugCheck 1001"]
        evidence = ["最近一次 %s（BugcheckCode=0）" % newest.time_text]
        if near:
            # 活动内核报告与意外关机时间贴合时，多半是驱动挂起，不是掉电
            detail += ("另外，%s 前后 45 分钟内存在活动内核报告（%s）—— "
                       "这类转储由驱动超时/挂起产生，说明当时很可能是某个驱动卡死"
                       "导致整机失去响应，而不是供电中断。"
                       % (newest.time_text, "、".join(near)))
            advice.insert(0, "在「日志分析」页搜 TDR / ResourceTimeout / 超时，"
                             "定位是哪个驱动或 GPU 挂起")
            evidence.append("活动内核报告 %s 与意外关机同时间窗" % "、".join(near))
        out.append(_f(
            "info", "power-loss",
            "最近 %d 次非正常关机不是蓝屏" % len(s.power_losses),
            detail, advice, evidence,
            count=len(s.power_losses), first=s.power_losses[-1].time,
            last=newest.time))

    # ---- 6. 目录状态
    if not s.disabled:
        if s.enabled in (3, 4) and s.minidump_path and not os.path.isdir(s.minidump_path):
            out.append(_f(
                "info", "mdir", "小型转储目录尚未创建",
                "%s 不存在。该目录在首次崩溃时由系统创建，现在没有属正常现象。"
                % s.minidump_path,
                [], [s.minidump_path]))
        if s.enabled in (1, 2) and s.dump_file_path:
            parent = os.path.dirname(s.dump_file_path)
            if parent and not os.path.isdir(parent):
                out.append(_f(
                    "crit", "dumpdir", "转储文件的目录不存在",
                    "DumpFile 指向 %s，但其所在目录 %s 不存在，写入必然失败。"
                    % (s.dump_file_path, parent),
                    ["把 DumpFile 改回 %SystemRoot%\\MEMORY.DMP"],
                    ["DumpFile = %s" % s.dump_file]))

    # ---- 7. 已有转储
    if s.dumps:
        latest = s.latest_dump
        total = sum(d.size for d in s.dumps)
        out.append(_f(
            "ok", "dump-present", "共 %d 个转储文件，合计 %s"
            % (len(s.dumps), human(total)),
            "最新一个是 %s（%s，%s）。可以直接拖进本工作台分析。"
            % (latest.name, latest.size_text, latest.time_text),
            [], ["%s  %s  %s" % (d.name, d.size_text, d.time_text)
                 for d in s.dumps[:4]],
            count=len(s.dumps), first=min((d.time for d in s.dumps if d.time),
                                          default=None),
            last=latest.time))
    elif not s.bugchecks and not s.disabled:
        out.append(_f(
            "ok", "no-crash", "没有找到蓝屏记录，也没有转储",
            "事件日志里既没有 BugCheck 1001，也没有转储文件 —— 自日志滚动以来"
            "这台机器没有蓝屏过，所以「没有 dump」是正常结果。",
            ["如果确实见过蓝屏，检查事件日志是否被清理或日志上限太小",
             "下次崩溃后可回到本页确认转储是否生成"],
            ["已检索 System 通道的 41 / 1001 事件"]))

    # ---- 8. 配置细节
    if s.log_event == 0:
        out.append(_f(
            "warn", "log-event", "「将事件写入系统日志」已关闭",
            "LogEvent=0 时蓝屏不会在 System 通道留 1001 记录，"
            "事后无法从事件日志判断是否蓝屏过。",
            ["系统属性 → 高级 → 启动和恢复 → 勾选「将事件写入系统日志」"],
            ["CrashControl\\LogEvent = 0"]))

    if s.overwrite == 0 and s.dumps:
        out.append(_f(
            "info", "no-overwrite", "「覆盖任何现有文件」未勾选",
            "已有转储不会被新崩溃覆盖。若旧文件占着名字且磁盘空间不足，"
            "新崩溃可能不写出文件。",
            ["定期清理旧转储，或勾选「覆盖任何现有文件」"],
            ["CrashControl\\Overwrite = 0"]))

    if s.auto_reboot == 1 and not s.disabled:
        out.append(_f(
            "info", "auto-reboot", "蓝屏后会自动重启",
            "AutoReboot=1 时蓝屏画面只停留约 30 秒就重启。"
            "现场看到错误码的机会不多，但转储不受影响。",
            ["需要拍照记录错误码时，临时取消「自动重新启动」"],
            ["CrashControl\\AutoReboot = 1"]))

    if s.is_active_dump:
        out.append(_f(
            "info", "active-dump", "当前配置为「活动内存转储」",
            "活动内存转储只写内核与当前进程用到的内存，比完整转储小得多，"
            "适合虚拟化主机。普通机器用「自动内存转储」即可。",
            [], ["CrashDumpEnabled=1 且 FilterPages=1"]))

    # 注意：**不要**按 DumpFilters 里有没有 dumpfve.sys 判断 BitLocker。
    # 本机实测（2026-10-23）C: 为「完全解密、保护关闭」，但
    # DumpFilters 里依然有 dumpfve.sys —— 它是默认过滤器，与是否加密无关。
    # 判断加密状态只能走 Win32_EncryptableVolume（需管理员）或 manage-bde，
    # 为一条可有可无的提示去起子进程不值，所以这条不做。

    if s.live_reports:
        out.append(_f(
            "info", "live-kernel", "另有 %d 个活动内核报告" % len(s.live_reports),
            "LiveKernelReports 里的转储来自「未蓝屏但内核出错」的场景"
            "（驱动挂起、GPU 超时等），不是 bugcheck，排查方向不同。",
            [], ["%s  %s" % (os.path.basename(p), _mtime_text(p))
                 for p in s.live_reports[:4]],
            count=len(s.live_reports)))

    return out


def verdict(findings: list) -> tuple:
    """返回 (等级, 一句话结论)。等级用于 KPI 配色。"""
    crit = [f for f in findings if f.severity == "crit"]
    warn = [f for f in findings if f.severity == "warn"]
    if crit:
        return "crit", "下次蓝屏很可能留不下证据：%s" % crit[0].title
    if warn:
        return "warn", "能留下转储，但有一项需要处理：%s" % warn[0].title
    if not findings:
        return "dim", "尚未体检"
    return "ok", "配置正常，蓝屏时会生成转储"


def summary_line(s: Snap) -> str:
    """一行事实摘要，给「概览」用。"""
    if s.disabled:
        return "转储已关闭"
    bits = [s.type_text]
    if s.ram:
        bits.append("RAM %s" % human(s.ram))
    if s.pagefile_bytes:
        bits.append("页面文件 %s" % human(s.pagefile_bytes))
    bits.append("%d 个转储" % len(s.dumps))
    return " · ".join(bits)
