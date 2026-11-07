# -*- coding: utf-8 -*-
"""网络诊断：本机网络概况 + 连通性 + DNS + 端口 + 代理 + hosts。

**全只读**：不修网络、不改注册表、不重置 Winsock。

为什么不用 `ping.exe` / `nslookup` / `netstat`：

- 这些命令在受管控的机器上可能被安全策略拦（本项目实测 `schtasks.exe` 被拦过）
- 它们的输出是本地化文本，中文系统上解析极易出错

所以这里全部走 ctypes + socket：

| 要拿什么 | 用什么 |
|---|---|
| IP / 掩码 / 网关 / DNS / DHCP | 注册表 `Tcpip\\Parameters\\Interfaces\\{GUID}` |
| 网卡描述 / 速率 / 介质 / 链路状态 | `GetIfTable`（MIB_IFROW） |
| ICMP | `IcmpCreateFile` / `IcmpSendEcho` |
| DNS 解析耗时 | `socket.getaddrinfo` |
| TCP 连通 | `socket.create_connection` |
| 监听端口 → 进程 | `GetExtendedTcpTable` + `QueryFullProcessImageNameW` |
"""
from __future__ import annotations

import ctypes
import csv
import io
import os
import socket
import struct
import subprocess
import time
from ctypes import wintypes
from dataclasses import dataclass, field

IS_WIN = os.name == "nt"
CREATE_NO_WINDOW = 0x08000000

HKLM = 0x80000002
TCPIP_IF = r"SYSTEM\CurrentControlSet\Services\Tcpip\Parameters\Interfaces"
NETCARDS = r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\NetworkCards"
INET_SETTINGS = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"

# 介质类型（IANA ifType 的常用子集）
IF_TYPE = {
    1: "其它", 6: "以太网", 23: "PPP", 24: "环回", 71: "无线 802.11",
    131: "隧道", 243: "WWAN", 244: "蓝牙",
}

# 外网探测目标：阿里 DNS / 腾讯 DNS / Google DNS，前两个国内一定可达
PUBLIC_TARGETS = [("223.5.5.5", "阿里 DNS"), ("119.29.29.29", "腾讯 DNS"),
                  ("8.8.8.8", "Google DNS")]

# hosts 里出现这些域名却指向非本机地址，基本可以确定被改过
WATCH_DOMAINS = ("microsoft.com", "windowsupdate.com", "windows.com",
                 "baidu.com", "qq.com", "taobao.com", "alipay.com",
                 "kaspersky", "360.cn", "safebox")


@dataclass
class Adapter:
    name: str = ""
    desc: str = ""
    ip: str = ""
    mask: str = ""
    gateway: str = ""
    dns: str = ""
    dhcp: bool = False
    speed: int = 0                 # bps，0 表示未知
    medium: str = ""
    up: bool = True
    is_virtual: bool = False

    @property
    def title(self) -> str:
        return self.desc or self.name

    @property
    def speed_text(self) -> str:
        if not self.speed:
            return "—"
        if self.speed >= 1_000_000_000:
            return f"{self.speed / 1e9:.1f} Gbps"
        if self.speed >= 1_000_000:
            return f"{self.speed / 1e6:.0f} Mbps"
        return f"{self.speed / 1e3:.0f} Kbps"

    @property
    def link_text(self) -> str:
        return "已连接" if self.up else "已断开"

    @property
    def detail_text(self) -> str:
        bits = [self.medium or "未知介质", self.speed_text, self.link_text,
                "DHCP" if self.dhcp else "静态"]
        return " · ".join(bits)


@dataclass
class Check:
    """一条检查结论。ok=None 表示这只是信息，不判好坏。"""
    name: str
    value: str
    ok: bool | None = None
    hint: str = ""
    metric: float = 0.0            # 可量化时填（毫秒），供界面做 KPI


@dataclass
class Listener:
    proto: str
    addr: str
    port: int
    pid: int = 0
    proc: str = ""


