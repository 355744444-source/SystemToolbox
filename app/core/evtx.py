# -*- coding: utf-8 -*-
"""EVTX 事件日志读取引擎。

走 Windows 原生 wevtapi.dll（EvtQuery / EvtNext / EvtRender），
因此同时支持：
  * 实时通道（System / Application / Security / 任意通道）
  * 离线 .evtx 文件（含别人拷回来的现场日志）

不依赖 pywin32、不依赖 PowerShell，单文件 exe 里没有外部进程调用。
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import os
import threading
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Iterable, Optional

# ---------------------------------------------------------------- Win32 常量
EvtQueryChannelPath = 0x1
EvtQueryFilePath = 0x2
EvtQueryReverseDirection = 0x200
EvtQueryTolerateQueryErrors = 0x1000

EvtRenderEventXml = 1
EvtFormatMessageEvent = 1

ERROR_INSUFFICIENT_BUFFER = 122
ERROR_NO_MORE_ITEMS = 259
ERROR_ACCESS_DENIED = 5
ERROR_EVT_CHANNEL_NOT_FOUND = 15007
ERROR_EVT_QUERY_RESULT_STALE = 15011

EVT_HANDLE = ctypes.c_void_p

_wevt = ctypes.WinDLL("wevtapi.dll", use_last_error=True)

_wevt.EvtQuery.argtypes = [EVT_HANDLE, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD]
_wevt.EvtQuery.restype = EVT_HANDLE
_wevt.EvtNext.argtypes = [EVT_HANDLE, wt.DWORD, ctypes.POINTER(EVT_HANDLE),
                          wt.DWORD, wt.DWORD, ctypes.POINTER(wt.DWORD)]
_wevt.EvtNext.restype = wt.BOOL
_wevt.EvtRender.argtypes = [EVT_HANDLE, EVT_HANDLE, wt.DWORD, wt.DWORD,
                            ctypes.c_void_p, ctypes.POINTER(wt.DWORD),
                            ctypes.POINTER(wt.DWORD)]
_wevt.EvtRender.restype = wt.BOOL
_wevt.EvtClose.argtypes = [EVT_HANDLE]
_wevt.EvtClose.restype = wt.BOOL
_wevt.EvtOpenPublisherMetadata.argtypes = [EVT_HANDLE, wt.LPCWSTR, wt.LPCWSTR,
                                           wt.DWORD, wt.DWORD]
_wevt.EvtOpenPublisherMetadata.restype = EVT_HANDLE
_wevt.EvtFormatMessage.argtypes = [EVT_HANDLE, EVT_HANDLE, wt.DWORD, wt.DWORD,
                                   ctypes.POINTER(wt.LPCWSTR), wt.DWORD,
                                   wt.DWORD, ctypes.c_void_p,
                                   ctypes.POINTER(wt.DWORD)]
_wevt.EvtFormatMessage.restype = wt.BOOL

LEVEL_NAMES = {1: "严重", 2: "错误", 3: "警告", 4: "信息", 5: "详细", 0: "日志始终"}

# 常用通道：显示名 -> 通道名
COMMON_CHANNELS: list[tuple[str, str]] = [
    ("系统 (System)", "System"),
    ("应用程序 (Application)", "Application"),
    ("安全 (Security)", "Security"),
    ("设置 (Setup)", "Setup"),
    ("转发事件 (ForwardedEvents)", "ForwardedEvents"),
    ("蓝色屏幕 (Microsoft-Windows-Kernel-Power)", "Microsoft-Windows-Kernel-Power/Admin"),
    ("磁盘诊断 (StorPort)", "Microsoft-Windows-StorPort/Operational"),
    ("WHEA 硬件错误", "Microsoft-Windows-Kernel-WHEA/Operational"),
    ("驱动框架 (DriverFrameworks)", "Microsoft-Windows-DriverFrameworks-UserMode/Operational"),
    ("应用程序崩溃 (AppCrash)", "Application"),
    ("任务计划", "Microsoft-Windows-TaskScheduler/Operational"),
    ("WLAN 无线", "Microsoft-Windows-WLAN-AutoConfig/Operational"),
    ("终端服务", "Microsoft-Windows-TerminalServices-LocalSessionManager/Operational"),
    ("Sysmon", "Microsoft-Windows-Sysmon/Operational"),
]


class EvtxError(RuntimeError):
    pass


@dataclass
class Event:
    time: Optional[datetime]
    channel: str
    provider: str
    event_id: int
    level: int
    task: int
    opcode: int
    record_id: int
    computer: str
    user: str
    process_id: int
    thread_id: int
    data: dict = field(default_factory=dict)
    message: str = ""
    raw_xml: str = ""

    @property
    def level_name(self) -> str:
        return LEVEL_NAMES.get(self.level, "未知")

    @property
    def time_text(self) -> str:
        return self.time.strftime("%Y-%m-%d %H:%M:%S") if self.time else "-"

    @property
    def data_text(self) -> str:
        if not self.data:
            return ""
        return "  ".join(f"{k}={v}" for k, v in list(self.data.items())[:6] if v not in ("", None))

    @property
    def summary(self) -> str:
        if self.message:
            return self.message.replace("\n", " ").replace("\r", " ").strip()
        return self.data_text


@dataclass
class ReadStats:
    total: int = 0
    source: str = ""
    elapsed: float = 0.0
    truncated: bool = False
    error: str = ""
    per_channel: dict = field(default_factory=dict)


def _parse_time(text: Optional[str]) -> Optional[datetime]:
    if not text:
        return None
    try:
        s = text.replace("Z", "+00:00")
        if "." in s:
            head, tail = s.split(".", 1)
            frac = ""
            rest = ""
            for i, ch in enumerate(tail):
                if ch.isdigit():
                    frac += ch
                else:
                    rest = tail[i:]
                    break
            s = f"{head}.{frac[:6].ljust(6, '0')}{rest}"
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is not None:
            dt = dt.astimezone().replace(tzinfo=None)
        return dt
    except Exception:
        return None


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def parse_event_xml(xml: str, channel_hint: str = "") -> Optional[Event]:
    """把单条事件 XML 解析成 Event。"""
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return None

    system = None
    data: dict = {}
    msg = ""
    for child in root:
        name = _local(child.tag)
        if name == "System":
            system = child
        elif name == "EventData":
            for item in child:
                key = item.get("Name") or f"#{len(data)}"
                data[key] = (item.text or "").strip()
        elif name == "UserData":
            for item in child:
                for sub in item:
                    key = _local(sub.tag)
                    data[key] = (sub.text or "").strip()

    if system is None:
        return None

    provider, event_id, level, task, opcode = "", 0, 4, 0, 0
    when, rec_id, computer, user, pid, tid, channel = None, 0, "", "", 0, 0, channel_hint
    for node in system:
        tag = _local(node.tag)
        if tag == "Provider":
            provider = node.get("Name") or ""
        elif tag == "EventID":
            try:
                event_id = int((node.text or "0").strip())
            except ValueError:
                event_id = 0
        elif tag == "Level":
            try:
                level = int((node.text or "4").strip())
            except ValueError:
                level = 4
        elif tag in ("Task", "Opcode", "EventRecordID"):
            # 这三个字段一直是裸 int()：`<Task>  </Task>`（只有空白）时
            # `or 0` 不生效，int("  ") 抛 ValueError，一条脏事件就能让整次
            # 读取崩掉 —— 而「读别人拷回来的现场 evtx」正是本工具的主场景。
            try:
                value = int((node.text or "0").strip() or 0)
            except (TypeError, ValueError):
                value = 0
            if tag == "Task":
                task = value
            elif tag == "Opcode":
                opcode = value
            else:
                rec_id = value
        elif tag == "TimeCreated":
            when = _parse_time(node.get("SystemTime"))
        elif tag == "Computer":
            computer = (node.text or "").strip()
        elif tag == "Channel":
            channel = (node.text or "").strip() or channel
        elif tag == "Execution":
            pid = int(node.get("ProcessID") or 0)
            tid = int(node.get("ThreadID") or 0)
        elif tag == "Security":
            user = node.get("UserID") or ""

    if not msg and data:
        msg = "  ".join(f"{k}={v}" for k, v in list(data.items())[:6] if v)

    return Event(time=when, channel=channel, provider=provider, event_id=event_id,
                 level=level, task=task, opcode=opcode, record_id=rec_id,
                 computer=computer, user=user, process_id=pid, thread_id=tid,
                 data=data, message=msg, raw_xml=xml)


def build_xpath(start: Optional[datetime] = None,
                end: Optional[datetime] = None,
                max_level: Optional[int] = None,
                event_ids: Optional[Iterable[int]] = None,
                providers: Optional[Iterable[str]] = None) -> str:
    """拼 XPath 1.0 查询。不传条件则为 '*'。"""
    conds: list[str] = []
    if max_level is not None:
        conds.append(f"Level<={int(max_level)}")
    if start is not None:
        conds.append(f"TimeCreated[@SystemTime>='{start.strftime('%Y-%m-%dT%H:%M:%S')}.000Z']")
    if end is not None:
        conds.append(f"TimeCreated[@SystemTime<='{end.strftime('%Y-%m-%dT%H:%M:%S')}.999Z']")
    if event_ids:
        ids = ",".join(str(int(i)) for i in event_ids)
        conds.append(f"EventID={ids}" if len(conds) == 0 else f"EventID={ids}")
    if providers:
        suffix = "".join(f" and Provider[@Name='{p}']" for p in providers)
        conds.append(f"Provider[@Name='{providers[0]}']" if not suffix else f"True{suffix}")
    if not conds:
        return "*"
    return "*[System[" + " and ".join(conds) + "]]"


def _render_xml(handle) -> str:
    used = wt.DWORD(0)
    props = wt.DWORD(0)
    ok = _wevt.EvtRender(None, handle, EvtRenderEventXml, 0, None,
                         ctypes.byref(used), ctypes.byref(props))
    if not ok:
        err = ctypes.get_last_error()
        if err != ERROR_INSUFFICIENT_BUFFER:
            return ""
    size = max(used.value, 4096)
    buf = ctypes.create_string_buffer(size)
    ok = _wevt.EvtRender(None, handle, EvtRenderEventXml, size, buf,
                         ctypes.byref(used), ctypes.byref(props))
    if not ok:
        return ""
    raw = buf.raw[:used.value]
    try:
        return raw.decode("utf-16-le").rstrip("\x00")
    except UnicodeDecodeError:
        return ""


class _MessageFormatter:
    """按 Provider 缓存 PublisherMetadata，取本地化消息文本。

    真实日志里同一条消息会重复上万次（比如坏块事件），
    所以再按 (provider, id, 数据) 缓存结果，避免重复调用 EvtFormatMessage。
    """

    def __init__(self) -> None:
        self._cache: dict[str, object] = {}
        self._dead: set = set()
        self._msgs: dict[tuple, str] = {}

    def close(self) -> None:
        for h in self._cache.values():
            try:
                _wevt.EvtClose(h)
            except Exception:
                pass
        self._cache.clear()
        self._msgs.clear()

    def format(self, provider: str, event_handle, key: tuple = ()) -> str:
        if key and key in self._msgs:
            return self._msgs[key]
        text = self._format_uncached(provider, event_handle)
        if key and text and len(self._msgs) < 8000:
            self._msgs[key] = text
        return text

    def _format_uncached(self, provider: str, event_handle) -> str:
        if not provider or provider in self._dead:
            return ""
        meta = self._cache.get(provider)
        if meta is None:
            meta = _wevt.EvtOpenPublisherMetadata(None, provider, None, 0, 0)
            if not meta:
                self._dead.add(provider)
                return ""
            self._cache[provider] = meta
        used = wt.DWORD(0)
        ok = _wevt.EvtFormatMessage(meta, event_handle, 0, 0, None,
                                    EvtFormatMessageEvent, 0, None, ctypes.byref(used))
        if not ok:
            err = ctypes.get_last_error()
            if err != ERROR_INSUFFICIENT_BUFFER:
                return ""
        size = max(used.value, 512)
        buf = ctypes.create_unicode_buffer(size + 2)
        ok = _wevt.EvtFormatMessage(meta, event_handle, 0, 0, None,
                                    EvtFormatMessageEvent, size, buf,
                                    ctypes.byref(used))
        if not ok:
            return ""
        return buf.value.strip()


# 这些 ID 即便级别是「信息」也要取本地化消息（蓝屏/开关机/时间同步等）
FORMAT_ALWAYS_IDS = frozenset({
    41, 1001, 6005, 6006, 6008, 6009, 6013, 1074, 1076, 12, 13, 20, 27, 98, 129,
    134, 153, 219, 1000, 1002, 7031, 7034, 7045, 7040, 7000, 7009, 7011, 1102, 104,
    17, 18, 19, 46, 47, 1, 2, 3, 4, 5, 6, 7, 11, 51, 55, 10016, 1026, 4625, 4740,
})


def query_events(path: str,
                 is_file: bool,
                 xpath: str = "*",
                 limit: int = 50000,
                 progress: Optional[Callable[[int], None]] = None,
                 cancel: Optional[threading.Event] = None,
                 message_mode: str = "auto") -> tuple[list[Event], ReadStats]:
    """读取通道或 .evtx 文件。返回 (事件列表, 统计)。

    message_mode: "auto" 只对告警级以上 + 崩溃相关 ID 取本地化消息（快）；
                  "all" 全部取（慢约 3.5 倍）；"none" 只拼数据摘要（最快）。
    """
    if isinstance(message_mode, bool):  # 兼容旧签名
        message_mode = "all" if message_mode else "none"
    flags = EvtQueryFilePath if is_file else EvtQueryChannelPath
    flags |= EvtQueryReverseDirection
    if not is_file:
        flags |= EvtQueryTolerateQueryErrors

    handle = _wevt.EvtQuery(None, path, xpath, flags)
    if not handle:
        err = ctypes.get_last_error()
        if err == ERROR_ACCESS_DENIED:
            raise EvtxError("访问被拒绝：读取该通道需要管理员权限（右键 → 以管理员身份运行）")
        if err in (ERROR_EVT_CHANNEL_NOT_FOUND, 2):
            raise EvtxError(f"通道或文件不存在：{path}")
        raise EvtxError(f"打开查询失败（错误码 {err}）：{path}")

    import time as _time
    _t0 = _time.time()
    batch = 32
    events: list[Event] = []
    stats = ReadStats(source=path)
    formatter = _MessageFormatter() if message_mode != "none" else None
    handles = (EVT_HANDLE * batch)()
    try:
        while True:
            if cancel is not None and cancel.is_set():
                stats.error = "已取消"
                break
            returned = wt.DWORD(0)
            ok = _wevt.EvtNext(handle, batch, handles, 2000, 0, ctypes.byref(returned))
            if not ok:
                err = ctypes.get_last_error()
                if err in (ERROR_NO_MORE_ITEMS, 0):
                    break
                if err == ERROR_EVT_QUERY_RESULT_STALE:
                    continue
                stats.error = f"读取事件失败（错误码 {err}）"
                break
            for i in range(returned.value):
                h = handles[i]
                try:
                    xml = _render_xml(h)
                    if not xml:
                        continue
                    ev = parse_event_xml(xml, "" if is_file else path)
                    if ev is None:
                        continue
                    if formatter is not None and (message_mode == "all"
                                                   or ev.level <= 3
                                                   or ev.event_id in FORMAT_ALWAYS_IDS):
                        try:
                            ckey = (ev.provider, ev.event_id,
                                    tuple(sorted(ev.data.items())[:8]))
                            ev.message = formatter.format(ev.provider, h, ckey)
                        except Exception:
                            ev.message = ""
                        if not ev.message:
                            ev.message = ev.data_text
                    else:
                        ev.message = ev.data_text
                    ev.raw_xml = xml
                    events.append(ev)
                    stats.per_channel[ev.channel] = stats.per_channel.get(ev.channel, 0) + 1
                finally:
                    _wevt.EvtClose(h)
                if len(events) % 200 == 0 and progress is not None:
                    progress(len(events))
                if len(events) >= limit:
                    stats.truncated = True
                    # 这一批是一次性取出来的（最多 32 个句柄），提前跳出时
                    # 后面那些句柄没人接管 —— 释放 EvtQuery 的结果集句柄
                    # **不会**连带关掉它们。不补这一段，每次 limit 截断就漏
                    # 最多 31 个内核句柄，反复读大日志会一路涨。
                    for j in range(i + 1, returned.value):
                        _wevt.EvtClose(handles[j])
                    break
            if stats.truncated:
                break
    finally:
        if formatter is not None:
            formatter.close()
        _wevt.EvtClose(handle)

    stats.total = len(events)
    stats.elapsed = _time.time() - _t0
    return events, stats


def enumerate_channels() -> list[str]:
    """枚举本机所有事件通道名。"""
    EvtOpenChannelEnum = _wevt.EvtOpenChannelEnum
    EvtOpenChannelEnum.argtypes = [EVT_HANDLE, wt.DWORD]
    EvtOpenChannelEnum.restype = EVT_HANDLE
    EvtNextChannelPath = _wevt.EvtNextChannelPath
    EvtNextChannelPath.argtypes = [EVT_HANDLE, wt.DWORD, wt.LPWSTR, ctypes.POINTER(wt.DWORD)]
    EvtNextChannelPath.restype = wt.BOOL

    result: list[str] = []
    h = EvtOpenChannelEnum(None, 0)
    if not h:
        return result
    buf = ctypes.create_unicode_buffer(512)
    used = wt.DWORD(0)
    try:
        while EvtNextChannelPath(h, 512, buf, ctypes.byref(used)):
            result.append(buf.value)
    finally:
        _wevt.EvtClose(h)
    return result


def default_log_dirs() -> list[str]:
    return [p for p in (os.environ.get("SystemRoot", r"C:\Windows") + r"\System32\winevt\Logs",)
            if os.path.isdir(p)]
