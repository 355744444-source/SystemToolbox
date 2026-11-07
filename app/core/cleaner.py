# -*- coding: utf-8 -*-
"""垃圾文件清理引擎。

设计原则（安全优先）：
1. **规则白名单**——只清扫下面 RULES 里写死的系统/应用缓存目录，
   绝不接受任意路径，也不做「全盘遍历找垃圾」这类动作。
2. **扫描只读**——scan 只做 stat，不移动、不删除任何文件。
3. **删除默认进回收站**——走 SHFileOperationW + FOF_ALLOWUNDO，
   误删可从回收站还原；用户显式勾选「永久删除」才走 os.remove。
4. **占用中的文件跳过**——正在被进程使用的文件删不掉，计入 skipped，
   不当成失败刷屏（这也是 %TEMP% 要按 1 小时冷却的原因）。
"""
from __future__ import annotations

import ctypes
import os
import stat as stat_mod
import time
from ctypes import wintypes
from dataclasses import dataclass, field

from . import fmt, winutil

IS_WIN = os.name == "nt"


# ------------------------------------------------------------------ 规则表
@dataclass(frozen=True)
class CleanRule:
    key: str
    name: str
    desc: str
    paths: tuple[str, ...]
    patterns: tuple[str, ...] = ("*",)
    recursive: bool = True
    min_age_hours: float = 0.0
    need_admin: bool = False
    default_on: bool = True
    risk: str = "低"
    note: str = ""
    max_depth: int = 0                 # 0 = 不限制
    exact_files: tuple[str, ...] = ()  # 目录之外单独点名的文件（如 MEMORY.DMP）

    def roots(self) -> list[str]:
        """展开环境变量，返回真实存在的目录。"""
        out = []
        for p in self.paths:
            real = os.path.expandvars(p)
            if "%" in real:                      # 变量没展开说明环境里没有
                continue
            if os.path.isdir(real):
                out.append(os.path.normpath(real))
        return out

    def files(self) -> list[str]:
        out = []
        for p in self.exact_files:
            real = os.path.expandvars(p)
            if "%" in real:
                continue
            if os.path.isfile(real):
                out.append(os.path.normpath(real))
        return out


