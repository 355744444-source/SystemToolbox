# -*- coding: utf-8 -*-
"""Win32 最小公共层：只放「多个模块都要、且容易写错」的那几件。

## 为什么要有这个文件

### 一、`is_admin`
`boot.py` / `cleaner.py` / `component.py` 各写了一份**逐字相同**的实现。
不是大问题，但「是否管理员」是本工具里大量结论的前提（读不到服务、
读不到开机记录、能不能改注册表），三份实现意味着哪天判定口径要改
（比如再加上 UAC 虚拟化检测），得记得改三处。

### 二、`proc_image_path(pid)` / `image_path_from_handle(h)`
`locks.py` / `netdiag.py` / `procs.py` 各写了一份 PID → 可执行文件路径。
其中 netdiag 那份**每次调用都 `ctypes.WinDLL("kernel32")` 新建一次**
（一次网络诊断刷新要调几百次），而且只声明了 argtypes，`CloseHandle`
的 restype 没声明。procs 那份则完全没有 argtypes 校验。

### 三、句柄类型必须是 `wintypes.HANDLE`
`OpenProcess` / `CreateFileW` / `CreateToolhelp32Snapshot` 的返回值默认被
ctypes 当成 `c_int`（32 位有符号）—— 64 位句柄一旦高位为 1 就变成负数，
传给后续 API 会让**整个进程崩掉**，而不是返回错误。本项目踩过两次
（`disk._k32` 的 CreateFileW、`services` 的 OpenSCManagerW）。
集中在这里配一次，别处只管调用。
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import os

IS_WIN = os.name == "nt"

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
ERROR_INSUFFICIENT_BUFFER = 122
TH32CS_SNAPPROCESS = 0x00000002
MAX_PATH = 260

#: `QueryFullProcessImageNameW` 的初始缓冲。先试小的：绝大多数路径不到 260 字符，
#: 一次 1 KB 的栈分配比直接要 32 KB 便宜；真不够时靠 ERROR_INSUFFICIENT_BUFFER 重试。
_BUF_SMALL = 1024
_BUF_LARGE = 32768


def is_admin() -> bool:
    """当前进程是否已提权。

    注意：返回 False 有两种含义 —— 「确实是普通用户」和「查询本身失败」。
    调用方据此给出的结论必须写成「需要管理员才能确认」，不能写成「没问题」。
    """
    if not IS_WIN:
        return False
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:                        # noqa: BLE001
        return False


_k32_cache = None


def k32():
    """配置好 argtypes/restype 的 kernel32（进程内只配置一次）。"""
    global _k32_cache
    if _k32_cache is not None:
        return _k32_cache
    lib = ctypes.WinDLL("kernel32", use_last_error=True)
    lib.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
    lib.OpenProcess.restype = wt.HANDLE
    lib.CloseHandle.argtypes = [wt.HANDLE]
    lib.CloseHandle.restype = wt.BOOL
    lib.QueryFullProcessImageNameW.argtypes = [wt.HANDLE, wt.DWORD, wt.LPWSTR,
                                               ctypes.POINTER(wt.DWORD)]
    lib.QueryFullProcessImageNameW.restype = wt.BOOL
    lib.ProcessIdToSessionId.argtypes = [wt.DWORD, ctypes.POINTER(wt.DWORD)]
    lib.ProcessIdToSessionId.restype = wt.BOOL
    lib.CreateToolhelp32Snapshot.argtypes = [wt.DWORD, wt.DWORD]
    lib.CreateToolhelp32Snapshot.restype = wt.HANDLE
    lib.Process32FirstW.argtypes = [wt.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
    lib.Process32FirstW.restype = wt.BOOL
    lib.Process32NextW.argtypes = [wt.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
    lib.Process32NextW.restype = wt.BOOL
    _k32_cache = lib
    return lib


def image_path_from_handle(handle) -> str:
    """已打开的进程句柄 → 镜像完整路径。拿不到（受保护进程）返回空串。"""
    if not handle:
        return ""
    lib = k32()
    for size in (_BUF_SMALL, _BUF_LARGE):
        buf = ctypes.create_unicode_buffer(size)
        used = wt.DWORD(size)
        if lib.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(used)):
            return buf.value
        # 只有「缓冲不够」值得重试；其它错误（拒绝访问、进程已退出）直接收手
        if ctypes.get_last_error() != ERROR_INSUFFICIENT_BUFFER:
            return ""
    return ""


def proc_image_path(pid: int) -> str:
    """PID → 镜像完整路径。

    `PROCESS_QUERY_LIMITED_INFORMATION` 是唯一一种**普通用户也能问同用户进程**
    的权限；用 `PROCESS_QUERY_INFORMATION` 在未提权时会一律失败，
    于是「查不到」被误读成「这个 PID 没有程序」。
    """
    if not IS_WIN or not pid or pid < 0:
        return ""
    lib = k32()
    h = lib.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
    if not h:
        return ""
    try:
        return image_path_from_handle(h)
    finally:
        lib.CloseHandle(h)


def proc_name(pid: int) -> str:
    """PID → 进程名（不含路径）。"""
    p = proc_image_path(pid)
    return p.rsplit("\\", 1)[-1] if p else ""


def session_of(pid: int) -> str:
    """PID → 所在会话 ID 文本（`tasklist` 的「会话#」列同源）。

    拿不到返回空串 —— 界面显示「—」，不要瞎填 0（0 是「服务会话」，含义完全不同）。
    """
    if not IS_WIN or not pid or pid < 0:
        return ""
    lib = k32()
    sid = wt.DWORD(0)
    try:
        if lib.ProcessIdToSessionId(int(pid), ctypes.byref(sid)):
            return str(sid.value)
    except Exception:                        # noqa: BLE001
        pass
    return ""


class PROCESSENTRY32W(ctypes.Structure):
    """Toolhelp 进程枚举条目（`procs` 与 `process_name_map` 共用一份）。"""

    _fields_ = [("dwSize", wt.DWORD), ("cntUsage", wt.DWORD),
                ("th32ProcessID", wt.DWORD),
                ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
                ("th32ModuleID", wt.DWORD), ("cntThreads", wt.DWORD),
                ("th32ParentProcessID", wt.DWORD),
                ("pcPriClassBase", ctypes.c_long), ("dwFlags", wt.DWORD),
                ("szExeFile", wt.WCHAR * MAX_PATH)]


def process_name_map() -> dict[int, str]:
    """pid → 进程名（整表一次枚举）。

    用 Toolhelp 而不是 `tasklist`：

    - Toolhelp **不需要打开目标进程**，所以非管理员也能拿到 Services 会话里的
      `svchost.exe`、`System`；而 `OpenProcess` 问不到它们（这正是原来
      `netdiag._proc_map` 用 tasklist 的原因）。
    - `tasklist` 是起一个控制台子进程（200~400 ms），返回**本地化 CSV**；
      内存列带千分位逗号时整行会挤错位。本机实测 Toolhelp 一轮 190 个进程 5 ms。

    枚举失败返回空字典（调用方显示「—」，不能说成「没有进程」）。
    """
    if not IS_WIN:
        return {}
    lib = k32()
    out: dict[int, str] = {}
    snap = lib.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if not snap or snap == ctypes.c_void_p(-1).value:
        return {}
    try:
        pe = PROCESSENTRY32W()
        pe.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        ok = lib.Process32FirstW(snap, ctypes.byref(pe))
        while ok:
            out[int(pe.th32ProcessID)] = pe.szExeFile
            ok = lib.Process32NextW(snap, ctypes.byref(pe))
    except Exception:                        # noqa: BLE001
        pass
    finally:
        lib.CloseHandle(snap)
    return out
