# -*- coding: utf-8 -*-
"""系统文件与组件健康（只读）。

只回答一个问题：**这台机器的系统文件/组件存储还完好吗**。蓝屏、更新失败、
「某些功能装不上」到最后往往落在这里。全部只读，不修、不删、不重启。

数据源与取舍（都经过本机实测）：

1. `%SystemRoot%\\Logs\\CBS\\CBS.log` —— 组件服务日志
   实测本机 1.11 MB / 6755 行，只读尾部 8 MB 就够（这个文件会被滚动成
   `CbsPersist_*.cab`，最新的内容一定在末尾）。
   **关键坑**：里面大量 `Failed to ...` 是常态噪音，本机 6 条样例全是
   `0x800f0805 CBS_E_INVALID_PACKAGE`（枚举一个不存在的包而已，每次刷更新都写）。
   把 `Failed` 直接当错误 → 一进页面一片红。所以按 HRESULT 分成
   「已知噪音」与「真错误」两档，只有后者才计数上色。

2. `dism /Online /Cleanup-Image /CheckHealth` —— 只读健康检查，实测 1.4 秒
   （只读缓存，不扫盘）。三种结论：
   - 「未检测到组件存储损坏」→ 正常
   - 「可以修复组件存储」→ **确实有损坏**，但可用 `/RestoreHealth` 修
   - 「组件存储已损坏/无法修复」→ 严重
   非管理员会失败（需要提权），此时降级为「未能检查」而不是判红。

3. 挂起操作 —— `CBS\\RebootPending` / `WU\\...\\RebootRequired` / `pending.xml`
   / `PendingFileRenameOperations`。
   **坑**：`CBS\\SessionsPending` 在正常机器上**常驻**（本机 subkeys=8），
   拿它当「有待重启」会永久误报，所以不采信。

4. `CBS\\Packages` —— 3620 个包，按 `Package_N_for_KB` 加数字提取 KB 号去重，
   读 `InstallTimeHigh` 排序取最近几个，用来核对补丁级别。

5. WinSxS 体积 —— **实测递归要十几秒到一分钟**（3 万多个目录，取决于磁盘与缓存），
   所以默认不统计（显示「未统计」），由用户点按钮触发，后台可中断、结果缓存。
   故意不放进自动检查：一个"进页面就卡一分钟"的检查比没有这个检查更糟。
"""
from __future__ import annotations

import os
import re
import subprocess
import time
import winreg
from dataclasses import dataclass, field
from datetime import datetime

from . import fmt
from .winutil import is_admin

HKLM = winreg.HKEY_LOCAL_MACHINE
CBS_KEY = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Component Based Servicing"
WU_KEY = r"SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate\Auto Update"
NT_KEY = r"SOFTWARE\Microsoft\Windows NT\CurrentVersion"

CREATE_NO_WINDOW = 0x08000000

# CBS.log 里的高频无害 HRESULT：枚举/打开一个不存在的包，属常态
CBS_NOISE = {
    "0x800f0805": "CBS_E_INVALID_PACKAGE",        # 包不存在（每次扫描都会出现）
    "0x80070002": "ERROR_FILE_NOT_FOUND",         # 同上，找不到文件
}

# 真正指向组件存储损坏的 HRESULT
CBS_BAD = {
    "0x800f081f": "CBS_E_SOURCE_MISSING",
    "0x800f0906": "CBS_E_DOWNLOAD_FAILURE",
    "0x800f0831": "CBS_E_STORE_CORRUPTION",
    "0x800f0988": "PSFX_E_MATCHING_COMPONENT_NOT_FOUND",
    "0x80073712": "ERROR_SXS_COMPONENT_STORE_CORRUPT",
    "0x800f0922": "CBS_E_INSTALLERS_FAILED",
}

LEVEL_CN = {"ok": "正常", "warn": "注意", "crit": "异常", "info": "提示"}
LEVEL_ORDER = {"crit": 0, "warn": 1, "info": 2, "ok": 3}

_HRESULT_RE = re.compile(r"0x([0-9a-fA-F]{8})")
_KB_RE = re.compile(r"Package_\d+_for_KB(\d+)", re.I)
# CBS 行首级别：`2026-10-22 10:22:01, Info    CBS    ...`
_LVL_RE = re.compile(r"^\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2},\s*(\w+)")


def _hr(code: str) -> str:
    """归一成 0x 小写 8 位。"""
    c = code.lower()
    return c if c.startswith("0x") else "0x" + c


