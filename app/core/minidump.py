# -*- coding: utf-8 -*-
"""崩溃转储解析引擎（纯 Python，零外部依赖）。

支持两种真实世界里最常见的文件：
  1. 用户模式 minidump  —— 魔数 'MDMP'（WER 应用崩溃、任务管理器创建转储）
  2. 内核转储           —— 魔数 'PAGE' + 'DU64' / 'DUMP'（C:\\Windows\\Minidump\\*.dmp、MEMORY.DMP）

用户模式按 MINIDUMP 结构逐流解析：SystemInfo / ModuleList / Exception /
ThreadList / MiscInfo / Memory64List / Comment；
内核转储从 DUMP_HEADER 里直接抠出蓝屏码 BugCheckCode 和 4 个参数。

所有读取都做了越界保护，遇到畸形文件只降级不崩溃。
"""
from __future__ import annotations

import os
import struct
import mmap
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

MAGIC_MDMP = b"MDMP"
MAGIC_PAGE = b"PAGE"

# MINIDUMP_STREAM_TYPE
ST_THREAD_LIST = 3
ST_MODULE_LIST = 4
ST_MEMORY_LIST = 5
ST_EXCEPTION = 6
ST_SYSTEM_INFO = 7
ST_MEMORY64_LIST = 9
ST_COMMENT_A = 10
ST_COMMENT_W = 11
ST_HANDLE_DATA = 12
ST_MISC_INFO = 15
ST_MEMORY_INFO_LIST = 16
ST_THREAD_INFO_LIST = 17

ARCH_NAMES = {
    0: "x86 (32 位)",
    1: "MIPS",
    2: "Alpha",
    3: "PowerPC",
    5: "ARM",
    6: "IA64",
    9: "x64 (AMD64)",
    12: "ARM64",
}

ARCH_PTR = {0: 4, 1: 4, 5: 4, 9: 8, 12: 8, 6: 8}

PRODUCT_TYPE = {1: "工作站", 2: "域控制器", 3: "服务器"}

# build -> 友好版本名
OS_BUILDS = {
    7600: "Windows 7", 7601: "Windows 7 SP1", 9200: "Windows 8",
    9600: "Windows 8.1", 10240: "Windows 10 1507", 10586: "Windows 10 1511",
    14393: "Windows 10 1607 / Server 2016", 15063: "Windows 10 1703",
    16299: "Windows 10 1709", 17134: "Windows 10 1803",
    17763: "Windows 10 1809 / Server 2019", 18362: "Windows 10 1903",
    18363: "Windows 10 1909", 19041: "Windows 10 2004", 19042: "Windows 10 20H2",
    19043: "Windows 10 21H1", 19044: "Windows 10 21H2", 19045: "Windows 10 22H2",
    20348: "Windows Server 2022", 22000: "Windows 11 21H2",
    22621: "Windows 11 22H2", 22631: "Windows 11 23H2", 26100: "Windows 11 24H2",
    26200: "Windows 11 25H2",
}


def nt_version_from_build(build: int) -> tuple[int, int]:
    """build 号反查 NT 主/次版本（内核转储只给 build，不给 6.1/10.0）。"""
    if build >= 10240:
        return (10, 0)
    if build >= 9600:
        return (6, 3)
    if build >= 9200:
        return (6, 2)
    if build >= 7600:
        return (6, 1)
    return (0, 0)


def os_build_name(build: int) -> str:
    if build in OS_BUILDS:
        return OS_BUILDS[build]
    if build >= 26100:
        return f"Windows 11 (build {build})"
    if build >= 22000:
        return f"Windows 11 (build {build})"
    if build >= 10240:
        return f"Windows 10 (build {build})"
    return f"Windows (build {build})"


