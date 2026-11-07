# -*- coding: utf-8 -*-
"""服务调优：白名单式判定 + 可回滚的启动类型修改。

判定优先级：核心服务（禁止操作） > 已知可调服务（给建议） > 第三方服务（标风险）
> 其它（不推荐操作）

所有读写都走 advapi32（OpenSCManager / QueryServiceStatus / ChangeServiceConfig），
不依赖 `sc.exe`：既快，也不会因为命令行被安全软件拦截而失效。
"""
from __future__ import annotations

import ctypes
import json
import os
import time
from ctypes import wintypes
from dataclasses import dataclass

from . import winver

IS_WIN = os.name == "nt"

SERVICES_KEY = r"SYSTEM\CurrentControlSet\Services"
HKLM = 0x80000002

SC_MANAGER_CONNECT = 0x0001
SERVICE_QUERY_STATUS = 0x0004
SERVICE_CHANGE_CONFIG = 0x0002
SERVICE_STOP = 0x0020
SERVICE_CONTROL_STOP = 0x00000001
SERVICE_STOPPED = 0x00000001
SERVICE_STOP_PENDING = 0x00000003
SERVICE_RUNNING = 0x00000004
ERROR_SERVICE_ALREADY_RUNNING = 1056
ERROR_SERVICE_NOT_ACTIVE = 1062
ERROR_DEPENDENT_SERVICES_RUNNING = 1061

START_TYPES = {0: "引导", 1: "系统", 2: "自动", 3: "手动", 4: "禁用"}
START_BY_TEXT = {v: k for k, v in START_TYPES.items()}

# 已知服务的调优建议：(显示名补充, 影响说明, 建议)
KNOWN = {
    "sysmain": ("Superfetch 预读", "机械盘上能加速冷启动；SSD 上收益很小，"
                                 "禁用可省下内存与后台磁盘占用", "建议禁用（SSD 机器）"),
    "wsearch": ("Windows 搜索索引", "禁用后开始菜单与资源管理器搜索变慢，"
                                   "换来更低的磁盘与 CPU 占用", "按需禁用"),
    "diagtrack": ("连接用户体验和遥测", "只上报诊断数据，禁用不影响任何功能", "建议禁用"),
    "dmwappushservice": ("WAP 推送消息路由", "遥测链路的配套服务，禁用无影响", "建议禁用"),
    "remoteregistry": ("远程注册表", "办公机不需要，禁用可减小攻击面", "建议禁用"),
    "fax": ("传真", "几乎无人使用", "建议禁用"),
    "retaildemo": ("零售演示模式", "只有门店演示机需要", "建议禁用"),
    "mapsbroker": ("下载地图管理器", "只在用离线地图时需要", "可禁用"),
    "lfsvc": ("地理位置服务", "只有地图、天气等需要定位的应用在用时需要", "按需禁用"),
    "tabletinputservice": ("触摸键盘与手写面板", "没有触摸屏可以禁用", "按需禁用"),
    "wmpnetworksvc": ("WMP 网络共享", "几乎无人使用", "可禁用"),
    "xblauthmanager": ("Xbox 身份验证", "不玩 Xbox 游戏可禁用", "建议禁用"),
    "xblgamesave": ("Xbox 游戏存档", "不玩 Xbox 游戏可禁用", "建议禁用"),
    "xboxnetapisvc": ("Xbox 网络 API", "不玩 Xbox 游戏可禁用", "建议禁用"),
    "xboxgipsvc": ("Xbox 输入协议", "不玩 Xbox 游戏可禁用", "建议禁用"),
    "phonesvc": ("电话服务", "无蜂窝模块的机器可禁用", "可禁用"),
    "wersvc": ("Windows 错误报告", "禁用后崩溃不再生成报告；排查蓝屏期间不要关", "按需禁用"),
    "spooler": ("打印后台", "没有打印机可以禁用；有打印机务必保留", "谨慎"),
    "ajrouter": ("AllJoyn 路由器", "物联网协议，普通用户不需要", "可禁用"),
    "alg": ("应用层网关", "老式防火墙穿透服务，几乎无用", "可禁用"),
    "pcasvc": ("程序兼容性助手", "禁用后不再弹兼容性提示", "按需禁用"),
    "trkwks": ("分布式链接跟踪", "只影响快捷方式移动后的追踪", "可禁用"),
    "scardsvr": ("智能卡", "没有读卡器可以禁用", "按需禁用"),
    "bthavctpsvc": ("蓝牙音频", "不用蓝牙耳机可禁用", "谨慎"),
    "bthhfsrv": ("蓝牙免提", "不用蓝牙耳机可禁用", "谨慎"),
    "nettcpportsharing": ("WCF 端口共享", "只有自己开发 WCF 服务时才需要", "可禁用"),
    "webclient": ("WebClient", "老式 WebDAV 客户端，禁用可减小攻击面", "可禁用"),
    "seclogon": ("Secondary Logon", "只有 runas 切换身份时才用", "按需禁用"),
    "lmhosts": ("TCP/IP NetBIOS Helper", "影响老式局域网名称解析，域环境不要关", "谨慎"),
    "sharedaccess": ("Internet 连接共享", "只在开热点或共享网络时需要", "按需禁用"),
    "wuauserv": ("Windows 更新", "个人机强烈不建议禁用；公司统一管控时按策略处理", "谨慎"),
    "bits": ("后台智能传输", "系统更新与部分下载依赖它", "不建议"),
    "wsaifabricsvc": ("Windows 安卓子系统", "只服务于 WSA（安卓应用），"
                                           "不装安卓应用可以禁用", "可禁用"),
}