@dataclass
class Report:
    adapters: list[Adapter] = field(default_factory=list)
    checks: list[Check] = field(default_factory=list)
    listeners: list[Listener] = field(default_factory=list)
    hosts_lines: int = 0
    hosts_entries: list[tuple[str, str]] = field(default_factory=list)
    hosts_warning: str = ""
    hosts_level: str = ""
    proxy_enabled: bool = False
    proxy_server: str = ""
    proxy_pac: str = ""
    elapsed: float = 0.0

    @property
    def problems(self) -> list[Check]:
        return [c for c in self.checks if c.ok is False]


# ------------------------------------------------------------------ 注册表
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


def _first(val) -> str:
    """注册表里 IPAddress / DefaultGateway 是字符串多值，取第一个非空。"""
    if isinstance(val, (list, tuple)):
        for v in val:
            if v and str(v).strip():
                return str(v).strip()
        return ""
    return str(val or "").strip()


# ------------------------------------------------------------------ 网卡
class _MIB_IFROW(ctypes.Structure):
    _fields_ = [
        ("wszName", wintypes.WCHAR * 256),
        ("dwIndex", wintypes.DWORD),
        ("dwType", wintypes.DWORD),
        ("dwMtu", wintypes.DWORD),
        ("dwSpeed", wintypes.DWORD),
        ("dwPhysAddrLen", wintypes.DWORD),
        ("bPhysAddr", ctypes.c_byte * 8),
        ("dwAdminStatus", wintypes.DWORD),
        ("dwOperStatus", wintypes.DWORD),
        ("dwLastChange", wintypes.DWORD),
        ("dwInOctets", wintypes.DWORD),
        ("dwInUcastPkts", wintypes.DWORD),
        ("dwInNUcastPkts", wintypes.DWORD),
        ("dwInDiscards", wintypes.DWORD),
        ("dwInErrors", wintypes.DWORD),
        ("dwInUnknownProtos", wintypes.DWORD),
        ("dwOutOctets", wintypes.DWORD),
        ("dwOutUcastPkts", wintypes.DWORD),
        ("dwOutNUcastPkts", wintypes.DWORD),
        ("dwOutDiscards", wintypes.DWORD),
        ("dwOutErrors", wintypes.DWORD),
        ("dwOutQLen", wintypes.DWORD),
        ("dwDescrLen", wintypes.DWORD),
        ("bDescr", ctypes.c_byte * 256),
    ]


class _MIB_IFTABLE(ctypes.Structure):
    _fields_ = [("dwNumEntries", wintypes.DWORD),
                ("table", _MIB_IFROW * 1)]


def _if_rows() -> list[dict]:
    """GetIfTable：拿到描述 / 速率 / 介质 / 链路状态。"""
    if not IS_WIN:
        return []
    try:
        iphlp = ctypes.WinDLL("iphlpapi", use_last_error=True)
        size = wintypes.ULONG(0)
        iphlp.GetIfTable(None, ctypes.byref(size), True)     # 先问大小
        if not size.value or size.value > 8 * 1024 * 1024:
            return []
        buf = ctypes.create_string_buffer(size.value)
        if iphlp.GetIfTable(buf, ctypes.byref(size), True) != 0:
            return []
        table = ctypes.cast(buf, ctypes.POINTER(_MIB_IFTABLE)).contents
        count = min(int(table.dwNumEntries), 256)
        rows = ctypes.cast(ctypes.byref(table.table),
                           ctypes.POINTER(_MIB_IFROW))
        out = []
        for i in range(count):
            r = rows[i]
            desc = bytes(bytearray((b & 0xFF) for b in r.bDescr))[:r.dwDescrLen]
            out.append({
                "index": int(r.dwIndex),
                "type": int(r.dwType),
                "speed": int(r.dwSpeed) & 0xFFFFFFFF,
                # INTERNAL_IF_OPER_STATUS：0 不可操作 / 1 不可达 / 2 断开 /
                # 3 正在连接 / 4 已连接 / 5 可运行。只有 >=4 才算真的通。
                "up": int(r.dwOperStatus) >= 4,
                "desc": desc.decode("mbcs", errors="ignore").strip("\x00 ").strip(),
            })
        return out
    except Exception:                        # noqa: BLE001
        return []