class _Buf:
    """带越界保护的小端读取器。"""

    def __init__(self, data: bytes):
        self.d = data
        self.n = len(data)

    def has(self, off: int, size: int) -> bool:
        return 0 <= off and off + size <= self.n

    def u8(self, off: int, default: int = 0) -> int:
        if not self.has(off, 1):
            return default
        return self.d[off]

    def u16(self, off: int, default: int = 0) -> int:
        if not self.has(off, 2):
            return default
        return struct.unpack_from("<H", self.d, off)[0]

    def u32(self, off: int, default: int = 0) -> int:
        if not self.has(off, 4):
            return default
        return struct.unpack_from("<I", self.d, off)[0]

    def u64(self, off: int, default: int = 0) -> int:
        if not self.has(off, 8):
            return default
        return struct.unpack_from("<Q", self.d, off)[0]

    def raw(self, off: int, size: int) -> bytes:
        if not self.has(off, size):
            return b""
        return self.d[off:off + size]

    def wstr(self, rva: int, max_chars: int = 520) -> str:
        if not self.has(rva, 2):
            return ""
        end = rva
        limit = min(self.n - 1, rva + max_chars * 2)
        while end + 1 < limit and not (self.d[end] == 0 and self.d[end + 1] == 0):
            end += 2
        try:
            return self.d[rva:end].decode("utf-16-le", "replace").strip("\x00 ")
        except Exception:
            return ""

    def astr(self, rva: int, max_chars: int = 520) -> str:
        if not self.has(rva, 1):
            return ""
        end = self.d.find(b"\x00", rva, min(self.n, rva + max_chars))
        if end < 0:
            end = min(self.n, rva + max_chars)
        try:
            return self.d[rva:end].decode("utf-8", "replace").strip()
        except Exception:
            return ""


@dataclass
class Module:
    base: int
    size: int
    name: str
    version: str = ""
    timestamp: Optional[datetime] = None

    @property
    def short_name(self) -> str:
        return os.path.basename(self.name) if self.name else "(未知模块)"

    @property
    def is_driver(self) -> bool:
        ext = os.path.splitext(self.name)[1].lower()
        return ext in (".sys", ".drv", ".ocx")

    @property
    def base_text(self) -> str:
        return f"{self.base:016X}"

    @property
    def size_text(self) -> str:
        return f"{self.size / 1024:.0f} KB"


@dataclass
class SystemInfo:
    arch: int = 0
    cpus: int = 0
    product_type: int = 0
    major: int = 0
    minor: int = 0
    build: int = 0
    csd: str = ""
    platform_id: int = 0

    @property
    def arch_name(self) -> str:
        return ARCH_NAMES.get(self.arch, f"未知 ({self.arch})")

    @property
    def os_name(self) -> str:
        if not self.build and not self.major:
            return "未知"
        if not self.build:
            return f"Windows {self.major}.{self.minor}（内核转储未记录 build）"
        name = os_build_name(self.build)
        if self.csd and "SP" in self.csd.upper():
            name += f" {self.csd}"
        return name

    @property
    def os_short(self) -> str:
        """KPI 卡用的短名：Win10 22H2 / Win11 24H2 / Server 2019。"""
        if not self.build:
            # 内核转储头只有主/次版本，10.0 覆盖 Win10 与 Win11
            if (self.major, self.minor) == (10, 0):
                return "Windows 10/11"
            return self.os_name.split("（")[0]
        name = os_build_name(self.build)
        if "Server" in name:
            return "Server " + name.split("Server ")[-1]
        if name.startswith("Windows 10 "):
            return "Win10 " + name[len("Windows 10 "):]
        if name.startswith("Windows 11 "):
            return "Win11 " + name[len("Windows 11 "):]
        return name.replace("Windows ", "Win")

    @property
    def version_text(self) -> str:
        if not self.build:
            return f"{self.major}.{self.minor}"
        return f"{self.major}.{self.minor}.{self.build}"


@dataclass
class BugCheck:
    code: int = 0
    params: list = field(default_factory=list)
    # 由知识库回填
    name: str = ""
    name_cn: str = ""
    advice: list = field(default_factory=list)
    culprit_hint: str = ""

    @property
    def code_text(self) -> str:
        if not self.code:
            return "-"
        return f"0x{self.code:08X}"


