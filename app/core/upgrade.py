# -*- coding: utf-8 -*-
"""Win11 升级体检：按微软公布的最低要求逐项判定。**全只读**。

判定的是「这台机器**硬件/固件层面**能不能装 Win11」，不判断账号、区域、
激活状态这些东西 —— 那些不属于本工具的范围，也不该替用户下结论。

| 项 | 要求 | 取数 |
|---|---|---|
| 处理器 | 64 位双核 1 GHz+，Intel 8 代 / AMD Zen2（Ryzen 3000）起 | 注册表 ProcessorNameString + 型号解析 |
| 内存 | 4 GB | `GlobalMemoryStatusEx` |
| 系统盘 | 64 GB | `GetDiskFreeSpaceExW` |
| 系统固件 | UEFI | `GetFirmwareType` |
| TPM | 2.0，已启用 | `root\\cimv2\\security\\microsofttpm` 的 `Win32_Tpm` |
| 安全启动 | 支持且已开启 | `Control\\SecureBoot\\State` 的 `UEFISecureBootEnabled` |
| 显卡 | 支持 DirectX 12 / WDDM 2.0 | 只能给型号与驱动日期，**不硬判** |

**CPU 代际是估算**：型号字符串里能读出的代际是可靠的，但「同代不同 SKU」
的个别型号（至强、赛扬、奔腾、部分 OEM 特供）微软另有名单，所以这类一律标
「需人工确认」而不是拍板——现场机器五花八门，宁可让工程师多看一眼。
"""
from __future__ import annotations

import ctypes
import json
import os
import re
from ctypes import wintypes
from dataclasses import dataclass, field

from .disk import _run_ps
from . import winver

IS_WIN = os.name == "nt"
HKLM = 0x80000002

CPU_KEY = r"HARDWARE\DESCRIPTION\System\CentralProcessor\0"
SECUREBOOT_KEY = r"SYSTEM\CurrentControlSet\Control\SecureBoot\State"

MIN_RAM_GB = 4
MIN_DISK_GB = 64
MIN_CPU_GHZ = 1.0

VERDICT_CN = {"ok": "满足", "warn": "需确认", "crit": "不满足", "info": "参考"}


@dataclass
class Item:
    name: str
    current: str                 # 本机现状
    required: str                # 官方要求
    verdict: str = "ok"          # ok / warn / crit / info
    note: str = ""

    @property
    def verdict_text(self) -> str:
        return VERDICT_CN.get(self.verdict, self.verdict)


@dataclass
class Report:
    items: list[Item] = field(default_factory=list)
    cpu: str = ""
    cpu_gen: str = ""
    ram_gb: float = 0.0
    disk_total_gb: float = 0.0
    disk_free_gb: float = 0.0
    firmware: str = ""
    tpm: str = ""
    secure_boot: str = ""
    already_11: bool = False

    @property
    def blockers(self) -> list[Item]:
        """真正的硬性不满足项。"""
        return [i for i in self.items if i.verdict == "crit"]

    @property
    def cautions(self) -> list[Item]:
        return [i for i in self.items if i.verdict == "warn"]

    @property
    def ready(self) -> bool:
        return not self.blockers

    @property
    def headline(self) -> str:
        if self.already_11:
            return "本机已是 Windows 11，可对照下表确认升级条件是否仍满足"
        if not self.blockers and not self.cautions:
            return "满足 Windows 11 的硬件要求，可以直接升级"
        if not self.blockers:
            return f"基本满足，但有 {len(self.cautions)} 项需人工确认"
        return "有 %d 项不满足 Windows 11 最低要求：%s" % (
            len(self.blockers), "、".join(i.name for i in self.blockers))


# ------------------------------------------------------------------ 取数
def _reg(root: int, path: str, name: str):
    if not IS_WIN:
        return None
    try:
        import winreg
        k = winreg.OpenKey(root, path, 0, winreg.KEY_READ)
    except OSError:
        return None
    try:
        return winreg.QueryValueEx(k, name)[0]
    except OSError:
        return None
    finally:
        k.Close()


def cpu_name() -> str:
    return str(_reg(HKLM, CPU_KEY, "ProcessorNameString") or "").strip()


def cpu_mhz() -> int:
    try:
        return int(_reg(HKLM, CPU_KEY, "~MHz") or 0)
    except (TypeError, ValueError):
        return 0


