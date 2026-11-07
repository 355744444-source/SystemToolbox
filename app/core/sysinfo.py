# -*- coding: utf-8 -*-
"""系统信息采集：纯只读，尽量走注册表 / ctypes，不依赖 psutil。

取数原则
- 能注册表读的一律注册表（快、不需要管理员）
- 需要 API 的走 ctypes（PhysicalCores / 内存 / 运行时长）
- 只有进程列表用 tasklist（进程数量与工作集，方便又不引第三方库）
- 每一项都有降级：读不到就显示「未知」，绝不让异常冒到界面
"""
from __future__ import annotations

import csv
import ctypes
import io
import os
import platform
import re
import subprocess
import time
from ctypes import wintypes
from dataclasses import dataclass, field

from . import winver
from .cleaner import human_size, is_admin
from .disk import list_volumes

IS_WIN = os.name == "nt"

CREATE_NO_WINDOW = 0x08000000
HKLM = 0x80000002
HKCU = 0x80000001

CV_KEY = r"SOFTWARE\Microsoft\Windows NT\CurrentVersion"
BIOS_KEY = r"HARDWARE\DESCRIPTION\System\BIOS"
CPU_KEY = r"HARDWARE\DESCRIPTION\System\CentralProcessor\0"
GPU_CLASS = (r"SYSTEM\CurrentControlSet\Control\Class"
             r"\{4d36e968-e325-11ce-bfc1-08002be10318}")
FIREWALL = (r"SYSTEM\CurrentControlSet\Services\SharedAccess\Parameters"
            r"\FirewallPolicy\{0}Profile")

# 版本名 / 版本代号 / 版本分支统一由 winver 提供（单一真相源），
# 这里只保留别名，避免老调用方报错。
_WIN_NAMES = dict(winver.WIN10_BUILDS)
_WIN_NAMES.update(winver.WIN11_BUILDS)
_EDITION_CN = winver.EDITION_CN

_POWER_PLANS = {
    "381b4222-f694-41f0-9685-ff5bb260df2e": "平衡",
    "8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c": "高性能",
    "a1841308-3541-4fab-bc81-f71556f20b4a": "节能",
    "e9a42b02-d5df-448d-aa00-03f14749eb61": "卓越性能",
}


# ------------------------------------------------------------------ 基础
def _reg(root: int, path: str, name: str):
    """读注册表值，失败返回 None（含 32/64 位视图的自动回退）。"""
    if not IS_WIN:
        return None
    for view in (0, 0x0100, 0x0200):        # 默认 → 64 位 → 32 位
        try:
            import winreg
            k = winreg.OpenKey(root, path, 0, winreg.KEY_READ | view)
            try:
                return winreg.QueryValueEx(k, name)[0]
            finally:
                k.Close()
        except OSError:
            continue
        except Exception:                    # noqa: BLE001
            return None
    return None


def _s(root: int, path: str, name: str) -> str:
    v = _reg(root, path, name)
    if v is None:
        return ""
    if isinstance(v, bytes):
        try:
            return v.decode("utf-16-le", errors="ignore").split("\x00")[0]
        except Exception:                    # noqa: BLE001
            return ""
    return str(v).strip("\x00").strip()


def _i(root: int, path: str, name: str) -> int | None:
    v = _reg(root, path, name)
    if isinstance(v, int):
        return v
    try:
        return int(str(v), 0)
    except (TypeError, ValueError):
        return None


def _subkeys(root: int, path: str) -> list[str]:
    if not IS_WIN:
        return []
    try:
        import winreg
        k = winreg.OpenKey(root, path, 0, winreg.KEY_READ)
    except OSError:
        return []
    out = []
    try:
        i = 0
        while True:
            try:
                out.append(winreg.EnumKey(k, i))
            except OSError:
                break
            i += 1
    finally:
        k.Close()
    return out


def _run(args: list[str], timeout: int = 12) -> str:
    """跑一个外部命令，统一按 UTF-8/GBK 兜底解码。"""
    if not IS_WIN:
        return ""
    try:
        p = subprocess.run(args, capture_output=True, timeout=timeout,
                           creationflags=CREATE_NO_WINDOW)
    except Exception:                        # noqa: BLE001
        return ""
    raw = p.stdout or b""
    for enc in ("utf-8", "gbk", "mbcs"):
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="replace")


# ------------------------------------------------------------------ 结构
@dataclass
class Info:
    label: str
    value: str
    hint: str = ""