@dataclass
class DumpReport:
    path: str = ""
    file_size: int = 0
    kind: str = ""                 # "minidump" | "kernel"
    kind_text: str = ""
    created: Optional[datetime] = None
    system: SystemInfo = field(default_factory=SystemInfo)
    bugcheck: BugCheck = field(default_factory=BugCheck)
    exception_code: int = 0
    exception_flags: int = 0
    exception_address: int = 0
    exception_params: list = field(default_factory=list)
    faulting_thread: int = 0
    faulting_module: Optional[Module] = None
    modules: list = field(default_factory=list)
    thread_count: int = 0
    comments: list = field(default_factory=list)
    process_id: int = 0
    process_create: Optional[datetime] = None
    driver_count: int = 0
    warnings: list = field(default_factory=list)
    raw_fields: dict = field(default_factory=dict)

    @property
    def name(self) -> str:
        return os.path.basename(self.path)

    @property
    def size_text(self) -> str:
        s = float(self.file_size)
        for unit in ("B", "KB", "MB", "GB"):
            if s < 1024 or unit == "GB":
                return f"{s:.1f} {unit}" if unit != "B" else f"{int(s)} B"
            s /= 1024
        return f"{s:.1f} GB"

    @property
    def created_text(self) -> str:
        return self.created.strftime("%Y-%m-%d %H:%M:%S") if self.created else "-"

    @property
    def exception_text(self) -> str:
        return f"0x{self.exception_code:08X}" if self.exception_code else "-"

    @property
    def key_summary(self) -> str:
        """一句话结论，用于列表/标题。"""
        if self.kind == "kernel" and self.bugcheck.code:
            label = self.bugcheck.name_cn or self.bugcheck.name or "未知蓝屏"
            return f"蓝屏 {self.bugcheck.code_text} · {label}"
        if self.exception_code:
            label = self.bugcheck.name_cn or "异常"
            mod = self.faulting_module.short_name if self.faulting_module else "未知模块"
            return f"异常 {self.exception_text} · {mod}"
        return "未识别到崩溃点"


def _filetime_to_dt(value: int) -> Optional[datetime]:
    if not value or value < 100000000000000000:
        return None
    try:
        return datetime(1601, 1, 1) + timedelta(microseconds=value / 10)
    except Exception:
        return None


def _unix_to_dt(value: int) -> Optional[datetime]:
    if not value or value < 100000 or value > 4102444800:
        return None
    try:
        return datetime.fromtimestamp(value, tz=timezone.utc).astimezone().replace(tzinfo=None)
    except Exception:
        return None


def _dt_from_u32_ts(value: int) -> Optional[datetime]:
    if not value or value < 100000:
        return None
    try:
        return datetime.fromtimestamp(value, tz=timezone.utc).astimezone().replace(tzinfo=None)
    except Exception:
        return None


def _version_text(ms: int, ls: int) -> str:
    if not ms and not ls:
        return ""
    return f"{ms >> 16}.{ms & 0xFFFF}.{ls >> 16}.{ls & 0xFFFF}"


# ------------------------------------------------------------------ 头部嗅探
def sniff(path: str) -> str:
    """返回 'minidump' / 'kernel' / ''。"""
    try:
        with open(path, "rb") as f:
            head = f.read(8)
    except OSError:
        return ""
    if head[:4] == MAGIC_MDMP:
        return "minidump"
    if head[:4] == MAGIC_PAGE:
        return "kernel"
    return ""


def parse(path: str) -> DumpReport:
    if not os.path.isfile(path):
        raise FileNotFoundError(path)
    size = os.path.getsize(path)
    if size < 32:
        raise ValueError("文件过小，不是有效的转储文件")

    # 用 mmap 读：MEMORY.DMP 可能有几个 GB，绝不能整个读进内存
    with open(path, "rb") as f:
        try:
            mm = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ)
        except (OSError, ValueError):
            mm = None
        buf = _Buf(mm if mm is not None else f.read())
        try:
            rep = DumpReport(path=path, file_size=size)
            head = buf.raw(0, 8)
            if head[:4] == MAGIC_MDMP:
                rep.kind = "minidump"
                rep.kind_text = "用户模式迷你转储 (MDMP)"
                _parse_mdmp(buf, rep)
            elif head[:4] == MAGIC_PAGE:
                rep.kind = "kernel"
                _parse_kernel(buf, rep, head)
            else:
                raise ValueError("未识别的转储格式：既不是 MDMP 也不是内核 PAGEDUMP")
        finally:
            if mm is not None:
                mm.close()

    # 故障模块定位
    if rep.exception_address:
        for m in rep.modules:
            if m.base and m.base <= rep.exception_address < m.base + max(m.size, 1):
                rep.faulting_module = m
                break
    rep.driver_count = sum(1 for m in rep.modules if m.is_driver)
    if not rep.created:
        rep.created = _dt_from_u32_ts(int(os.path.getmtime(path))) if size else None
    return rep