# 路径都用固定位置，避免误伤用户数据目录。
RULES: tuple[CleanRule, ...] = (
    CleanRule(
        "user_temp", "用户临时文件", "%TEMP%（当前登录用户）",
        ("%LOCALAPPDATA%\\Temp",), min_age_hours=1.0, default_on=True,
        note="正在运行的程序会重建所需的临时文件；本工具自身的解包目录已排除",
    ),
    CleanRule(
        "win_temp", "系统临时文件", "C:\\Windows\\Temp",
        ("%SystemRoot%\\Temp",), min_age_hours=1.0, need_admin=True, default_on=True,
    ),
    CleanRule(
        "wu_download", "Windows 更新缓存", "已下载的更新安装包（安装完即可清）",
        ("%SystemRoot%\\SoftwareDistribution\\Download",),
        need_admin=True, default_on=True,
        note="清空后系统需要时会自动重新下载；不影响已安装的更新",
    ),
    CleanRule(
        "crash_dumps", "崩溃转储文件", "C:\\Windows\\Minidump 与 MEMORY.DMP",
        ("%SystemRoot%\\Minidump",), patterns=("*.dmp",),
        exact_files=("%SystemRoot%\\MEMORY.DMP",),
        need_admin=True, default_on=True,
        note="如果还在排查蓝屏，建议先用本工具的「蓝屏转储」页分析完再清理",
    ),
    CleanRule(
        "wer_reports", "错误报告存档（WER）", "程序崩溃时生成的 ReportArchive / ReportQueue",
        ("%LOCALAPPDATA%\\Microsoft\\Windows\\WER\\ReportArchive",
         "%LOCALAPPDATA%\\Microsoft\\Windows\\WER\\ReportQueue",
         "%ProgramData%\\Microsoft\\Windows\\WER\\ReportArchive",
         "%ProgramData%\\Microsoft\\Windows\\WER\\ReportQueue"),
        need_admin=False, default_on=True,
    ),
    CleanRule(
        "user_crashdumps", "用户态崩溃转储", "%LOCALAPPDATA%\\CrashDumps 下的 .dmp",
        ("%LOCALAPPDATA%\\CrashDumps",), patterns=("*.dmp",), default_on=True,
    ),
    CleanRule(
        "thumbcache", "缩略图缓存", "资源管理器生成的 thumbcache_*.db",
        ("%LOCALAPPDATA%\\Microsoft\\Windows\\Explorer",),
        patterns=("thumbcache_*.db",), recursive=False, default_on=True,
        note="清完需重启资源管理器才看到图标重建，属于正常现象",
    ),
    CleanRule(
        "inetcache", "IE / 系统网页缓存", "%LOCALAPPDATA%\\Microsoft\\Windows\\INetCache",
        ("%LOCALAPPDATA%\\Microsoft\\Windows\\INetCache",), default_on=True,
    ),
    CleanRule(
        "d3d_cache", "显卡着色器缓存", "DirectX / NVIDIA / AMD 的着色器缓存",
        ("%LOCALAPPDATA%\\D3DSCache", "%LOCALAPPDATA%\\NVIDIA\\DXCache",
         "%LOCALAPPDATA%\\NVIDIA\\GLCache", "%LOCALAPPDATA%\\AMD\\DxCache"),
        default_on=True,
        note="清空后首次进入游戏/三维程序会重新编译着色器，稍慢一次",
    ),
    CleanRule(
        "prefetch", "预读取文件", "C:\\Windows\\Prefetch 的 *.pf",
        ("%SystemRoot%\\Prefetch",), patterns=("*.pf",), recursive=False,
        need_admin=True, default_on=False, risk="中",
        note="清掉后前几次开机/启动程序会变慢，系统会自动重建",
    ),
    CleanRule(
        "cbs_logs", "组件安装日志", "CBS / DISM 日志（可达数百 MB）",
        ("%SystemRoot%\\Logs\\CBS", "%SystemRoot%\\Logs\\DISM"),
        patterns=("*.log", "*.cab", "*.evtx"), recursive=True,
        need_admin=True, default_on=False, risk="中",
        note="排查系统更新故障时需要这些日志；平常可清",
    ),
    CleanRule(
        "delivery_opt", "传递优化缓存", "Windows 更新的 P2P 分发缓存",
        ("%SystemRoot%\\ServiceProfiles\\NetworkService\\AppData\\Local\\"
         "Microsoft\\Windows\\DeliveryOptimization\\Cache",),
        need_admin=True, default_on=False, risk="中",
    ),
    CleanRule(
        "browser_cache", "浏览器缓存", "Edge / Chrome 的 Cache 与 Code Cache",
        ("%LOCALAPPDATA%\\Microsoft\\Edge\\User Data",
         "%LOCALAPPDATA%\\Google\\Chrome\\User Data"),
        patterns=("Cache", "Code Cache", "GPUCache", "ShaderCache"),
        recursive=True, max_depth=3, default_on=False, risk="中",
        note="需要先完全退出浏览器，否则文件被占用会跳过；不会动书签、密码、历史记录",
    ),
    CleanRule(
        "old_logs", "系统日志备份", "C:\\Windows\\Logs 下的旧日志归档",
        ("%SystemRoot%\\Logs",), patterns=("*.etl", "*.cab", "*.old"),
        need_admin=True, default_on=False, risk="中",
    ),
)


def rule_by_key(key: str) -> CleanRule | None:
    for r in RULES:
        if r.key == key:
            return r
    return None


# ------------------------------------------------------------------ 扫描
@dataclass
class ScanResult:
    rule: CleanRule
    files: list[tuple[str, int, float]] = field(default_factory=list)
    total_size: int = 0
    count: int = 0
    skipped: int = 0
    errors: int = 0
    roots: list[str] = field(default_factory=list)
    detail_capped: bool = False

    @property
    def ok(self) -> bool:
        return bool(self.roots or self.files)


DETAIL_CAP = 3000            # 明细只留前 3000 条，统计仍然全量


def _is_excluded(path: str) -> bool:
    """排除本工具自己的 PyInstaller 解包目录。"""
    return os.path.basename(path).lower().startswith("_mei")