def cpu_logical() -> int:
    """逻辑处理器数量（每个逻辑 CPU 在 CentralProcessor 下有一个子键）。"""
    if not IS_WIN:
        return 0
    try:
        import winreg
        k = winreg.OpenKey(HKLM, r"HARDWARE\DESCRIPTION\System\CentralProcessor",
                           0, winreg.KEY_READ)
    except OSError:
        return 0
    n = 0
    try:
        while True:
            try:
                winreg.EnumKey(k, n)
            except OSError:
                break
            n += 1
    finally:
        k.Close()
    return n


def ram_gb() -> float:
    """物理内存总量（GB），走 GlobalMemoryStatusEx，不依赖 WMI。"""
    if not IS_WIN:
        return 0.0

    class MEMORYSTATUSEX(ctypes.Structure):
        _fields_ = [("dwLength", wintypes.DWORD),
                    ("dwMemoryLoad", wintypes.DWORD),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

    try:
        st = MEMORYSTATUSEX()
        st.dwLength = ctypes.sizeof(st)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st)):
            return 0.0
        return st.ullTotalPhys / (1024 ** 3)
    except Exception:                        # noqa: BLE001
        return 0.0


def system_drive() -> str:
    return os.environ.get("SystemDrive", "C:") + "\\"


def disk_space() -> tuple[float, float]:
    """系统盘 (总容量 GB, 可用 GB)。"""
    try:
        u = ctypes.windll.kernel32.GetDiskFreeSpaceExW
        free = ctypes.c_ulonglong(0)
        total = ctypes.c_ulonglong(0)
        u(ctypes.c_wchar_p(system_drive()), ctypes.byref(free),
          ctypes.byref(total), None)
        return (total.value / (1024 ** 3), free.value / (1024 ** 3))
    except Exception:                        # noqa: BLE001
        return (0.0, 0.0)


def firmware_type() -> str:
    """UEFI / 传统 BIOS。GetFirmwareType 是 Win8+ 的 API。"""
    if not IS_WIN:
        return ""
    try:
        t = ctypes.c_uint(0)
        fn = getattr(ctypes.windll.kernel32, "GetFirmwareType", None)
        if fn and fn(ctypes.byref(t)):
            return {1: "BIOS（传统）", 2: "UEFI", 3: "不明确"}.get(t.value, "")
    except Exception:                        # noqa: BLE001
        pass
    # 兜底：SecureBoot 的 State 键只在 UEFI 上存在
    if _reg(HKLM, SECUREBOOT_KEY, "UEFISecureBootEnabled") is not None:
        return "UEFI"
    return ""


def secure_boot() -> str:
    """已开启 / 已关闭 / 不支持。"""
    v = _reg(HKLM, SECUREBOOT_KEY, "UEFISecureBootEnabled")
    if v is None:
        return "不支持（固件非 UEFI 或未启用安全启动）"
    return "已开启" if int(v) else "已关闭"


_PS_TPM = (
    "[Console]::OutputEncoding=[Text.Encoding]::UTF8;"
    "try{$t=Get-CimInstance -Namespace root/cimv2/security/microsofttpm "
    "-ClassName Win32_Tpm -ErrorAction Stop;"
    "@{present=$true;enabled=[bool]$t.IsEnabled_InitialValue;"
    "activated=[bool]$t.IsActivated_InitialValue;"
    "spec=[string]$t.SpecVersion;mfr=[string]$t.ManufacturerIdTxt}"
    "|ConvertTo-Json -Compress}catch{'{}'}")


def tpm_info() -> dict:
    """{present, enabled, activated, spec, mfr}。取不到时 present=False。"""
    empty = {"present": False, "enabled": False, "activated": False,
             "spec": "", "mfr": ""}
    if not IS_WIN:
        return empty
    raw = (_run_ps(_PS_TPM, timeout=20) or "").strip()
    if not raw:
        return empty
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return empty
    if not isinstance(data, dict) or not data.get("present"):
        return empty
    out = dict(empty)
    out.update({k: data.get(k, empty[k]) for k in empty})
    out["present"] = True
    return out


# ------------------------------------------------------------------ CPU 代际
INTEL_GEN_RE = re.compile(r"(\d{1,2})(?:st|nd|rd|th)\s+Gen", re.I)
INTEL_MODEL_RE = re.compile(r"i[3579]-(\d{3,5})", re.I)
AMD_RYZEN_RE = re.compile(r"Ryzen\s+(?:Threadripper\s+)?(\d)\s+(\d{4})", re.I)