# ------------------------------------------------------------------ 用户模式
def _parse_mdmp(buf: _Buf, rep: DumpReport) -> None:
    version = buf.u32(4)
    nstreams = buf.u32(8)
    dir_rva = buf.u32(12)
    ts = buf.u32(20)
    flags = buf.u64(24)
    rep.raw_fields["header"] = {
        "签名": "MDMP",
        "结构版本": f"0x{version:08X}",
        "流数量": nstreams,
        "流目录 RVA": f"0x{dir_rva:X}",
        "标志": f"0x{flags:X}",
    }
    rep.created = _dt_from_u32_ts(ts)
    if nstreams > 512 or not buf.has(dir_rva, 12):
        rep.warnings.append("流目录异常，文件可能被截断")
        return

    streams: dict[int, tuple[int, int]] = {}
    for i in range(nstreams):
        off = dir_rva + i * 12
        if not buf.has(off, 12):
            break
        st = buf.u32(off)
        dsize = buf.u32(off + 4)
        drva = buf.u32(off + 8)
        streams.setdefault(st, (dsize, drva))

    if ST_SYSTEM_INFO in streams:
        _parse_system_info(buf, streams[ST_SYSTEM_INFO][1], rep)
    if ST_MODULE_LIST in streams:
        _parse_modules(buf, streams[ST_MODULE_LIST], rep)
    if ST_EXCEPTION in streams:
        _parse_exception(buf, streams[ST_EXCEPTION][1], rep)
    if ST_THREAD_LIST in streams:
        rep.thread_count = buf.u32(streams[ST_THREAD_LIST][1])
    if ST_MISC_INFO in streams:
        _parse_misc(buf, streams[ST_MISC_INFO], rep)
    for st in (ST_COMMENT_W, ST_COMMENT_A):
        if st in streams:
            dsize, rva = streams[st]
            text = buf.wstr(rva) if st == ST_COMMENT_W else buf.astr(rva)
            text = text.strip()
            if text:
                rep.comments.append(text[:2000])
    if ST_MEMORY64_LIST in streams:
        dsize, rva = streams[ST_MEMORY64_LIST]
        regions = buf.u64(rva)
        rep.raw_fields["内存区段"] = regions
    if ST_MEMORY_INFO_LIST in streams:
        dsize, rva = streams[ST_MEMORY_INFO_LIST]
        rep.raw_fields["内存信息条目"] = buf.u64(rva + 8)


def _parse_system_info(buf: _Buf, rva: int, rep: DumpReport) -> None:
    s = SystemInfo()
    s.arch = buf.u16(rva)
    s.cpus = buf.u8(rva + 6)
    s.product_type = buf.u8(rva + 7)
    s.major = buf.u32(rva + 8)
    s.minor = buf.u32(rva + 12)
    s.build = buf.u32(rva + 16)
    s.platform_id = buf.u32(rva + 20)
    csd_rva = buf.u32(rva + 24)
    s.csd = buf.wstr(csd_rva) if csd_rva else ""
    rep.system = s


def _parse_modules(buf: _Buf, loc: tuple[int, int], rep: DumpReport) -> None:
    dsize, rva = loc
    count = buf.u32(rva)
    if count > 20000:
        rep.warnings.append(f"模块数量异常 ({count})，已忽略")
        return
    mods: list[Module] = []
    for i in range(count):
        off = rva + 4 + i * 108
        if not buf.has(off, 108):
            break
        base = buf.u64(off)
        size = buf.u32(off + 8)
        ts = buf.u32(off + 16)
        name_rva = buf.u32(off + 20)
        ms = buf.u32(off + 32)
        ls = buf.u32(off + 36)
        name = buf.wstr(name_rva)
        if not name:
            continue
        mods.append(Module(base=base, size=size, name=name,
                           version=_version_text(ms, ls),
                           timestamp=_dt_from_u32_ts(ts)))
    rep.modules = mods