def _match_name(name: str, pats: tuple[str, ...]) -> bool:
    low = name.lower()
    for p in pats:
        if p.startswith("*."):
            if low.endswith(p[1:]):
                return True
        elif low == p:
            return True
    return False


def _match_dir(name: str, pats: tuple[str, ...]) -> bool:
    """目录名只做精确匹配：形如 *.db 的通配不参与，避免把同名目录整棵收走。"""
    low = name.lower()
    for p in pats:
        if not p.startswith("*") and low == p:
            return True
    return False


def _walk_all(root: str):
    """收集子树下所有文件（不跟随 reparse point）。"""
    stack = [root]
    while stack:
        cur = stack.pop()
        try:
            it = os.scandir(cur)
        except OSError:
            continue
        with it:
            for e in it:
                try:
                    if e.is_dir(follow_symlinks=False):
                        stack.append(e.path)
                    else:
                        yield e.path
                except OSError:
                    continue


def _iter_files(root: str, patterns: tuple[str, ...], recursive: bool,
                max_depth: int = 0):
    """遍历目录下的文件；不跟随 junction / 符号链接，避免绕圈。

    两种命中方式：
    - 文件名匹配 patterns（支持 *.ext 后缀）；
    - 目录名精确匹配 patterns 时，整个子树全收（浏览器 Cache 这类）。
    """
    pats = tuple(p.lower() for p in patterns)
    wildcard = pats == ("*",)
    depth_of = {os.path.normpath(root): 0}
    stack = [root]
    while stack:
        cur = stack.pop()
        base_depth = depth_of.get(os.path.normpath(cur), 0)
        try:
            it = os.scandir(cur)
        except (PermissionError, FileNotFoundError, OSError):
            continue
        with it:
            for e in it:
                try:
                    if e.is_dir(follow_symlinks=False):
                        if not recursive:
                            continue
                        if not wildcard and _match_dir(e.name, pats):
                            yield from _walk_all(e.path)
                            continue
                        if max_depth and base_depth + 1 > max_depth:
                            continue
                        depth_of[os.path.normpath(e.path)] = base_depth + 1
                        stack.append(e.path)
                        continue
                except OSError:
                    continue
                if wildcard or _match_name(e.name, pats):
                    yield e.path


def collect_files(rule: CleanRule, stop_flag=None) -> list[tuple[str, int]]:
    """枚举规则命中的全部文件 (路径, 大小)。清理前重新调用一次，
    保证不会因为扫描时明细截断而漏删。"""
    out: list[tuple[str, int]] = []
    cutoff = time.time() - rule.min_age_hours * 3600.0 if rule.min_age_hours else 0.0
    sources = [iter(rule.files())]
    for root in rule.roots():
        sources.append(_iter_files(root, rule.patterns, rule.recursive, rule.max_depth))
    for it in sources:
        for path in it:
            if stop_flag and stop_flag[0]:
                return out
            if _is_excluded(path):
                continue
            try:
                st = os.stat(path, follow_symlinks=False)
            except (OSError, PermissionError):
                continue
            if not stat_mod.S_ISREG(st.st_mode):
                continue
            if cutoff and st.st_mtime > cutoff:
                continue
            out.append((path, st.st_size))
    return out


def scan_rule(rule: CleanRule, stop_flag=None) -> ScanResult:
    """只读扫描单条规则。stop_flag 为 list[bool]，置 True 提前收工。"""
    res = ScanResult(rule=rule, roots=rule.roots())
    if not res.roots and not rule.files():
        return res
    cutoff = time.time() - rule.min_age_hours * 3600.0 if rule.min_age_hours else 0.0
    items: list[tuple[str, int, float]] = []
    sources = [iter(rule.files())]
    for root in res.roots:
        sources.append(_iter_files(root, rule.patterns, rule.recursive, rule.max_depth))
    for it in sources:
        for path in it:
            if stop_flag and stop_flag[0]:
                return _finish(res, items)
            if _is_excluded(path):
                res.skipped += 1
                continue
            try:
                st = os.stat(path, follow_symlinks=False)
            except (OSError, PermissionError):
                res.errors += 1
                continue
            if not stat_mod.S_ISREG(st.st_mode):
                continue
            if cutoff and st.st_mtime > cutoff:
                res.skipped += 1
                continue
            res.total_size += st.st_size
            res.count += 1
            items.append((path, st.st_size, st.st_mtime))
    return _finish(res, items)


