# -*- coding: utf-8 -*-
"""设备与驱动体检：设备树、问题码、驱动版本与日期。**全只读**。

为什么不走 `Get-PnpDevice` / `pnputil`：

- PowerShell 起一次 ~1 秒，`pnputil` 的输出是本地化文本，中文系统上解析易错
- **问题码只有 Configuration Manager API 给得准**：注册表 `Enum\\...` 里没有
  这一项（老资料里的 `CSConfigFlags` 早已废弃），必须 `CM_Get_DevNode_Status`
- 设备清单本身读注册表就够快，462 个实例（含未用枚举器）约 0.2 秒

取数分工：

| 要拿什么 | 用什么 |
|---|---|
| 设备清单 / 名称 / 厂商 / 服务 | 注册表 `SYSTEM\\CurrentControlSet\\Enum` |
| 是否在位 / 有问题 / 被禁用（问题码） | `cfgmgr32!CM_Locate_DevNodeW` + `CM_Get_DevNode_Status` |
| 驱动版本 / 日期 / 提供者 / INF | 注册表 `Control\\Class\\{GUID}\\0000…` |
| 类别中文名 | `Control\\Class\\{GUID}` 的 `ClassDesc`（MUI 间接字符串） |

**设备名是 INF 字符串引用**：`DeviceDesc` 的实际形态是
`@acpitime.inf,%*acpi000e.devicedesc%;ACPI Wake Alarm`——
`SHLoadIndirectString` 只认 `@dll,-123` 那种 DLL 资源，INF 的 `%token%`
必须查 INF 文件自己的 `[Strings]` 段。照着「取最后一个反斜杠」的写法处理，
界面上就会出现一堆 `machine.inf` `usb.inf` 当设备名的荒唐结果。
"""
from __future__ import annotations

import ctypes
import os
import time
from ctypes import wintypes
from dataclasses import dataclass, field

from .services import resolve_mui

IS_WIN = os.name == "nt"

HKLM = 0x80000002
ENUM_ROOT = r"SYSTEM\CurrentControlSet\Enum"
CLASS_ROOT = r"SYSTEM\CurrentControlSet\Control\Class"

# 纯软件投影出来的枚举器：不是「设备」，而且条目极多（本机 SWD 有 102 条）
SKIP_ENUM = ("SWD", "SW")

# CM_Get_DevNode_Status 的问题码（cfgmgr32.h 的 CM_PROB_*）
PROBLEM_CN = {
    1: ("设备未配置", "驱动未安装或安装未完成"),
    3: ("驱动内存不足", "系统资源紧张，重启后重试"),
    4: ("注册表损坏", "设备相关注册表项异常"),
    5: ("需要重启", "改动需要重启才生效"),
    6: ("引导配置冲突", "与其它设备的引导配置冲突"),
    7: ("缺少过滤器驱动", "上层/下层过滤器驱动缺失"),
    8: ("缺少设备加载器", "设备加载器未安装"),
    9: ("驱动加载失败", "驱动加载过程出错"),
    10: ("设备无法启动", "最常见的驱动/固件类故障"),
    11: ("设备未启动", "设备存在但未能启动"),
    12: ("资源不足", "内存/IO/中断资源冲突"),
    13: ("需要重启", "改动需要重启才生效"),
    14: ("需要重启", "设备需重新枚举"),
    15: ("需要重启", "设备需重新枚举"),
    16: ("无法识别资源", "固件上报的资源无法识别"),
    17: ("无法识别资源", "固件上报的资源无法识别"),
    18: ("需要重装驱动", "建议卸载设备后重新扫描"),
    19: ("注册表故障", "设备注册表项读取失败"),
    20: ("驱动加载失败", "驱动加载过程出错"),
    21: ("正在删除", "设备正在被移除"),
    22: ("已被禁用", "在设备管理器里被手动禁用"),
    23: ("未知故障", "系统未给出具体原因"),
    24: ("设备不存在", "设备未连接（插槽空）"),
    25: ("设备未就绪", "设备尚未准备好"),
    26: ("设备未就绪", "设备尚未准备好"),
    27: ("缺少必需资源", "固件未提供必需资源"),
    28: ("驱动未安装", "没有匹配的驱动"),
    29: ("固件未提供资源", "需要更新 BIOS / 固件"),
    30: ("资源被占用", "资源被其它设备占用"),
    31: ("驱动无法加载", "驱动无法加载到内核"),
    32: ("驱动冲突", "与其它驱动冲突"),
    33: ("需要重启", "改动需要重启才生效"),
    34: ("设备故障", "需要检查 BIOS 设置"),
    35: ("设备故障", "设备自身报告故障"),
    36: ("中断转换失败", "IRQ 映射失败"),
    37: ("驱动初始化失败", "驱动初始化过程失败"),
    38: ("驱动未加载", "驱动没有加载"),
    39: ("驱动损坏或缺失", "驱动文件不存在或已损坏"),
    40: ("驱动签名问题", "驱动未签名或签名无效"),
    41: ("驱动加载失败", "驱动加载过程出错"),
    42: ("等待设备重启", "设备需要重新启动"),
    43: ("驱动启动失败", "驱动向系统报告启动失败"),
    44: ("设备未启动", "设备存在但未能启动"),
    45: ("设备未连接", "设备已从系统移除"),
    46: ("设备不可用", "设备当前不可用"),
    47: ("设备已移除", "设备已被移除"),
    48: ("设备被阻止", "被策略或用户阻止启动"),
    49: ("设备故障", "设备自身报告故障"),
    50: ("固件故障", "固件报告故障"),
    51: ("请求被取消", "操作被取消"),
    52: ("设备已暂停", "设备处于暂停状态"),
}

