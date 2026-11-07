# -*- coding: utf-8 -*-
"""磁盘检测与优化动作。

- 卷信息全部走 Win32 API（GetDiskFreeSpaceEx / GetVolumeInformation / GetDriveType）；
- 介质类型（SSD / HDD）用 IOCTL_STORAGE_QUERY_PROPERTY 的 SeekPenalty 属性判断，
  这是 Windows 自己判断「该不该碎片整理」的依据，比看型号猜要准；
- 物理盘健康走 PowerShell Get-PhysicalDisk，取不到就退化为「未获取」，不阻塞界面；
- 优化动作调用系统自带的 defrag.exe / chkdsk.exe，**不自己搬动任何数据**，
  所以不存在把盘搞坏的风险，最坏情况是动作失败或需要管理员权限。
"""
from __future__ import annotations

import ctypes
import json
import os
import re
import subprocess
from ctypes import wintypes
from dataclasses import dataclass

IS_WIN = os.name == "nt"
CREATE_NO_WINDOW = 0x08000000

DRIVE_TYPES = {0: "未知", 1: "无效", 2: "可移动", 3: "本地磁盘", 4: "网络", 5: "光驱", 6: "内存盘"}


# ------------------------------------------------------------------ 卷信息
@dataclass
class Volume:
    letter: str = ""            # "C:"
    label: str = ""
    fs: str = ""
    total: int = 0
    free: int = 0
    drive_type: int = 0
    is_ssd: bool | None = None  # None = 无法判断
    bus: str = ""

    @property
    def used(self) -> int:
        return max(0, self.total - self.free)

    @property
    def used_ratio(self) -> float:
        return (self.used / self.total) if self.total else 0.0

    @property
    def media_text(self) -> str:
        if self.drive_type != 3:
            return DRIVE_TYPES.get(self.drive_type, "未知")
        if self.bus == "USB":
            return "可移动存储"
        if self.is_ssd is True:
            return "SSD 固态"
        if self.is_ssd is False:
            return "HDD 机械"
        return "介质未知"

    @property
    def is_fixed(self) -> bool:
        return self.drive_type == 3


@dataclass
class PhysicalDisk:
    model: str = ""
    media: str = ""
    health: str = ""
    status: str = ""
    size: int = 0
    bus: str = ""


def _volume_info(letter: str) -> Volume:
    v = Volume(letter=letter)
    root = letter + "\\"
    if not IS_WIN:
        return v
    try:
        free = ctypes.c_ulonglong()
        total = ctypes.c_ulonglong()
        ctypes.windll.kernel32.GetDiskFreeSpaceExW(
            ctypes.c_wchar_p(root),
            ctypes.byref(free), ctypes.byref(total), None)
        v.total, v.free = int(total.value), int(free.value)
    except Exception:       # noqa: BLE001
        pass
    label = ctypes.create_unicode_buffer(261)
    fs = ctypes.create_unicode_buffer(261)
    try:
        ctypes.windll.kernel32.GetVolumeInformationW(
            ctypes.c_wchar_p(root), label, 261, None, None, None, fs, 261)
        v.label = label.value
        v.fs = fs.value
    except Exception:       # noqa: BLE001
        pass
    try:
        v.drive_type = int(ctypes.windll.kernel32.GetDriveTypeW(ctypes.c_wchar_p(root)))
    except Exception:       # noqa: BLE001
        v.drive_type = 0
    if v.drive_type == 3:
        v.is_ssd = _seek_penalty_is_ssd(letter)
    return v


# IOCTL_STORAGE_QUERY_PROPERTY
_IOCTL_STORAGE_QUERY_PROPERTY = 0x2D1400
_StorageDeviceProperty = 0
_StorageDeviceSeekPenaltyProperty = 7
_PropertyStandardQuery = 0
_GENERIC_READ = 0x80000000
_FILE_SHARE_READ = 0x00000001
_FILE_SHARE_WRITE = 0x00000002
_OPEN_EXISTING = 3


