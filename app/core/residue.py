# -*- coding: utf-8 -*-
"""卸载残留识别（置信度模型）。

思路：**先建立"已安装软件"的索引，再去文件系统里找对不上的目录**。

与注册表冗余的"硬证据"不同，这里只能给出概率，所以：
- 结果按置信度排序，每条都附判定理由（为什么认为它是残留）；
- 置信度 < 60 的一律不展示，宁可漏报；
- 界面默认**不勾选**任何一项，必须人工确认后才删；
- 删除走回收站，可还原。

扫描范围限定在四个用户数据/程序目录的 1~2 层，不碰系统目录。
"""
from __future__ import annotations

import difflib
import os
import re
import time
from dataclasses import dataclass, field

from . import registry_clean as reg

# 顶层目录白名单：系统自身与硬件厂商，直接跳过
WHITELIST = {
    "microsoft", "windows", "windowsapps", "windows nt", "windows defender",
    "common files", "internet explorer", "msbuild", "reference assemblies",
    "dotnet", "microsoft.net", "microsoft office", "microsoft vs",
    "intel", "amd", "nvidia", "nvidia corporation", "realtek", "qualcomm",
    "dell", "hp", "lenovo", "asus", "acer", "msi", "samsung", "toshiba",
    "broadcom", "marvell", "synaptics", "conexant", "waves", "dolby",
    "desktop.ini", "temp", "tmp", "cache", "logs", "packages", "programdata",
    "system32", "syswow64", "winsxs", "assembly", "servicing", "catroot",
    "driverstore", "inf", "installer", "fonts", "web", "debug", "ime",
    "connecteddevicesplatform", "d3dscache", "publisher", "publishers",
    "virtualstore", "placeholder", "comms", "history", "cef",
    "microsoft edge", "microsoft teams", "google", "mozilla",
    "cloudflare", "winstep", "crashdumps", "cls", "crashlogsuite",
    "templates", "uninstall information", "windowspowershell",
    "modifiablewindowsapps", "windows mail", "windows media player",
    "windows photo viewer", "windows portable devices", "windows sidebar",
    "windows journal", "windows security", "windows multimedia platform",
    "installshield installation information", "elevateddiagnostics",
    "isolatedstorage", "peerdistrepub", "application data", "start menu",
    "package cache", "nuget", "nuget cache", "pip", "npm-cache", "yarn",
    "deno", "cargo", "go-build", "gradle", "maven", "docker", "wsl",
}

# 路径片段黑名单：出现在路径里就直接放过（系统目录、共享组件仓库）
PATH_BLOCKLIST = (
    "\\windowsapps\\", "\\windows\\", "\\common files\\", "\\windows nt\\",
    "\\internet explorer\\", "\\windows defender\\", "\\modifiablewindowsapps\\",
    "\\reference assemblies\\", "\\installshield installation information\\",
    "\\uninstall information\\", "\\msbuild\\", "\\dotnet\\", "\\packages\\",
    "\\publishers\\", "\\connecteddevicesplatform\\", "\\d3dscache\\",
    "\\crashdumps\\", "\\placeholder\\", "\\virtualstore\\",
    "\\isolatedstorage\\", "\\elevateddiagnostics\\", "\\peerdistrepub\\",
    "\\temp\\", "\\wintemp\\", "\\windows media player\\",
    "\\windows mail\\", "\\windows photo viewer\\", "\\windows sidebar\\",
)

# 目录名里出现这些词，说明大概率是清理过后的残余
_JUNK_HINTS = ("uninstall", "backup", "old", "bak", "temp", "tmp", "cache",
               "leftover", "stub", "removed")

_CACHE_EXT = (".log", ".tmp", ".cache", ".db", ".dat", ".etl", ".dmp", ".old")