# 这些「问题」其实是正常状态：笔记本上没插的设备就是 24/45
ABSENT_CODES = (24, 45)
# 需要重启不是故障，别把用户吓一跳
REBOOT_CODES = (5, 13, 14, 15, 33)

DN_STARTED = 0x00000008
DN_DISABLED = 0x00000200
DN_HAS_PROBLEM = 0x00000400
DN_PRIVATE_PROBLEM = 0x00008000


@dataclass
class Device:
    name: str = ""
    cls: str = ""                  # 类别中文名
    vendor: str = ""
    driver: str = ""               # 服务名（Service）
    instance: str = ""             # 设备实例 ID
    problem: int = 0
    status: str = "正常"
    hint: str = ""
    level: str = "ok"              # ok / info / warn / crit
    present: bool = True

    @property
    def state_text(self) -> str:
        return self.status


@dataclass
class Driver:
    desc: str = ""
    cls: str = ""
    version: str = ""
    date: str = ""                 # 原样显示（系统里就是 6-21-2006 这种）
    ts: float = 0.0                # 排序用
    provider: str = ""
    inf: str = ""
    devices: int = 0               # 该 INF 挂着几台设备
    instances: int = 1             # 同名驱动项（每个设备实例一条）
    third_party: bool = False

    @property
    def vendor_text(self) -> str:
        return self.provider or "—"


@dataclass
class Stats:
    devices: int = 0
    problems: int = 0
    disabled: int = 0
    absent: int = 0
    removed: int = 0               # 注册表残留、系统里已不存在的旧节点
    drivers: int = 0
    third_party: int = 0
    old_drivers: int = 0           # 第三方里 3 年以上没更新的
    rows: list[str] = field(default_factory=list)


# ------------------------------------------------------------------ 注册表
def _reg(root: int, path: str, name: str = ""):
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
    out: list[str] = []
    try:
        import winreg
        k = winreg.OpenKey(root, path, 0, winreg.KEY_READ)
    except OSError:
        return []
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


# ------------------------------------------------------------------ 文本解析
_INF_CACHE: dict[str, dict[str, str]] = {}


