"""U 盘 / 移动盘读写测速。

设计依据全部来自本机实测（Windows 11 26H2，NVMe 系统盘 + USB3 移动盘）：

1. **必须绕过系统缓存**，否则读到的是内存速度。
   顺序读用 `FILE_FLAG_NO_BUFFERING`，顺序写再加 `FILE_FLAG_WRITE_THROUGH`。
   代价是**缓冲区地址、读写长度、文件偏移三者都要按扇区对齐**，
   否则 `WriteFile` 直接返回 ERROR_INVALID_PARAMETER(87)。
   所以这里用 `VirtualAlloc` 分配后手工向上对齐到 4096。

2. **不测 4K 随机写**。实测同一块 USB 盘：
   - 仅 WRITE_THROUGH：5678 IOPS
   - WRITE_THROUGH + 每块 FlushFileBuffers：2365 IOPS
   - 无标志 + 结尾 flush：5047 IOPS
   三种口径都是 0.2~0.4 ms/次，U 盘物理上做不到 —— 固件把 4K 写收进内部
   缓存就返回了（消费级 U 盘没有掉电保护，普遍这么做）。软件层拿不到真实值，
   给了反而误导用户，所以只保留 4K 随机读，并在界面上标注「受设备缓存影响」。

3. **「是不是 U 盘」不能看 drive_type**。实测本机三个 USB 卷
   `drive_type` 全是 3（固定盘）而不是 2（可移动）—— 移动硬盘盒和不少 U 盘
   都会被 Windows 报成固定盘。只能看 `bus == "USB"`。

4. **测试文件不小于 128 MB**。实测同一块盘 64 MB 时 47 MB/s、128 MB 时
   58 MB/s，差别来自文件创建与簇分配这类一次开销（约 0.4 秒）。
   文件越小，系统开销占比越高，测出来的数越低。

5. `GetDiskFreeSpaceExW` 只认 `"X:\\"`，传 `"X:/"` 会静默返回 0，
   看起来像「磁盘已满」。所以所有路径进来先规范化。

写入范围严格受限：只创建一个名为 `SystemToolbox_speedtest.bin` 的文件，
同名文件已存在则直接放弃（绝不覆盖用户文件），测完无论如何都删除。
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import json
import os
import random
import time
from dataclasses import dataclass, field
from typing import Callable

from . import disk

IS_WIN = os.name == "nt"

TEST_NAME = "SystemToolbox_speedtest.bin"
HISTORY = "%LOCALAPPDATA%\\SystemToolbox\\usb_speed.json"
SIZES = (128, 256, 512, 1024)      # MB，128 是实测下限，再小开销占比过高

_k32 = ctypes.WinDLL("kernel32", use_last_error=True) if IS_WIN else None
if IS_WIN:
    _k32.CreateFileW.restype = wt.HANDLE
    _k32.CreateFileW.argtypes = [wt.LPCWSTR, wt.DWORD, wt.DWORD, ctypes.c_void_p,
                                 wt.DWORD, wt.DWORD, wt.HANDLE]
    _k32.WriteFile.argtypes = [wt.HANDLE, ctypes.c_void_p, wt.DWORD,
                               ctypes.POINTER(wt.DWORD), ctypes.c_void_p]
    _k32.WriteFile.restype = wt.BOOL
    _k32.ReadFile.argtypes = [wt.HANDLE, ctypes.c_void_p, wt.DWORD,
                              ctypes.POINTER(wt.DWORD), ctypes.c_void_p]
    _k32.ReadFile.restype = wt.BOOL
    _k32.SetFilePointerEx.argtypes = [wt.HANDLE, ctypes.c_longlong,
                                      ctypes.POINTER(ctypes.c_longlong), wt.DWORD]
    _k32.SetFilePointerEx.restype = wt.BOOL
    _k32.CloseHandle.argtypes = [wt.HANDLE]
    _k32.CloseHandle.restype = wt.BOOL
    _k32.GetDiskFreeSpaceExW.argtypes = [wt.LPCWSTR,
                                         ctypes.POINTER(ctypes.c_ulonglong),
                                         ctypes.POINTER(ctypes.c_ulonglong),
                                         ctypes.POINTER(ctypes.c_ulonglong)]
    _k32.GetDiskFreeSpaceExW.restype = wt.BOOL
    _k32.VirtualAlloc.restype = ctypes.c_void_p
    _k32.VirtualAlloc.argtypes = [ctypes.c_void_p, ctypes.c_size_t, wt.DWORD,
                                  wt.DWORD]
    _k32.VirtualFree.argtypes = [ctypes.c_void_p, ctypes.c_size_t, wt.DWORD]
    _k32.VirtualFree.restype = wt.BOOL

GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
CREATE_ALWAYS = 2
OPEN_EXISTING = 3
FILE_ATTRIBUTE_TEMPORARY = 0x00000100
FILE_FLAG_WRITE_THROUGH = 0x80000000
FILE_FLAG_NO_BUFFERING = 0x20000000
FILE_BEGIN = 0
MEM_COMMIT_RESERVE = 0x3000
MEM_RELEASE = 0x8000
PAGE_READWRITE = 0x04
ALIGN = 4096                 # 扇区对齐；4Kn 盘也满足
CHUNK = 4 << 20              # 4 MB 一块，USB 上够大也够细
RAND_BLOCK = 4096
RAND_COUNT = 4000


# ------------------------------------------------------------------ 盘
@dataclass
class Target:
    """一个可测的卷。"""

    letter: str = ""             # "E:"
    label: str = ""
    fs: str = ""
    total: int = 0
    free: int = 0
    bus: str = ""
    drive_type: int = 0

    @property
    def root(self) -> str:
        """`GetDiskFreeSpaceExW` 与 `CreateFileW` 都只认这个写法。"""
        return self.letter.rstrip("\\/") + "\\"

    @property
    def is_usb(self) -> bool:
        return self.bus.upper() == "USB"

    @property
    def removable(self) -> bool:
        return self.drive_type == 2

    @property
    def text(self) -> str:
        tag = self.label or "未命名"
        return f"{self.letter} {tag}"

    @property
    def detail(self) -> str:
        parts = [self.fs or "未知格式", f"{self.free / 2 ** 30:.1f} GB 可用"]
        if self.is_usb:
            parts.insert(0, "USB")
        elif self.bus:
            parts.insert(0, self.bus)
        return " · ".join(parts)

    @property
    def key(self) -> str:
        """历史记录的键：同一个盘拔到别的口还是它。"""
        return f"{self.label}|{self.total}"


def scan() -> list[Target]:
    """列出所有可写卷。USB 盘排在前面 —— 这个页面主要就是测它们。"""
    out: list[Target] = []
    try:
        vols = disk.list_volumes()
    except Exception:       # noqa: BLE001
        return out
    for v in vols:
        if not v.total or not v.fs:
            continue        # 空光驱 / 未格式化
        if v.drive_type in (4, 5):
            continue        # 网络盘、光驱测了没意义
        bus = v.bus
        if not bus:
            # `disk.list_volumes()` 只给 drive_type==3 的卷查总线，可移动盘留空。
            # 这里必须补上：判断「插没插在 USB 2.0 口」全靠它，实测 volume_bus
            # 对可移动卷同样可用（0~1 ms）。
            try:
                bus = disk.volume_bus(v.letter)
            except Exception:       # noqa: BLE001
                bus = ""
        out.append(Target(letter=v.letter, label=v.label, fs=v.fs,
                          total=v.total, free=v.free, bus=bus,
                          drive_type=v.drive_type))
    out.sort(key=lambda t: (not t.is_usb, not t.removable, t.letter))
    return out


def free_bytes(letter: str) -> int:
    root = letter.rstrip("\\/") + "\\"
    if not IS_WIN:
        return 0
    avail = ctypes.c_ulonglong(0)
    total = ctypes.c_ulonglong(0)
    free = ctypes.c_ulonglong(0)
    ok = _k32.GetDiskFreeSpaceExW(ctypes.c_wchar_p(root), ctypes.byref(avail),
                                  ctypes.byref(total), ctypes.byref(free))
    return int(avail.value) if ok else 0


# ------------------------------------------------------------------ 结果
@dataclass
class Sample:
    """一次测速的各项数值。"""

    size_mb: int = 0
    write_mbs: float = 0.0
    read_mbs: float = 0.0
    rand_read_iops: float = 0.0
    rand_read_mbs: float = 0.0
    seconds: float = 0.0
    when: str = ""
    bus: str = ""

    @property
    def ratio(self) -> float:
        """写读比。U 盘普遍 0.1~0.5，低于 0.15 说明写入被严重限制。"""
        return (self.write_mbs / self.read_mbs) if self.read_mbs else 0.0


@dataclass
class Report:
    target: Target = field(default_factory=Target)
    sample: Sample = field(default_factory=Sample)
    errors: list[str] = field(default_factory=list)
    cleaned: bool = True
    leftover: str = ""
    prev: Sample | None = None

    # -------- 分级
    @property
    def read_grade(self) -> tuple[str, str]:
        """(等级文字, 颜色 role)"""
        v = self.sample.read_mbs
        if v >= 1000:
            return ("NVMe 级", "ok")
        if v >= 400:
            return ("USB 3.2 / SATA SSD 级", "ok")
        if v >= 150:
            return ("USB 3.x 正常", "ok")
        if v >= 40:
            return ("USB 3.0 偏慢", "warn")
        if v >= 10:
            return ("USB 2.0 高速", "warn")
        return ("低于 USB 2.0 水平", "bad")

    @property
    def write_grade(self) -> tuple[str, str]:
        v = self.sample.write_mbs
        if v >= 800:
            return ("NVMe 级", "ok")
        if v >= 300:
            return ("SSD 级", "ok")
        if v >= 100:
            return ("USB 3.x 良好", "ok")
        if v >= 40:
            return ("USB 3.x 一般", "warn")
        if v >= 10:
            return ("USB 2.0 高速", "warn")
        return ("写入极慢", "bad")

    @property
    def ratio_grade(self) -> tuple[str, str]:
        """写读比分级。U 盘普遍 0.1~0.3 —— 写入要擦块，天然慢于读取。"""
        r = self.sample.ratio
        if not r:
            return ("-", "dim")
        if r >= 0.6:
            return ("优秀", "ok")
        if r >= 0.3:
            return ("正常", "ok")
        if r >= 0.15:
            return ("写入偏慢", "warn")
        return ("写入受限", "warn")

    @property
    def level(self) -> str:
        """整盘结论：ok / warn / bad。"""
        if self.errors:
            return "bad"
        roles = [self.read_grade[1], self.write_grade[1], self.ratio_grade[1]]
        if "bad" in roles:
            return "bad"
        if "warn" in roles:
            return "warn"
        return "ok"

    @property
    def headline(self) -> str:
        if self.errors:
            return "测速未完成：" + self.errors[0]
        s = self.sample
        return (f"读 {s.read_mbs:.0f} MB/s · 写 {s.write_mbs:.0f} MB/s"
                f"（{self.read_grade[0]} / {self.write_grade[0]}）")

    @property
    def hints(self) -> list[str]:
        """按实测数据给判断，不写没依据的话。"""
        out: list[str] = []
        s = self.sample
        if not s.read_mbs:
            return out
        if self.target.is_usb and s.read_mbs < 45 and s.write_mbs < 40:
            out.append("读写都没超过 USB 2.0 的量级：可能插在 USB 2.0 口上，"
                       "也可能用了只支持 2.0 的线。换到蓝色/红色 USB 3.x 口再测一次。")
        if s.read_mbs >= 150 and s.ratio and s.ratio < 0.35:
            out.append(f"读 {s.read_mbs:.0f} MB/s 但写只有 {s.write_mbs:.0f} MB/s"
                       f"（{s.ratio * 100:.0f}%）—— 这个组合通常说明盘没有 SLC 缓存"
                       "或写入被固件限速。拷贝大文件会比读取慢得多，属该型号的固有表现，"
                       "不是接口或系统的问题。")
        if s.ratio and s.ratio < 0.1 and s.read_mbs >= 150:
            out.append("写读比低于 0.1，写入是这块盘的主要瓶颈，"
                       "不建议直接在上面剪辑或装系统镜像。")
        if self.prev is not None and self.prev.write_mbs:
            drop = 1 - s.write_mbs / self.prev.write_mbs
            if drop >= 0.3:
                out.append(f"与上次（{self.prev.when}，写 {self.prev.write_mbs:.0f} MB/s）"
                           f"相比写入下降 {drop * 100:.0f}%，盘可能有老化或坏块，"
                           "建议结合 SMART / 全盘校验确认。")
        if s.rand_read_iops and s.rand_read_iops > 8000 and s.read_mbs < 200:
            out.append("4K 随机读数值偏高，多半是设备自身在读缓存，"
                       "这项只作参考，判断盘快慢看顺序读写。")
        if self.leftover:
            out.append(f"临时文件没能删除：{self.leftover}，可以手动删掉。")
        return out


# ------------------------------------------------------------------ 底层 IO
class _AlignedBuf:
    """VirtualAlloc 分配后手工向上对齐 —— NO_BUFFERING 的硬要求。"""

    __slots__ = ("want", "_raw", "addr")

    def __init__(self, size: int):
        self.want = size
        self._raw = _k32.VirtualAlloc(None, size + ALIGN, MEM_COMMIT_RESERVE,
                                      PAGE_READWRITE)
        if not self._raw:
            raise OSError("缓冲区分配失败")
        self.addr = (self._raw + ALIGN - 1) & ~(ALIGN - 1)

    def fill(self, byte: int) -> None:
        ctypes.memset(self.addr, byte, self.want)

    def free(self) -> None:
        if self._raw:
            _k32.VirtualFree(self._raw, 0, MEM_RELEASE)
            self._raw = None


def _open(path: str, write: bool, write_through: bool,
          create: bool = False):
    access = GENERIC_WRITE if write else GENERIC_READ
    if create:
        access |= GENERIC_READ
    flags = FILE_FLAG_NO_BUFFERING
    if write_through:
        flags |= FILE_FLAG_WRITE_THROUGH
    disp = CREATE_ALWAYS if create else OPEN_EXISTING
    h = _k32.CreateFileW(path, access, 0, None, disp,
                         flags | FILE_ATTRIBUTE_TEMPORARY, None)
    if not h or h == ctypes.c_void_p(-1).value or h == wt.HANDLE(-1).value:
        raise OSError(f"打不开 {path}（错误码 {ctypes.get_last_error()}）")
    return h


# ------------------------------------------------------------------ 测试
def run(letter: str, size_mb: int = 256,
        on_progress: Callable[[float, str], None] | None = None,
        stop_flag: Callable[[], bool] | None = None) -> Report:
    """跑一次测速。`on_progress(百分比 0~1, 阶段文字)`。

    只写 `TEST_NAME` 一个文件，同名已存在就放弃，结束时一律删除。
    """
    rep = Report()
    target = _target_of(letter)
    rep.target = target
    if not IS_WIN:
        rep.errors.append("仅支持 Windows")
        return rep
    if not target.letter:
        rep.errors.append("盘不存在或读不到容量")
        return rep
    free = free_bytes(target.letter)
    need = size_mb * 2 * (1 << 20)
    if free and free < need:
        rep.errors.append(f"剩余空间不足：需要约 {need / 2 ** 30:.1f} GB，"
                          f"当前只有 {free / 2 ** 30:.1f} GB")
        return rep

    path = os.path.join(target.root, TEST_NAME)
    if os.path.exists(path):
        rep.errors.append(f"盘上已有同名文件 {TEST_NAME}，为免误删请先改名或移走")
        return rep

    size = size_mb << 20
    size = (size // ALIGN) * ALIGN        # NO_BUFFERING 要求长度整扇区
    t_all = time.perf_counter()
    try:
        _seq_write(path, size, rep, on_progress, stop_flag)
        if stop_flag and stop_flag():
            rep.errors.append("已取消")
            return rep
        _seq_read(path, size, rep, on_progress, stop_flag)
        if stop_flag and stop_flag():
            rep.errors.append("已取消")
            return rep
        _rand_read(path, size, rep, on_progress, stop_flag)
    except OSError as exc:
        rep.errors.append(str(exc))
    finally:
        rep.sample.seconds = time.perf_counter() - t_all
        rep.sample.size_mb = size_mb
        rep.sample.when = time.strftime("%Y-%m-%d %H:%M")
        rep.sample.bus = target.bus
        try:
            if os.path.exists(path):
                os.remove(path)
                rep.cleaned = True
        except OSError:
            rep.cleaned = not os.path.exists(path)
            rep.leftover = path
    if not rep.errors:
        rep.prev = last_of(target)
        if rep.sample.read_mbs or rep.sample.write_mbs:
            _remember(target, rep.sample)
    return rep


def _target_of(letter: str) -> Target:
    letter = (letter or "").strip().rstrip("\\/")
    if len(letter) == 1:
        letter += ":"
    for t in scan():
        if t.letter.upper() == letter.upper():
            return t
    return Target(letter=letter.upper() if letter else "")


def _seq_write(path: str, size: int, rep: Report,
               prog, stop) -> None:
    buf = _AlignedBuf(CHUNK)
    buf.fill(0x5A)
    h = _open(path, write=True, write_through=True, create=True)
    written = wt.DWORD(0)
    done = 0
    t0 = time.perf_counter()
    try:
        while done < size:
            if stop and stop():
                return
            n = min(CHUNK, size - done)
            if not _k32.WriteFile(h, buf.addr, n, ctypes.byref(written), None):
                raise OSError(f"写入失败（错误码 {ctypes.get_last_error()}）")
            done += written.value or n
            if prog:
                prog(done / size * 0.4, f"顺序写入 {done / 2 ** 20:.0f} / "
                                        f"{size / 2 ** 20:.0f} MB")
    finally:
        _k32.CloseHandle(h)
        buf.free()
    sec = time.perf_counter() - t0
    rep.sample.write_mbs = (size / 2 ** 20) / sec if sec else 0.0


def _seq_read(path: str, size: int, rep: Report,
              prog, stop) -> None:
    buf = _AlignedBuf(CHUNK)
    h = _open(path, write=False, write_through=False)
    got = wt.DWORD(0)
    done = 0
    t0 = time.perf_counter()
    try:
        while done < size:
            if stop and stop():
                return
            n = min(CHUNK, size - done)
            if not _k32.ReadFile(h, buf.addr, n, ctypes.byref(got), None):
                raise OSError(f"读取失败（错误码 {ctypes.get_last_error()}）")
            if not got.value:
                break
            done += got.value
            if prog:
                prog(0.4 + done / size * 0.4, f"顺序读取 {done / 2 ** 20:.0f} / "
                                              f"{size / 2 ** 20:.0f} MB")
    finally:
        _k32.CloseHandle(h)
        buf.free()
    sec = time.perf_counter() - t0
    rep.sample.read_mbs = (done / 2 ** 20) / sec if sec else 0.0


def _rand_read(path: str, size: int, rep: Report,
               prog, stop) -> None:
    buf = _AlignedBuf(RAND_BLOCK)
    h = _open(path, write=False, write_through=False)
    got = wt.DWORD(0)
    pos = ctypes.c_longlong(0)
    slots = max(1, size // RAND_BLOCK)
    rnd = random.Random(20261022)
    ok = 0
    t0 = time.perf_counter()
    try:
        for i in range(RAND_COUNT):
            if stop and stop():
                break
            off = rnd.randrange(slots) * RAND_BLOCK
            if not _k32.SetFilePointerEx(h, off, ctypes.byref(pos), FILE_BEGIN):
                break
            if not _k32.ReadFile(h, buf.addr, RAND_BLOCK, ctypes.byref(got), None):
                break
            if got.value != RAND_BLOCK:
                break
            ok += 1
            if prog and i % 200 == 0:
                prog(0.8 + i / RAND_COUNT * 0.2,
                     f"4K 随机读 {i} / {RAND_COUNT} 次")
    finally:
        _k32.CloseHandle(h)
        buf.free()
    sec = time.perf_counter() - t0
    rep.sample.rand_read_iops = ok / sec if sec else 0.0
    rep.sample.rand_read_mbs = (ok * RAND_BLOCK / 2 ** 20) / sec if sec else 0.0


# ------------------------------------------------------------------ 历史
def _hist_path() -> str:
    return os.path.expandvars(HISTORY)


def load_history() -> dict:
    try:
        with open(_hist_path(), encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def last_of(target: Target) -> Sample | None:
    """上一次同一块盘的记录，用来判老化。"""
    recs = load_history().get(target.key) or []
    if not recs:
        return None
    r = recs[-1]
    try:
        return Sample(size_mb=int(r.get("size_mb", 0)),
                      write_mbs=float(r.get("write_mbs", 0)),
                      read_mbs=float(r.get("read_mbs", 0)),
                      rand_read_iops=float(r.get("rand_read_iops", 0)),
                      when=str(r.get("when", "")),
                      bus=str(r.get("bus", "")))
    except (TypeError, ValueError):
        return None


def _remember(target: Target, s: Sample) -> None:
    data = load_history()
    recs = data.get(target.key)
    if not isinstance(recs, list):
        recs = []
    recs.append({"when": s.when, "size_mb": s.size_mb,
                 "write_mbs": round(s.write_mbs, 1),
                 "read_mbs": round(s.read_mbs, 1),
                 "rand_read_iops": round(s.rand_read_iops),
                 "bus": s.bus})
    data[target.key] = recs[-8:]      # 只留最近 8 次，够看趋势
    path = _hist_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except OSError:
        pass


def history_rows(target: Target) -> list[tuple[str, str, str, str]]:
    """(时间, 顺序读, 顺序写, 较上次写入变化) —— 直接塞表格。

    写入变化用相对上一条记录算：U 盘老化、颗粒磨损最先体现在写入掉速，
    读者一眼能看出「这次比上次慢了三成」比看绝对值更有用。
    """
    out: list[tuple[str, str, str, str]] = []
    prev = 0.0
    for r in (load_history().get(target.key) or [])[-8:]:
        try:
            read = float(r.get("read_mbs", 0))
            write = float(r.get("write_mbs", 0))
        except (TypeError, ValueError):
            continue
        delta = ""
        if prev > 0:
            d = (write / prev - 1) * 100
            delta = f"{d:+.0f}%" if abs(d) >= 5 else "基本持平"
        out.append((str(r.get("when", "")), f"{read:.0f} MB/s",
                    f"{write:.0f} MB/s", delta))
        prev = write
    return out