def _finish(res: ScanResult, items: list[tuple[str, int, float]]) -> ScanResult:
    items.sort(key=lambda x: -x[1])
    res.files = items[:DETAIL_CAP]
    res.detail_capped = len(items) > DETAIL_CAP
    return res


def scan_rules(rules, stop_flag=None, progress=None) -> list[ScanResult]:
    out = []
    for i, r in enumerate(rules):
        if stop_flag and stop_flag[0]:
            break
        if progress:
            progress(i, len(rules), r.name)
        out.append(scan_rule(r, stop_flag))
    if progress:
        progress(len(rules), len(rules), "")
    return out


# ------------------------------------------------------------------ 删除
if IS_WIN:
    class _SHFILEOPSTRUCTW(ctypes.Structure):
        _fields_ = [
            ("hwnd", wintypes.HWND),
            ("wFunc", wintypes.UINT),
            ("pFrom", wintypes.LPCWSTR),
            ("pTo", wintypes.LPCWSTR),
            ("fFlags", ctypes.c_uint16),
            ("fAnyOperationsAborted", wintypes.BOOL),
            ("hNameMappings", ctypes.c_void_p),
            ("lpszProgressTitle", wintypes.LPCWSTR),
        ]

    class _SHQUERYRBINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD),
                    ("i64Size", ctypes.c_int64),
                    ("i64NumItems", ctypes.c_int64)]


FO_DELETE = 3
FOF_SILENT = 0x0004
FOF_NOCONFIRMATION = 0x0010
FOF_ALLOWUNDO = 0x0040
FOF_NOERRORUI = 0x0400
FOF_WANTNUKEWARNING = 0x4000

SHERB_NOCONFIRMATION = 0x00000001
SHERB_NOPROGRESSUI = 0x00000002
SHERB_NOSOUND = 0x00000004


def _to_recycle_bin(paths: list[str]) -> bool:
    """批量送入回收站。返回是否整批成功（SHFileOperation 的返回值语义较粗，
    调用方还会用 os.path.exists 复核）。"""
    if not IS_WIN or not paths:
        return False
    # QThread 是新线程，Shell API 前先做 COM 初始化（已初始化会返回 S_FALSE，无害）
    try:
        ctypes.windll.ole32.CoInitializeEx(None, 0x2)      # COINIT_APARTMENTTHREADED
    except Exception:       # noqa: BLE001
        pass
    buf = "\0".join(paths) + "\0\0"
    op = _SHFILEOPSTRUCTW()
    op.hwnd = None
    op.wFunc = FO_DELETE
    op.pFrom = buf
    op.pTo = None
    op.fFlags = (FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_SILENT
                 | FOF_NOERRORUI | FOF_WANTNUKEWARNING)
    try:
        rc = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
    except Exception:       # noqa: BLE001
        return False
    return rc == 0 and not op.fAnyOperationsAborted


def recycle_bin_info() -> tuple[int, int]:
    """(回收站占用字节, 项目数)。"""
    if not IS_WIN:
        return (0, 0)
    info = _SHQUERYRBINFO()
    info.cbSize = ctypes.sizeof(_SHQUERYRBINFO)
    try:
        if ctypes.windll.shell32.SHQueryRecycleBinW(None, ctypes.byref(info)) == 0:
            return (int(info.i64Size), int(info.i64NumItems))
    except Exception:       # noqa: BLE001
        pass
    return (0, 0)


def empty_recycle_bin() -> bool:
    if not IS_WIN:
        return False
    try:
        rc = ctypes.windll.shell32.SHEmptyRecycleBinW(
            None, None,
            SHERB_NOCONFIRMATION | SHERB_NOPROGRESSUI | SHERB_NOSOUND)
        return rc in (0, 1)          # S_OK / S_FALSE(已经是空的)
    except Exception:       # noqa: BLE001
        return False