# 只在某一代系统上出现的服务：在另一代上扫到基本是降级残留，不给「建议调优」
OS_ONLY = {
    "wsaifabricsvc": "win11",      # 安卓子系统是 Win11 21H2 起才有的
}

# 核心服务：连操作按钮都不给，避免误伤。
# 注意：与 KNOWN 重合的服务以 KNOWN 为准（verdict_of 里先查 KNOWN），
# 例如 Spooler / SysMain / PcaSvc 虽然敏感，但属于「可提出建议」的范围。
CORE = {
    "rpcss", "rpceptmapper", "dcomlaunch", "lsm", "power", "plugplay", "profsvc",
    "eventlog", "schedule", "winmgmt", "themes", "cryptsvc", "bfe", "mpssvc",
    "lanmanworkstation", "lanmanserver", "dhcp", "dnscache", "nlasvc",
    "audiosrv", "audioendpointbuilder", "wlangsvc", "windefend",
    "securityhealthservice", "trustedinstaller", "msiserver", "staterepository",
    "usermanager", "samss", "gpsvc", "w32time", "termservice", "sessionenv",
    "umrdpservice", "winrm", "keyiso", "vaultsvc", "sens", "eventsystem",
    "systemeventsbroker", "timebrokersvc", "coremessagingregistrar",
    "userdatasvc", "wpnservice", "wpnuserservice", "shellhwdetection",
    "textinputframework", "cdpusersvc", "tokenbroker", "camsvc",
    "deviceassociationservice", "winhttpautoproxysvc", "ncasvc", "dsmsvc",
    "appxsvc", "clipsvc", "brokerinfrastructure", "dabssvc", "diagsvc",
    "dispbrokerdesktopsvc", "dusmsvc", "efssvc", "fontcacheservice",
    "installservice", "licensemanager", "ngcsvc", "pla", "radiosvc",
    "usosvc", "wbengine", "wudfsvc", "spoolsv", "seclogon_svc",
}

CREATE_JSON = "%LOCALAPPDATA%\\SystemToolbox\\backup\\services.json"


class _SERVICE_STATUS(ctypes.Structure):
    _fields_ = [("dwServiceType", wintypes.DWORD),
                ("dwCurrentState", wintypes.DWORD),
                ("dwControlsAccepted", wintypes.DWORD),
                ("dwWin32ExitCode", wintypes.DWORD),
                ("dwServiceSpecificExitCode", wintypes.DWORD),
                ("dwCheckPoint", wintypes.DWORD),
                ("dwWaitHint", wintypes.DWORD)]


@dataclass
class Service:
    name: str
    display: str
    desc: str
    start: int
    state: str = ""              # 运行中 / 已停止 / 未知
    running: bool = False
    verdict: str = "常规"
    reason: str = ""
    advice: str = ""
    path: str = ""
    is_third_party: bool = False
    safe_disable: bool = True
    safe_enable: bool = True

    @property
    def start_text(self) -> str:
        return START_TYPES.get(self.start, str(self.start))


# ------------------------------------------------------------------ API
_adv_cache = None