@dataclass
class Row:
    name: str
    value: str
    level: str = "ok"                  # ok / warn / crit / info
    detail: str = ""
    where: str = ""

    @property
    def level_text(self) -> str:
        return LEVEL_CN.get(self.level, self.level)


@dataclass
class CbsError:
    """CBS.log 里一条被判定为真错误的行。"""
    when: str
    hresult: str
    text: str


@dataclass
class Report:
    rows: list[Row] = field(default_factory=list)
    cbs_path: str = ""
    cbs_size_mb: float = 0.0
    cbs_mtime: str = ""
    cbs_lines: int = 0
    cbs_noise: int = 0                 # 噪音行数（不报警）
    cbs_bad: int = 0                   # 真错误行数
    errors: list[CbsError] = field(default_factory=list)
    sr_lines: int = 0                  # "sfc /scannow" 的 [SR] 标记行数
    dism_state: str = ""               # ok / repairable / corrupt / unknown
    dism_text: str = ""
    pending: list[str] = field(default_factory=list)
    patches: list[tuple[str, str]] = field(default_factory=list)   # (KB, 时间)
    ubr: str = ""
    winsxs: tuple[int, int, bool] | None = None    # (bytes, files, truncated)
    winsxs_elapsed: float = 0.0
    admin: bool = False
    elapsed: float = 0.0

    @property
    def problems(self) -> int:
        return sum(1 for r in self.rows if r.level == "crit")

    @property
    def warns(self) -> int:
        return sum(1 for r in self.rows if r.level == "warn")

    @property
    def headline(self) -> str:
        if self.problems:
            return "发现 %d 项需要处理的组件问题" % self.problems
        if self.warns:
            return "%d 项需要留意，系统文件总体正常" % self.warns
        return "系统文件与组件存储正常"


def cbs_path() -> str:
    root = os.environ.get("SystemRoot") or r"C:\Windows"
    return os.path.join(root, "Logs", "CBS", "CBS.log")


def _reg_exists(path: str) -> bool:
    try:
        with winreg.OpenKey(HKLM, path, 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY):
            return True
    except OSError:
        return False