CONN_GUID = (r"SYSTEM\CurrentControlSet\Control\Network"
             r"\{4d36e972-e325-11ce-bfc1-08002be10318}")


def _conn_names() -> dict[str, str]:
    """GUID → 网络连接的友好名（「以太网 2」「vEthernet (WSL)」这种）。

    虚拟网卡在 `NetworkCards` 里没有 Description，只能从这里取名字，
    否则界面上只剩一串 GUID 前 8 位，等于没说。
    """
    out: dict[str, str] = {}
    for guid in _subkeys(HKLM, CONN_GUID):
        name = _reg(HKLM, CONN_GUID + "\\" + guid + "\\Connection", "Name")
        if name:
            out[guid.lower()] = str(name).strip()
    return out


VIRTUAL_HINTS = ("vethernet", "wsl", "hyper-v", "virtualbox", "vmware",
                 "vpn", "tap-", "tap adapter", "loopback", "teredo",
                 "isatap", "bluetooth", "npcap", "zerotier", "tailscale")


def _looks_virtual(text: str) -> bool:
    low = (text or "").lower()
    return any(h in low for h in VIRTUAL_HINTS)


def _card_descs() -> dict[str, str]:
    """GUID → 网卡描述（注册表 NetworkCards 里的 Description）。"""
    out: dict[str, str] = {}
    for name in _subkeys(HKLM, NETCARDS):
        base = NETCARDS + "\\" + name
        guid = str(_reg(HKLM, base, "ServiceName") or "").lower()
        desc = str(_reg(HKLM, base, "Description") or "").strip()
        if guid:
            out[guid] = desc
    return out


def adapters() -> list[Adapter]:
    """列出有 IPv4 地址的网卡（含虚拟网卡，靠 is_virtual 区分）。"""
    descs = _card_descs()
    conns = _conn_names()
    ifrows = _if_rows()
    out: list[Adapter] = []
    for guid in _subkeys(HKLM, TCPIP_IF):
        base = TCPIP_IF + "\\" + guid
        ip = _first(_reg(HKLM, base, "DhcpIPAddress")) or \
            _first(_reg(HKLM, base, "IPAddress"))
        if not ip or ip.startswith("0."):
            continue
        mask = _first(_reg(HKLM, base, "DhcpSubnetMask")) or \
            _first(_reg(HKLM, base, "SubnetMask"))
        gw = _first(_reg(HKLM, base, "DhcpDefaultGateway")) or \
            _first(_reg(HKLM, base, "DefaultGateway"))
        dns_raw = _reg(HKLM, base, "DhcpNameServer") or \
            _reg(HKLM, base, "NameServer") or ""
        dns = ", ".join(str(dns_raw).replace(",", " ").split())
        dhcp = bool(_reg(HKLM, base, "EnableDHCP"))
        desc = descs.get(guid.lower(), "")
        conn = conns.get(guid.lower(), "")
        a = Adapter(name=conn or guid[:8], desc=desc or conn,
                    ip=ip, mask=mask, gateway=gw, dns=dns, dhcp=dhcp)
        a.is_virtual = _looks_virtual(f"{desc} {conn}")
        # 用描述把 GetIfTable 的速率/介质/状态对上去。
        # 先精确相等，再退回「包含」，而且包含只认**唯一命中**：
        # 「以太网」是「以太网 2」的子串，不加这条就会把另一块网卡的
        # 速率/介质/连接状态套到当前这块上 —— 界面会出现「在用的网卡显示已断开」。
        # 宁可不填（下面会落成「未知」），也不要填错。
        for key in (desc, conn):
            if not key:
                continue
            k = key.lower()
            exact = [r for r in ifrows if (r["desc"] or "").lower() == k]
            cand = exact
            if not cand:
                loose = [r for r in ifrows
                         if (r["desc"] or "").lower()
                         and k in (r["desc"] or "").lower()]
                cand = loose if len(loose) == 1 else []
            if cand:
                row = cand[0]
                a.speed = row["speed"]
                a.medium = IF_TYPE.get(row["type"], f"类型 {row['type']}")
                a.up = row["up"]
                break
        if not a.medium:
            a.medium = "环回" if ip.startswith("127.") else "未知"
        out.append(a)
    # 主用物理网卡 → 其它物理 → 虚拟 → 环回
    out.sort(key=lambda x: (x.ip.startswith("127."), x.is_virtual,
                            not x.up, not x.gateway, x.ip))
    return out