class _STORAGE_PROPERTY_QUERY(ctypes.Structure):
    _fields_ = [("PropertyId", wintypes.DWORD),
                ("QueryType", wintypes.DWORD),
                ("AdditionalParameters", ctypes.c_ubyte * 4)]


class _DEVICE_SEEK_PENALTY_DESCRIPTOR(ctypes.Structure):
    _fields_ = [("Version", wintypes.DWORD),
                ("Size", wintypes.DWORD),
                ("IncursSeekPenalty", wintypes.BOOLEAN)]


class _STORAGE_DEVICE_DESCRIPTOR(ctypes.Structure):
    _fields_ = [("Version", wintypes.DWORD),
                ("Size", wintypes.DWORD),
                ("DeviceType", ctypes.c_ubyte),
                ("DeviceTypeModifier", ctypes.c_ubyte),
                ("RemovableMedia", wintypes.BOOLEAN),
                ("CommandQueueing", wintypes.BOOLEAN),
                ("VendorIdOffset", wintypes.DWORD),
                ("ProductIdOffset", wintypes.DWORD),
                ("ProductRevisionOffset", wintypes.DWORD),
                ("SerialNumberOffset", wintypes.DWORD),
                ("BusType", wintypes.DWORD),
                ("RawPropertiesLength", wintypes.DWORD),
                ("RawDeviceProperties", ctypes.c_ubyte * 1)]


BUS_TYPES = {1: "SCSI", 2: "ATAPI", 3: "ATA", 4: "1394", 6: "光纤", 7: "USB",
             8: "RAID", 9: "iSCSI", 10: "SAS", 11: "SATA", 12: "SD", 13: "MMC",
             14: "虚拟", 15: "文件虚拟", 16: "存储空间", 17: "NVMe", 18: "SCM",
             19: "UFS"}


def _open_volume(letter: str):
    try:
        # 必须显式声明返回类型：不声明时 ctypes 默认按 c_int 处理，
        # 64 位句柄会被截断成 32 位 —— 句柄号小的机器上一切正常，
        # 句柄号大的机器上 DeviceIoControl 一律失败（本项目在 usbperf /
        # sign 里都踩过，这里漏了）。
        k32 = _k32()
        h = k32.CreateFileW(
            ctypes.c_wchar_p(f"\\\\.\\{letter}"),
            _GENERIC_READ, _FILE_SHARE_READ | _FILE_SHARE_WRITE,
            None, _OPEN_EXISTING, 0, None)
        if h in (None, -1, 0xFFFFFFFFFFFFFFFF):
            return None
        return h
    except Exception:       # noqa: BLE001
        return None


def _devioctl(h, code: int, in_buf, out_buf, out_size: int) -> bool:
    returned = wintypes.DWORD(0)
    return bool(_k32().DeviceIoControl(
        h, code, in_buf, ctypes.sizeof(in_buf),
        out_buf, out_size, ctypes.byref(returned), None))


def _k32():
    """取配置过 argtypes 的 kernel32（模块级只建一次）。"""
    global _K32
    if _K32 is None:
        dll = ctypes.WinDLL("kernel32", use_last_error=True)
        dll.CreateFileW.restype = wintypes.HANDLE
        dll.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD,
                                    wintypes.DWORD, ctypes.c_void_p,
                                    wintypes.DWORD, wintypes.DWORD,
                                    wintypes.HANDLE]
        dll.DeviceIoControl.restype = wintypes.BOOL
        dll.DeviceIoControl.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                        ctypes.c_void_p, wintypes.DWORD,
                                        ctypes.c_void_p, wintypes.DWORD,
                                        ctypes.POINTER(wintypes.DWORD),
                                        ctypes.c_void_p]
        dll.CloseHandle.argtypes = [wintypes.HANDLE]
        dll.CloseHandle.restype = wintypes.BOOL
        _K32 = dll
    return _K32


_K32 = None


