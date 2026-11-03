# -*- coding: utf-8 -*-
"""响应速度微调：把常见的注册表/电源微调做成可开关的清单。

四条硬规则
1. **只改值，不删键**：每项只动一个值名，键本身留着
2. **改前先记原值**：`tweaks.json` 里存 exists/type/value，还原时优先照原值写回，
   原值本来不存在的就删掉这个值 —— 保证还原后与动手前一模一样
3. **不碰安全策略**：Defender、更新、UAC 一概不做，避免变成"优化大师"
4. **按系统版本分流**：每项都带 `os` 标注（any / win10 / win11）。Win11 的
   「任务栏小组件」在 Win10 上没有对应值，Win10 的「时间线」在 Win11 已被移除，
   硬套只会给出点了没反应的开关。
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from dataclasses import dataclass

from . import winver

IS_WIN = os.name == "nt"
CREATE_NO_WINDOW = 0x08000000

HKLM = "HKLM"
HKCU = "HKCU"
_HIVE = {"HKLM": 0x80000002, "HKCU": 0x80000001}

REG_SZ = "REG_SZ"
REG_DWORD = "REG_DWORD"

CONFIG = "%LOCALAPPDATA%\\SystemToolbox\\backup\\tweaks.json"

POWER_HIGH = "8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c"
POWER_BALANCED = "381b4222-f694-41f0-9685-ff5bb260df2e"

OS_TAGS = ("any", "win10", "win11")
OS_CN = {"any": "全部", "win10": "Win10", "win11": "Win11"}


@dataclass
class Tweak:
    key: str
    name: str
    desc: str
    risk: str = "低"
    admin: bool = False
    reboot: str = ""
    hive: str = HKCU
    path: str = ""
    value: str = ""
    vtype: str = REG_SZ
    on: object = None
    off: object = None          # None = 还原时删除这个值
    kind: str = "reg"           # reg / power
    os: str = "any"             # any / win10 / win11
    min_build: int = 0          # 最低 build 要求
    os_note: str = ""           # 不适用时的说明（空则自动生成）

    # ---- 版本适配
    def applies_to(self, ver: winver.WinVer | None = None) -> bool:
        v = ver or winver.current()
        return v.supports(self.os, self.min_build)

    def why_not(self, ver: winver.WinVer | None = None) -> str:
        v = ver or winver.current()
        if self.applies_to(v):
            return ""
        return self.os_note or v.why_not(self.os, self.min_build)

    @property
    def os_text(self) -> str:
        return OS_CN.get(self.os, self.os)

    # ---- 状态
    @property
    def supported(self) -> bool:
        return self.kind in ("reg", "power")

    def state(self) -> str:
        """返回 已优化 / 未优化 / 不适用 / 未知。"""
        if not self.applies_to():
            return "不适用"
        if self.kind == "power":
            cur = current_power_guid()
            if not cur:
                return "未知"
            return "已优化" if cur == POWER_HIGH else "未优化"
        if not IS_WIN:
            return "未知"
        exists, val, _typ = read_value(self.hive, self.path, self.value)
        if not exists:
            return "未优化" if self.off is None else (
                "已优化" if self.off == self.on else "未优化")
        return "已优化" if _same(val, self.on, self.vtype) else "未优化"

    def current_text(self) -> str:
        if self.kind == "power":
            return {"high": "高性能", "balanced": "平衡"}.get(
                current_power_name(), current_power_name() or "未知")
        exists, val, _typ = read_value(self.hive, self.path, self.value)
        if not exists:
            return "未设置"
        return str(val)


def _same(a, b, vtype: str) -> bool:
    try:
        if vtype == REG_DWORD:
            return int(a) == int(b)
        return str(a) == str(b)
    except (TypeError, ValueError):
        return False


# ------------------------------------------------------------------ 注册表
def read_value(hive: str, path: str, name: str) -> tuple[bool, object, str]:
    """返回 (是否存在, 值, 类型名)。"""
    if not IS_WIN:
        return (False, None, "")
    import winreg
    try:
        k = winreg.OpenKey(_HIVE[hive], path, 0, winreg.KEY_READ)
    except OSError:
        return (False, None, "")
    try:
        val, typ = winreg.QueryValueEx(k, name)
        tname = {winreg.REG_SZ: REG_SZ, winreg.REG_EXPAND_SZ: REG_SZ,
                 winreg.REG_DWORD: REG_DWORD}.get(typ, str(typ))
        return (True, val, tname)
    except OSError:
        return (False, None, "")
    finally:
        k.Close()


def _write_value(hive: str, path: str, name: str, value, vtype: str) -> tuple[bool, str]:
    if not IS_WIN:
        return (False, "非 Windows 平台")
    import winreg
    try:
        k = winreg.CreateKeyEx(_HIVE[hive], path, 0, winreg.KEY_SET_VALUE)
    except PermissionError:
        return (False, "需要管理员权限")
    except OSError as exc:
        return (False, f"打开注册表键失败：{exc}")
    try:
        typ = winreg.REG_DWORD if vtype == REG_DWORD else winreg.REG_SZ
        val = int(value) if vtype == REG_DWORD else str(value)
        winreg.SetValueEx(k, name, 0, typ, val)
        return (True, "")
    except PermissionError:
        return (False, "需要管理员权限")
    except OSError as exc:
        return (False, f"写入失败：{exc}")
    finally:
        k.Close()


def _delete_value(hive: str, path: str, name: str) -> tuple[bool, str]:
    if not IS_WIN:
        return (False, "非 Windows 平台")
    import winreg
    try:
        k = winreg.OpenKey(_HIVE[hive], path, 0, winreg.KEY_SET_VALUE)
    except FileNotFoundError:
        return (True, "")
    except PermissionError:
        return (False, "需要管理员权限")
    except OSError as exc:
        return (False, f"打开注册表键失败：{exc}")
    try:
        winreg.DeleteValue(k, name)
        return (True, "")
    except FileNotFoundError:
        return (True, "")
    except PermissionError:
        return (False, "需要管理员权限")
    except OSError as exc:
        return (False, f"删除失败：{exc}")
    finally:
        k.Close()


# ------------------------------------------------------------------ 电源
def _powercfg(args: list[str]) -> tuple[int, str]:
    if not IS_WIN:
        return (1, "非 Windows 平台")
    try:
        p = subprocess.run(["powercfg"] + args, capture_output=True, timeout=15,
                           creationflags=CREATE_NO_WINDOW)
    except Exception as exc:                    # noqa: BLE001
        return (1, str(exc))
    raw = p.stdout or b""
    for enc in ("gbk", "utf-8"):
        try:
            return (p.returncode, raw.decode(enc))
        except (UnicodeDecodeError, LookupError):
            continue
    return (p.returncode, "")


def current_power_guid() -> str:
    rc, out = _powercfg(["/getactivescheme"])
    if rc != 0:
        return ""
    import re
    m = re.search(r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})",
                  out, re.I)
    return m.group(1).lower() if m else ""


def current_power_name() -> str:
    guid = current_power_guid()
    return {POWER_HIGH: "high", POWER_BALANCED: "balanced"}.get(guid, "")


# ------------------------------------------------------------------ 清单
# os 字段决定「这一项在哪代系统上才有意义」，不是可选项：
# - Win10 专属项在 Win11 上值都不存在（实测 ShowCortanaButton / EnableActivityFeed /
#   TaskbarNoThumbnail 在 Win11 26H2 全部 VALUE_MISSING），点了也不会有反应
# - Win11 专属项在 Win10 上同理（TaskbarDa / TaskbarMn / Start_IrisRecommendations）
TWEAKS: list[Tweak] = [
    Tweak("menu_delay", "菜单展开延迟", "开始菜单、右键菜单弹出前的 400ms 等待改成立即弹出",
          hive=HKCU, path=r"Control Panel\Desktop", value="MenuShowDelay",
          vtype=REG_SZ, on="0", off="400", reboot="资源管理器或重新登录后生效"),
    Tweak("hover_time", "鼠标悬停响应", "任务栏预览、工具提示的悬停等待从 400ms 降到 100ms",
          hive=HKCU, path=r"Control Panel\Desktop", value="MouseHoverTime",
          vtype=REG_SZ, on="100", off="400", reboot="重新登录后生效"),
    Tweak("min_animate", "窗口缩放动画", "关闭最小化 / 还原时的窗口动画，切换更干脆",
          hive=HKCU, path=r"Control Panel\Desktop\WindowMetrics", value="MinAnimate",
          vtype=REG_SZ, on="0", off="1", reboot="重新登录后生效"),
    Tweak("startup_delay", "登录后启动项不延迟",
          "取消登录后等待 10 秒才拉起启动项的机制（Serialize 键默认不存在）",
          hive=HKCU,
          path=r"Software\Microsoft\Windows\CurrentVersion\Explorer\Serialize",
          value="StartupDelayInMSec", vtype=REG_DWORD, on=0, off=None,
          reboot="重新登录后生效"),
    Tweak("transparency", "关闭透明效果",
          "关掉开始菜单 / 任务栏的毛玻璃与亚克力，核显、远程桌面、"
          "多屏高分下拖窗口更跟手（同时省一点 GPU）",
          hive=HKCU, path=r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
          value="EnableTransparency", vtype=REG_DWORD, on=0, off=1,
          reboot="资源管理器重启后生效"),
    Tweak("bing_search", "开始菜单搜索不查网络",
          "搜索结果只来自本机，不再附加 Bing 网页结果（更快、不外发关键词）",
          hive=HKCU, path=r"Software\Microsoft\Windows\CurrentVersion\Search",
          value="BingSearchEnabled", vtype=REG_DWORD, on=0, off=1,
          reboot="重启资源管理器后生效"),
    Tweak("wer_ui", "崩溃时不弹错误报告窗口",
          "程序崩溃后不再弹「已停止工作」对话框，售后机器上少一道打扰",
          hive=HKCU, path=r"Software\Microsoft\Windows\Windows Error Reporting",
          value="DontShowUI", vtype=REG_DWORD, on=1, off=0),
    # 注意位置：Win10/11 都只读 HKCU 这一份。HKLM\SYSTEM\CurrentControlSet\Control
    # 下的同名值是 NT 时代遗留，实测 Win11 26H2 上根本不存在，写过去等于白写。
    Tweak("hung_app", "更快结束无响应程序",
          "系统判定「程序无响应」的等待时间缩短到 2 秒，卡死窗口更快出现「结束程序」",
          risk="低",
          hive=HKCU, path=r"Control Panel\Desktop", value="HungAppTimeout",
          vtype=REG_SZ, on="2000", off="5000"),
    Tweak("wait_kill", "关机等待服务超时",
          "关机时等待服务退出的上限 5s → 3s，关机更快（个别服务可能被强杀；"
          "现值已比 3s 更小则不必动）",
          risk="中", admin=True,
          hive=HKLM, path=r"SYSTEM\CurrentControlSet\Control",
          value="WaitToKillServiceTimeout", vtype=REG_SZ, on="3000", off="5000"),
    Tweak("long_paths", "启用长路径支持",
          "解除 260 字符路径限制，深层目录不再报「路径过长」（部分老程序不兼容）",
          risk="中", admin=True, reboot="重启系统后生效", min_build=14393,
          hive=HKLM, path=r"SYSTEM\CurrentControlSet\Control\FileSystem",
          value="LongPathsEnabled", vtype=REG_DWORD, on=1, off=0),
    Tweak("power_high", "切到高性能电源计划",
          "CPU 不再降频省电，笔记本会明显更耗电、更热",
          risk="中", kind="power"),

    # ---------- Win11 专属
    Tweak("w11_widgets", "隐藏任务栏「小组件」",
          "Win11 任务栏左侧的资讯小组件入口，隐藏后不再常驻拉取内容",
          hive=HKCU, path=r"Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced",
          value="TaskbarDa", vtype=REG_DWORD, on=0, off=1, os="win11",
          reboot="重启资源管理器后生效",
          os_note="Win10 没有「小组件」（只有新闻和兴趣），该项仅 Win11 适用"),
    Tweak("w11_chat", "隐藏任务栏「聊天」",
          "Win11 内置的 Teams 聊天入口，隐藏后不再随系统启动",
          hive=HKCU, path=r"Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced",
          value="TaskbarMn", vtype=REG_DWORD, on=0, off=1, os="win11",
          reboot="重启资源管理器后生效",
          os_note="该项是 Win11 的「聊天」入口，Win10 无此按钮"),
    Tweak("w11_start_rec", "收起开始菜单「推荐」",
          "关掉推荐区域，开始菜单更短；24H2 之后该区域改叫「最近使用」，"
          "部分版本由设置页接管",
          hive=HKCU, path=r"Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced",
          value="Start_IrisRecommendations", vtype=REG_DWORD, on=0, off=1,
          os="win11", min_build=22621,
          reboot="重启资源管理器后生效",
          os_note="「推荐」区域是 Win11 22H2 起才有的，Win10 开始菜单没有这块"),

    # ---------- Win10 专属
    Tweak("w10_cortana", "隐藏任务栏 Cortana 按钮",
          "去掉任务栏上的 Cortana 圆环，腾一个图标位（不影响搜索）",
          hive=HKCU, path=r"Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced",
          value="ShowCortanaButton", vtype=REG_DWORD, on=0, off=1, os="win10",
          reboot="重启资源管理器后生效",
          os_note="Cortana 按钮是 Win10 的形态，Win11 已把它并进搜索"),
    Tweak("w10_timeline", "关闭任务视图「时间线」",
          "不再记录你在每个应用里开过的活动，任务视图只剩窗口列表",
          hive=HKCU, path=r"Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced",
          value="EnableActivityFeed", vtype=REG_DWORD, on=0, off=1, os="win10",
          reboot="重新登录后生效",
          os_note="时间线（Timeline）已在 Win11 移除，这项只对 Win10 有意义"),
    Tweak("w10_activity", "停止上传活动历史",
          "关闭「允许 Windows 上传我的活动历史」，活动记录不再跨设备同步",
          hive=HKCU, path=r"Software\Microsoft\Windows\CurrentVersion\Privacy",
          value="PublishUserActivities", vtype=REG_DWORD, on=0, off=1, os="win10",
          reboot="重新登录后生效",
          os_note="活动历史同步是 Win10 的功能，Win11 已取消"),
    Tweak("w10_no_thumb", "关闭任务栏缩略图预览",
          "鼠标悬停任务栏不再弹出窗口预览，只看得到标题",
          hive=HKCU, path=r"Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced",
          value="TaskbarNoThumbnail", vtype=REG_DWORD, on=1, off=0, os="win10",
          reboot="重启资源管理器后生效",
          os_note="Win11 任务栏由系统设置接管，这个值写进去不会生效"),
]


def by_key(key: str) -> Tweak | None:
    for t in TWEAKS:
        if t.key == key:
            return t
    return None


def usable(ver: winver.WinVer | None = None) -> list[Tweak]:
    """当前系统真正能用的项。"""
    v = ver or winver.current()
    return [t for t in TWEAKS if t.applies_to(v)]


def others(ver: winver.WinVer | None = None) -> list[Tweak]:
    """为另一代系统准备的项（界面灰显，点了也没用）。"""
    v = ver or winver.current()
    return [t for t in TWEAKS if not t.applies_to(v)]


def stats(ver: winver.WinVer | None = None) -> dict:
    """界面顶部的一句话统计。"""
    v = ver or winver.current()
    ok = usable(v)
    return {
        "total": len(TWEAKS),
        "usable": len(ok),
        "other": len(TWEAKS) - len(ok),
        "applied": sum(1 for t in ok if t.state() == "已优化"),
        "w10": sum(1 for t in TWEAKS if t.os == "win10"),
        "w11": sum(1 for t in TWEAKS if t.os == "win11"),
        "ver": v,
    }


# ------------------------------------------------------------------ 备份
def _config_path() -> str:
    p = os.path.expandvars(CONFIG)
    try:
        os.makedirs(os.path.dirname(p), exist_ok=True)
    except OSError:
        return ""
    return p


def load_backup() -> dict:
    p = _config_path()
    if not p or not os.path.isfile(p):
        return {}
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f) or {}
    except (OSError, ValueError):
        return {}


def _save_backup(data: dict) -> None:
    p = _config_path()
    if not p:
        return
    try:
        with open(p, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except OSError:
        pass


def _remember(t: Tweak) -> None:
    """首次改动前记录原始值（之后不再覆盖，保证还原到最初状态）。"""
    data = load_backup()
    if t.key in data:
        return
    if t.kind == "power":
        data[t.key] = {"kind": "power", "guid": current_power_guid(),
                       "saved": time.strftime("%Y-%m-%d %H:%M:%S")}
    else:
        exists, val, vtype = read_value(t.hive, t.path, t.value)
        data[t.key] = {"kind": "reg", "exists": exists,
                       "value": val if exists else None,
                       "type": vtype or t.vtype,
                       "saved": time.strftime("%Y-%m-%d %H:%M:%S")}
    _save_backup(data)


# ------------------------------------------------------------------ 应用
def apply(t: Tweak) -> tuple[bool, str]:
    if not t.applies_to():
        return (False, t.why_not() or "当前系统不适用")
    _remember(t)
    if t.kind == "power":
        rc, out = _powercfg(["/setactive", POWER_HIGH])
        return (True, "") if rc == 0 else (False, (out or "powercfg 执行失败").strip())
    return _write_value(t.hive, t.path, t.value, t.on, t.vtype)


def revert(t: Tweak) -> tuple[bool, str]:
    """还原：优先按备份里的原始值写回；没备份则删值回到系统出厂默认。

    没备份时**不硬写 `off`**：`off` 只是文档性的默认值，同一项在不同 build
    上默认值可能不同（例如 HungAppTimeout 在 Win11 上是 3000 而不是 5000）。
    删值让系统自己套用当前版本的默认，比猜一个数字安全。
    """
    data = load_backup().get(t.key) or {}
    if t.kind == "power":
        guid = data.get("guid") or POWER_BALANCED
        rc, out = _powercfg(["/setactive", guid])
        return (True, "") if rc == 0 else (False, (out or "powercfg 执行失败").strip())

    if data and data.get("kind") == "reg":
        if not data.get("exists"):
            return _delete_value(t.hive, t.path, t.value)
        return _write_value(t.hive, t.path, t.value,
                            data.get("value"), data.get("type") or t.vtype)
    return _delete_value(t.hive, t.path, t.value)


def apply_many(tweaks, progress=None) -> tuple[int, list[str]]:
    ok = 0
    errs: list[str] = []
    for i, t in enumerate(tweaks):
        if progress:
            progress(i, len(tweaks), t.name)
        if not t.applies_to():
            continue                      # 另一代系统的专属项，静默跳过
        good, msg = apply(t)
        if good:
            ok += 1
        else:
            errs.append(f"{t.name}：{msg}")
    if progress:
        progress(len(tweaks), len(tweaks), "完成")
    return (ok, errs)


def revert_all(progress=None) -> tuple[int, list[str]]:
    """把所有记录过备份的项还原（用于「一键还原」）。"""
    data = load_backup()
    targets = [t for t in TWEAKS if t.key in data]
    ok = 0
    errs: list[str] = []
    for i, t in enumerate(targets):
        if progress:
            progress(i, len(targets), t.name)
        good, msg = revert(t)
        if good:
            ok += 1
        else:
            errs.append(f"{t.name}：{msg}")
    if progress:
        progress(len(targets), len(targets), "完成")
    return (ok, errs)