def _inf_strings(inf_name: str) -> dict[str, str]:
    """读 `%SystemRoot%\\INF\\<inf>` 的 [Strings] 段（token 小写 → 文本）。"""
    key = (inf_name or "").lower()
    if key in _INF_CACHE:
        return _INF_CACHE[key]
    table: dict[str, str] = {}
    root = os.environ.get("SystemRoot", r"C:\Windows")
    path = os.path.join(root, "INF", inf_name)
    try:
        if os.path.isfile(path):
            with open(path, encoding="utf-16", errors="replace") as f:
                text = f.read()
            if "\x00" in text[:200]:                 # 少数 INF 是 ANSI
                with open(path, encoding="mbcs", errors="replace") as f:
                    text = f.read()
            in_sec = False
            for line in text.splitlines():
                s = line.strip()
                if s.startswith("[") and s.endswith("]"):
                    in_sec = s.lower() == "[strings]"
                    continue
                if not in_sec or "=" not in s or s.startswith(";"):
                    continue
                k, _, v = s.partition("=")
                k = k.strip().strip('"').lower()
                if k and k not in table:
                    table[k] = v.strip().strip('"')
    except OSError:
        pass
    _INF_CACHE[key] = table
    return table


def dev_text(raw) -> str:
    """设备/厂商名的两种间接字符串都还原成可读文本。

    1. `@xxx.inf,%token%;回退文本` —— INF 字符串表，优先用分号后的回退文本
       （它已经是本机语言），没有再查 INF 的 [Strings]
    2. `@%SystemRoot%\\system32\\xxx.dll,-123` —— DLL 资源，交给 SHLoadIndirectString
    """
    s = str(raw or "").strip()
    if not s:
        return ""
    if not s.startswith("@"):
        return s
    if "," in s and "%" in s:                        # INF 字符串表形态
        if ";" in s:
            tail = s.rsplit(";", 1)[1].strip()
            if tail:
                return tail
        head, _, rest = s[1:].partition(",")
        token = rest.strip().strip("%").strip()
        hit = _inf_strings(head).get(token.lower())
        if hit:
            return hit
        return ""
    return resolve_mui(s)


def _class_names() -> dict[str, str]:
    """{ClassGUID(大写): 类别中文名}，名字来自 ClassDesc → Class。"""
    out: dict[str, str] = {}
    for guid in _subkeys(HKLM, CLASS_ROOT):
        base = CLASS_ROOT + "\\" + guid
        desc = dev_text(_reg(HKLM, base, "ClassDesc"))
        if not desc:
            desc = dev_text(_reg(HKLM, base, "Class"))
        if desc:
            out[guid.upper()] = desc
    return out


# ------------------------------------------------------------------ 问题码
_cm = None


def _cfgmgr():
    """配置好原型的 cfgmgr32（DEVINST 是 32 位，但仍显式声明更稳）。"""
    global _cm
    if _cm is not None:
        return _cm
    cm = ctypes.WinDLL("cfgmgr32", use_last_error=True)
    cm.CM_Locate_DevNodeW.argtypes = [ctypes.POINTER(wintypes.DWORD),
                                      wintypes.LPCWSTR, wintypes.DWORD]
    cm.CM_Locate_DevNodeW.restype = ctypes.c_long
    cm.CM_Get_DevNode_Status.argtypes = [ctypes.POINTER(wintypes.DWORD),
                                         ctypes.POINTER(wintypes.DWORD),
                                         wintypes.DWORD, wintypes.DWORD]
    cm.CM_Get_DevNode_Status.restype = ctypes.c_long
    _cm = cm
    return cm


CM_LOCATE_DEVNODE_NORMAL = 0
CM_LOCATE_DEVNODE_PHANTOM = 1


def node_status(instance: str) -> tuple[int, int, bool]:
    """返回 (状态位, 问题码, 是否查询成功)。

    问题码是「设备管理器里那个黄色感叹号」的原始依据，
    注册表里查不到 —— 只有 CM API 有。
    """
    if not IS_WIN or not instance:
        return (0, 0, False)
    try:
        cm = _cfgmgr()
        dev = wintypes.DWORD(0)
        rc = cm.CM_Locate_DevNodeW(ctypes.byref(dev), instance,
                                   CM_LOCATE_DEVNODE_NORMAL)
        if rc != 0:
            # 不在位的设备要用 PHANTOM 才定位得到
            rc = cm.CM_Locate_DevNodeW(ctypes.byref(dev), instance,
                                       CM_LOCATE_DEVNODE_PHANTOM)
            if rc != 0:
                return (0, 0, False)
        status = wintypes.DWORD(0)
        problem = wintypes.DWORD(0)
        rc = cm.CM_Get_DevNode_Status(ctypes.byref(status),
                                      ctypes.byref(problem), dev, 0)
        if rc != 0:
            return (0, 0, False)
        return (int(status.value), int(problem.value), True)
    except Exception:                        # noqa: BLE001
        return (0, 0, False)


