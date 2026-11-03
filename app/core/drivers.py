# -*- coding: utf-8 -*-
"""驱动体检：装了哪些第三方驱动、驱动文件签名如何、此刻谁在内核里跑。

## 三个数据源（本机 Win11 26H2 / 2026-10-27 实测）

**一、`HKLM\\SYSTEM\\DriverDatabase`（驱动包数据库，整棵读 17 ms）**

- `DriverInfFiles\\<INF 名>` 的**默认值**是该 INF 的所有包标识，
  `Active` 指向当前生效的那一个。实测 294 个 INF / 294 个包，没有多版本残留。
- `DriverPackages\\<xxx.inf_amd64_<hash>>` 的字段：
  `Provider` / `InfName` / `SignerName` / `Catalog` / `ImportDate` /
  `OemPath` / `FileSize` / `StatusFlags` / `SignerScore`。
- **只有第三方包（`oemNN.inf`）才有 `SignerName` 和 `ImportDate`**：
  本机 23 个 oem 包全都有，271 个内置包全都没有 —— 所以「谁签的、什么时候装的、
  从哪个目录装的」这三件事只对第三方驱动有意义。
- `Version` 是 48 字节二进制，**头 8 字节不是 FILETIME**（实测 `0x000000000009FFFF`），
  别试图从它解版本号。日期一律用 `ImportDate`（8 字节 FILETIME，可解），
  文案里写「导入日期」而不是「驱动版本日期」。

**二、`psapi.EnumDeviceDrivers` + `GetDeviceDriverBaseNameW`（0.3 ms）**

拿到此刻加载的 235 个内核模块。注意服务名与模块名**不总相等**
（服务 `ACPI` → 模块 `acpi.sys`；服务 `Ntfs` 干脆没有 ImagePath）。
所以「已加载」先按 **ImagePath 的文件名** 匹配（实测 186/461 命中），
服务名兜底（173/461）。

**三、`sign.verify`（并行 8 线程，540 个文件冷扫 ~2 秒，18 ms/个/线程）**

本机结论（540 个文件）：537 个有效（内嵌 + 目录签名）、2 个「签名可疑」、
1 个未签名（`bthmodem.sys`，未加载）。串行 540 个要 10.9 秒，
所以两件事都要做：结果按 `(路径, 大小, mtime)` 缓存到
`%LOCALAPPDATA%\\SystemToolbox\\drvsign.json`（热扫 0.35 秒），
冷扫则用 8 线程并行（200 个文件实测 3.77s → 0.78s）。
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import datetime
import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from . import sign as sg
from .analysis import Finding

__all__ = ["DriverPackage", "DriverService", "DriverFile", "Report",
           "packages", "services", "loaded_modules", "scan_signatures",
           "audit", "verdict", "resolve_image", "START_CN", "TYPE_CN"]

DRIVER_DB = r"SYSTEM\DriverDatabase"
SERVICES_KEY = r"SYSTEM\CurrentControlSet\Services"

#: 服务 Start 类型 → 中文（和「服务」页签口径一致）
START_CN = {0: "引导", 1: "系统", 2: "自动", 3: "手动", 4: "禁用"}

#: 服务 Type 低 4 位 → 中文
TYPE_CN = {1: "内核驱动", 2: "文件系统驱动", 8: "文件系统驱动",
           0x0C: "用户态驱动"}

#: 签名扫描的默认预算。484 个 × 18 ms ≈ 8 秒，留一倍余量给慢盘。
SIGN_BUDGET_S = 25.0

#: 缓存文件（和启动项备份同一个目录）
def cache_file() -> str:
    base = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")),
                        "SystemToolbox")
    return os.path.join(base, "drvsign.json")


def _drivers_dir() -> str:
    return os.path.join(os.environ.get("SystemRoot", r"C:\Windows"),
                        "System32", "drivers")


# ---------------------------------------------------------------- 注册表小工具
def _reg_values(path: str) -> dict:
    import winreg
    out: dict = {}
    try:
        key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path)
    except OSError:
        return out
    try:
        while True:
            try:
                name, val, _kind = winreg.EnumValue(key, len(out))
            except OSError:
                break
            out[name] = val
    finally:
        winreg.CloseKey(key)
    return out


def _reg_subkeys(path: str) -> list[str]:
    import winreg
    names: list[str] = []
    try:
        key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path)
    except OSError:
        return names
    try:
        while True:
            try:
                names.append(winreg.EnumKey(key, len(names)))
            except OSError:
                break
    finally:
        winreg.CloseKey(key)
    return names


def _filetime_text(raw) -> str:
    """8 字节 FILETIME → `2026-10-22`。空值或明显不合理返回空串。"""
    if not isinstance(raw, (bytes, bytearray)) or len(raw) != 8:
        return ""
    ticks = int.from_bytes(raw, "little")
    if not ticks:                       # 0 = 从未导入
        return ""
    try:
        ts = datetime.datetime(1601, 1, 1) + datetime.timedelta(
            microseconds=ticks / 10)
    except (OverflowError, OSError):
        return ""
    if ts.year < 1990 or ts.year > 2100:
        return ""
    return ts.strftime("%Y-%m-%d")


# ---------------------------------------------------------------- 驱动包
#: 签名者全名太长，表格里放不下。微软那个占本机 21/23 个第三方包，
#: 全名 48 个字符会把「来源」列挤没 —— 表格用短名，tooltip 给全名。
SIGNER_SHORT = {
    "Microsoft Windows Hardware Compatibility Publisher": "Microsoft WHQL",
    "Microsoft Windows": "Microsoft",
    "Microsoft Corporation": "Microsoft",
}


@dataclass
class DriverPackage:
    """一个驱动包（DriverDatabase\\DriverPackages 的一项）。"""
    key: str                 # `xxx.inf_amd64_hash`
    inf: str = ""            # 发布名：第三方便是 oemNN.inf，内置是原 INF 名
    original_inf: str = ""   # InfName（原始 INF 文件名）
    provider: str = ""
    signer: str = ""         # 只有第三方包有
    catalog: str = ""
    installed: str = ""      # 导入日期（只有第三方包有）
    source: str = ""         # OemPath：装的时候从哪个目录拿的
    size: int = 0
    active: bool = False

    @property
    def third_party(self) -> bool:
        """`oemNN.inf` 是 Windows 给后装驱动的发布名。"""
        return bool(re.match(r"(?i)^oem\d+\.inf$", self.inf))

    @property
    def signer_text(self) -> str:
        """表格里显示的短名。"""
        if self.signer:
            return SIGNER_SHORT.get(self.signer, self.signer)
        return "Microsoft" if not self.third_party else "（未记录）"

    @property
    def signer_full(self) -> str:
        """鼠标悬停时的全名。"""
        if self.signer:
            return self.signer
        return "Microsoft（系统内置驱动包）" if not self.third_party else "（未记录签名者）"

    @property
    def size_text(self) -> str:
        if not self.size:
            return "—"
        if self.size >= 1048576:
            return "%.1f MB" % (self.size / 1048576.0)
        return "%d KB" % max(1, self.size // 1024)

    @property
    def source_text(self) -> str:
        """OemPath 常是临时解压目录，太长就砍中间。"""
        if not self.source:
            return "—"
        if len(self.source) <= 46:
            return self.source
        return self.source[:20] + "…" + self.source[-24:]


def packages() -> list[DriverPackage]:
    """读全部驱动包。实测 294 个 / 17 ms。"""
    inf_files = _reg_subkeys(DRIVER_DB + "\\DriverInfFiles")
    out: list[DriverPackage] = []
    for inf in inf_files:
        v = _reg_values(DRIVER_DB + "\\DriverInfFiles\\" + inf)
        ids = v.get("") or []
        if isinstance(ids, str):
            ids = [ids]
        active = v.get("Active") or ""
        for pid in ids:
            d = _reg_values(DRIVER_DB + "\\DriverPackages\\" + pid)
            out.append(DriverPackage(
                key=pid,
                inf=inf,
                original_inf=str(d.get("InfName") or ""),
                provider=str(d.get("Provider") or ""),
                signer=str(d.get("SignerName") or ""),
                catalog=str(d.get("Catalog") or ""),
                installed=_filetime_text(d.get("ImportDate")),
                source=str(d.get("OemPath") or ""),
                size=int(d.get("FileSize") or 0),
                active=(pid == active),
            ))
    out.sort(key=lambda p: (not p.third_party, p.inf.lower()))
    return out


# ---------------------------------------------------------------- 已加载模块
def loaded_modules() -> list[str]:
    """此刻加载的内核模块（`ntoskrnl.exe` `tcpip.sys` …）。实测 0.3 ms。"""
    try:
        psapi = ctypes.WinDLL("psapi", use_last_error=True)
    except OSError:
        return []
    psapi.EnumDeviceDrivers.argtypes = [ctypes.POINTER(ctypes.c_void_p), wt.DWORD,
                                        ctypes.POINTER(wt.DWORD)]
    psapi.GetDeviceDriverBaseNameW.argtypes = [ctypes.c_void_p, wt.LPWSTR, wt.DWORD]
    need = wt.DWORD(0)
    psapi.EnumDeviceDrivers(None, 0, ctypes.byref(need))
    count = need.value // ctypes.sizeof(ctypes.c_void_p)
    if count <= 0:
        return []
    arr = (ctypes.c_void_p * count)()
    if not psapi.EnumDeviceDrivers(arr, ctypes.sizeof(arr), ctypes.byref(need)):
        return []
    out: list[str] = []
    for i in range(need.value // ctypes.sizeof(ctypes.c_void_p)):
        buf = ctypes.create_unicode_buffer(260)
        if psapi.GetDeviceDriverBaseNameW(arr[i], buf, 260):
            out.append(buf.value)
    return out


def resolve_image(image_path: str) -> str:
    """`ImagePath` → 真实文件路径。

    实测本机 461 个驱动服务的 ImagePath 有四种写法：
    `\\SystemRoot\\System32\\drivers\\x.sys`（231）、`System32\\drivers\\x.sys`（212）、
    `\\??\\C:\\...`（6）、空（12，全是 Ntfs/Fastfat/Beep 这类内置文件系统驱动）。
    **空 ImagePath 不是「文件缺失」**，是内置驱动的正常写法。
    """
    raw = (image_path or "").strip()
    if not raw:
        return ""
    if raw.startswith('"'):
        raw = raw[1:].split('"')[0]
    else:
        m = re.search(r"\.sys\b", raw, re.I)
        if m:
            raw = raw[:m.end()]
    raw = os.path.expandvars(raw)
    low = raw.lower()
    root = os.environ.get("SystemRoot", r"C:\Windows")
    if low.startswith("\\??\\"):
        raw = raw[4:]
    elif low.startswith("\\systemroot\\"):
        raw = os.path.join(root, raw[len("\\SystemRoot\\"):])
    elif low.startswith("system32\\") or low.startswith("system32/"):
        raw = os.path.join(root, raw)
    return os.path.normpath(raw)


# ---------------------------------------------------------------- 驱动服务
@dataclass
class DriverService:
    name: str
    start: int = 3
    type: int = 1
    image: str = ""
    file: str = ""
    exists: bool = True
    loaded: bool = False

    @property
    def start_text(self) -> str:
        return START_CN.get(self.start, "未知(%s)" % self.start)

    @property
    def type_text(self) -> str:
        return TYPE_CN.get(self.type & 0x0F, "驱动")

    @property
    def has_file(self) -> bool:
        """ImagePath 为空是内置驱动的正常写法，不参与「文件缺失」判定。"""
        return bool(self.file)


def services() -> list[DriverService]:
    """全部内核/文件系统驱动服务。实测 461 个 / 106 ms。"""
    out: list[DriverService] = []
    for name in _reg_subkeys(SERVICES_KEY):
        v = _reg_values(SERVICES_KEY + "\\" + name)
        t = v.get("Type")
        if not isinstance(t, int) or (t & 0x0F) not in (1, 2):
            continue
        s = v.get("Start")
        image = str(v.get("ImagePath") or "")
        path = resolve_image(image)
        out.append(DriverService(
            name=name,
            start=int(s) if isinstance(s, int) else 4,
            type=t,
            image=image,
            file=path,
            exists=(os.path.isfile(path) if path else True),
        ))
    out.sort(key=lambda s: s.name.lower())
    return out


# ---------------------------------------------------------------- 签名扫描
@dataclass
class DriverFile:
    path: str
    info: sg.SignInfo | None = None
    loaded: bool = False
    services: list[str] = field(default_factory=list)
    start: int = -1
    cached: bool = False

    @property
    def name(self) -> str:
        return os.path.basename(self.path)

    @property
    def exists(self) -> bool:
        return os.path.isfile(self.path)

    @property
    def kind(self) -> str:
        return self.info.kind if self.info else "error"

    @property
    def level_text(self) -> str:
        return self.info.level_text if self.info else "未扫描"

    @property
    def level(self) -> str:
        """给表格着色。签名可疑但**已加载**的抬到警示档：那是真在跑的东西。"""
        if self.info is None:
            return "faint"
        if self.info.kind in ("uncatalogued", "bad"):
            return "crit" if self.loaded else "warn"
        if self.info.kind == "warn":
            return "warn" if self.loaded else "info"
        return "ok"

    @property
    def problem(self) -> bool:
        """需要报出来的：没签名、签名坏了、签名可疑。"""
        return bool(self.info and self.info.kind in ("uncatalogued", "bad", "warn"))

    @property
    def start_text(self) -> str:
        return START_CN.get(self.start, "—") if self.start >= 0 else "—"


def _load_cache(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_cache(path: str, data: dict) -> None:
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        os.replace(tmp, path)
    except OSError:
        pass


def _cached_info(p: str, old: dict) -> sg.SignInfo:
    return sg.SignInfo(path=p, rc=int(old.get("rc") or 0),
                       kind=str(old.get("kind") or "ok"),
                       method=str(old.get("method") or ""),
                       catalog=str(old.get("cat") or ""),
                       note=str(old.get("note") or ""))


def _stamp(p: str) -> list[int] | None:
    try:
        st = os.stat(p)
    except OSError:
        return None
    return [int(st.st_size), int(st.st_mtime)]


def scan_signatures(paths: list[str], stop_flag=None, progress=None,
                    budget_s: float = SIGN_BUDGET_S, use_cache: bool = True,
                    cache_path: str | None = None
                    ) -> tuple[list[sg.SignInfo], int, bool]:
    """逐个校验签名。返回 `(结果, 命中缓存数, 是否被截断)`。

    **缓存键是 `(大小, mtime)`**：驱动文件被替换（打补丁、升级）一定会变，
    命中就跳过 WinVerifyTrust —— 这是让「全部体检」不每次等 10 秒的关键。

    **并行 8 线程**：WinVerifyTrust 与 CryptCAT* 都是每次调用自建上下文
    （`_verify_catalog` 里 Acquire/Release 是局部的），没有共享句柄，
    可以安全并行。本机实测 200 个 `.sys`：串行 3.77s / 4 线程 1.48s /
    8 线程 0.78s（16 核机器）。540 个文件冷扫 ~2 秒。
    """
    cache_path = cache_path or cache_file()
    cache = _load_cache(cache_path) if use_cache else {}
    out: list[sg.SignInfo] = []
    todo: list[tuple[str, list[int]]] = []
    hits = 0

    for p in paths:
        stamp = _stamp(p)
        if stamp is None:
            out.append(sg.SignInfo(path=p, kind="missing", note="文件不存在"))
            continue
        old = cache.get(p)
        if (use_cache and isinstance(old, dict)
                and old.get("size") == stamp[0] and old.get("mtime") == stamp[1]):
            out.append(_cached_info(p, old))
            hits += 1
        else:
            todo.append((p, stamp))

    total = len(paths)
    done = len(out)
    cut = False
    fresh: dict = {}

    if progress is not None and done:
        progress(min(done, total), total)

    if todo:
        workers = min(8, max(2, (os.cpu_count() or 4)))
        chunk = 32
        deadline = time.perf_counter() + max(0.0, budget_s)

        def one(job):
            p, stamp = job
            return sg.verify(p), p, stamp

        for i in range(0, len(todo), chunk):
            if _stopped(stop_flag) or time.perf_counter() >= deadline:
                cut = True
                break
            part = todo[i:i + chunk]
            with ThreadPoolExecutor(max_workers=min(workers, len(part))) as pool:
                for info, p, stamp in pool.map(one, part):
                    out.append(info)
                    fresh[p] = {"size": stamp[0], "mtime": stamp[1],
                                "rc": info.rc, "kind": info.kind,
                                "method": info.method, "cat": info.catalog,
                                "note": info.note}
                    done += 1
                    # 每 8 个报一次：540 次跨线程信号会让 UI 掉帧
                    if progress is not None and (done % 8 == 0 or done >= total):
                        progress(min(done, total), total)

    if use_cache and fresh:
        cache.update(fresh)
        keep = {k: v for k, v in cache.items() if os.path.isfile(k)}
        _save_cache(cache_path, keep)
    return out, hits, cut


def _stopped(flag) -> bool:
    """兼容两种约定：`[bool]`（run_bg 的 stop_flag）和 `callable`。"""
    if flag is None:
        return False
    if callable(flag):
        return bool(flag())
    try:
        return bool(flag[0])
    except (TypeError, IndexError, KeyError):
        return False


# ---------------------------------------------------------------- 汇总
@dataclass
class Report:
    packages: list[DriverPackage] = field(default_factory=list)
    services: list[DriverService] = field(default_factory=list)
    files: list[DriverFile] = field(default_factory=list)
    modules: int = 0
    image_less: int = 0          # ImagePath 为空的内置驱动服务（Ntfs / Beep…）
    cache_hits: int = 0
    signature_cut: bool = False
    elapsed_ms: float = 0.0

    @property
    def third_packages(self) -> list[DriverPackage]:
        return [p for p in self.packages if p.third_party]

    @property
    def builtin_count(self) -> int:
        return sum(1 for p in self.packages if not p.third_party)

    @property
    def unsigned(self) -> list[DriverFile]:
        return [f for f in self.files
                if f.info is not None and f.info.kind in ("uncatalogued", "bad")]

    @property
    def unsigned_loaded(self) -> list[DriverFile]:
        return [f for f in self.unsigned if f.loaded]

    @property
    def suspicious_sign(self) -> list[DriverFile]:
        return [f for f in self.files if f.info is not None and f.info.kind == "warn"]

    @property
    def missing_files(self) -> list[DriverService]:
        """服务登记了 ImagePath 但文件不在 —— 这才是真问题。"""
        return [s for s in self.services if s.has_file and not s.exists]

    @property
    def loaded_count(self) -> int:
        return sum(1 for f in self.files if f.loaded)


def audit(stop_flag=None, progress=None, want_sign: bool = True,
          budget_s: float = SIGN_BUDGET_S) -> Report:
    """跑一遍驱动体检。`progress(done, total, text)` 与 run_bg 的 tick 对齐。"""
    t0 = time.perf_counter()
    rep = Report()

    def tick(done, total, text=""):
        if progress is not None:
            progress(done, total, text)

    rep.packages = packages()
    tick(1, 4, "读驱动包数据库")
    svcs = services()
    rep.services = svcs
    mods = loaded_modules()
    rep.modules = len(mods)
    tick(2, 4, "取已加载模块")

    # 服务 → 文件 → 加载状态
    # key 必须 normcase：ImagePath 里 `DRIVERS` / `drivers` 大小写不一致很常见，
    # 不归一会在文件表里出现同一个文件的两行（实测多出 164 行）。
    by_file: dict[str, DriverFile] = {}
    stem = {m.lower().rsplit(".", 1)[0] for m in mods}
    for s in svcs:
        if not s.file:
            rep.image_less += 1      # 内置文件系统驱动，Ntfs/Beep 这类，没有独立文件
            continue
        key = os.path.normcase(s.file)
        f = by_file.get(key)
        if f is None:
            f = DriverFile(path=s.file)
            by_file[key] = f
        f.services.append(s.name)
        if f.start < 0 or s.start < f.start:
            f.start = s.start
    for key, f in by_file.items():
        name = os.path.basename(f.path).lower()
        f.loaded = (name.rsplit(".", 1)[0] in stem)

    # 签名：除了 drivers 目录里的 .sys，服务登记的**其它合法路径**（System32 根目录、
    # 厂商自带目录）也要查 —— 本机实测这类有 56 个，漏掉就会在表里显示「未扫描」。
    drv_dir = _drivers_dir()
    targets: list[str] = []
    seen: set[str] = set()
    try:
        for n in sorted(os.listdir(drv_dir)):
            p = os.path.join(drv_dir, n)
            if n.lower().endswith(".sys") and os.path.isfile(p):
                targets.append(p)
                seen.add(os.path.normcase(p))
    except OSError:
        pass
    for f in by_file.values():
        if f.path and os.path.normcase(f.path) not in seen and os.path.isfile(f.path):
            targets.append(f.path)
            seen.add(os.path.normcase(f.path))

    if want_sign and targets:
        state = {"last": 0.0}

        def prog(done, total):
            # 每 8 个报一次：487 次跨线程信号没必要，反而让 UI 掉帧
            if done - state["last"] < 8 and done < total:
                return
            state["last"] = done
            tick(2 + 1, 4, "校验驱动签名 %d/%d" % (done, total))

        infos, hits, cut = scan_signatures(targets, stop_flag, prog, budget_s)
        rep.cache_hits = hits
        rep.signature_cut = cut
        found = {os.path.normcase(i.path): i for i in infos}
        for p in targets:
            # 这里一定要用 normcase 查：by_file 的键是归一过的，
            # 直接用原路径查永远落空，会把同一个文件建成两个对象（踩过）。
            f = by_file.get(os.path.normcase(p)) or DriverFile(path=p)
            f.info = found.get(os.path.normcase(p))
            rep.files.append(f)
        for key, f in by_file.items():
            if f not in rep.files:
                rep.files.append(f)
    else:
        rep.files = list(by_file.values())

    rep.files.sort(key=lambda f: (not f.problem, not f.loaded, f.name.lower()))
    tick(4, 4, "汇总")
    rep.elapsed_ms = (time.perf_counter() - t0) * 1000
    return rep


def verdict(rep: Report) -> tuple[str, str]:
    """一句话结论：`(severity, 文案)`。"""
    findings = build_findings(rep)
    if not findings:
        return "ok", "驱动层面没有发现问题"
    order = {"crit": 0, "warn": 1, "info": 2, "ok": 3}
    worst = min(findings, key=lambda f: order.get(f.severity, 9))
    return worst.severity, worst.title


def build_findings(rep: Report) -> list:
    """把报告变成结论卡。"""
    out = []

    if rep.unsigned_loaded:
        names = "、".join(f.name for f in rep.unsigned_loaded[:4])
        out.append(Finding(
            severity="crit", topic="drv-unsigned-loaded",
            title="%d 个无签名驱动正在内核里运行：%s"
                  % (len(rep.unsigned_loaded), names),
            detail="这些 .sys 既没有内嵌签名，也没有出现在系统目录（catalog）里，"
                   "但当前已加载。反作弊、杀软、驱动签名强制策略都会因此报错，"
                   "也可能是被替换过的系统文件。",
            advice=["确认来源：不是自己装的驱动就查一下同目录的其它 .sys；"
                    "确认无用可在设备管理器卸载对应设备后重启。"],
            count=len(rep.unsigned_loaded)))

    idle = [f for f in rep.unsigned if not f.loaded]
    if idle:
        names = "、".join(f.name for f in idle[:4])
        out.append(Finding(
            severity="warn", topic="drv-unsigned",
            title="%d 个驱动文件没有有效签名：%s" % (len(idle), names),
            detail="当前没有加载，所以不影响开机；但一旦被某个服务拉起，"
                   "就会触发签名校验失败。",
            advice=["多半是第三方工具（硬件监控、虚拟化、外设）留下的旧文件，"
                    "确认不再使用可以删掉。"],
            count=len(idle)))

    warn_sign = rep.suspicious_sign
    if warn_sign:
        # 「签名可疑 + 已加载」比「签名可疑但躺着」严重一档：本机实测
        # OrayUSBVHCI.sys（向日葵的 USB 重定向驱动）就是证书链不完整还在跑。
        live = [f for f in warn_sign if f.loaded]
        names = "、".join(f.name for f in warn_sign[:4])
        notes = {f.info.note for f in warn_sign if f.info and f.info.note}
        head = "%d 个驱动签名只能打问号：%s" % (len(warn_sign), names)
        if live:
            head = "%d 个签名可疑的驱动正在运行：%s" % (
                len(live), "、".join(f.name for f in live[:4]))
            advice = ["签名可疑 = 证书链或有效期有问题，不代表文件被改过"
                      "（签名摘要本身校验通过了）。这类驱动常见于外设、远控、"
                      "虚拟化工具；如果不想让它继续加载，去设备管理器卸载对应设备。"]
            sev = "warn"
        else:
            advice = ["证书过期在系统自带驱动里很常见（微软不再续签老组件），"
                      "不影响使用；只要签名摘要校验通过就不是篡改。"]
            sev = "info"
        out.append(Finding(
            severity=sev, topic="drv-sign-warn",
            title=head,
            detail="；".join(sorted(notes)) or "证书链或有效期有问题。",
            advice=advice,
            count=len(warn_sign)))

    miss = rep.missing_files
    if miss:
        names = "、".join(s.name for s in miss[:4])
        out.append(Finding(
            severity="warn", topic="drv-svc-missing",
            title="%d 个驱动服务登记的文件不存在：%s" % (len(miss), names),
            detail="服务注册表里写着 ImagePath，但那个 .sys 已经不在磁盘上 —— "
                   "典型是卸载没清干净，或被杀软隔离。"
                   "这类服务要么加载失败，要么让设备显示感叹号。",
            advice=["在设备管理器里卸载对应设备（勾选删除驱动），或确认是残留后"
                    "在「系统优化 → 服务」里禁用该服务。"],
            count=len(miss)))

    third = rep.third_packages
    if third:
        last = max((p.installed for p in third if p.installed), default="")
        out.append(Finding(
            severity="info", topic="drv-third",
            title="除系统自带外，本机额外装了 %d 个驱动包" % len(third),
            detail="最近一次导入" + (("于 " + last) if last else "日期未记录")
                   + "。签名者：" + "、".join(sorted({p.signer_text for p in third})[:3])
                   + "。明细见右侧「第三方驱动包」。",
            advice=["这里只列事实。装了不认识的驱动包时，可以按「来源」列回溯"
                    "它是从哪个目录装的。"],
            count=len(third)))

    if not out:
        out.append(Finding(
            severity="ok", topic="drv-ok",
            title="%d 个驱动包、%d 个驱动文件，签名与加载状态都正常"
                  % (len(rep.packages), len(rep.files)),
            detail="内置 %d 个 + 第三方 %d 个；此刻加载 %d 个内核模块。"
                   % (rep.builtin_count, len(third), rep.modules),
            advice=["驱动层没有需要处理的项目。"]))
    order = {"crit": 0, "warn": 1, "info": 2, "ok": 3}
    out.sort(key=lambda f: order.get(f.severity, 9))
    return out
