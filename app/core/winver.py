# -*- coding: utf-8 -*-
"""Windows 版本判定：Win10 / Win11 分支识别。

为什么单独一个模块：整个「系统优化」工作台都要按版本分支——

- 微调项要标注适用系统：Win11 的「任务栏小组件」在 Win10 上根本没有这个值，
  给了开关也点不出效果；Win10 的「时间线」在 Win11 已被微软移除。
- 服务建议里 Win11 独有的服务，不该在 Win10 机器上被当成「建议调优」。
- 界面要明确告诉用户「当前按哪个分支呈现、有几项是为另一版本准备的」。

**判定只认 build 号**：注册表 `ProductName` 在 Win11 上写的仍然是
`Windows 10 Pro`，照它判断会全线判错 —— 这是同类工具最常见的翻车点。
build ≥ 22000 才是 Win11。
"""
from __future__ import annotations

import os
from dataclasses import dataclass

IS_WIN = os.name == "nt"

CV_KEY = r"SOFTWARE\Microsoft\Windows NT\CurrentVersion"
HKLM = 0x80000002
KEY_WOW64_64KEY = 0x0100

WIN11_BASE = 22000

# build → 版本代号（微软从不在注册表里给出「22H2 对应 19045」这种映射）
WIN10_BUILDS = {
    10240: "1507", 10586: "1511", 14393: "1607", 15063: "1703",
    16299: "1709", 17134: "1803", 17763: "1809", 18362: "1903",
    18363: "1909", 19041: "2004", 19042: "20H2", 19043: "21H1",
    19044: "21H2", 19045: "22H2",
}
WIN11_BUILDS = {
    22000: "21H2", 22621: "22H2", 22631: "23H2",
    26100: "24H2", 26200: "25H2", 26300: "26H2",
}

# EditionID → 中文名。EditionID 大小写不统一（Professional / professional 都见过），
# 查之前一律小写。
EDITION_CN = {
    "professional": "专业版", "core": "家庭版",
    "corecountryspecific": "家庭中文版", "coresinglelanguage": "家庭单语言版",
    "enterprise": "企业版", "enterprises": "企业版 LTSC",
    "education": "教育版", "professionaleducation": "专业教育版",
    "professionalworkstation": "专业工作站版", "serverrdsh": "企业多会话版",
    "iotenterprise": "IoT 企业版", "iotenterprises": "IoT 企业版 LTSC",
    "enterpriseltssc": "企业版 LTSC", "serverstandard": "Server 标准版",
}

# 分支的通俗说法，界面直接用
FAMILY_CN = {"win10": "Windows 10", "win11": "Windows 11"}


def _codename_of(build: int) -> str:
    """build → 22H2 这类代号；落在两个已知 build 之间时取较低的那个。"""
    table = WIN11_BUILDS if build >= WIN11_BASE else WIN10_BUILDS
    best = ""
    for b in sorted(table):
        if build >= b:
            best = table[b]
        else:
            break
    return best


def _reg(name: str):
    if not IS_WIN:
        return None
    try:
        import winreg
    except ImportError:
        return None
    key = None
    for view in (KEY_WOW64_64KEY, 0):
        try:
            key = winreg.OpenKey(HKLM, CV_KEY, 0, winreg.KEY_READ | view)
            break
        except OSError:
            continue
    if key is None:
        return None
    try:
        return winreg.QueryValueEx(key, name)[0]
    except OSError:
        return None
    finally:
        key.Close()


@dataclass(frozen=True)
class WinVer:
    """一台机器的 Windows 版本。构造只走 detect()，不要手搓。"""

    major: int = 10               # 10 或 11
    build: int = 0
    ubr: int = 0
    display: str = ""             # 注册表 DisplayVersion，如 22H2
    edition_id: str = ""          # 注册表 EditionID，如 Professional

    # ---- 分支
    @property
    def family(self) -> str:
        return "win11" if self.major >= 11 else "win10"

    @property
    def is_11(self) -> bool:
        return self.major >= 11

    # ---- 文本
    @property
    def codename(self) -> str:
        return self.display or _codename_of(self.build)

    @property
    def edition(self) -> str:
        eid = (self.edition_id or "").lower()
        if eid in EDITION_CN:
            return EDITION_CN[eid]
        # EditionID 缺失时用 ProductName 的尾巴兜底，交给调用方
        return ""

    @property
    def build_text(self) -> str:
        if not self.build:
            return ""
        return f"{self.build}.{self.ubr}" if self.ubr else str(self.build)

    @property
    def full_name(self) -> str:
        """Windows 11 专业版"""
        base = f"Windows {self.major}"
        return f"{base} {self.edition}".strip() if self.edition else base

    @property
    def version_text(self) -> str:
        """26H2 · 26300.9711"""
        parts = [p for p in (self.codename, self.build_text) if p]
        return " · ".join(parts)

    @property
    def short(self) -> str:
        """Win11 26H2 —— 界面窄位置用"""
        tail = f" {self.codename}" if self.codename else ""
        return f"Win{self.major}{tail}"

    @property
    def label(self) -> str:
        """Windows 11 专业版 · 26H2 · 26300.9711"""
        return " · ".join(p for p in (self.full_name, self.version_text) if p)

    # ---- 适配判断
    def supports(self, tag: str = "any", min_build: int = 0) -> bool:
        """tag: any / win10 / win11；min_build 为最低 build 要求。"""
        if min_build and self.build and self.build < min_build:
            return False
        t = (tag or "any").strip().lower()
        if t in ("any", "*", ""):
            return True
        return t == self.family

    def why_not(self, tag: str = "any", min_build: int = 0) -> str:
        """不适用时的原因文本，界面直接显示。"""
        if self.supports(tag, min_build):
            return ""
        t = (tag or "any").strip().lower()
        if t in ("win10", "win11"):
            want = FAMILY_CN.get(t, t)
            return f"仅 {want} 可用（当前 {self.short}）"
        if min_build and self.build and self.build < min_build:
            return f"需 build ≥ {min_build}（当前 {self.build}）"
        return "当前系统不适用"


def detect(build: int | None = None, ubr: int | None = None,
           display: str | None = None, edition: str | None = None) -> WinVer:
    """读当前系统；参数可覆盖（单测用注入，不碰真机）。"""
    if build is None:
        try:
            build = int(_reg("CurrentBuild") or 0)
        except (TypeError, ValueError):
            build = 0
    if ubr is None:
        try:
            ubr = int(_reg("UBR") or 0)
        except (TypeError, ValueError):
            ubr = 0
    if display is None:
        display = str(_reg("DisplayVersion") or "").strip()
    if edition is None:
        edition = str(_reg("EditionID") or "").strip()
    major = 11 if (build or 0) >= WIN11_BASE else 10
    return WinVer(major=major, build=int(build or 0), ubr=int(ubr or 0),
                  display=display, edition_id=edition)


_cache: WinVer | None = None


def current(force: bool = False) -> WinVer:
    """带缓存：整页刷新会重复问几十次，注册表不必读那么多遍。"""
    global _cache
    if _cache is None or force:
        _cache = detect()
    return _cache


def reset_cache() -> None:
    """单测改注入值后调用。"""
    global _cache
    _cache = None