def _apply_state(d: Device, flags: int, problem: int, ok: bool) -> None:
    """把状态位与问题码翻成人话，并定出级别。"""
    d.problem = problem
    if problem in ABSENT_CODES:
        d.present = False
        d.status = "未连接"
        d.hint = "设备当前不在位（笔记本拔掉的外设也这样），不算故障"
        d.level = "info"
        return
    if problem:
        cn, hint = PROBLEM_CN.get(problem, ("设备异常", "系统未给出具体原因"))
        d.status = cn
        d.hint = f"问题码 {problem}：{hint}"
        if problem == 22:
            d.level = "warn"
        elif problem in REBOOT_CODES:
            d.level = "info"
        else:
            d.level = "crit"
        return
    if flags & DN_DISABLED:
        d.status = "已被禁用"
        d.hint = "在设备管理器里被禁用"
        d.level = "warn"
        return
    if not ok:
        # CM_Locate 返回 CR_NO_SUCH_DEVNODE：注册表里还留着这个节点，
        # 系统里已经没有这台设备了（拔掉的 U 盘、旧摄像头的老实例）。
        # 这既不是故障也不是「在位」，如实说成「已移除」。
        d.present = False
        d.status = "已移除"
        d.hint = "系统里已不存在，注册表残留的旧节点；无害，设备管理器里「显示隐藏的设备」能看到"
        d.level = "info"
        return
    # 没有问题时不要拿 DN_STARTED 当「故障」判据：
    # 「Motherboard resources」「System timer」这类资源节点本来就不报启动标志，
    # 设备管理器里也没有感叹号。只有 CM 报了问题码才算异常。
    d.status = "正常"
    d.level = "ok"
    if not flags & DN_STARTED:
        d.hint = "资源型节点，无问题码（设备管理器里无感叹号即正常）"


def devices(skip: tuple[str, ...] | None = None) -> list[Device]:
    """枚举设备树，问题设备排在前面。"""
    if not IS_WIN:
        return []
    classes = _class_names()
    skipped = tuple(s.upper() for s in (SKIP_ENUM if skip is None else skip))
    out: list[Device] = []
    for enum in _subkeys(HKLM, ENUM_ROOT):
        if enum.upper() in skipped:
            continue
        enum_path = ENUM_ROOT + "\\" + enum
        for hwid in _subkeys(HKLM, enum_path):
            hw_path = enum_path + "\\" + hwid
            for inst in _subkeys(HKLM, hw_path):
                base = hw_path + "\\" + inst
                name = dev_text(_reg(HKLM, base, "FriendlyName")) or \
                    dev_text(_reg(HKLM, base, "DeviceDesc"))
                if not name:
                    continue                     # 名字取不出来的节点没有展示价值
                inst_id = "%s\\%s\\%s" % (enum, hwid, inst)
                flags, problem, ok = node_status(inst_id)
                guid = str(_reg(HKLM, base, "ClassGUID") or "").upper()
                d = Device(name=name,
                           cls=classes.get(guid, ""),
                           vendor=dev_text(_reg(HKLM, base, "Mfg")),
                           driver=str(_reg(HKLM, base, "Service") or ""),
                           instance=inst_id)
                _apply_state(d, flags, problem, ok)
                out.append(d)
    out.sort(key=_sort_key)
    return out


def _sort_key(d: Device):
    rank = {"crit": 0, "warn": 1, "info": 2, "ok": 3}
    return (rank.get(d.level, 4), not d.present, d.cls, d.name.lower())


def problem_devices(items: list[Device] | None = None) -> list[Device]:
    """需要看一眼的设备：有问题的 + 被禁用的，**不含未连接**。

    未连接单独统计 —— 一台笔记本动不动就十几个未连接，
    混进「异常」里只会把真正的问题淹掉。
    """
    src = devices() if items is None else items
    return [d for d in src if d.present and d.level in ("crit", "warn")]