def resolve_mui(text: str) -> str:
    """解析 `@%SystemRoot%\\system32\\xxx.dll,-100` 这种 MUI 间接字符串。

    服务的 DisplayName / Description 大量使用这种「资源引用」写法，
    直接显示就是一堆 @ 路径。SHLoadIndirectString 是系统专门干这个的 API。
    """
    s = (text or "").strip()
    if not s.startswith("@"):
        return s
    try:
        sh = ctypes.WinDLL("shlwapi", use_last_error=True)
        sh.SHLoadIndirectString.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR,
                                            wintypes.UINT, ctypes.c_void_p]
        sh.SHLoadIndirectString.restype = ctypes.c_long
        buf = ctypes.create_unicode_buffer(1024)
        if sh.SHLoadIndirectString(s, buf, 1024, None) == 0 and buf.value.strip():
            return buf.value.strip()
    except Exception:                          # noqa: BLE001
        pass
    # 兜底：至少去掉 @ 前缀和 ",id" 尾巴，别让界面出现一长串 dll 路径
    tail = s.rsplit(",", 1)[0].lstrip("@")
    return tail.rsplit("\\", 1)[-1] if "\\" in tail else tail


def _adv():
    """配置好 argtypes/restype 的 advapi32。

    必须显式声明：HANDLE 是 64 位指针，ctypes 默认按 c_int 处理会截断，
    截断后的句柄传给 QueryServiceStatus 直接让进程崩掉。
    """
    global _adv_cache
    if _adv_cache is not None:
        return _adv_cache
    adv = ctypes.WinDLL("advapi32", use_last_error=True)
    H = wintypes.HANDLE
    adv.OpenSCManagerW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD]
    adv.OpenSCManagerW.restype = H
    adv.OpenServiceW.argtypes = [H, wintypes.LPCWSTR, wintypes.DWORD]
    adv.OpenServiceW.restype = H
    adv.QueryServiceStatus.argtypes = [H, ctypes.POINTER(_SERVICE_STATUS)]
    adv.QueryServiceStatus.restype = wintypes.BOOL
    adv.ControlService.argtypes = [H, wintypes.DWORD, ctypes.POINTER(_SERVICE_STATUS)]
    adv.ControlService.restype = wintypes.BOOL
    adv.ChangeServiceConfigW.argtypes = [
        H, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, wintypes.LPCWSTR,
        wintypes.LPCWSTR, ctypes.c_void_p, wintypes.LPCWSTR, wintypes.LPCWSTR,
        wintypes.LPCWSTR, wintypes.LPCWSTR]
    adv.ChangeServiceConfigW.restype = wintypes.BOOL
    adv.CloseServiceHandle.argtypes = [H]
    adv.CloseServiceHandle.restype = wintypes.BOOL
    _adv_cache = adv
    return adv


# ------------------------------------------------------------------ 注册表
def _read_services() -> list[tuple[str, dict]]:
    if not IS_WIN:
        return []
    import winreg
    try:
        root = winreg.OpenKey(HKLM, SERVICES_KEY, 0, winreg.KEY_READ)
    except OSError:
        return []
    out: list[tuple[str, dict]] = []
    i = 0
    try:
        while True:
            try:
                name = winreg.EnumKey(root, i)
            except OSError:
                break
            i += 1
            try:
                k = winreg.OpenKey(root, name, 0, winreg.KEY_READ)
            except OSError:
                continue
            info: dict = {}
            try:
                for field in ("DisplayName", "Description", "ImagePath"):
                    try:
                        v = winreg.QueryValueEx(k, field)[0]
                        info[field] = v if isinstance(v, str) else str(v)
                    except OSError:
                        info[field] = ""
                for field in ("Start", "Type"):
                    try:
                        info[field] = int(winreg.QueryValueEx(k, field)[0])
                    except (OSError, ValueError, TypeError):
                        info[field] = None
            finally:
                k.Close()
            out.append((name, info))
    finally:
        root.Close()
    return out


# ------------------------------------------------------------------ 状态
def _query_states(names: list[str]) -> dict[str, bool]:
    """一次打开 SCM，批量查询运行状态。返回 {名称: 是否运行中}。"""
    result: dict[str, bool] = {}
    if not IS_WIN:
        return result
    try:
        adv = _adv()
        scm = adv.OpenSCManagerW(None, None, SC_MANAGER_CONNECT)
    except Exception:                          # noqa: BLE001
        return result
    if not scm:
        return result
    try:
        for name in names:
            try:
                h = adv.OpenServiceW(scm, name, SERVICE_QUERY_STATUS)
            except Exception:                  # noqa: BLE001
                h = None
            if not h:
                continue
            try:
                st = _SERVICE_STATUS()
                if adv.QueryServiceStatus(h, ctypes.byref(st)):
                    result[name] = (st.dwCurrentState == SERVICE_RUNNING)
            finally:
                adv.CloseServiceHandle(h)
    finally:
        adv.CloseServiceHandle(scm)
    return result