_STRIP_TOKENS = re.compile(
    r"\b(inc|ltd|llc|co|corp|corporation|gmbh|sa|bv|software|studio|technolog(y|ies)|"
    r"app|application|client|desktop|setup|installer|x64|x86|64|32|bit|pro|premium|"
    r"free|trial|version|v\d+)\b", re.I)


@dataclass
class Leftover:
    path: str
    name: str
    kind: str = "目录"           # 目录 / 文件
    size: int = 0
    files: int = 0
    mtime: float = 0.0
    confidence: int = 0
    reasons: list[str] = field(default_factory=list)
    category: str = "程序目录残留"

    @property
    def level(self) -> str:
        if self.confidence >= 82:
            return "高"
        if self.confidence >= 68:
            return "中"
        return "低"


def _norm(name: str) -> str:
    s = name.lower()
    s = re.sub(r"[\(\)\[\]（）【】\-_.]", " ", s)
    s = _STRIP_TOKENS.sub(" ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _similar(a: str, b: str) -> bool:
    a, b = _norm(a), _norm(b)
    if len(a) < 3 or len(b) < 3:
        return a == b
    if a == b:
        return True
    short, long_ = (a, b) if len(a) <= len(b) else (b, a)
    # 短名被长名完整包含（如 Photoshop ⊂ Adobe Photoshop 2021）视为同一产品；
    # 只对 5 字符以上的名字生效，避免 app / pro 这类词误命中
    if len(short) >= 5 and short in long_:
        return True
    return difflib.SequenceMatcher(None, a, b).ratio() >= 0.84


# ------------------------------------------------------------------ 已安装索引
@dataclass
class InstalledIndex:
    names: list[str] = field(default_factory=list)
    paths: list[str] = field(default_factory=list)
    count: int = 0

    def matches(self, name: str) -> bool:
        # normcase 在整个循环里是同一个值，提到外面算一次（候选目录动辄上千个）
        target = os.path.normcase(name).rstrip("\\")
        for p in self.paths:
            if not p:
                continue
            try:
                if os.path.normcase(p).rstrip("\\").endswith(target):
                    return True
            except (ValueError, OSError):
                continue
        return any(_similar(name, n) for n in self.names)


def installed_index(stop_flag=None) -> InstalledIndex:
    idx = InstalledIndex()
    if not reg.IS_WIN or reg.winreg is None:
        return idx
    seen: set[str] = set()
    for hive, base in reg.UNINSTALL_KEYS:
        for sub_name in reg._subkeys(hive, base):
            if stop_flag and stop_flag[0]:
                break
            try:
                k = reg._open(hive, f"{base}\\{sub_name}")
            except OSError:
                continue
            with k:
                display = reg._get_str(k, "DisplayName")
                loc = reg._get_str(k, "InstallLocation")
                publisher = reg._get_str(k, "Publisher")
            if display and display not in seen:
                seen.add(display)
                idx.names.append(display)
            if publisher and publisher not in seen:
                seen.add(publisher)
                idx.names.append(publisher)
            if loc:
                idx.paths.append(os.path.expandvars(loc.strip().strip('"')))
            # 反安装程序所在目录也是"在用"的证据
            for val in ("UninstallString", "DisplayIcon"):
                with k:
                    raw = reg._get_str(k, val)
                exe = reg.expand_path(raw)
                if exe and os.path.isabs(exe):
                    idx.paths.append(os.path.dirname(exe))
    idx.count = len(idx.names)
    return idx


# ------------------------------------------------------------------ 候选目录
@dataclass(frozen=True)
class ScanRoot:
    path: str
    category: str
    max_depth: int = 1


def default_roots() -> list[ScanRoot]:
    out = []
    pf = os.path.expandvars("%ProgramFiles%")
    pf86 = os.path.expandvars("%ProgramFiles(x86)%")
    pd = os.path.expandvars("%ProgramData%")
    la = os.path.expandvars("%LOCALAPPDATA%")
    ra = os.path.expandvars("%APPDATA%")
    for p in (pf, pf86):
        if "%" not in p and os.path.isdir(p):
            out.append(ScanRoot(p, "程序目录残留", max_depth=2))
    if "%" not in pd and os.path.isdir(pd):
        out.append(ScanRoot(pd, "公共数据残留", max_depth=1))
    if "%" not in la and os.path.isdir(la):
        out.append(ScanRoot(la, "用户缓存残留", max_depth=1))
    if "%" not in ra and os.path.isdir(ra):
        out.append(ScanRoot(ra, "用户配置残留", max_depth=1))
    return out


def _candidates(root: ScanRoot) -> list[tuple[str, str]]:
    """返回 (路径, 类别)。厂商/系统目录下钻一层继续找，其余直接当候选。"""
    out: list[tuple[str, str]] = []
    try:
        top = list(os.scandir(root.path))
    except OSError:
        return out
    for e in top:
        try:
            if not e.is_dir(follow_symlinks=False):
                continue
        except OSError:
            continue
        if e.name.startswith("$"):
            continue
        if not _blocked(e.path):
            out.append((e.path, root.category))
            continue
        # 白名单目录本身不报，但里面的具体产品目录可能是残留（如 Tencent\某个已卸产品）
        if e.name.lower() in WHITELIST and root.max_depth > 1:
            try:
                for sub in os.scandir(e.path):
                    if sub.is_dir(follow_symlinks=False) and not sub.name.startswith("$"):
                        if not _blocked(sub.path):
                            out.append((sub.path, root.category))
            except OSError:
                continue
    return out


def _has_exe(path: str, limit: int = 4000) -> tuple[int, int]:
    """(可执行文件数, 总文件数)，只看两层，够判断了。"""
    exes = 0
    total = 0
    base_depth = path.count(os.sep)
    stack = [path]
    while stack and total < limit:
        cur = stack.pop()
        try:
            it = os.scandir(cur)
        except OSError:
            continue
        with it:
            for e in it:
                total += 1
                if total >= limit:
                    break
                try:
                    if e.is_dir(follow_symlinks=False):
                        if e.path.count(os.sep) - base_depth < 2:
                            stack.append(e.path)
                        continue
                except OSError:
                    continue
                if e.name.lower().endswith((".exe", ".dll", ".sys")):
                    exes += 1
    return (exes, total)


def _has_reg_trace(name: str) -> bool:
    """HKCU / HKLM 的 Software 下还有同名子键 → 说明程序可能还在用。"""
    if reg.winreg is None:
        return False
    keys = ("Software", r"SOFTWARE\WOW6432Node")
    for hive in ("HKCU", "HKLM"):
        for base in keys:
            path = f"{base}\\{name}"
            try:
                k = reg._open(hive, path)
                k.Close()
                return True
            except OSError:
                continue
    return False


def _blocked(path: str) -> bool:
    low = path.lower() + "\\"
    if any(b in low for b in PATH_BLOCKLIST):
        return True
    # 系统属性的目录（隐藏/系统）基本是 Windows 自建，不参与判定
    try:
        attrs = os.stat(path, follow_symlinks=False).st_file_attributes
        if attrs & 0x2 or attrs & 0x4:          # HIDDEN / SYSTEM
            return True
    except (OSError, AttributeError):
        pass
    name = os.path.basename(path.rstrip("\\"))
    if name.lower() in WHITELIST:
        return True
    if name.lower().startswith("microsoft"):
        return True
    # ProgramData 下的中文目录（「开始」菜单、「应用数据」等）是系统创建的
    if not name.isascii():
        return True
    return False


def _profile_dir(path: str, max_files: int = 200_000) -> tuple[int, int, int, float]:
    """一次遍历拿到 (总字节, 文件数, exe/dll/sys 数, 最新 mtime)。"""
    total = files = exes = 0
    newest = 0.0
    base_depth = path.count(os.sep)
    stack = [path]
    while stack:
        if files > max_files:
            break
        cur = stack.pop()
        try:
            it = os.scandir(cur)
        except OSError:
            continue
        with it:
            for e in it:
                try:
                    if e.is_dir(follow_symlinks=False):
                        if e.path.count(os.sep) - base_depth < 12:
                            stack.append(e.path)
                        continue
                    st = e.stat(follow_symlinks=False)
                except OSError:
                    continue
                total += st.st_size
                files += 1
                if st.st_mtime > newest:
                    newest = st.st_mtime
                if e.name.lower().endswith((".exe", ".dll", ".sys")):
                    exes += 1
    return (total, files, exes, newest)


def score_candidate(path: str, category: str, idx: InstalledIndex,
                    stop_flag=None) -> Leftover | None:
    name = os.path.basename(path.rstrip("\\"))
    if not name or len(name) < 3:
        return None
    if _blocked(path):
        return None
    if idx.matches(name):
        return None                     # 明确对得上已安装软件，直接放过

    if stop_flag and stop_flag[0]:
        return None
    size, files, exes, newest = _profile_dir(path)
    if files == 0:
        return None                     # 空壳目录没有清理价值，只添噪音

    days = (time.time() - newest) / 86400.0 if newest else 0.0

    score = 42
    reasons: list[str] = ["在已安装软件清单中找不到对应程序"]

    if exes == 0:
        score += 20
        reasons.append("目录内没有任何 exe/dll，程序主体已不存在")
    elif exes <= 2:
        score += 6
        reasons.append(f"仅剩 {exes} 个可执行文件，可能是卸载后没清掉的组件")
    else:
        score -= 26
        reasons.append(f"目录内仍有 {exes} 个可执行文件，可能是在用的绿色软件")

    if days >= 365:
        score += 16
        reasons.append(f"已 {int(days)} 天没有改动")
    elif days >= 180:
        score += 10
        reasons.append(f"已 {int(days)} 天没有改动")
    elif days < 30:
        score -= 12
        reasons.append(f"{int(days)} 天内有改动，可能仍在使用")

    if any(h in name.lower() for h in _JUNK_HINTS):
        score += 8
        reasons.append("目录名含 uninstall / backup / old 等卸载痕迹词")

    if re.fullmatch(r"\{?[0-9A-Fa-f\-]{32,38}\}?", name):
        score += 10
        reasons.append("目录名为 GUID 形式，通常是安装缓存目录")

    if size < 1024 * 1024 and files < 6:
        score -= 10
        reasons.append("内容极少（不足 1 MB）")

    if _has_reg_trace(name):
        score -= 20
        reasons.append("注册表 Software 下有同名键，可能仍在使用")

    score = max(0, min(100, score))
    if score < 60:
        return None
    return Leftover(path=path, name=name, kind="目录", size=size, files=files,
                    mtime=newest, confidence=score, reasons=reasons,
                    category=category)


def scan_leftovers(roots: list[ScanRoot] | None = None, stop_flag=None,
                   progress=None) -> tuple[list[Leftover], int]:
    """返回 (残留列表, 已检查目录数)。"""
    roots = roots or default_roots()
    if progress:
        progress(0, 0, "正在建立已安装软件索引…")
    idx = installed_index(stop_flag)
    out: list[Leftover] = []
    checked = 0
    total_roots = len(roots)
    for r_i, root in enumerate(roots):
        for path, category in _candidates(root):
            if stop_flag and stop_flag[0]:
                return (out, checked)
            checked += 1
            if progress:
                progress(r_i, total_roots, os.path.basename(path))
            try:
                item = score_candidate(path, category, idx, stop_flag=stop_flag)
            except Exception:       # noqa: BLE001
                continue
            if item is not None:
                out.append(item)
    if progress:
        progress(total_roots, total_roots, "")
    out.sort(key=lambda x: (-x.confidence, -x.size))
    return (out, checked)