def _seek_penalty_is_ssd(letter: str) -> bool | None:
    """IncursSeekPenalty == 0 → 随机访问无代价 → SSD。"""
    h = _open_volume(letter)
    if h is None:
        return None
    try:
        q = _STORAGE_PROPERTY_QUERY()
        q.PropertyId = _StorageDeviceSeekPenaltyProperty
        q.QueryType = _PropertyStandardQuery
        out = _DEVICE_SEEK_PENALTY_DESCRIPTOR()
        if not _devioctl(h, _IOCTL_STORAGE_QUERY_PROPERTY, q, ctypes.byref(out),
                         ctypes.sizeof(out)):
            return None
        return not bool(out.IncursSeekPenalty)
    finally:
        try:
            ctypes.windll.kernel32.CloseHandle(h)
        except Exception:   # noqa: BLE001
            pass


def volume_bus(letter: str) -> str:
    h = _open_volume(letter)
    if h is None:
        return ""
    try:
        q = _STORAGE_PROPERTY_QUERY()
        q.PropertyId = _StorageDeviceProperty
        q.QueryType = _PropertyStandardQuery
        buf = ctypes.create_string_buffer(1024)
        if not _devioctl(h, _IOCTL_STORAGE_QUERY_PROPERTY, q, buf, 1024):
            return ""
        desc = ctypes.cast(buf, ctypes.POINTER(_STORAGE_DEVICE_DESCRIPTOR)).contents
        return BUS_TYPES.get(int(desc.BusType), "")
    finally:
        try:
            ctypes.windll.kernel32.CloseHandle(h)
        except Exception:   # noqa: BLE001
            pass


def list_volumes() -> list[Volume]:
    out: list[Volume] = []
    if not IS_WIN:
        return out
    try:
        mask = ctypes.windll.kernel32.GetLogicalDrives()
    except Exception:       # noqa: BLE001
        return out
    for i in range(26):
        if not (mask >> i) & 1:
            continue
        letter = f"{chr(ord('A') + i)}:"
        v = _volume_info(letter)
        if v.drive_type not in (2, 3):     # 网络盘、光驱不参与优化
            continue
        v.bus = volume_bus(letter) if v.drive_type == 3 else ""
        out.append(v)
    out.sort(key=lambda x: (0 if x.letter.startswith("C") else 1, x.letter))
    return out