@dataclass
class Proc:
    name: str
    pid: int
    mem: int                       # 字节
    session: str = ""


@dataclass
class Snapshot:
    os_rows: list[Info] = field(default_factory=list)
    hw_rows: list[Info] = field(default_factory=list)
    sec_rows: list[Info] = field(default_factory=list)
    mem_total: int = 0
    mem_avail: int = 0
    mem_percent: int = 0
    uptime: float = 0.0            # 秒
    boot_at: float = 0.0           # unix 时间戳
    volumes: list = field(default_factory=list)
    procs: list[Proc] = field(default_factory=list)
    proc_total: int = 0
    power: str = ""
    admin: bool = False
    host: str = ""
    user: str = ""
    ver: object = None             # winver.WinVer，界面用来显示分支

    @property
    def mem_used(self) -> int:
        return max(0, self.mem_total - self.mem_avail)


# ------------------------------------------------------------------ OS
def os_name() -> tuple[str, str]:
    """返回 (完整名称, 版本号文本)。判定统一交给 winver —— 只认 build 号。"""
    v = winver.current(force=True)
    name = v.full_name
    if not v.edition:
        # EditionID 缺失时从 ProductName 里取尾巴（"Windows 10 Pro" → "Pro"）
        product = _s(HKLM, CV_KEY, "ProductName") or ""
        tail = product.replace("Windows 10", "").replace("Windows 11", "").strip()
        if tail:
            name = f"{name} {tail}".strip()
    return (name, v.version_text or "未知")


def branch_info() -> Info:
    """系统优化工作台按哪一代系统呈现 —— 放在系统表里最直观。"""
    v = winver.current()
    fam = winver.FAMILY_CN.get(v.family, v.family)
    if not v.build:
        return Info("适配分支", f"{fam}（build 未知）",
                    "build 号读取失败，按默认分支呈现")
    return Info("适配分支", f"{v.short}（build {v.build}）",
                f"{fam} 分支：系统优化、服务判定、微调清单均按此裁剪")


def _install_date() -> str:
    ts = _i(HKLM, CV_KEY, "InstallDate")
    if not ts:
        return "未知"
    try:
        return time.strftime("%Y-%m-%d", time.localtime(ts))
    except (OSError, ValueError, OverflowError):
        return "未知"


# ------------------------------------------------------------------ CPU
class _LPI(ctypes.Structure):
    """LOGICAL_PROCESSOR_INFORMATION（x64 下 32 字节）。"""
    _fields_ = [("ProcessorMask", ctypes.c_ulonglong),
                ("Relationship", ctypes.c_ulong),
                ("_pad", ctypes.c_ulong),
                ("_data", ctypes.c_byte * 16)]


def _physical_cores() -> int:
    """物理核心数；ctypes 失败则回退 0（界面显示逻辑核）。"""
    if not IS_WIN:
        return 0
    try:
        k32 = ctypes.windll.kernel32
        need = wintypes.DWORD(0)
        # 第一次调用只为拿所需缓冲区大小
        k32.GetLogicalProcessorInformation(None, ctypes.byref(need))
        if not need.value:
            return 0
        count = need.value // ctypes.sizeof(_LPI)
        if count <= 0 or count > 1024:
            return 0
        buf = (_LPI * count)()
        size = wintypes.DWORD(ctypes.sizeof(buf))
        if not k32.GetLogicalProcessorInformation(buf, ctypes.byref(size)):
            return 0
        used = size.value // ctypes.sizeof(_LPI)
        return sum(1 for i in range(min(used, count)) if buf[i].Relationship == 0)
    except Exception:                        # noqa: BLE001
        return 0


def cpu_rows() -> list[Info]:
    name = _s(HKLM, CPU_KEY, "ProcessorNameString") or platform.processor() or "未知"
    mhz = _i(HKLM, CPU_KEY, "~MHz")
    logical = os.cpu_count() or 0
    cores = _physical_cores()
    core_text = f"{cores} 核 / {logical} 线程" if cores else f"{logical} 线程"
    rows = [Info("处理器", name, core_text)]
    if mhz:
        rows.append(Info("基准频率", f"{mhz} MHz"))
    return rows


# ------------------------------------------------------------------ 内存
class _MEMSTAT(ctypes.Structure):
    _fields_ = [("dwLength", wintypes.DWORD), ("dwMemoryLoad", wintypes.DWORD),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]


