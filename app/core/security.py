# -*- coding: utf-8 -*-
"""安全基线：只读体检，只报「现在是什么状态」，不改任何设置。

**为什么只读**：安全项一旦自动「修」，等于替用户改了防火墙、UAC、SMB 这些
会影响业务连通性的东西，出了问题没人能复盘。所以这里只给现状 + 风险说明 +
该去哪儿改。

| 项 | 取数 | 判据 |
|---|---|---|
| Defender 实时保护 | `Get-MpComputerStatus` + SecurityCenter2 在册杀软 | 无杀软接管且关闭 → 风险 |
| 防火墙三个配置文件 | `FirewallPolicy\\{Domain,Standard,Public}Profile` | 任一关闭 → 警告 |
| UAC | `Policies\\System` 的 `EnableLUA` / `ConsentPromptBehaviorAdmin` | 关闭 → 严重 |
| SMBv1 | `LanmanServer\\Parameters` / `mrxsmb10` | 启用 → 严重（勒索软件主入口） |
| 远程桌面 | `Terminal Server` 的 `fDenyTSConnections` | 允许 → 提示 |
| 自动登录明文密码 | `Winlogon` 的 `AutoAdminLogon` / `DefaultPassword` | 有明文 → 严重 |
| 匿名 SMB 会话 | `LanmanServer\\Parameters` 的 `RestrictNullSessAccess` | 允许 → 提示 |
| 安全启动 / TPM | 复用 `upgrade` | 未启用 → 提示 |
| 系统支持期 | `winver` | Win10 已于 2025-10-14 停止支持 |
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field

from . import winver
from .disk import _run_ps
from .upgrade import secure_boot, tpm_info

IS_WIN = os.name == "nt"
HKLM = 0x80000002

FW_BASE = (r"SYSTEM\CurrentControlSet\Services\SharedAccess\Parameters"
           r"\FirewallPolicy")
UAC_KEY = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System"
WINLOGON = r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon"
LANMAN = r"SYSTEM\CurrentControlSet\Services\LanmanServer\Parameters"
MRX = r"SYSTEM\CurrentControlSet\Services\mrxsmb10"
TERMSRV = r"SYSTEM\CurrentControlSet\Control\Terminal Server"
DEFENDER_RTP = (r"SOFTWARE\Microsoft\Windows Defender\Real-Time Protection")

LEVEL_CN = {"ok": "正常", "info": "提示", "warn": "注意", "crit": "风险"}
LEVEL_ORDER = {"crit": 0, "warn": 1, "info": 2, "ok": 3}


@dataclass
class Finding:
    name: str
    value: str
    level: str = "ok"          # ok / info / warn / crit
    detail: str = ""
    where: str = ""            # 去哪儿改（只给指引，不自动改）

    @property
    def level_text(self) -> str:
        return LEVEL_CN.get(self.level, self.level)


@dataclass
class Report:
    rows: list[Finding] = field(default_factory=list)
    elapsed: float = 0.0

    @property
    def risks(self) -> list[Finding]:
        return [r for r in self.rows if r.level == "crit"]

    @property
    def warns(self) -> list[Finding]:
        return [r for r in self.rows if r.level == "warn"]

    @property
    def score(self) -> int:
        """0~100。只做「给人一个概览」用，不是安全评级。"""
        s = 100
        for r in self.rows:
            s -= {"crit": 18, "warn": 7, "info": 0, "ok": 0}.get(r.level, 0)
        return max(0, min(100, s))

    @property
    def headline(self) -> str:
        if self.risks:
            return "发现 %d 项风险：%s" % (
                len(self.risks), "、".join(r.name for r in self.risks))
        if self.warns:
            return "未发现高风险项，%d 项建议核对" % len(self.warns)
        return "常见安全基线项都在正常状态"


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


def _int(v, default=None):
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


_PS_SECURITY = (
    "[Console]::OutputEncoding=[Text.Encoding]::UTF8;"
    "$o=@{};"
    "try{$m=Get-MpComputerStatus -ErrorAction Stop;"
    "$o.defender=@{ok=$true;av=[bool]$m.AntivirusEnabled;"
    "rt=[bool]$m.RealTimeProtectionEnabled;sig=[int]$m.AntivirusSignatureAge}}catch{};"
    "try{$o.av=@(Get-CimInstance -Namespace root/SecurityCenter2 "
    "-ClassName AntiVirusProduct -ErrorAction Stop | ForEach-Object {"
    "@{name=[string]$_.displayName;state=[int]$_.productState}})}catch{};"
    "$o|ConvertTo-Json -Compress")


def _ps_security() -> dict:
    raw = (_run_ps(_PS_SECURITY, timeout=20) or "").strip()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def active_av(ps: dict) -> list[str]:
    """SecurityCenter2 里**当前启用**的杀毒产品名。

    `productState` 的 0x1000 位表示「已启用」，这是各厂商通用的编码，
    比按名字猜「装没装 360」靠谱。
    """
    out = []
    for item in (ps.get("av") or []):
        if not isinstance(item, dict):
            continue
        try:
            state = int(item.get("state") or 0)
        except (TypeError, ValueError):
            state = 0
        if state & 0x1000:
            out.append(str(item.get("name") or "未知安全软件"))
    return out


# ------------------------------------------------------------------ 各项
def _defender(ps: dict) -> Finding:
    others = [n for n in active_av(ps) if "defender" not in n.lower()]
    d = ps.get("defender")
    if isinstance(d, dict) and d.get("ok"):
        rt = bool(d.get("rt"))
        sig = _int(d.get("sig"), 0) or 0
        if not d.get("av"):
            if others:
                # 装了第三方杀软时 Defender 会自己退场，这是**正常**状态，
                # 直接报「杀毒已关闭」会把一半装了 360/火绒的机器误判成高风险
                return Finding("Defender 防病毒", "已让位给 %s" % others[0], "ok",
                               "本机由第三方安全软件接管", "")
            return Finding("Defender 防病毒", "已关闭", "crit",
                           "杀毒功能被关闭，且没有第三方安全软件接管",
                           "Windows 安全中心 › 病毒和威胁防护")
        if not rt:
            return Finding("Defender 实时保护", "已关闭（防病毒本身开启）", "warn",
                           "实时保护关闭时，下载/解压的样本不会被即时拦截",
                           "Windows 安全中心 › 病毒和威胁防护 › 管理设置")
        lvl = "warn" if sig > 7 else "ok"
        return Finding("Defender 实时保护",
                       "已开启 · 病毒库 %d 天前" % sig, lvl,
                       "病毒库超过 7 天未更新" if sig > 7 else "",
                       "Windows 安全中心 › 检查更新" if sig > 7 else "")
    if others:
        return Finding("Defender 防病毒", "第三方接管：%s" % others[0], "ok", "", "")
    # 回退：读注册表（装了第三方杀软时 Defender 会自己退场，读到关闭是正常的）
    dis = _int(_reg(HKLM, DEFENDER_RTP, "DisableRealtimeMonitoring"))
    if dis is None:
        return Finding("Defender 实时保护", "未检测到（可能由第三方安全软件接管）",
                       "info", "本机读不到 Defender 状态，请确认已装其它杀毒软件",
                       "Windows 安全中心")
    if dis:
        return Finding("Defender 实时保护", "已关闭", "warn",
                       "实时保护被策略或设置关闭", "Windows 安全中心")
    return Finding("Defender 实时保护", "已开启", "ok", "", "")


def _firewall() -> Finding:
    rows = []
    off = []
    for key, name in (("DomainProfile", "域"), ("StandardProfile", "专用"),
                      ("PublicProfile", "公用")):
        v = _int(_reg(HKLM, FW_BASE + "\\" + key, "EnableFirewall"))
        on = v == 1
        rows.append("%s%s" % (name, "开" if on else "关"))
        if not on:
            off.append(name)
    if not rows:
        return Finding("防火墙", "未能读取", "info", "请手动确认防火墙状态",
                       "控制面板 › Windows Defender 防火墙")
    value = " · ".join(rows)
    note = "注册表读到的设置；如由组策略统一管理，以策略为准"
    if off:
        if len(off) == 3:
            return Finding("防火墙", value, "crit",
                           "域、专用、公用 三个配置文件的防火墙都已关闭；" + note,
                           "Windows 安全中心 › 防火墙和网络保护")
        return Finding("防火墙", value, "warn",
                       "%s 配置文件的防火墙已关闭；%s" % ("、".join(off), note),
                       "Windows 安全中心 › 防火墙和网络保护")
    return Finding("防火墙", value, "ok", "", "")


def _uac() -> Finding:
    lua = _int(_reg(HKLM, UAC_KEY, "EnableLUA"))
    prompt = _int(_reg(HKLM, UAC_KEY, "ConsentPromptBehaviorAdmin"))
    if lua is None:
        return Finding("UAC 用户账户控制", "未能读取", "info", "", "")
    if not lua:
        return Finding("UAC 用户账户控制", "已关闭", "crit",
                       "关闭 UAC 后任意程序都能静默提权，是最常被利用的配置",
                       "控制面板 › 用户账户 › 更改用户账户控制设置")
    if prompt == 0:
        return Finding("UAC 用户账户控制", "开启，但管理员提权不提示", "warn",
                       "ConsentPromptBehaviorAdmin=0：提权时不弹确认框",
                       "控制面板 › 用户账户 › 更改用户账户控制设置")
    return Finding("UAC 用户账户控制", "已开启（管理员提权会提示）", "ok", "", "")


def _smb1() -> Finding:
    srv = _int(_reg(HKLM, LANMAN, "SMB1"))
    cli = _int(_reg(HKLM, MRX, "Start"))
    # SMB1 服务默认是「按需启动」(Start=3)；0/1/2 才算真的启用
    client_on = cli in (0, 1, 2)
    server_on = srv == 1
    if server_on or client_on:
        bits = []
        if server_on:
            bits.append("服务端已启用")
        if client_on:
            bits.append("客户端已启用")
        return Finding("SMBv1 协议", " · ".join(bits), "crit",
                       "SMBv1 是 WannaCry / 勒索软件的经典入口，现代环境不需要它",
                       "控制面板 › 程序和功能 › 启用或关闭 Windows 功能 › SMB 1.0")
    return Finding("SMBv1 协议", "已禁用", "ok", "", "")


def _rdp() -> Finding:
    deny = _int(_reg(HKLM, TERMSRV, "fDenyTSConnections"))
    if deny is None:
        return Finding("远程桌面", "未能读取", "info", "", "")
    if deny == 0:
        return Finding("远程桌面", "已允许远程连接", "warn",
                       "暴露 3389 端口会被互联网上的爆破脚本持续扫描；"
                       "只在需要时开启，并限制来源 IP",
                       "设置 › 系统 › 远程桌面")
    return Finding("远程桌面", "已关闭远程连接", "ok", "", "")


def _autologon() -> Finding:
    auto = _reg(HKLM, WINLOGON, "AutoAdminLogon")
    pwd = _reg(HKLM, WINLOGON, "DefaultPassword")
    if str(auto) == "1" and pwd:
        return Finding("自动登录", "已开启，且在注册表里存有明文密码", "crit",
                       "DefaultPassword 是明文，任何能读注册表的程序都能拿到；"
                       "确需自动登录请改用 LsaStorePrivateData 或 Autologon 工具",
                       "控制面板 › 用户账户 › netplwiz")
    if str(auto) == "1":
        return Finding("自动登录", "已开启（未发现明文密码）", "warn",
                       "自动登录会跳过登录界面，物理接触即可进入桌面",
                       "控制面板 › 用户账户 › netplwiz")
    return Finding("自动登录", "未开启", "ok", "", "")


def _null_session() -> Finding:
    restrict = _int(_reg(HKLM, LANMAN, "RestrictNullSessAccess"))
    if restrict is None:
        return Finding("匿名 SMB 会话", "默认策略（未显式设置）", "info",
                       "系统默认允许匿名枚举共享名，需要更严可设为 1", "")
    if not restrict:
        return Finding("匿名 SMB 会话", "允许匿名访问共享", "warn",
                       "RestrictNullSessAccess=0：未认证用户可枚举共享与用户",
                       "本地安全策略 › 安全选项 › 不允许 SAM 账户的匿名枚举")
    return Finding("匿名 SMB 会话", "已限制匿名访问", "ok", "", "")


def _secure_boot() -> Finding:
    sb = secure_boot()
    if sb.startswith("已开启"):
        return Finding("安全启动", sb, "ok", "", "")
    if sb.startswith("已关闭"):
        return Finding("安全启动", sb, "info", "开启后能拦住未签名的引导层恶意程序",
                       "BIOS / UEFI 设置")
    return Finding("安全启动", sb, "info",
                   "固件非 UEFI 或未启用安全启动", "BIOS / UEFI 设置")


def _tpm() -> Finding:
    t = tpm_info()
    if not t["present"]:
        return Finding("TPM", "未检测到", "info",
                       "BitLocker 与部分凭据保护需要 TPM", "BIOS / UEFI 设置")
    spec = str(t["spec"] or "").split(",")[0].strip() or "?"
    enabled = "已启用" if t["enabled"] else "未启用"
    return Finding("TPM", "TPM %s（%s）" % (spec, enabled),
                   "ok" if t["enabled"] else "info",
                   "" if t["enabled"] else "启用后可支持 BitLocker / 凭据保护",
                   "BIOS / UEFI 设置")


def _support() -> Finding:
    ver = winver.current()
    if ver.is_11:
        return Finding("系统支持期", "%s（仍在支持期）" % ver.full_name, "ok", "", "")
    if ver.build >= 19045:
        return Finding("系统支持期", "%s — 已于 2025-10-14 结束支持" % ver.full_name,
                       "warn",
                       "Win10 已停止安全更新，长期联网使用建议升级；"
                       "内网/工控场景按实际评估", "设置 › Windows 更新")
    return Finding("系统支持期", "%s（版本过旧）" % ver.full_name, "crit",
                   "系统版本早于 22H2，缺少近年所有安全修复",
                   "安装最新累积更新或升级系统")


def scan() -> Report:
    """跑一遍安全基线（只读）。约 1~2 秒，其中 WMI 查询占大头。"""
    import time
    t0 = time.perf_counter()
    ps = _ps_security()
    rep = Report()
    rep.rows = [
        _defender(ps),
        _firewall(),
        _uac(),
        _smb1(),
        _rdp(),
        _autologon(),
        _null_session(),
        _secure_boot(),
        _tpm(),
        _support(),
    ]
    rep.rows.sort(key=lambda r: LEVEL_ORDER.get(r.level, 9))
    rep.elapsed = time.perf_counter() - t0
    return rep