# ------------------------------------------------------------------ 物理盘健康
def _decode(data: bytes) -> str:
    for enc in ("utf-8", "gbk", "latin-1"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", "replace")


def _run_ps(script: str, timeout: int = 25) -> str:
    """跑一段 PowerShell，返回 stdout。失败返回空串（调用方降级处理）。"""
    if not IS_WIN:
        return ""
    exe = "powershell.exe"
    try:
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        p = subprocess.run(
            [exe, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
             "-Command", script],
            capture_output=True, timeout=timeout, creationflags=CREATE_NO_WINDOW,
            startupinfo=si)
        return _decode(p.stdout or b"")
    except Exception:       # noqa: BLE001
        return ""


_PS_DISKS = (
    "[Console]::OutputEncoding=[Text.Encoding]::UTF8;"
    "Get-PhysicalDisk | Select-Object FriendlyName,MediaType,HealthStatus,"
    "OperationalStatus,Size,BusType | ConvertTo-Json -Compress")


def physical_disks() -> list[PhysicalDisk]:
    raw = _run_ps(_PS_DISKS)
    raw = raw.strip()
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if isinstance(data, dict):
        data = [data]
    out = []
    for d in data:
        if not isinstance(d, dict):
            continue
        media = str(d.get("MediaType") or "")
        media_text = {"4": "SSD", "3": "HDD", "5": "SCM"}.get(media, media or "-")
        if media_text in ("Unspecified", ""):
            media_text = "-"
        health = str(d.get("HealthStatus") or "")
        health = {"0": "Healthy", "1": "Warning", "2": "Unhealthy"}.get(health, health or "-")
        status = d.get("OperationalStatus")
        if isinstance(status, list):
            status = ",".join(str(s) for s in status)
        bus = str(d.get("BusType") or "")
        bus = BUS_TYPES.get(_num(bus), bus or "-")
        try:
            size = int(d.get("Size") or 0)
        except (TypeError, ValueError):
            size = 0
        out.append(PhysicalDisk(
            model=str(d.get("FriendlyName") or "-"),
            media=media_text, health=health, status=str(status or "-"),
            size=size, bus=bus))
    return out


def _num(s: str) -> int:
    try:
        return int(s)
    except (TypeError, ValueError):
        return -1


# ------------------------------------------------------------------ 优化动作
@dataclass
class Action:
    key: str
    name: str
    desc: str
    cmd: list[str]
    need_admin: bool = True
    danger: str = "低"


def build_action(volume: Volume, kind: str) -> Action | None:
    """按卷与动作类型组装命令行。medium 感知：SSD 上不给「碎片整理」。"""
    letter = volume.letter
    if kind == "optimize":
        return Action(
            "optimize", "按介质自动优化",
            "SSD 执行 TRIM 重删，HDD 执行碎片整理（系统 defrag /O）",
            ["defrag.exe", letter, "/O", "/U", "/V"])
    if kind == "retrim":
        if volume.is_ssd is False:
            return None
        return Action(
            "retrim", "TRIM 全盘回收",
            "通知 SSD 控制器回收无效块，恢复写入性能（defrag /L）",
            ["defrag.exe", letter, "/L", "/U", "/V"])
    if kind == "analyze":
        return Action(
            "analyze", "分析碎片情况",
            "只读分析，不改动任何数据（defrag /A）",
            ["defrag.exe", letter, "/A", "/V"], need_admin=False)
    if kind == "chkdsk":
        return Action(
            "chkdsk", "扫描磁盘错误",
            "在线只读扫描文件系统，不锁定卷、可随时中断（chkdsk /scan）",
            ["chkdsk.exe", letter, "/scan"])
    return None


def run_action(action: Action, on_line=None, stop_flag=None) -> tuple[int, list[str]]:
    """流式执行命令，逐行回调。返回 (退出码, 输出行列表)。"""
    lines: list[str] = []
    if not IS_WIN:
        return (-1, lines)
    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    try:
        p = subprocess.Popen(
            action.cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL, creationflags=CREATE_NO_WINDOW,
            startupinfo=si, bufsize=0)
    except Exception as exc:        # noqa: BLE001
        msg = f"启动失败：{exc}"
        lines.append(msg)
        if on_line:
            on_line(msg)
        return (-1, lines)
    try:
        assert p.stdout is not None
        for raw in iter(p.stdout.readline, b""):
            if stop_flag and stop_flag[0]:
                try:
                    p.terminate()
                except Exception:   # noqa: BLE001
                    pass
                break
            text = _decode(raw).rstrip("\r\n")
            if not text:
                continue
            lines.append(text)
            if on_line:
                on_line(text)
        p.wait(timeout=10)
    except Exception:       # noqa: BLE001
        try:
            p.kill()
        except Exception:   # noqa: BLE001
            pass
    return (p.returncode if p.returncode is not None else -1, lines)


_PROGRESS_HINTS = ("碎片整理", "完成", "进度", "优化", "complete", "progress",
                   "defragment", "optimiz")


def parse_defrag_percent(line: str) -> int | None:
    """从 defrag 输出里抽进度百分比（中文/英文界面都可）。

    只认"已完成 N% 的碎片整理"这类进度行；分析报告里的
    "总碎片空间 = 18%" 不能被当成进度，否则进度条会乱跳。
    """
    low = line.lower()
    if not any(h in low for h in _PROGRESS_HINTS):
        return None
    m = re.search(r"(\d{1,3})\s*%", line)
    if m:
        try:
            return max(0, min(100, int(m.group(1))))
        except ValueError:
            return None
    return None