def primary(items: list[Adapter] | None = None) -> Adapter | None:
    """默认路由所在的那块网卡：优先「物理 + 已连接 + 有网关」。

    传入 items 时在给定列表里选（界面已经扫过一次，不必再读一遍注册表，
    也让 `a is primary(...)` 这种同一性判断成立）。
    """
    src = items if items is not None else adapters()
    cands = [a for a in src
             if a.gateway and not a.ip.startswith(("127.", "169.254."))]
    for a in cands:
        if not a.is_virtual and a.up:
            return a
    for a in cands:
        if not a.is_virtual:
            return a
    return cands[0] if cands else None


# ------------------------------------------------------------------ ICMP
class _IP_OPTION_INFO(ctypes.Structure):
    _fields_ = [("Ttl", ctypes.c_ubyte), ("Tos", ctypes.c_ubyte),
                ("Flags", ctypes.c_ubyte), ("OptionsSize", ctypes.c_ubyte),
                ("OptionsData", ctypes.c_void_p)]


class _ICMP_ECHO_REPLY(ctypes.Structure):
    _fields_ = [("Address", wintypes.ULONG), ("Status", wintypes.ULONG),
                ("RoundTripTime", wintypes.ULONG), ("DataSize", wintypes.USHORT),
                ("Reserved", wintypes.USHORT), ("Data", ctypes.c_void_p),
                ("Options", _IP_OPTION_INFO)]


_icmp_dll = None


def _icmp():
    global _icmp_dll
    if _icmp_dll is None:
        # 必须 use_last_error：否则 IcmpSendEcho 失败后拿不到 11010 这类真实原因
        d = ctypes.WinDLL("iphlpapi", use_last_error=True)
        d.IcmpCreateFile.restype = wintypes.HANDLE
        d.IcmpSendEcho.argtypes = [wintypes.HANDLE, wintypes.ULONG,
                                   ctypes.c_char_p, wintypes.USHORT,
                                   ctypes.POINTER(_IP_OPTION_INFO),
                                   ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD]
        d.IcmpSendEcho.restype = wintypes.DWORD
        d.IcmpCloseHandle.argtypes = [wintypes.HANDLE]
        d.IcmpCloseHandle.restype = wintypes.BOOL
        _icmp_dll = d
    return _icmp_dll


_ICMP_ERR = {11001: "目标网络不可达", 11002: "目标网络不可达",
             11003: "目标地址不可达", 11004: "目标端口不可达",
             11005: "源地址不可用", 11006: "请求超时",
             11010: "请求超时（目标不响应 ICMP）", 11012: "IP 头错误",
             11013: "IP 选项错误", 11014: "缓冲区太小",
             11015: "ICMP 报文过大", 11016: "目标不支持该操作",
             11017: "参数错误", 11018: "请求与应答不匹配"}