def _parse_exception(buf: _Buf, rva: int, rep: DumpReport) -> None:
    rep.faulting_thread = buf.u32(rva)
    base = rva + 8  # MINIDUMP_EXCEPTION 紧跟 ThreadId + 对齐
    rep.exception_code = buf.u32(base)
    rep.exception_flags = buf.u32(base + 4)
    rep.exception_address = buf.u64(base + 16)
    nparams = min(buf.u32(base + 24), 15)
    params = [buf.u64(base + 32 + j * 8) for j in range(nparams)]
    rep.exception_params = params
    rep.bugcheck.code = rep.exception_code
    rep.bugcheck.params = params
    rep.raw_fields["异常记录"] = {
        "ExceptionCode": f"0x{rep.exception_code:08X}",
        "ExceptionFlags": f"0x{rep.exception_flags:X}",
        "ExceptionAddress": f"0x{rep.exception_address:X}",
        "NumberParameters": nparams,
        **{f"参数{j + 1}": f"0x{v:X}" for j, v in enumerate(params[:4])},
    }


def _parse_misc(buf: _Buf, loc: tuple[int, int], rep: DumpReport) -> None:
    dsize, rva = loc
    info_size = buf.u32(rva)
    rep.process_id = buf.u32(rva + 8)
    rep.process_create = _unix_to_dt(buf.u32(rva + 12))
    misc = {"ProcessId": rep.process_id}
    if info_size >= 32 and dsize >= 32:
        misc["处理器标称频率"] = f"{buf.u32(rva + 24)} MHz"
        misc["处理器当前频率"] = f"{buf.u32(rva + 28)} MHz"
    if rep.process_create:
        misc["进程创建时间"] = rep.process_create.strftime("%Y-%m-%d %H:%M:%S")
    rep.raw_fields["杂项信息"] = misc


# ------------------------------------------------------------------ 内核转储
def _parse_kernel(buf: _Buf, rep: DumpReport, head: bytes) -> None:
    valid = head[4:8]
    is64 = valid == b"DU64"
    rep.kind_text = "内核转储 (完整/小内存转储)" if is64 else "内核转储 (32 位)"
    raw_major = buf.u32(0x08)
    raw_minor = buf.u32(0x0C)
    s = SystemInfo()
    s.arch = 9 if is64 else 0
    # 真机转储（Win10/11）里 DUMP_HEADER64 的 MinorVersion 字段装的是 **build 号**，
    # 例如 Win11 24H2 是 15 / 26100；Win10 是 10 / 19045。
    # 老系统（Win7/8.x）才是真正的 6.1 / 6.3，那些 build 号落在 7600+ 之外的字段里。
    if 7600 <= raw_minor <= 99999:
        s.build = raw_minor
        s.major, s.minor = nt_version_from_build(raw_minor)
    else:
        s.build = 0
        s.major, s.minor = raw_major, raw_minor
    s.cpus = buf.u32(0x34) if is64 else buf.u32(0x24)
    if is64:
        code = buf.u32(0x38)
        params = [buf.u64(0x40 + i * 8) for i in range(4)]
        image_type = buf.u32(0x30)
    else:
        code = buf.u32(0x28)
        params = [buf.u32(0x2C + i * 4) for i in range(4)]
        image_type = buf.u32(0x20)
    # 内核转储里的 bugcheck 必须是合理值
    if code and code <= 0x0000FFFF:
        rep.bugcheck.code = code
        rep.bugcheck.params = params
    else:
        rep.warnings.append("未能从内核转储头中取得有效的蓝屏码")
    rep.system = s
    rep.raw_fields["内核转储头"] = {
        "签名": head[:4].decode("ascii", "replace") + " / " + valid.decode("ascii", "replace"),
        "MachineImageType": f"0x{image_type:X}",
        "处理器数量": s.cpus,
        "操作系统版本": f"{s.major}.{s.minor}" + (f".{s.build}" if s.build else ""),
        "头部原始版本字段": f"{raw_major}.{raw_minor}（MinorVersion 实为 build 号）",
        "蓝屏码": f"0x{code:08X}" if code else "-",
        **{f"参数{j + 1}": f"0x{v:016X}" if is64 else f"0x{v:08X}"
           for j, v in enumerate(params)},
    }
    rep.raw_fields["内核转储说明"] = (
        "内核转储内部按物理内存页组织，导出模块列表与堆栈需要 cdb / WinDbg 解析符号。"
        "本工具已给出蓝屏码与参数，足以定位问题方向。"
    )