# ------------------------------------------------------------------ 判定
def is_system_path(image_path: str) -> bool:
    """判断服务程序是否来自系统目录。

    必须先把 %SystemRoot% / %windir% 展开：大量系统服务的 ImagePath 写成
    `%SystemRoot%\\system32\\svchost.exe -k netsvcs`，不展开就会被当成第三方。
    """
    raw = (image_path or "").strip()
    if not raw:
        return True                     # 没有 ImagePath 的多为驱动/受保护服务，按系统处理
    low = os.path.expandvars(raw).lower()
    sysroot = os.environ.get("SystemRoot", r"C:\Windows").lower().rstrip("\\")
    return (sysroot in low or "\\windows\\" in low
            or "system32\\" in low or "syswow64\\" in low
            or low.startswith("\\??\\") or low.startswith("system32"))


def verdict_of(name: str, info: dict) -> tuple[str, str, str, bool]:
    """返回 (verdict, reason, advice, 是否第三方)。"""
    low = name.lower()
    third = not is_system_path(info.get("ImagePath") or "")

    if low in KNOWN:
        _extra, reason, advice = KNOWN[low]
        only = OS_ONLY.get(low, "")
        if only and not winver.current().supports(only):
            # 当前系统根本没有这项服务，扫到多半是从更高版本降级后的残留
            return ("常规", "当前系统版本不含该服务（可能是降级残留）",
                    "不建议操作", third)
        return ("可调优", reason, advice, third)
    if low in CORE:
        return ("核心服务", "系统运行必需，关闭会导致功能异常甚至无法登录", "禁止操作", third)
    if third:
        return ("第三方", "由第三方软件安装，禁用可能影响该软件的功能", "按需处理", third)
    return ("常规", "系统自带服务，没有明确的调优收益", "不建议操作", third)


def scan(stop_flag=None, progress=None) -> list[Service]:
    rows = _read_services()
    states = _query_states([n for n, i in rows
                            if (i.get("Type") or 0) & 0x30])
    out: list[Service] = []
    for idx, (name, info) in enumerate(rows):
        if stop_flag and stop_flag[0]:
            break
        typ = info.get("Type") or 0
        start = info.get("Start")
        if start is None or start < 2:        # 引导/系统级驱动不列
            continue
        if not (typ & 0x30):                  # 只要 Win32 服务（含共享进程）
            continue
        if progress and idx % 40 == 0:
            progress(idx, len(rows), name)
        verdict, reason, advice, third = verdict_of(name, info)
        running = states.get(name, False)
        display = resolve_mui(info.get("DisplayName") or "") or name
        out.append(Service(
            name=name,
            display=display,
            desc=resolve_mui((info.get("Description") or "").strip()),
            start=start,
            state="运行中" if running else ("已停止" if name in states else "未知"),
            running=running,
            verdict=verdict, reason=reason, advice=advice,
            path=info.get("ImagePath") or "",
            is_third_party=third,
            safe_disable=verdict != "核心服务",
            safe_enable=True))
    # 可调优的排前面，其次第三方，再按启动类型
    order = {"可调优": 0, "第三方": 1, "常规": 2, "核心服务": 3}
    out.sort(key=lambda s: (order.get(s.verdict, 9), s.start, s.name.lower()))
    if progress:
        progress(len(rows), len(rows), "完成")
    return out


# ------------------------------------------------------------------ 操作
def _backup_path() -> str:
    p = os.path.expandvars(CREATE_JSON)
    try:
        os.makedirs(os.path.dirname(p), exist_ok=True)
    except OSError:
        return ""
    return p


def _load_backup() -> dict:
    p = _backup_path()
    if not p or not os.path.isfile(p):
        return {}
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f) or {}
    except (OSError, ValueError):
        return {}