def ping(host: str, timeout_ms: int = 1200) -> tuple[bool, float, int, str]:
    """ICMP 探测。返回 (通, 往返毫秒, TTL, 错误说明)。"""
    if not IS_WIN:
        return (False, 0.0, 0, "非 Windows")
    try:
        addr = socket.gethostbyname(host)
        packed = struct.unpack("!I", socket.inet_aton(addr))[0]
    except OSError as exc:
        return (False, 0.0, 0, f"域名解析失败：{exc}")
    try:
        dll = _icmp()
        h = dll.IcmpCreateFile()
        if not h or h == wintypes.HANDLE(-1).value:
            return (False, 0.0, 0, "无法创建 ICMP 句柄（权限不足）")
        try:
            payload = b"SystemToolbox-probe" * 3
            opt = _IP_OPTION_INFO(128, 0, 0, 0, None)
            reply_size = ctypes.sizeof(_ICMP_ECHO_REPLY) + len(payload) + 32
            buf = ctypes.create_string_buffer(reply_size)
            n = dll.IcmpSendEcho(h, packed, payload, len(payload),
                                 ctypes.byref(opt),
                                 ctypes.cast(buf, ctypes.c_void_p),
                                 reply_size, timeout_ms)
            if n < 1:
                err = ctypes.get_last_error()
                return (False, 0.0, 0,
                        _ICMP_ERR.get(err, f"无应答（错误码 {err}）"))
            rep = ctypes.cast(buf, ctypes.POINTER(_ICMP_ECHO_REPLY)).contents
            if rep.Status != 0:
                return (False, 0.0, 0, _ICMP_ERR.get(int(rep.Status),
                                                     f"ICMP 状态 {rep.Status}"))
            return (True, float(rep.RoundTripTime), int(rep.Options.Ttl), "")
        finally:
            dll.IcmpCloseHandle(h)
    except Exception as exc:                 # noqa: BLE001
        return (False, 0.0, 0, f"探测异常：{exc}")


# ------------------------------------------------------------------ DNS / TCP
def resolve(host: str) -> tuple[bool, list[str], float, str]:
    """解析域名并报告耗时。"""
    t0 = time.perf_counter()
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError as exc:
        return (False, [], (time.perf_counter() - t0) * 1000, str(exc))
    ms = (time.perf_counter() - t0) * 1000
    ips: list[str] = []
    for info in infos:
        ip = info[4][0]
        if ip not in ips:
            ips.append(ip)
    return (True, ips, ms, "")


def tcp_check(host: str, port: int, timeout: float = 1.5) -> tuple[bool, float, str]:
    t0 = time.perf_counter()
    try:
        s = socket.create_connection((host, port), timeout=timeout)
    except OSError as exc:
        return (False, (time.perf_counter() - t0) * 1000, str(exc))
    s.close()
    return (True, (time.perf_counter() - t0) * 1000, "")


# ------------------------------------------------------------------ 端口
class _MIB_TCPROW_PID(ctypes.Structure):
    _fields_ = [("dwState", wintypes.DWORD), ("dwLocalAddr", wintypes.DWORD),
                ("dwLocalPort", wintypes.DWORD), ("dwRemoteAddr", wintypes.DWORD),
                ("dwRemotePort", wintypes.DWORD), ("dwOwningPid", wintypes.DWORD)]


class _MIB_TCPTABLE_PID(ctypes.Structure):
    _fields_ = [("dwNumEntries", wintypes.DWORD),
                ("table", _MIB_TCPROW_PID * 1)]


def _proc_path(pid: int) -> str:
    """进程完整路径；权限不足返回空。"""
    if not IS_WIN or not pid:
        return ""
    try:
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        k32.OpenProcess.restype = wintypes.HANDLE
        k32.QueryFullProcessImageNameW.argtypes = [
            wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
            ctypes.POINTER(wintypes.DWORD)]
        k32.QueryFullProcessImageNameW.restype = wintypes.BOOL
        k32.CloseHandle.argtypes = [wintypes.HANDLE]
        # PROCESS_QUERY_LIMITED_INFORMATION：普通权限也能问同用户进程
        h = k32.OpenProcess(0x1000, False, pid)
        if not h:
            return ""
        try:
            buf = ctypes.create_unicode_buffer(512)
            size = wintypes.DWORD(512)
            if k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
                return buf.value
            return ""
        finally:
            k32.CloseHandle(h)
    except Exception:                        # noqa: BLE001
        return ""


def _proc_name(pid: int) -> str:
    p = _proc_path(pid)
    return p.rsplit("\\", 1)[-1] if p else ""