# ------------------------------------------------------------------ 驱动
def _date_ts(text: str) -> float:
    """`DriverDate` 是 `6-21-2006` 这种美式写法，按几种格式兜底解析。"""
    s = (text or "").strip()
    if not s:
        return 0.0
    for fmt in ("%m-%d-%Y", "%Y-%m-%d", "%m/%d/%Y", "%Y/%m/%d"):
        try:
            return time.mktime(time.strptime(s, fmt))
        except (ValueError, OverflowError):
            continue
    return 0.0


def drivers() -> list[Driver]:
    """已安装的驱动（来自 Control\\Class 下的 0000/0001… 子键）。

    同一个 INF 会为每台设备各存一份（本机 8 个 USB Root Hub 就是 8 条），
    这里按「描述 + 版本 + INF」合并，另用 instances 记住合并了几条。
    """
    if not IS_WIN:
        return []
    classes = _class_names()
    merged: dict[tuple, Driver] = {}
    for guid in _subkeys(HKLM, CLASS_ROOT):
        guid_path = CLASS_ROOT + "\\" + guid
        cls_name = classes.get(guid.upper(), "")
        for num in _subkeys(HKLM, guid_path):
            if not num.isdigit():
                continue
            base = guid_path + "\\" + num
            desc = _reg(HKLM, base, "DriverDesc")
            if not desc:
                continue
            inf = str(_reg(HKLM, base, "InfPath") or "")
            version = str(_reg(HKLM, base, "DriverVersion") or "")
            name = dev_text(desc)
            key = (name.lower(), version, inf.lower())
            if key in merged:
                merged[key].instances += 1
                continue
            date = str(_reg(HKLM, base, "DriverDate") or "")
            provider = str(_reg(HKLM, base, "ProviderName") or "")
            merged[key] = Driver(
                desc=name, cls=cls_name, version=version, date=date,
                ts=_date_ts(date), provider=provider, inf=inf,
                third_party=bool(provider) and "microsoft" not in provider.lower(),
            )
    out = list(merged.values())
    out.sort(key=lambda x: (-x.ts, x.desc.lower()))
    return out


def link_devices(items: list[Device], drvs: list[Driver]) -> None:
    """标出「这个 INF 实际挂着几台设备」。

    没有设备引用的驱动项多半是旧版本残留，是升级/换驱动后出问题的常见嫌疑。
    匹配用「INF 文件名去掉 .inf == 设备的 Service 名」（RT640x64 / rt640x64.inf），
    只认同名，不做包含匹配 —— 包含匹配会把 usb.inf 挂到几十台设备上，等于没说。
    """
    svc_count: dict[str, int] = {}
    for dev in items:
        key = (dev.driver or "").lower()
        if key:
            svc_count[key] = svc_count.get(key, 0) + 1
    for d in drvs:
        stem = (d.inf or "").lower().rsplit(".", 1)[0]
        d.devices = svc_count.get(stem, 0)


OLD_DAYS = 365 * 3


def stats(items: list[Device] | None = None,
          drvs: list[Driver] | None = None) -> Stats:
    devs = devices() if items is None else items
    drv = drivers() if drvs is None else drvs
    st = Stats(devices=len(devs), drivers=len(drv))
    now = time.time()
    for d in devs:
        if d.status == "已移除":
            st.removed += 1
        elif not d.present:
            st.absent += 1
        elif d.problem == 22:
            st.disabled += 1
        elif d.level in ("crit", "warn"):
            st.problems += 1
    st.third_party = sum(1 for d in drv if d.third_party)
    # 只统计第三方驱动的「年龄」：微软收件箱驱动的日期普遍是 2006 年，
    # 算进来就变成「390 个驱动三年没更新」这种没人会看的数字
    st.old_drivers = sum(1 for d in drv
                         if d.third_party and d.ts
                         and (now - d.ts) > OLD_DAYS * 86400)
    st.rows = [
        f"共 {st.devices} 台设备 / {st.drivers} 个驱动",
        f"异常 {st.problems} 台 · 已禁用 {st.disabled} 台 · 未连接 {st.absent} 台"
        + (f" · 已移除残留 {st.removed} 台" if st.removed else ""),
        f"第三方驱动 {st.third_party} 个 · 其中 3 年未更新 {st.old_drivers} 个",
    ]
    return st