def memory() -> tuple[int, int, int]:
    """返回 (总量, 可用, 占用百分比)，单位字节。"""
    if not IS_WIN:
        return (0, 0, 0)
    try:
        st = _MEMSTAT()
        st.dwLength = ctypes.sizeof(_MEMSTAT)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st)):
            return (int(st.ullTotalPhys), int(st.ullAvailPhys), int(st.dwMemoryLoad))
    except Exception:                        # noqa: BLE001
        pass
    return (0, 0, 0)


def uptime() -> float:
    """开机至今的秒数（GetTickCount64，不受系统时间篡改影响）。"""
    if not IS_WIN:
        return 0.0
    try:
        return float(ctypes.windll.kernel32.GetTickCount64() / 1000.0)
    except Exception:                        # noqa: BLE001
        return 0.0


def human_duration(sec: float) -> str:
    sec = max(0.0, float(sec))
    d, rem = divmod(int(sec), 86400)
    h, rem = divmod(rem, 3600)
    m = rem // 60
    if d:
        return f"{d} 天 {h} 小时"
    if h:
        return f"{h} 小时 {m} 分"
    return f"{m} 分"


# ------------------------------------------------------------------ 硬件
def board_rows() -> list[Info]:
    rows: list[Info] = []
    vendor = _s(HKLM, BIOS_KEY, "SystemManufacturer")
    model = _s(HKLM, BIOS_KEY, "SystemProductName")
    if vendor or model:
        rows.append(Info("整机", f"{vendor} {model}".strip()))
    bm = _s(HKLM, BIOS_KEY, "BaseBoardManufacturer")
    bp = _s(HKLM, BIOS_KEY, "BaseBoardProduct")
    if bm or bp:
        rows.append(Info("主板", f"{bm} {bp}".strip()))
    bv = _s(HKLM, BIOS_KEY, "BIOSVendor")
    bver = _s(HKLM, BIOS_KEY, "BIOSVersion")
    bdate = _s(HKLM, BIOS_KEY, "BIOSReleaseDate")
    if bver or bv:
        rows.append(Info("BIOS", " ".join(x for x in (bv, bver) if x),
                         f"日期 {bdate}" if bdate else ""))
    return rows


def gpu_rows() -> list[Info]:
    """从显示适配器类键里取所有显卡；虚拟显示器（远程控制软件装的）单独标注。"""
    rows: list[Info] = []
    for sub in _subkeys(HKLM, GPU_CLASS):
        if not re.fullmatch(r"\d{4}", sub):
            continue
        base = f"{GPU_CLASS}\\{sub}"
        desc = _s(HKLM, base, "DriverDesc")
        if not desc:
            continue
        ver = _s(HKLM, base, "DriverVersion")
        prov = _s(HKLM, base, "ProviderName")
        vram = _i(HKLM, base, "HardwareInformation.MemorySize")
        if vram is None:
            vram = _i(HKLM, base, "HardwareInformation.qwMemorySize")
        bits = []
        if prov:
            bits.append(prov)
        if vram and 0 < vram < 1 << 40:
            bits.append(f"显存 {human_size(vram)}")
        if ver:
            bits.append(f"驱动 {ver}")
        low = desc.lower()
        virtual = any(k in low for k in ("idd", "virtual", "mirror",
                                         "remote display", "spacedesk"))
        rows.append(Info("虚拟显示" if virtual else "显卡", desc, " · ".join(bits)))
    return rows


def disk_rows(volumes: list | None = None) -> list[Info]:
    """卷信息行。`volumes` 传进来就不重扫 —— 每卷都要开句柄问一次
    IOCTL，扫两遍等于把 2~6 次设备查询白花掉（collect 里原来就是这么花的）。"""
    rows: list[Info] = []
    for v in (volumes if volumes is not None else list_volumes()):
        if not v.total:
            continue
        label = v.label or v.media_text
        rows.append(Info(f"磁盘 {v.letter}", f"{label} · {v.fs}",
                         f"可用 {human_size(v.free)} / 共 {human_size(v.total)}"
                         f"（{v.used_ratio * 100:.0f}% 已用）"))
    return rows


def power_plan() -> str:
    out = _run(["powercfg", "/getactivescheme"], timeout=8)
    m = re.search(r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})",
                  out, re.I)
    if not m:
        return ""
    guid = m.group(1).lower()
    name = _POWER_PLANS.get(guid, "")
    tail = re.search(r"\(([^)]+)\)", out)
    if not name and tail:
        name = tail.group(1).strip()
    return f"{name}" if name else guid