def _proc_map() -> dict[int, str]:
    """pid → 进程名。走 tasklist 而不是 OpenProcess：

    非管理员下 OpenProcess 打不开 Services 会话里的系统进程（svchost、System），
    而 tasklist 能列全。一次调用换整表映射，比逐进程 Open 更省。
    """
    if not IS_WIN:
        return {}
    try:
        p = subprocess.run(["tasklist", "/fo", "csv", "/nh"],
                           capture_output=True, timeout=15,
                           creationflags=CREATE_NO_WINDOW)
    except Exception:                        # noqa: BLE001
        return {}
    raw = p.stdout or b""
    text = ""
    for enc in ("utf-8", "gbk", "mbcs"):
        try:
            text = raw.decode(enc)
            break
        except (UnicodeDecodeError, LookupError):
            continue
    out: dict[int, str] = {}
    for row in csv.reader(io.StringIO(text)):
        if len(row) < 3:
            continue
        try:
            out[int(row[1])] = row[0]
        except (ValueError, IndexError):
            continue
    return out


def listening(limit: int = 400) -> list[Listener]:
    """TCP 监听端口（GetExtendedTcpTable，等价 netstat -ano 的 LISTENING）。"""
    if not IS_WIN:
        return []
    out: list[Listener] = []
    try:
        iphlp = ctypes.WinDLL("iphlpapi")
        size = wintypes.DWORD(0)
        # TCP_TABLE_OWNER_PID_LISTENER = 3，AF_INET = 2
        iphlp.GetExtendedTcpTable(None, ctypes.byref(size), False, 2, 3, 0)
        if not size.value or size.value > 16 * 1024 * 1024:
            return []
        buf = ctypes.create_string_buffer(size.value)
        rc = iphlp.GetExtendedTcpTable(buf, ctypes.byref(size), False, 2, 3, 0)
        if rc != 0:
            return []
        table = ctypes.cast(buf, ctypes.POINTER(_MIB_TCPTABLE_PID)).contents
        rows = ctypes.cast(ctypes.byref(table.table),
                           ctypes.POINTER(_MIB_TCPROW_PID))
        for i in range(min(int(table.dwNumEntries), limit)):
            r = rows[i]
            addr = socket.inet_ntoa(struct.pack("<L", r.dwLocalAddr))
            port = socket.ntohs(r.dwLocalPort & 0xFFFF)
            pid = int(r.dwOwningPid)
            out.append(Listener("TCP", addr, port, pid))
    except Exception:                        # noqa: BLE001
        return []
    # 端口升序，同端口按 pid
    out.sort(key=lambda x: (x.port, x.pid))
    return out


# ------------------------------------------------------------------ 代理 / hosts
def proxy() -> dict:
    enabled = int(_reg(0x80000001, INET_SETTINGS, "ProxyEnable") or 0)
    return {
        "enabled": bool(enabled),
        "server": str(_reg(0x80000001, INET_SETTINGS, "ProxyServer") or ""),
        "pac": str(_reg(0x80000001, INET_SETTINGS, "AutoConfigURL") or ""),
    }


def hosts_path() -> str:
    root = os.environ.get("SystemRoot", r"C:\Windows")
    return os.path.join(root, "System32", "drivers", "etc", "hosts")


HOSTS_BLOCK = ("0.0.0.0", "127.", "::1", "255.255.255.255")


def hosts_kind(ip: str, name: str) -> str:
    """单条 hosts 记录的性质：block 屏蔽 / hijack 劫持 / map 普通映射。

    UI 侧按这个上色 —— 「屏蔽」是常规操作只灰不红，只有劫持才该红。
    """
    if ip.startswith(HOSTS_BLOCK):
        return "block"
    if any(w in name.lower() for w in WATCH_DOMAINS):
        return "hijack"
    return "map"


