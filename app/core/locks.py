# -*- coding: utf-8 -*-
"""文件占用排查：这个文件到底被谁占着。

## 数据源

Restart Manager（`rstrtmgr.dll`）。它就是「安装程序提示请关闭以下程序」背后的
那个接口，能直接给出**占用进程的 PID 与程序名**，比自己枚举句柄快得多。

实测（Win11 26H2，2026-10-26）：

    单文件         8~68 ms
    不存在的路径   回 rc=0 且 0 个进程（要自己做存在性判断）
    pagefile.sys   RmRegisterResources 回 32（ERROR_SHARING_VIOLATION）
    目录           RmRegisterResources 回 5（ERROR_ACCESS_DENIED）

## 三个必须记住的坑

1. **`RmRegisterResources` 的参数顺序**
   `(session, nFiles, 文件数组, nApps, 应用数组, nServices, 服务名数组)`。
   把文件数组填到「应用数组」那个槽位，它会把文件路径当 `RM_UNIQUE_PROCESS`
   解析，结果是**连不存在的路径都会报 1 个占用**，PID 是垃圾值。
   我第一次就是这么写的，症状很迷惑。

2. **一批里只要有一个坏路径，整批登记就失败**。所以批量查询失败后必须
   退化成逐个查，才能指出是哪一个文件登记不上。

3. **`AppStatus` 是位掩码不是枚举**，别拿它当 `1=运行中` 用。
   `ApplicationType` 里的 `1000` 也不是错误码，是 `RmCritical`。

## 口径限制（写进界面文案）

Restart Manager 报告的是「为了替换/删除这个文件，需要重启哪些程序」，
所以它**看不到内核态持有者**：pagefile.sys、休眠文件、注册表 hive、
内存映射的驱动文件都会回「登记失败」，而不是「没人占用」。
这类必须单独解释，不能写成「没有进程占用」。
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import os
import time
from dataclasses import dataclass, field

__all__ = ["LockProcess", "LockFile", "LockReport", "query", "query_files",
           "APP_TYPES", "RC_TEXT", "BUDGET_S"]

_k32 = ctypes.WinDLL("kernel32", use_last_error=True)
_rm = ctypes.WinDLL("rstrtmgr", use_last_error=True)

CCH_RM_SESSION_KEY = 32
CCH_RM_MAX_APP_NAME = 255
CCH_RM_MAX_SVC_NAME = 63

ERROR_FILE_NOT_FOUND = 2
ERROR_PATH_NOT_FOUND = 3
ERROR_ACCESS_DENIED = 5
ERROR_SHARING_VIOLATION = 32
ERROR_INVALID_PARAMETER = 87
ERROR_INVALID_NAME = 123
ERROR_BAD_PATHNAME = 161


class _FILETIME(ctypes.Structure):
    _fields_ = [("dwLowDateTime", wt.DWORD), ("dwHighDateTime", wt.DWORD)]


class _RM_UNIQUE_PROCESS(ctypes.Structure):
    _fields_ = [("dwProcessId", wt.DWORD), ("ProcessStartTime", _FILETIME)]


class _RM_PROCESS_INFO(ctypes.Structure):
    _fields_ = [("Process", _RM_UNIQUE_PROCESS),
                ("strAppName", ctypes.c_wchar * (CCH_RM_MAX_APP_NAME + 1)),
                ("strServiceShortName", ctypes.c_wchar * (CCH_RM_MAX_SVC_NAME + 1)),
                ("ApplicationType", wt.ULONG),
                ("AppStatus", wt.ULONG),
                ("TSSessionId", wt.DWORD),
                ("bRestartable", wt.BOOL)]


_rm.RmStartSession.argtypes = [ctypes.POINTER(wt.DWORD), ctypes.POINTER(wt.DWORD),
                               ctypes.c_wchar_p]
_rm.RmStartSession.restype = wt.ULONG
_rm.RmRegisterResources.argtypes = [wt.DWORD, wt.UINT, ctypes.POINTER(ctypes.c_wchar_p),
                                    wt.UINT, ctypes.POINTER(_RM_UNIQUE_PROCESS),
                                    wt.UINT, ctypes.POINTER(ctypes.c_wchar_p)]
_rm.RmRegisterResources.restype = wt.ULONG
_rm.RmGetList.argtypes = [wt.DWORD, ctypes.POINTER(wt.UINT), ctypes.POINTER(wt.UINT),
                          ctypes.POINTER(_RM_PROCESS_INFO), ctypes.POINTER(wt.ULONG)]
_rm.RmGetList.restype = wt.ULONG
_rm.RmEndSession.argtypes = [wt.DWORD]
_rm.RmEndSession.restype = wt.ULONG

_k32.OpenProcess.restype = wt.HANDLE
_k32.QueryFullProcessImageNameW.argtypes = [wt.HANDLE, wt.DWORD,
                                            ctypes.c_wchar_p, ctypes.POINTER(wt.DWORD)]

APP_TYPES = {
    0: "未知程序",
    1: "有主窗口",
    2: "后台程序",
    3: "系统服务",
    4: "资源管理器",
    1000: "关键进程",
}

#: AppStatus 是位掩码（RM_APP_STATUS）
STATUS_BITS = [
    (0x01, "运行中"),
    (0x02, "已停止"),
    (0x04, "牵连停止"),
    (0x08, "需重启"),
    (0x10, "停止出错"),
    (0x20, "重启出错"),
    (0x40, "关机被屏蔽"),
    (0x80, "重启被屏蔽"),
]

#: 结果码 → 人话。空串表示正常。
RC_TEXT = {
    0: "",
    ERROR_FILE_NOT_FOUND: "路径不存在",
    ERROR_PATH_NOT_FOUND: "路径不存在",
    ERROR_ACCESS_DENIED: "拒绝访问：目录或受保护路径（Restart Manager 只接受具体文件）",
    ERROR_SHARING_VIOLATION: "文件被系统独占（pagefile / 休眠文件 / 注册表 hive 这类），"
                             "内核态占用查不到，只能重启后再操作",
    ERROR_INVALID_PARAMETER: "路径格式不对（过长或含通配符）",
    ERROR_INVALID_NAME: "路径含非法字符",
    ERROR_BAD_PATHNAME: "路径不完整，试试完整绝对路径",
}

#: 一次查询的时间预算（秒）。**这一步是必需的止损**：二分只在「少数文件被占」
#: 时是 O(log n)；如果目录里几乎每个文件都被同一个服务占着（典型：
#: System32\winevt\Logs，446 个 .evtx 里三分之一被事件日志服务持有），
#: 每一片叶子都得单独开一次 Restart Manager 会话，实测固定开销 ~220ms +
#: 每文件 ~12ms，整个目录要 110 秒以上。超预算就停手，返回已经确定的部分。
BUDGET_S = 12.0

#: 每批先整体问一次再二分。一次会话的成本是「220ms 固定 + 12ms/文件」，
#: 446 个文件塞进一个会话要 5.7 秒、而且必然带占用（只能整批劈开），
#: 等于白花 5 秒。按 32 个一块，最坏也只是 0.4 秒一次，先把「整块干净」
#: 的判掉，预算花在真正被占用的那几块上。
CHUNK = 32


#: 这些文件由内核全程独占，Restart Manager **不会**报占用者（实测回 0 个），
#: 必须按文件名识别，否则会给出「没有进程占用」的错误结论。
KERNEL_HELD_NAMES = {
    "pagefile.sys": "页面文件，由内存管理器全程独占",
    "hiberfil.sys": "休眠文件，仅能通过关闭休眠释放",
    "swapfile.sys": "备选页面文件（UWP 应用用）",
    "dumpstack.log.tmp": "崩溃转储暂存文件",
    "bootstat.dat": "启动状态记录文件",
}

#: 注册表 hive 也是内核加载的，同样查不到「进程」
KERNEL_HELD_DIR = "\\system32\\config"


def _kernel_held(path: str) -> str:
    name = os.path.basename(path).lower()
    if name in KERNEL_HELD_NAMES:
        return KERNEL_HELD_NAMES[name] + "，内核态占用查不到进程"
    low = path.lower()
    if os.path.dirname(low).endswith(KERNEL_HELD_DIR):
        return "注册表 hive 由内核加载，查不到占用进程"
    return ""


@dataclass
class LockProcess:
    pid: int = 0
    name: str = ""
    service: str = ""
    app_type: int = 0
    status: int = 0
    restartable: bool = False
    exe: str = ""

    @property
    def label(self) -> str:
        if self.name:
            return self.name
        if self.service:
            return self.service
        if self.exe:
            return os.path.basename(self.exe)
        return "PID %d" % self.pid

    @property
    def type_text(self) -> str:
        return APP_TYPES.get(self.app_type, "进程")

    @property
    def status_text(self) -> str:
        names = [n for bit, n in STATUS_BITS if self.status & bit]
        return "、".join(names) if names else "占用中"

    @property
    def is_system(self) -> bool:
        low = (self.exe or "").lower()
        return "\\windows\\system32\\" in low or "\\windows\\syswow64\\" in low


@dataclass
class LockFile:
    path: str
    exists: bool = True
    is_dir: bool = False
    rc: int = 0
    processes: list[LockProcess] = field(default_factory=list)
    kernel_held: str = ""            # 非空 = 内核独占文件的解释
    timeout: bool = False            # 时间预算用完，这一个没单独确认
    hint: str = ""                   # 没查完，但所属那一批确实有占用者

    @property
    def name(self) -> str:
        return os.path.basename(self.path) or self.path

    @property
    def parent(self) -> str:
        return os.path.dirname(self.path)

    @property
    def error(self) -> str:
        return RC_TEXT.get(self.rc, "查询失败（错误码 %d）" % self.rc)

    @property
    def locked(self) -> bool:
        return bool(self.processes)

    @property
    def verdict(self) -> str:
        if not self.exists:
            return "路径不存在"
        if self.timeout:
            return "组内占用" if self.hint else "未查完"
        if self.rc:
            return "查不了"
        if self.locked:
            return "被占用"
        if self.kernel_held:
            return "系统独占"
        return "未占用"

    @property
    def verdict_level(self) -> str:
        """给表格着色：占用=警示，查不了/系统独占/未查完=中性，未占用=正常。"""
        if not self.exists:
            return "faint"
        if self.timeout or self.rc or self.kernel_held:
            return "dim"
        return "warn" if self.locked else "ok"

    @property
    def detail(self) -> str:
        # 有进程就先说进程：注册表 hive 这类文件 RM 有时真的能给出占用者
        # （System / Registry），此时 kernel_held 的解释反而是干扰。
        if self.processes:
            return "、".join(p.label for p in self.processes[:4])
        if self.timeout:
            if self.hint:
                return ("这一组文件里确实有占用，但时间预算用完了，没来得及"
                        "逐个确认是哪几个。占用者：%s" % self.hint)
            return ("超出查询时间预算，没来得及单独确认这一个。"
                    "文件太多时先看「被占用」的那些行，或直接指定具体文件再查")
        if self.kernel_held:
            return self.kernel_held
        if self.rc:
            return self.error
        return ""


@dataclass
class LockReport:
    target: str = ""
    files: list[LockFile] = field(default_factory=list)
    elapsed_ms: float = 0.0
    skipped: int = 0            # 目录里因数量上限没查的文件数
    budget_s: float = BUDGET_S
    notes: list[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.files)

    @property
    def locked_files(self) -> list[LockFile]:
        return [f for f in self.files if f.locked]

    @property
    def locked_count(self) -> int:
        return len(self.locked_files)

    @property
    def process_count(self) -> int:
        seen = set()
        for f in self.files:
            for p in f.processes:
                seen.add((p.pid, f.path))
        return len(seen)

    @property
    def kernel_count(self) -> int:
        """内核独占且没查出进程。「没查完」的不算——那些还没有结论。"""
        return sum(1 for f in self.files
                   if f.kernel_held and not f.processes and not f.timeout)

    @property
    def timeout_count(self) -> int:
        return sum(1 for f in self.files if f.timeout)

    @property
    def headline(self) -> str:
        if not self.files:
            return "没有可查询的文件"
        tmo = self.timeout_count
        tail = ("；另有 %d 个文件没逐个确认（超出 %.0f 秒预算）"
                % (tmo, self.budget_s)) if tmo else ""
        if self.locked_count:
            names = []
            for f in self.locked_files:
                for p in f.processes:
                    if p.label not in names:
                        names.append(p.label)
            return "%d 个进程占着 %d 个文件：%s%s" % (
                len(names), self.locked_count, "、".join(names[:4])
                + ("…" if len(names) > 4 else ""), tail)
        bad = sum(1 for f in self.files if f.rc)
        if bad:
            return "有 %d 个文件查不了（见下表说明）%s" % (bad, tail)
        if self.kernel_count:
            return "%d 个文件由内核独占，查不到进程（属正常）%s" % (
                self.kernel_count, tail)
        if tmo:
            return "%d 个文件没逐个确认（超出 %.0f 秒预算），已确认的没有进程占用" % (
                tmo, self.budget_s)
        return "目前没有进程占用"


def _proc_exe(pid: int) -> str:
    """PID → 可执行文件路径。特权进程拿不到就返回空串。"""
    if pid <= 0:
        return ""
    h = _k32.OpenProcess(0x1000, False, pid)      # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = wt.DWORD(1024)
        if _k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return buf.value
        return ""
    finally:
        _k32.CloseHandle(h)


def _new_session():
    """返回 (session_handle, rc, ok)。**句柄 0 是合法值**，别用 falsy 判失败。"""
    handle = wt.DWORD(0)
    key = ctypes.create_unicode_buffer(CCH_RM_SESSION_KEY + 1)
    rc = int(_rm.RmStartSession(ctypes.byref(handle), None, key))
    return handle.value, rc, rc == 0


def _collect(session: int) -> list[LockProcess]:
    need, count, reason = wt.UINT(0), wt.UINT(0), wt.ULONG(0)
    rc = _rm.RmGetList(session, ctypes.byref(need), ctypes.byref(count), None,
                       ctypes.byref(reason))
    if rc not in (0, 234) or not need.value:      # 234 = ERROR_MORE_DATA
        return []
    buf = (_RM_PROCESS_INFO * need.value)()
    count = wt.UINT(need.value)
    rc = _rm.RmGetList(session, ctypes.byref(need), ctypes.byref(count), buf,
                       ctypes.byref(reason))
    if rc != 0:
        return []
    out = []
    for i in range(min(count.value, need.value)):
        b = buf[i]
        pid = b.Process.dwProcessId
        out.append(LockProcess(
            pid=pid,
            name=b.strAppName,
            service=b.strServiceShortName,
            app_type=int(b.ApplicationType),
            status=int(b.AppStatus),
            restartable=bool(b.bRestartable),
            exe=_proc_exe(pid),
        ))
    return out


def _query_batch(paths: list[str]) -> tuple[int, list[LockProcess]]:
    """一次会话登记多个文件。返回 (rc, 进程列表)。"""
    session, rc, ok = _new_session()
    if not ok:
        return rc or -1, []
    try:
        arr = (ctypes.c_wchar_p * len(paths))(*paths)
        rc = int(_rm.RmRegisterResources(session, len(paths), arr, 0, None, 0, None))
        if rc != 0:
            return rc, []
        return 0, _collect(session)
    finally:
        _rm.RmEndSession(session)


class _Ctl:
    """一次查询的横切状态：时间预算、取消位、进度。"""

    __slots__ = ("deadline", "stop", "done", "total", "progress", "aborted")

    def __init__(self, total: int, budget_s: float, stop_flag, progress) -> None:
        self.deadline = time.perf_counter() + max(0.0, budget_s)
        self.stop = stop_flag
        self.done = 0
        self.total = max(int(total), 1)
        self.progress = progress
        self.aborted = False

    def over(self) -> bool:
        """该收手了吗？用户点了停止，或者预算用光。"""
        if self.stop is not None and self.stop[0]:
            self.aborted = True
            return True
        if time.perf_counter() >= self.deadline:
            self.aborted = True
            return True
        return False

    def notify(self, n: int = 0) -> None:
        self.done = min(self.done + n, self.total)
        if self.progress:
            self.progress(self.done, self.total, "逐批定位占用者")


def query_files(paths: list[str], budget_s: float = BUDGET_S,
                stop_flag=None, progress=None) -> LockReport:
    """查一组文件。

    Restart Manager 的会话是「一批文件 → 一批进程」，**不给对应关系**。
    逐个查最直观但很慢（491 个文件 × 8 ms ≈ 4 秒），所以这里用二分：
    先查整批，只有「这一批里有占用/有错误」时才劈成两半继续问，
    干净的那一半直接判定。代价从 O(n) 降到 O(log n)。

    但「几乎每个文件都被占着」的目录会让二分退化成 O(n)（每次会话都命中占用，
    两半都得继续劈），所以还有 `budget_s` 兜底：超时/被取消就停手，
    剩下的标 timeout，返回已经确定的那部分。
    """
    t0 = time.perf_counter()
    rep = LockReport(target=paths[0] if paths else "", budget_s=budget_s)
    paths = [p for p in paths if p]
    if not paths:
        return rep

    files: dict[str, LockFile] = {}
    existing = []
    for p in paths:
        lf = LockFile(path=p, exists=os.path.exists(p), is_dir=os.path.isdir(p),
                      kernel_held=_kernel_held(p) if os.path.exists(p) else "")
        files[p] = lf
        rep.files.append(lf)
        if lf.exists:
            existing.append(p)

    if not existing:
        rep.elapsed_ms = (time.perf_counter() - t0) * 1000
        return rep

    ctl = _Ctl(len(existing), budget_s, stop_flag, progress)
    # 分块推进：这样即使中途超预算，前面的块也已经给出逐个结论，
    # 而不是像整批二分那样「12 秒过去一条结论都没有」。
    idx = 0
    while idx < len(existing):
        if ctl.over():
            break
        _resolve(existing[idx:idx + CHUNK], files, ctl, None)
        idx += CHUNK
    for p in existing[idx:]:
        files[p].timeout = True
    if ctl.aborted:
        if stop_flag is not None and stop_flag[0]:
            rep.notes.append("已手动停止，剩下的文件没查")
        else:
            rep.notes.append("文件太多且大多被占用，超过 %.0f 秒预算后停手，"
                             "标「未查完 / 组内占用」的行没有逐个确认"
                             "——指定具体文件再查更准" % budget_s)
    rep.elapsed_ms = (time.perf_counter() - t0) * 1000
    return rep


def _resolve(paths: list[str], out: dict[str, "LockFile"], ctl: "_Ctl",
             hint: list[LockProcess] | None) -> None:
    """把一批文件的占用结果落到各自的 LockFile 上，必要时二分。

    `hint` 是**上级批次**查出来的占用者：预算在某个子树里用完时，虽然没能
    逐个确认是谁占的，但「这一组里有占用，占用者是 X」是已经证实的事实，
    比一句「未查完」有用得多。
    """
    if not paths:
        return
    if ctl.over():
        labels = []
        for p in hint or []:
            if p.label not in labels:
                labels.append(p.label)
        text = "、".join(labels[:3]) + ("…" if len(labels) > 3 else "")
        for p in paths:
            lf = out[p]
            lf.timeout = True
            lf.hint = text
        return
    ctl.notify()
    rc, procs = _query_batch(paths)
    if len(paths) == 1:
        out[paths[0]].rc = rc
        out[paths[0]].processes = procs
        ctl.notify(1)
        return
    if rc == 0 and not procs:
        for p in paths:
            out[p].rc = 0
            out[p].processes = []
        ctl.notify(len(paths))
        return
    mid = len(paths) // 2
    nxt = procs or hint
    _resolve(paths[:mid], out, ctl, nxt)
    _resolve(paths[mid:], out, ctl, nxt)


MAX_DIR_FILES = 500


def expand_target(target: str) -> tuple[list[str], int, str]:
    """把用户输入展开成待查文件列表。返回 (文件列表, 被截断的数量, 说明)。

    目录只查**直接子文件**，不递归：这个功能的用途是「这个文件夹删不掉」，
    递归下去几千个文件也答不出「是哪个占着」。
    """
    target = (target or "").strip().strip('"')
    if not target:
        return [], 0, "请先选择一个文件或目录"
    target = os.path.abspath(os.path.expandvars(target))
    if os.path.isfile(target):
        return [target], 0, ""
    if not os.path.isdir(target):
        return [target], 0, ""          # 交给 query_files 报「不存在」
    try:
        names = sorted(os.listdir(target))
    except OSError as exc:
        return [], 0, "目录读不了：%s" % exc
    files = []
    for n in names:
        fp = os.path.join(target, n)
        try:
            if os.path.isfile(fp):
                files.append(fp)
        except OSError:
            continue
    truncated = max(0, len(files) - MAX_DIR_FILES)
    note = ""
    if truncated:
        note = "目录里文件太多，只查了前 %d 个（按文件名排序）" % MAX_DIR_FILES
    elif not files:
        note = "这个目录里没有文件（只有子目录）—— 目录本身占用要走「删除」才知道"
    return files[:MAX_DIR_FILES], truncated, note


def query(target: str, budget_s: float = BUDGET_S,
          stop_flag=None, progress=None) -> LockReport:
    """对外入口：文件或目录都行。"""
    t0 = time.perf_counter()
    paths, truncated, note = expand_target(target)
    if not paths:
        rep = LockReport(target=target, notes=[note] if note else [],
                         budget_s=budget_s)
        rep.elapsed_ms = (time.perf_counter() - t0) * 1000
        return rep
    rep = query_files(paths, budget_s=budget_s, stop_flag=stop_flag,
                      progress=progress)
    rep.target = target
    rep.skipped = truncated
    if note:
        rep.notes.append(note)
    if os.path.isdir(os.path.expandvars((target or "").strip().strip('"'))):
        rep.notes.append("按目录查只覆盖直接子文件，不递归子目录")
    rep.elapsed_ms = (time.perf_counter() - t0) * 1000
    return rep