# ------------------------------------------------------------------ 安全
def security_rows() -> list[Info]:
    rows: list[Info] = []
    # 防火墙：三个配置文件都开才算「已开启」
    states = []
    for prof in ("Domain", "Standard", "Public"):
        v = _i(HKLM, FIREWALL.format(prof), "EnableFirewall")
        states.append(v)
    known = [v for v in states if v is not None]
    if known:
        on = sum(1 for v in known if v == 1)
        text = "已开启" if on == len(known) else ("部分开启" if on else "已关闭")
        rows.append(Info("防火墙", text, f"{on}/{len(known)} 个配置文件启用"))
    else:
        rows.append(Info("防火墙", "未知", "需要读取安全策略"))

    # Defender 实时保护：策略关闭 / 实时监控关闭都算关
    policy = _i(HKLM, r"SOFTWARE\Policies\Microsoft\Windows Defender", "DisableAntiSpyware")
    rt = _i(HKLM, r"SOFTWARE\Microsoft\Windows Defender\Real-Time Protection",
            "DisableRealtimeMonitoring")
    if policy == 1:
        rows.append(Info("Defender", "已被策略关闭", "DisableAntiSpyware=1"))
    elif rt == 1:
        rows.append(Info("Defender", "实时保护已关闭", "DisableRealtimeMonitoring=1"))
    elif rt == 0 or policy == 0:
        rows.append(Info("Defender", "运行中", "实时保护已开启"))
    else:
        rows.append(Info("Defender", "未知", "系统未开放该键的读取权限"))
    return rows


# ------------------------------------------------------------------ 进程
def top_processes(limit: int = 12, stop_flag=None) -> tuple[list[Proc], int]:
    """按工作集内存排序取前 N 个进程，同时给出进程总数。"""
    if not IS_WIN:
        return ([], 0)
    out = _run(["tasklist", "/fo", "csv", "/nh"], timeout=20)
    if not out.strip():
        return ([], 0)
    procs: list[Proc] = []
    total = 0
    reader = csv.reader(io.StringIO(out))
    for row in reader:
        if stop_flag and stop_flag[0]:
            break
        if len(row) < 5:
            continue
        raw = row[4].replace(",", "").replace("K", "").replace("k", "").strip()
        try:
            mem = int(raw) * 1024
        except ValueError:
            mem = 0
        try:
            pid = int(row[1])
        except ValueError:
            pid = 0
        procs.append(Proc(name=row[0], pid=pid, mem=mem, session=row[2]))
        total += 1
    procs.sort(key=lambda p: -p.mem)
    return (procs[:limit], total)


# ------------------------------------------------------------------ 汇总
def collect(stop_flag=None) -> Snapshot:
    snap = Snapshot()
    snap.host = platform.node()
    snap.user = os.environ.get("USERNAME", "")
    snap.admin = is_admin()

    name, ver = os_name()
    snap.ver = winver.current()
    snap.os_rows = [
        Info("操作系统", name, ver),
        branch_info(),
        Info("计算机名", snap.host, snap.user or ""),
        Info("安装日期", _install_date()),
        Info("架构", f"{platform.machine()} · {platform.architecture()[0]}"),
        Info("注册用户", _s(HKLM, CV_KEY, "RegisteredOwner") or "未知"),
    ]
    if snap.admin:
        snap.os_rows.append(Info("当前权限", "管理员", "可执行服务与系统级改动"))

    snap.mem_total, snap.mem_avail, snap.mem_percent = memory()
    # 卷信息只扫一次，硬件表和「磁盘」页签共用这一份
    volumes = list_volumes()
    snap.hw_rows = cpu_rows() + board_rows() + gpu_rows() + disk_rows(volumes)
    snap.hw_rows.insert(0, Info("内存", human_size(snap.mem_total) if snap.mem_total else "未知",
                                f"可用 {human_size(snap.mem_avail)}"
                                f"（已用 {snap.mem_percent}%）" if snap.mem_total else ""))

    snap.uptime = uptime()
    snap.boot_at = time.time() - snap.uptime if snap.uptime else 0.0
    snap.volumes = volumes
    snap.power = power_plan()
    snap.sec_rows = security_rows()
    snap.procs, snap.proc_total = top_processes(12, stop_flag)
    return snap