def hosts_entries() -> tuple[int, list[tuple[str, str]], str, str]:
    """返回 (总行数, [(IP, 域名)], 级别, 说明)。

    级别："" 正常 / "info" 仅提示 / "warn" 可疑。
    别把「广告屏蔽」当异常 —— 大量 `0.0.0.0 xxx` 是迅雷、去广告工具的常规写法；
    真正可疑的是**把常见域名指向某个具体公网 IP**（劫持）或指向非本机的内网地址。
    """
    path = hosts_path()
    if not os.path.isfile(path):
        return (0, [], "info", "找不到 hosts 文件")
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            raw = f.read().splitlines()
    except OSError as exc:
        return (0, [], "info", f"读取失败：{exc}")

    entries: list[tuple[str, str]] = []
    for line in raw:
        s = line.split("#", 1)[0].strip()
        if not s:
            continue
        parts = s.split()
        if len(parts) >= 2:
            entries.append((parts[0], parts[1]))
    if not entries:
        return (len(raw), [], "", "没有自定义记录（正常）")

    redirect = [(ip, n) for ip, n in entries
                if hosts_kind(ip, n) != "block"]
    hijack = [(ip, n) for ip, n in redirect
              if hosts_kind(ip, n) == "hijack"]
    if hijack:
        ip, name = hijack[0]
        more = f"（共 {len(hijack)} 条）" if len(hijack) > 1 else ""
        return (len(raw), entries, "warn",
                f"常见域名被指向 {ip}：{name}{more} —— 可能是屏蔽更新或流量劫持")
    if redirect:
        return (len(raw), entries, "info",
                f"{len(redirect)} 条指向具体地址（多为内网服务器映射），建议核对")
    return (len(raw), entries, "",
            f"{len(entries)} 条屏蔽记录（广告/推广域名，属正常）")


# ------------------------------------------------------------------ 汇总
STEPS = 7