def _prune_empty_dirs(roots: list[str]) -> None:
    """删除后留下的空壳目录顺手收掉（只删空目录，不递归删内容）。"""
    for root in roots:
        for dirpath, dirnames, filenames in os.walk(root, topdown=False):
            if os.path.normpath(dirpath) == os.path.normpath(root):
                continue
            if filenames or dirnames:
                continue
            try:
                os.rmdir(dirpath)
            except OSError:
                pass


@dataclass
class CleanReport:
    deleted: int = 0
    freed: int = 0
    failed: int = 0
    skipped: int = 0
    problems: list[str] = field(default_factory=list)
    to_recycle: bool = True


def delete_files(files, *, use_recycle: bool = True, stop_flag=None,
                 progress=None) -> CleanReport:
    """删除给定 (path, size) 列表。

    回收站模式下按 96 个一批提交给 Shell；永久删除逐个 os.remove，
    先去掉只读属性。删除后用 exists 复核，避免 Shell 回报成功其实没动。
    """
    rep = CleanReport(to_recycle=use_recycle)
    items = [(p, s) for p, s, *_ in files]
    total = len(items)
    if not total:
        return rep

    if use_recycle:
        batch: list[tuple[str, int]] = []
        for idx, (path, size) in enumerate(items):
            if stop_flag and stop_flag[0]:
                break
            batch.append((path, size))
            if len(batch) >= 96:
                _flush_recycle(batch, rep)
                batch.clear()
                if progress:
                    progress(idx + 1, total)
        if batch and not (stop_flag and stop_flag[0]):
            _flush_recycle(batch, rep)
        if progress:
            progress(total, total)
    else:
        for idx, (path, size) in enumerate(items):
            if stop_flag and stop_flag[0]:
                break
            try:
                os.chmod(path, stat_mod.S_IWRITE)
            except OSError:
                pass
            try:
                os.remove(path)
                rep.deleted += 1
                rep.freed += size
            except PermissionError:
                rep.skipped += 1
            except FileNotFoundError:
                rep.skipped += 1
            except OSError as exc:
                rep.failed += 1
                if len(rep.problems) < 8:
                    rep.problems.append(f"{os.path.basename(path)}: {exc.strerror or exc}")
            if progress and idx % 64 == 0:
                progress(idx + 1, total)
        if progress:
            progress(total, total)
    return rep


def _flush_recycle(batch: list[tuple[str, int]], rep: CleanReport) -> None:
    paths = [p for p, _ in batch]
    _to_recycle_bin(paths)
    for path, size in batch:
        if os.path.exists(path):
            # Shell 没删掉：多半是文件被占用或权限不足
            try:
                st = os.stat(path, follow_symlinks=False)
                if stat_mod.S_ISREG(st.st_mode):
                    rep.skipped += 1
                    continue
            except OSError:
                pass
            rep.skipped += 1
        else:
            rep.deleted += 1
            rep.freed += size


def prune_empty_dirs(results) -> None:
    roots: list[str] = []
    for r in results:
        roots.extend(r.roots)
    if roots:
        _prune_empty_dirs(roots)


# ------------------------------------------------------------------ 工具
# `human_size` / `is_admin` 已经搬去公共模块（v2.3.0），这里只做再导出 ——
# 全项目 90 多处写的是 `cleaner.human_size(...)`，搬走实现不必跟着改调用点。
# 谁要格式化体积，请直接用 `app.core.fmt`，不要在这里加第二份。
human_size = fmt.human_size
is_admin = winutil.is_admin


def relaunch_as_admin() -> bool:
    """以管理员身份重启自己（UAC）。启动成功后调用方应立即退出。"""
    if not IS_WIN:
        return False
    import sys
    try:
        if getattr(sys, "frozen", False):
            exe, params = sys.executable, ""
        else:
            exe = sys.executable
            params = " ".join(f'"{a}"' for a in sys.argv)
        rc = ctypes.windll.shell32.ShellExecuteW(None, "runas", exe, params, None, 1)
        return int(rc) > 32
    except Exception:       # noqa: BLE001
        return False