def _reg_value(path: str, name: str):
    try:
        with winreg.OpenKey(HKLM, path, 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as k:
            return winreg.QueryValueEx(k, name)[0]
    except OSError:
        return None


def pending_reasons() -> list[str]:
    """当前挂起/待重启的证据（只采信确凿的，`SessionsPending` 常驻故不采）。"""
    out: list[str] = []
    if _reg_exists(CBS_KEY + r"\RebootPending"):
        out.append("组件服务待重启（CBS\\RebootPending）")
    if _reg_exists(CBS_KEY + r"\PackagesPending"):
        out.append("有已暂存未完成的更新包（CBS\\PackagesPending）")
    if _reg_exists(WU_KEY + r"\RebootRequired"):
        out.append("Windows 更新待重启（RebootRequired）")
    root = os.environ.get("SystemRoot") or r"C:\Windows"
    if os.path.isfile(os.path.join(root, "WinSxS", "pending.xml")):
        out.append("组件挂起操作未完成（WinSxS\\pending.xml）")
    pfr = _reg_value(r"SYSTEM\CurrentControlSet\Control\Session Manager",
                     "PendingFileRenameOperations")
    if pfr:
        out.append("有文件待重启改名（%d 条）" % len(pfr))
    return out


def parse_cbs(path: str | None = None, tail_bytes: int = 8 * 1024 * 1024,
              max_errors: int = 12) -> dict:
    """读 CBS.log 尾部，返回统计。不抛异常，读不到就返回空统计。"""
    p = path or cbs_path()
    info = {"path": p, "exists": False, "size_mb": 0.0, "mtime": "", "lines": 0,
            "noise": 0, "bad": 0, "errors": [], "sr": 0, "cannot_repair": 0}
    try:
        st = os.stat(p)
    except OSError:
        return info
    info["exists"] = True
    info["size_mb"] = st.st_size / 1048576.0
    info["mtime"] = datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M")
    try:
        with open(p, "rb") as f:
            back = min(st.st_size, tail_bytes)
            f.seek(st.st_size - back)
            raw = f.read()
    except OSError:
        return info
    text = raw.decode("utf-8", "ignore")
    errors: list[CbsError] = []
    bad = noise = sr = cannot = 0
    for line in text.splitlines():
        info["lines"] += 1
        low = line.lower()
        if "[SR]" in line:
            sr += 1
        if "cannot repair" in low:
            cannot += 1
        hit = _HRESULT_RE.search(line)
        code = _hr("0x" + hit.group(1)) if hit else ""
        # 先排噪音：0x800f0805 之类是每次枚举包都会写的常态，且多为 Info 级
        if code in CBS_NOISE:
            noise += 1
            continue
        lvl_m = _LVL_RE.match(line)
        lvl = (lvl_m.group(1).lower() if lvl_m else "")
        # 只有「已知坏码 / Error 级 / 修不好 / 明说损坏」才算真问题。
        # 两个实测过的误报陷阱：
        #   1. 不能只看关键字：Info 级里有 `does not have an install time`
        #      这种完全正常的记录，按关键字一抓就误报。
        #   2. 也不能把 Warning 级一律当问题：本机 CBS.log 里刷的
        #      `WatsonHelper: File already set: [...]` 是上报模块把同一个
        #      日志文件重复加入列表，属正常行为。Warning 级仅在带已知坏码、
        #      或明说 corrupt / cannot repair 时才算。
        is_bad = (code in CBS_BAD
                  or lvl == "error"
                  or "cannot repair" in low
                  or "corrupt" in low)
        if not is_bad:
            continue
        bad += 1
        if len(errors) < max_errors:
            when = line[:19] if line[:4].isdigit() else ""
            errors.append(CbsError(when=when, hresult=code or "—",
                                   text=_tidy(line)))
    info.update(noise=noise, bad=bad, errors=errors, sr=sr, cannot_repair=cannot)
    return info


def _tidy(line: str) -> str:
    """去掉时间戳与列对齐的空白，只留可读正文。"""
    s = _HRESULT_RE.sub("", line)
    s = re.sub(r"^\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}(,\s*\w+)?", "", s)
    s = re.sub(r"\b(Info|Warning|Error)\b\s+", "", s, count=1)
    return " ".join(s.split())[:160]


def run_dism(timeout: int = 30) -> tuple[str, str]:
    """跑 `dism /CheckHealth`（只读）。返回 (state, 原文摘要)。

    state: ok / repairable / corrupt / needs_admin / unknown
    """
    exe = os.path.join(os.environ.get("SystemRoot") or r"C:\Windows",
                       "System32", "dism.exe")
    if not os.path.isfile(exe):
        exe = "dism.exe"
    try:
        r = subprocess.run([exe, "/Online", "/Cleanup-Image", "/CheckHealth"],
                           capture_output=True, timeout=timeout,
                           creationflags=CREATE_NO_WINDOW)
    except subprocess.TimeoutExpired:
        return "unknown", "检查超时（%d 秒），请在命令行手动运行 dism /Online /Cleanup-Image /CheckHealth" % timeout
    except OSError as e:
        return "unknown", "无法启动 dism：%s" % e
    out = (r.stdout or b"").decode("mbcs", "ignore")
    err = (r.stderr or b"").decode("mbcs", "ignore")
    blob = out + "\n" + err
    low = blob.lower()
    # 结论句优先（中英文两套，避免系统语言影响判断）
    if ("未检测到组件存储损坏" in blob or "no component store corruption" in low):
        return "ok", "未检测到组件存储损坏"
    if "可以修复组件存储" in blob or "component store is repairable" in low:
        return "repairable", "组件存储存在损坏，但可以修复（可用 /RestoreHealth 修）"
    if ("组件存储已损坏" in blob or "not repairable" in low
            or "无法修复" in blob):
        return "corrupt", "组件存储已损坏且无法自动修复"
    if r.returncode != 0:
        if "740" in blob or "提升" in blob or "elevat" in low or "拒绝访问" in blob:
            return "needs_admin", "需要以管理员身份运行才能检查组件存储"
        tail = " ".join(blob.split())[-160:]
        return "unknown", "DISM 返回 %d：%s" % (r.returncode, tail or "无输出")
    tail = " ".join(blob.split())[-160:]
    return "unknown", tail or "DISM 无明确结论"


def patches(limit: int = 8) -> list[tuple[str, str]]:
    """最近安装的补丁（KB 号 + 安装日期），按 `InstallTimeHigh` 排序。

    3620 个包里同一 KB 会有多个 `Package_N_for_KB...`，先按 KB 去重再读时间，
    否则要读七千多次注册表。
    """
    base = CBS_KEY + r"\Packages"
    cand: dict[str, str] = {}
    try:
        with winreg.OpenKey(HKLM, base, 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as k:
            subs, _v, _ = winreg.QueryInfoKey(k)
            for i in range(subs):
                try:
                    nm = winreg.EnumKey(k, i)
                except OSError:
                    continue
                m = _KB_RE.search(nm)
                if m:
                    cand.setdefault("KB" + m.group(1), nm)
    except OSError:
        return []
    rows: list[tuple[str, datetime]] = []
    for kb, nm in cand.items():
        hi = _reg_value(base + "\\" + nm, "InstallTimeHigh")
        lo = _reg_value(base + "\\" + nm, "InstallTimeLow")
        if not isinstance(hi, int) or not isinstance(lo, int):
            continue
        try:
            v = (hi << 32) | (lo & 0xFFFFFFFF)
            rows.append((kb, datetime.fromtimestamp(v / 10_000_000 - 11644473600)))
        except (ValueError, OSError, OverflowError):
            continue
    rows.sort(key=lambda r: r[1], reverse=True)
    return [(kb, ts.strftime("%Y-%m-%d")) for kb, ts in rows[:limit]]


def measure_winsxs(stop_flag=None, progress=None, timeout: float = 240.0,
                   root: str | None = None) -> tuple[int, int, bool]:
    """递归统计 WinSxS 体积。返回 (字节, 文件数, 是否中断)。

    实测要十几秒到一分钟，所以必须可中断：UI 侧给 stop_flag，切页/关窗时置位。
    `root` 只给单测用（把 SystemRoot 指到临时目录）。
    """
    root = root or os.path.join(
        os.environ.get("SystemRoot") or r"C:\Windows", "WinSxS")
    total = files = 0
    t0 = time.perf_counter()
    dirs = 0
    stack = [root]
    truncated = False
    while stack:
        if stop_flag is not None and stop_flag():
            truncated = True
            break
        if time.perf_counter() - t0 > timeout:
            truncated = True
            break
        d = stack.pop()
        dirs += 1
        try:
            with os.scandir(d) as it:
                for e in it:
                    try:
                        if e.is_dir(follow_symlinks=False):
                            stack.append(e.path)
                        elif e.is_file(follow_symlinks=False):
                            total += e.stat(follow_symlinks=False).st_size
                            files += 1
                    except OSError:
                        pass
        except OSError:
            pass
        if progress and dirs % 2000 == 0:
            try:
                progress(files, 0, "已扫描 %d 个目录 / %.2f GB"
                         % (dirs, fmt.bytes_to_gb(total)))
            except Exception:                # noqa: BLE001
                pass
    return total, files, truncated


def scan(with_dism: bool = True) -> Report:
    """跑一遍组件健康检查。`with_dism=False` 时不调 dism（单测与无管理员场景更快）。"""
    t0 = time.perf_counter()
    rep = Report(admin=is_admin())
    cbs = parse_cbs()
    rep.cbs_path = cbs["path"]
    rep.cbs_size_mb = cbs["size_mb"]
    rep.cbs_mtime = cbs["mtime"]
    rep.cbs_lines = cbs["lines"]
    rep.cbs_noise = cbs["noise"]
    rep.cbs_bad = cbs["bad"]
    rep.errors = cbs["errors"]
    rep.sr_lines = cbs["sr"]

    # ---- 1. 组件存储（DISM 只读健康检查）
    if with_dism:
        state, text = run_dism()
    else:
        state, text = "unknown", "未执行检查"
    rep.dism_state, rep.dism_text = state, text
    if state == "ok":
        rep.rows.append(Row("组件存储", text, "ok", "DISM /CheckHealth：未检测到损坏", ""))
    elif state == "repairable":
        # 现状列只放结论，长句塞进说明列 —— 否则表格里必然被截成「可用 /RestoreHe…」
        rep.rows.append(Row(
            "组件存储", "存在可修复损坏", "warn",
            "%s。通常表现为某些更新装不上或功能异常；修复需要联网与管理员权限的 "
            "`DISM /Online /Cleanup-Image /RestoreHealth`" % text,
            "管理员命令行：DISM /Online /Cleanup-Image /RestoreHealth"))
    elif state == "corrupt":
        rep.rows.append(Row(
            "组件存储", text, "crit",
            "组件存储损坏且无法自动修复，建议先备份数据，再考虑就地重装或还原",
            "设置 › 恢复 / 管理员命令行：sfc /scannow"))
    elif state == "needs_admin":
        rep.rows.append(Row("组件存储", text, "info", text, "以管理员身份重新运行本工具"))
    else:
        rep.rows.append(Row("组件存储", text or "未能判断", "info",
                            "未能取得 DISM 结论，可手动运行一次确认", ""))

    # ---- 2. 系统文件损坏证据
    cannot = cbs.get("cannot_repair", 0)
    if cannot:
        rep.rows.append(Row(
            "系统文件损坏", "CBS.log 中 %d 处「Cannot repair member file」" % cannot,
            "crit", "SFC 修不好的文件，通常是组件存储本身缺源或磁盘出错",
            "管理员命令行：sfc /scannow 后 DISM /RestoreHealth"))
    elif rep.sr_lines:
        rep.rows.append(Row(
            "SFC 扫描记录", "CBS.log 中找到 %d 行 [SR] 记录" % rep.sr_lines, "ok",
            "说明本机跑过 sfc /scannow，且最近一次没有留下无法修复的记录", "sfc /scannow"))
    else:
        rep.rows.append(Row(
            "SFC 扫描记录", "未运行过 sfc /scannow", "info",
            "本机组件日志里没有 SFC 扫描痕迹。怀疑系统文件损坏时先跑一次", "cmd：sfc /scannow"))

    # ---- 3. CBS 日志里的真错误
    detail = ""
    if rep.cbs_noise:
        detail = "另有 %d 行 0x800f0805／0x80070002 属枚举包时的常态噪音，已排除" % rep.cbs_noise
    if rep.cbs_bad:
        sample = rep.errors[0]
        rep.rows.append(Row(
            "组件服务错误", "尾部 %d 行中有 %d 行异常" % (rep.cbs_lines, rep.cbs_bad),
            "warn" if rep.dism_state != "corrupt" else "crit",
            "最近一条：%s（%s）。%s" % (sample.text, sample.hresult, detail),
            "打开 CBS.log 对照时间点"))
    else:
        rep.rows.append(Row("组件服务错误", "未见异常记录", "ok",
                            detail or "尾部 %d 行全部为常规信息" % rep.cbs_lines, ""))

    # ---- 4. 挂起操作 / 待重启
    rep.pending = pending_reasons()
    if rep.pending:
        rep.rows.append(Row(
            "挂起操作", "；".join(rep.pending), "warn",
            "有未完成的安装事务。这种状态下更新/清理类操作容易失败，"
            "也会让组件判断失真 —— 先重启一次再看", "重启后重新检查"))
    else:
        rep.rows.append(Row("挂起操作", "无", "ok",
                            "没有待重启事务，也没有 pending.xml 残留", ""))

    # ---- 5. CBS 日志本身的状态
    if cbs["exists"]:
        big = rep.cbs_size_mb > 200
        rep.rows.append(Row(
            "组件日志", "%.2f MB · 最后写入 %s" % (rep.cbs_size_mb, rep.cbs_mtime),
            "info" if big else "ok",
            "超过 200 MB 说明刚做过大更新，可清理" if big else
            "日志会滚动成 CbsPersist_*.cab，本工具只读末尾 8 MB",
            "" if not big else "磁盘清理 › 清理系统文件"))
    else:
        rep.rows.append(Row("组件日志", "未找到 CBS.log", "info",
                            "路径：%s" % rep.cbs_path, ""))

    # ---- 6. 累积更新级别
    build = _reg_value(NT_KEY, "CurrentBuildNumber") or ""
    ubr = _reg_value(NT_KEY, "UBR")
    disp = _reg_value(NT_KEY, "DisplayVersion") or ""
    if ubr is not None:
        rep.ubr = "%s %s.%s" % (disp, build, ubr)
        rep.rows.append(Row(
            "累积更新", rep.ubr, "ok",
            "系统盘版本号（build.UBR），核对补丁级别用这个，不看版本名", ""))
    else:
        rep.rows.append(Row("累积更新", "未能读取 UBR", "info", "", ""))

    # ---- 7. 最近安装的补丁
    rep.patches = patches()
    if rep.patches:
        rep.rows.append(Row(
            "最近补丁", "%d 个" % len(rep.patches), "ok",
            "、".join("%s(%s)" % (kb, t) for kb, t in rep.patches[:5]),
            ""))
    else:
        rep.rows.append(Row("最近补丁", "未能读取", "info",
                            "CBS\\Packages 无记录或权限不足", ""))

    # ---- 8. WinSxS 体积（默认不统计：实测全量要 60 秒以上）
    rep.rows.append(Row("组件存储体积", "未统计", "info",
                        "WinSxS 有 3 万多个目录，统计要十几秒到一分钟，"
                        "点右侧「统计体积」按需执行（后台可中断）",
                        ""))

    rep.rows.sort(key=lambda r: LEVEL_ORDER.get(r.level, 9))
    rep.elapsed = time.perf_counter() - t0
    return rep