def diagnose(stop_flag=None, progress=None) -> Report:
    """按顺序跑一遍检查。任一步失败都不会中断，只是记一条结论。

    顺序上**先测外网再测网关**：很多路由器/防火墙默认不回 ICMP，
    「网关 ping 不通但外网正常」是最常见的误报来源，先知道外网通不通才好定调。
    """
    t0 = time.perf_counter()
    rep = Report()

    def tick(i, msg):
        if progress:
            progress(i, STEPS, msg)

    def alive() -> bool:
        return not (stop_flag and stop_flag[0])

    # ---- 1. 网卡
    tick(0, "读取网卡配置")
    rep.adapters = adapters()
    pri = primary(rep.adapters)
    phys = [a for a in rep.adapters
            if not a.ip.startswith("127.") and not a.is_virtual]
    virt = [a for a in rep.adapters
            if not a.ip.startswith("127.") and a.is_virtual]
    if not phys:
        rep.checks.append(Check("本机地址", "没有可用的物理网卡 IPv4 地址", False,
                                "网卡未连接，或没拿到 DHCP 地址"))
    for a in phys:
        detail = f"{a.ip}/{a.mask}" if a.mask else a.ip
        if a.gateway:
            detail += f" · 网关 {a.gateway}"
        if a is pri:
            rep.checks.append(Check(f"网卡 {a.title[:26]}", detail, a.up,
                                    a.detail_text + ("（主用）" if a.up else " · 链路未连接")))
        else:
            rep.checks.append(Check(f"网卡 {a.title[:26]}", detail, None,
                                    a.detail_text))
    if virt:
        names = "、".join(x.title[:14] for x in virt[:3])
        rep.checks.append(Check(
            "虚拟网卡", f"{len(virt)} 块：{names}" + ("…" if len(virt) > 3 else ""),
            None, "WSL / Hyper-V / VPN 之类，与上外网无关"))
    if not alive():
        rep.elapsed = time.perf_counter() - t0
        return rep

    # ---- 2. ICMP 自检：本机都 ping 不通就说明 ICMP 被拦，后面结论要打折
    tick(1, "ICMP 自检")
    icmp_ok, _ms, _ttl, _err = ping("127.0.0.1", 2000)
    if not icmp_ok:
        rep.checks.append(Check("ICMP 自检", "127.0.0.1 无应答", None,
                                "本机 ICMP 可能被安全软件拦截，ping 类结论仅供参考"))

    # ---- 3. 外网
    tick(2, "ping 外网")
    ext_ok = False
    if alive():
        hit = None
        for target, label in PUBLIC_TARGETS:
            ok, ms, ttl, _err = ping(target, 1500)
            if ok:
                hit = (target, label, ms, ttl)
                break
        if hit:
            ext_ok = True
            rep.checks.append(Check("外网连通",
                                    f"{hit[1]} {hit[0]} · {hit[2]:.0f} ms", True,
                                    f"TTL {hit[3]}", metric=hit[2]))
        elif not icmp_ok:
            rep.checks.append(Check("外网连通", "ICMP 不可用，无法判断", None,
                                    "用「DNS 解析」和「端口」两项间接判断"))
        else:
            rep.checks.append(Check("外网连通", "三个公网地址都 ping 不通", False,
                                    "若 DNS 能解析，多为防火墙只放行 DNS/HTTP"))

    # ---- 4. 网关
    tick(3, "ping 网关")
    if alive():
        if pri and pri.gateway:
            ok, ms, ttl, err = ping(pri.gateway)
            if ok:
                rep.checks.append(Check("网关连通", f"{pri.gateway} · {ms:.0f} ms",
                                        True, f"TTL {ttl}", metric=ms))
            elif ext_ok:
                rep.checks.append(Check(
                    "网关连通", f"{pri.gateway} 不响应 ICMP", None,
                    "但外网可达 —— 多数路由器默认禁 ping，不算故障"))
            else:
                rep.checks.append(Check("网关连通", f"{pri.gateway} 不通", False,
                                        err or "检查网线 / Wi-Fi / 路由器"))
        else:
            rep.checks.append(Check("网关连通", "没有默认网关", False,
                                    "可能未联网，或路由表被改"))

    # ---- 5. DNS
    tick(4, "解析域名")
    if alive():
        ok, ips, ms, err = resolve("www.baidu.com")
        if ok:
            slow = ms >= 800
            rep.checks.append(Check(
                "DNS 解析", f"www.baidu.com → {ips[0]} · {ms:.0f} ms", not slow,
                "解析偏慢，建议换公共 DNS（223.5.5.5 / 119.29.29.29）" if slow else "",
                metric=ms))
        else:
            rep.checks.append(Check(
                "DNS 解析", "解析 www.baidu.com 失败", False,
                f"{err} —— 能 ping 通 IP 却解析不了域名，就是 DNS 问题"))
        if pri and pri.dns:
            rep.checks.append(Check("DNS 服务器", pri.dns, None, "来自主网卡配置"))

    # ---- 6. 本机关键端口
    tick(5, "检查本机端口")
    if alive():
        for port, label in ((445, "SMB 文件共享"), (3389, "远程桌面")):
            ok, _ms, _err = tcp_check("127.0.0.1", port, 0.8)
            rep.checks.append(Check(
                f"本机 {port}（{label}）", "监听中" if ok else "未监听", None,
                "该服务已启动" if ok else "本机没开这个服务，属正常"))

    # ---- 7. 代理 / hosts / 监听端口
    tick(6, "读取代理、hosts 与监听端口")
    p = proxy()
    rep.proxy_enabled = p["enabled"]
    rep.proxy_server = p["server"]
    rep.proxy_pac = p["pac"]
    if p["enabled"]:
        rep.checks.append(Check("系统代理", p["server"] or "已开启但没填地址", None,
                                "浏览器与系统流量会走这个代理"))
    elif p["server"]:
        rep.checks.append(Check("系统代理", "未启用", True,
                                f"但配置里留着 {p['server']}，需要时打开即可"))
    else:
        rep.checks.append(Check("系统代理", "未启用", True, "直连"))
    if p["pac"]:
        rep.checks.append(Check("自动配置脚本", p["pac"], None, "PAC 地址"))

    n, ent, level, note = hosts_entries()
    rep.hosts_lines = n
    rep.hosts_entries = ent
    rep.hosts_level = level
    rep.hosts_warning = note
    rep.checks.append(Check(
        "hosts 文件", f"{len(ent)} 条自定义记录", 
        False if level == "warn" else (None if level == "info" else True), note))

    tick(6, "统计监听端口")
    if alive():
        rep.listeners = listening()
        pmap = _proc_map()
        for it in rep.listeners:
            it.proc = pmap.get(it.pid, "") or _proc_name(it.pid)

    rep.elapsed = time.perf_counter() - t0
    return rep