def _save_backup(data: dict) -> None:
    p = _backup_path()
    if not p:
        return
    try:
        with open(p, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except OSError:
        pass


def remember_original(svc: Service) -> None:
    """第一次改动某服务时记下原始启动类型，之后还原以它为准。"""
    data = _load_backup()
    if svc.name not in data:
        data[svc.name] = {"start": svc.start, "display": svc.display,
                          "saved": time.strftime("%Y-%m-%d %H:%M:%S")}
        _save_backup(data)


def original_start(name: str) -> int | None:
    return (_load_backup().get(name) or {}).get("start")


def _open_service(name: str, access: int):
    """打开服务句柄；失败返回 None。"""
    try:
        adv = _adv()
        scm = adv.OpenSCManagerW(None, None, SC_MANAGER_CONNECT)
        if not scm:
            return None
        try:
            return adv.OpenServiceW(scm, name, access) or None
        finally:
            adv.CloseServiceHandle(scm)
    except Exception:                          # noqa: BLE001
        return None


def set_start_type(name: str, start: int) -> tuple[bool, str]:
    """修改服务启动类型（2 自动 / 3 手动 / 4 禁用）。"""
    if start not in (2, 3, 4):
        return (False, "不支持的启动类型")
    adv = _adv()
    h = _open_service(name, SERVICE_CHANGE_CONFIG)
    if not h:
        return (False, "无权修改该服务（请以管理员身份运行）")
    try:
        ok = adv.ChangeServiceConfigW(h, 0xFFFFFFFF, start, 0xFFFFFFFF,
                                      None, None, None, None, None, None, None)
        if ok:
            return (True, "")
        err = ctypes.get_last_error()
        hint = {5: "权限不足，请以管理员身份运行"}.get(err, f"错误码 {err}")
        return (False, f"修改失败：{hint}")
    except Exception as exc:                   # noqa: BLE001
        return (False, f"修改失败：{exc}")
    finally:
        try:
            adv.CloseServiceHandle(h)
        except Exception:                      # noqa: BLE001
            pass


def stop(name: str, timeout: float = 8.0) -> tuple[bool, str]:
    """停止服务（会等到真正停下，最多 timeout 秒）。"""
    adv = _adv()
    h = _open_service(name, SERVICE_STOP | SERVICE_QUERY_STATUS)
    if not h:
        return (False, "无权停止该服务（请以管理员身份运行）")
    try:
        st = _SERVICE_STATUS()
        if not adv.ControlService(h, SERVICE_CONTROL_STOP, ctypes.byref(st)):
            err = ctypes.get_last_error()
            # 1062 = 服务本来就没在跑。这是「已经是要的状态」，不是失败。
            # 原来只判了 1056（那是**启动**已运行服务时的错误码），于是
            # 停一个已停的服务会报「停止失败（错误码 1062）」。
            if err in (ERROR_SERVICE_NOT_ACTIVE, ERROR_SERVICE_ALREADY_RUNNING):
                return (True, "")
            if err == ERROR_DEPENDENT_SERVICES_RUNNING:
                return (False, "还有其它服务依赖它，需要先停依赖项")
            return (False, f"停止失败（错误码 {err}）")
        deadline = time.time() + timeout
        while time.time() < deadline:
            st2 = _SERVICE_STATUS()
            if adv.QueryServiceStatus(h, ctypes.byref(st2)):
                # 只认 SERVICE_STOPPED(1)。用 `!= RUNNING` 会把
                # STOP_PENDING(3)、PAUSED(7) 都算成已停止 —— 服务还在收尾就
                # 报成功，紧接着改启动类型会失败（运行中的服务不让禁）。
                if st2.dwCurrentState == SERVICE_STOPPED:
                    return (True, "")
            time.sleep(0.2)
        return (False, "已发出停止指令，但服务仍未停下")
    except Exception as exc:                   # noqa: BLE001
        return (False, f"停止失败：{exc}")
    finally:
        try:
            adv.CloseServiceHandle(h)
        except Exception:                      # noqa: BLE001
            pass


def apply_change(svc: Service, disable: bool,
                 also_stop: bool = True) -> tuple[bool, str]:
    """禁用（手动/自动 → 禁用）或还原（禁用 → 原类型）。"""
    remember_original(svc)
    if disable:
        ok, msg = set_start_type(svc.name, 4)
        if not ok:
            return (ok, msg)
        note = ""
        if also_stop and svc.running:
            stopped, smsg = stop(svc.name)
            if not stopped:
                note = f"（启动类型已设为禁用，但未能立即停止：{smsg}）"
        return (True, note)
    target = original_start(svc.name)
    if target is None or target == 4:
        target = 3
    ok, msg = set_start_type(svc.name, target)
    return (ok, msg)


def batch_apply(services, disable: bool, progress=None) -> tuple[int, list[str]]:
    ok = 0
    errs: list[str] = []
    for i, svc in enumerate(services):
        if progress:
            progress(i, len(services), svc.display)
        good, msg = apply_change(svc, disable)
        if good:
            ok += 1
        else:
            errs.append(f"{svc.display}: {msg}")
    if progress:
        progress(len(services), len(services), "完成")
    return (ok, errs)