def cpu_generation(name: str) -> tuple[str, int, str]:
    """解析出 (厂商, 代际, 展示文本)。代际 0 表示读不出来。

    只对「型号能明确读出代际」的情况给数字，其余留 0 让调用方标「需确认」。
    """
    s = (name or "").strip()
    if not s:
        return ("", 0, "")
    m = INTEL_GEN_RE.search(s)
    if m:
        gen = int(m.group(1))
        return ("Intel", gen, f"Intel 第 {gen} 代")
    m = INTEL_MODEL_RE.search(s)
    if m:
        digits = m.group(1)
        if len(digits) == 4:
            gen = int(digits[0])                 # i7-9700 → 9
        elif len(digits) == 5:
            head = int(digits[:2])               # i7-12700 → 12
            gen = head if 10 <= head <= 19 else int(digits[0])
        else:
            gen = 0
        return ("Intel", gen, f"Intel 第 {gen} 代" if gen else "Intel（代际未能识别）")
    if "intel" in s.lower():
        return ("Intel", 0, "Intel（型号无法判断代际）")
    m = AMD_RYZEN_RE.search(s)
    if m:
        series = int(m.group(2))
        if series >= 9000:
            gen, bucket = 9, 9000
        elif series >= 7000:
            gen, bucket = 7, 7000
        elif series >= 5000:
            gen, bucket = 5, 5000
        elif series >= 3000:
            gen, bucket = 3, 3000
        elif series >= 2000:
            gen, bucket = 2, 2000
        else:
            gen, bucket = 1, 1000
        # 显示按「系列」而不是型号数字：Ryzen 7 7735HS 属 7000 系，
        # 写成「Ryzen 7735 系」会让工程师以为有个 7735 系列
        return ("AMD", gen, f"AMD Ryzen {bucket} 系")
    if "amd" in s.lower():
        return ("AMD", 0, "AMD（型号无法判断系列）")
    return ("", 0, "")


def _cpu_item(name: str, mhz: int, logical: int) -> Item:
    vendor, gen, text = cpu_generation(name)
    cur_bits = [name or "未能读取处理器型号"]
    if mhz:
        cur_bits.append(f"{mhz / 1000:.2f} GHz")
    if logical:
        cur_bits.append(f"{logical} 个逻辑处理器")
    current = " · ".join(cur_bits)
    req = "64 位双核 1 GHz 以上，Intel 8 代 / AMD Ryzen 3000 起"

    if not name:
        return Item("处理器", current, req, "warn", "读不到型号，请用 CPU-Z 等方式核对")
    item = Item("处理器", current, req, "ok", text)
    if mhz and mhz < MIN_CPU_GHZ * 1000:
        item.verdict = "crit"
        item.note = f"主频低于 {MIN_CPU_GHZ:g} GHz"
        return item
    if logical and logical < 2:
        item.verdict = "crit"
        item.note = "少于 2 个逻辑处理器"
        return item
    if vendor == "Intel":
        if gen == 0:
            item.verdict = "warn"
            item.note = (text + "：至强 / 赛扬 / 奔腾与部分 OEM 型号另有官方名单，"
                         "需人工核对")
        elif gen < 8:
            item.verdict = "crit"
            item.note = f"Intel 第 {gen} 代不在官方支持列表（第 8 代起）"
        else:
            item.note = f"{text}，在官方支持范围内"
    elif vendor == "AMD":
        if gen == 0:
            item.verdict = "warn"
            item.note = text + "：需人工核对官方 CPU 支持列表"
        elif gen < 2:
            item.verdict = "crit"
            item.note = "Ryzen 1000 系（Zen）不在官方支持列表"
        elif gen == 2:
            item.verdict = "warn"
            item.note = "Ryzen 2000 系为 Zen+，官方名单只收录部分型号，需核对"
        else:
            item.note = f"{text}，在官方支持范围内"
    else:
        item.verdict = "warn"
        item.note = "非 Intel / AMD 处理器，需人工核对"
    return item


def check() -> Report:
    """跑一遍体检。全部只读，约 1~2 秒（TPM 那次 PowerShell 占大头）。"""
    ver = winver.current()
    rep = Report(already_11=ver.is_11)
    name = cpu_name()
    vendor, gen, text = cpu_generation(name)
    rep.cpu = name
    rep.cpu_gen = text
    rep.items.append(_cpu_item(name, cpu_mhz(), cpu_logical()))

    # ---- 内存
    mem = ram_gb()
    rep.ram_gb = mem
    ok = mem >= MIN_RAM_GB
    rep.items.append(Item(
        "内存", f"{mem:.1f} GB" if mem else "未能读取", f"{MIN_RAM_GB} GB 及以上",
        "ok" if ok else "crit",
        "" if ok else f"差 {MIN_RAM_GB - mem:.1f} GB，需加装内存"))

    # ---- 系统盘
    total, free = disk_space()
    rep.disk_total_gb, rep.disk_free_gb = total, free
    ok = total >= MIN_DISK_GB
    rep.items.append(Item(
        "系统盘容量", f"{system_drive()} 共 {total:.0f} GB（可用 {free:.0f} GB）",
        f"{MIN_DISK_GB} GB 及以上", "ok" if ok else "crit",
        "" if ok else f"差 {MIN_DISK_GB - total:.0f} GB，需换更大的盘"))

    # ---- 固件
    fw = firmware_type()
    rep.firmware = fw
    if not fw:
        rep.items.append(Item("系统固件", "未能判断", "UEFI",
                              "warn", "请进 BIOS 确认启动模式为 UEFI"))
    elif fw.startswith("UEFI"):
        rep.items.append(Item("系统固件", fw, "UEFI", "ok", ""))
    else:
        rep.items.append(Item(
            "系统固件", fw, "UEFI", "crit",
            "传统 BIOS 需先把系统盘转为 GPT 并把启动模式改为 UEFI"))

    # ---- TPM
    tpm = tpm_info()
    if tpm["present"]:
        spec = str(tpm["spec"] or "").split(",")[0].strip()
        state = "已启用" if tpm["enabled"] else "未启用"
        rep.tpm = f"TPM {spec or '?'}（{state}）"
        good = spec.startswith("2") and tpm["enabled"]
        rep.items.append(Item(
            "TPM", rep.tpm, "TPM 2.0 且已启用",
            "ok" if good else "crit",
            "" if good else ("版本低于 2.0，需硬件模块或固件升级"
                             if not spec.startswith("2")
                             else "在 BIOS 里启用 TPM（Intel PTT / AMD fTPM）")))
    else:
        rep.tpm = "未检测到"
        rep.items.append(Item(
            "TPM", "未检测到 TPM", "TPM 2.0 且已启用", "crit",
            "多数主板可在 BIOS 里开启 fTPM / PTT；老平台需加装 TPM 模块"))

    # ---- 安全启动
    sb = secure_boot()
    rep.secure_boot = sb
    if sb.startswith("已开启"):
        rep.items.append(Item("安全启动", sb, "支持并可开启", "ok", ""))
    elif sb.startswith("已关闭"):
        rep.items.append(Item("安全启动", sb, "支持并可开启", "warn",
                              "在 BIOS 里开启 Secure Boot 即可，不影响升级可行性"))
    else:
        rep.items.append(Item("安全启动", sb, "支持并可开启", "warn",
                              "固件不支持安全启动，Win11 会以「不满足要求」提示"))

    # ---- 显卡（只给参考，不硬判）
    rep.items.append(Item(
        "显卡", _gpu_text(), "支持 DirectX 12 / WDDM 2.0", "info",
        "驱动模型版本需在 dxdiag 里确认，本工具不替你下结论"))

    # ---- 系统版本
    rep.items.append(Item(
        "当前系统", ver.full_name or ver.label, "Windows 11",
        "info" if ver.is_11 else "ok",
        "已是 Windows 11" if ver.is_11 else "可升级到 Windows 11"))
    return rep


def _gpu_text() -> str:
    """显卡型号（来自设备注册表，不起外部进程）。"""
    try:
        from . import devices as dv
        cards = [d for d in dv.devices()
                 if d.cls in ("显示适配器", "Display adapters")]
        if not cards:
            return "未检测到独立/集成显示适配器"
        # 远程控制/串流软件装的虚拟显示器不是真显卡，有真卡时就不列它
        virtual = ("virtual", "虚拟", "remote", "gameviewer", "idd")
        real = [d for d in cards
                if not any(v in d.name.lower() for v in virtual)]
        use = real or cards
        return "、".join(d.name for d in use[:3])
    except Exception:                        # noqa: BLE001
        return "未能读取"
