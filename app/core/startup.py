# -*- coding: utf-8 -*-
"""启动项管理：注册表 Run / 启动文件夹 / 计划任务。

设计要点
- **只改「启用状态」，绝不删除启动项本体**：
  注册表项走 `Explorer\\StartupApproved\\Run*`（任务管理器的同一套机制），
  启动文件夹项移到备份区、计划任务用 schtasks /disable。
  三种方式都能原样还原，不会出现「关了就找不回来」。
- 每次开关动作都往 `startup_actions.jsonl` 追加一条，记录原始数据，
  出问题可以照着还原。
"""
from __future__ import annotations

import ctypes
import json
import os
import re
import shutil
import subprocess
import time
from ctypes import wintypes
from dataclasses import dataclass

IS_WIN = os.name == "nt"
CREATE_NO_WINDOW = 0x08000000

HKLM = "HKLM"
HKCU = "HKCU"
_HIVE_CONST = {"HKLM": 0x80000002, "HKCU": 0x80000001}

RUN = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_WOW = r"Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Run"
APPROVED = r"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\{0}"

# StartupApproved 的数据格式：12 字节，首字节奇偶表示启用/禁用，
# 后 8 字节是禁用时间（FILETIME），任务管理器也是这么写的
_ENABLED = bytes([0x02, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0])


def disabled_data() -> bytes:
    ft = int((time.time() + 11644473600) * 10_000_000)
    return bytes([0x03, 0, 0, 0]) + ft.to_bytes(8, "little")


def is_disabled_blob(data: bytes) -> bool:
    """StartupApproved 的首字节为奇数即「已禁用」（02/06 启用，03/07 禁用）。"""
    return bool(data) and (data[0] & 1) == 1


# ------------------------------------------------------------------ 数据结构
@dataclass
class StartupItem:
    uid: str
    name: str
    command: str
    kind: str                    # 注册表 · 当前用户 / 注册表 · 本机 / 启动文件夹 / 计划任务
    location: str
    enabled: bool = True
    can_toggle: bool = True
    note: str = ""
    publisher: str = ""
    target: str = ""
    exists: bool = True
    # 操作元数据
    hive: str = ""
    reg_path: str = ""
    value_name: str = ""
    approval_path: str = ""
    file_path: str = ""
    task_name: str = ""

    @property
    def state_text(self) -> str:
        return "已启用" if self.enabled else "已禁用"

    @property
    def is_ms(self) -> bool:
        """微软自带任务（位于 \\Microsoft 文件夹下）。"""
        return self.location.lower().startswith("\\microsoft")


# ------------------------------------------------------------------ 通用
def _run(args: list[str], timeout: int = 20) -> tuple[int, str]:
    if not IS_WIN:
        return (1, "")
    try:
        p = subprocess.run(args, capture_output=True, timeout=timeout,
                           creationflags=CREATE_NO_WINDOW)
    except Exception as exc:                   # noqa: BLE001
        return (1, str(exc))
    raw = p.stdout or b""
    for enc in ("utf-8", "gbk", "mbcs"):
        try:
            return (p.returncode, raw.decode(enc))
        except (UnicodeDecodeError, LookupError):
            continue
    return (p.returncode, raw.decode("utf-8", errors="replace"))


def parse_target(cmd: str) -> str:
    """从命令行里取出可执行文件路径（兼容带空格但没引号的老式写法）。"""
    s = (cmd or "").strip()
    if not s:
        return ""
    if s.startswith('"'):
        end = s.find('"', 1)
        return s[1:end] if end > 0 else s[1:]
    m = re.match(r"^(.+?\.(?:exe|com|bat|cmd|vbs|ps1|lnk|dll))(?:\s|,|$)", s, re.I)
    if m:
        return m.group(1)
    # 没引号又带空格：贪婪到最后一个像可执行文件的片段
    m = re.match(r"^(.+\.(?:exe|bat|cmd|vbs|ps1|lnk))(?:\s|$)", s, re.I)
    return m.group(1) if m else s.split(" ")[0]


def expand_target(path: str) -> str:
    """展开命令行里的 %windir% / %SystemRoot% 这类变量。"""
    if not path:
        return ""
    if "%" not in path:
        return path
    out = os.path.expandvars(path)
    if "%" in out:                       # 还有没展开的（未知变量），手动处理常见的
        for name, val in (("windir", os.environ.get("SystemRoot", r"C:\Windows")),
                          ("systemroot", os.environ.get("SystemRoot", r"C:\Windows")),
                          ("programfiles", os.environ.get("ProgramFiles", "")),
                          ("programfiles(x86)", os.environ.get("ProgramFiles(x86)", "")),
                          ("commonprogramfiles", os.environ.get("CommonProgramFiles", ""))):
            out = re.sub(r"%" + re.escape(name) + r"%", val.replace("\\", "\\\\"),
                         out, flags=re.I)
    return out


def is_temp_path(path: str) -> bool:
    """是否落在临时目录（这类启动项通常是「直接运行压缩包里的 exe」留下的残留）。"""
    low = (path or "").lower()
    temps = []
    for key in ("TEMP", "TMP"):
        v = os.environ.get(key)
        if v:
            temps.append(v.lower().rstrip("\\"))
    temps.append(os.path.expandvars(r"%LOCALAPPDATA%\Temp").lower())
    return any(t and low.startswith(t) for t in temps)


def file_company(path: str) -> str:
    """读 exe 的 CompanyName（版本资源），失败返回空串。"""
    if not IS_WIN or not path or not os.path.isfile(path):
        return ""
    try:
        v = ctypes.windll.version
        size = v.GetFileVersionInfoSizeW(path, None)
        if not size:
            return ""
        buf = ctypes.create_string_buffer(size)
        if not v.GetFileVersionInfoW(path, 0, size, buf):
            return ""
        ptr = ctypes.c_void_p()
        ln = wintypes.UINT()
        if not v.VerQueryValueW(buf, "\\VarFileInfo\\Translation",
                                ctypes.byref(ptr), ctypes.byref(ln)) or not ln.value:
            return ""
        pairs = ctypes.cast(ptr, ctypes.POINTER(wintypes.WORD))
        lang, cp = pairs[0], pairs[1]
        sub = f"\\StringFileInfo\\{lang:04x}{cp:04x}\\CompanyName"
        if not v.VerQueryValueW(buf, sub, ctypes.byref(ptr), ctypes.byref(ln)) or not ln.value:
            return ""
        return ctypes.wstring_at(ptr, ln.value).strip()
    except Exception:                          # noqa: BLE001
        return ""


# ------------------------------------------------------------------ 注册表读
def _read_values(hive: str, path: str) -> list[tuple[str, str]]:
    """返回 [(值名, 字符串值)]；键不存在返回空。"""
    if not IS_WIN:
        return []
    try:
        import winreg
        k = winreg.OpenKey(_HIVE_CONST[hive], path, 0, winreg.KEY_READ)
    except OSError:
        return []
    out: list[tuple[str, str]] = []
    try:
        i = 0
        while True:
            try:
                name, val, _typ = winreg.EnumValue(k, i)
            except OSError:
                break
            i += 1
            if name and isinstance(val, str):
                out.append((name, val))
    finally:
        k.Close()
    return out


def _read_approval(hive: str, path: str, name: str) -> bytes | None:
    if not IS_WIN:
        return None
    try:
        import winreg
        k = winreg.OpenKey(_HIVE_CONST[hive], path, 0, winreg.KEY_READ)
    except OSError:
        return None
    try:
        val, _typ = winreg.QueryValueEx(k, name)
        return val if isinstance(val, bytes) else None
    except OSError:
        return None
    finally:
        k.Close()


def _write_approval(hive: str, path: str, name: str, data: bytes) -> tuple[bool, str]:
    if not IS_WIN:
        return (False, "非 Windows 平台")
    try:
        import winreg
        k = winreg.CreateKeyEx(_HIVE_CONST[hive], path, 0,
                               winreg.KEY_SET_VALUE | winreg.KEY_READ)
        try:
            winreg.SetValueEx(k, name, 0, winreg.REG_BINARY, data)
        finally:
            k.Close()
        return (True, "")
    except PermissionError:
        return (False, "需要管理员权限才能修改本机启动项")
    except OSError as exc:
        return (False, f"写入失败：{exc}")


def _delete_approval(hive: str, path: str, name: str) -> tuple[bool, str]:
    if not IS_WIN:
        return (False, "非 Windows 平台")
    try:
        import winreg
        k = winreg.OpenKey(_HIVE_CONST[hive], path, 0, winreg.KEY_SET_VALUE)
        try:
            winreg.DeleteValue(k, name)
        finally:
            k.Close()
        return (True, "")
    except FileNotFoundError:
        return (True, "")                     # 本来就没有，等价成功
    except PermissionError:
        return (False, "需要管理员权限才能修改本机启动项")
    except OSError as exc:
        return (False, f"写入失败：{exc}")


# ------------------------------------------------------------------ 扫描
def _make_item(hive: str, reg_path: str, approval: str,
               name: str, cmd: str, kind: str) -> StartupItem:
    target = expand_target(parse_target(cmd))
    exists = bool(target) and os.path.isfile(target)
    blob = _read_approval(hive, APPROVED.format(approval), name)
    enabled = not is_disabled_blob(blob) if blob else True
    company = file_company(target) if exists else ""
    note = ""
    if not exists:
        note = "指向的程序已不存在（可直接清理）"
    elif is_temp_path(target):
        note = "位于临时目录，多半是直接运行压缩包留下的残留"
    return StartupItem(
        uid=f"reg::{hive}::{reg_path}::{name}",
        name=name, command=cmd, kind=kind,
        location=f"{hive}\\{reg_path}",
        enabled=enabled, can_toggle=True, note=note,
        publisher=company, target=target, exists=exists,
        hive=hive, reg_path=reg_path, value_name=name,
        approval_path=APPROVED.format(approval))


def scan_registry(stop_flag=None) -> list[StartupItem]:
    items: list[StartupItem] = []
    groups = (
        (HKCU, RUN, "Run", "注册表 · 当前用户"),
        (HKLM, RUN, "Run", "注册表 · 本机"),
        (HKLM, RUN_WOW, "Run32", "注册表 · 本机 32 位"),
    )
    for hive, path, approval, kind in groups:
        for name, val in _read_values(hive, path):
            if stop_flag and stop_flag[0]:
                return items
            items.append(_make_item(hive, path, approval, name, val, kind))
    return items


def _startup_dirs() -> list[tuple[str, str]]:
    out = []
    appdata = os.path.expandvars(r"%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup")
    program = os.path.expandvars(r"%ProgramData%\Microsoft\Windows\Start Menu\Programs\Startup")
    for p, kind in ((appdata, "启动文件夹 · 当前用户"), (program, "启动文件夹 · 所有用户")):
        if "%" in p:
            continue
        if os.path.isdir(p):
            out.append((p, kind))
    return out


def scan_folder(stop_flag=None) -> list[StartupItem]:
    items: list[StartupItem] = []
    for folder, kind in _startup_dirs():
        try:
            entries = sorted(os.scandir(folder), key=lambda e: e.name.lower())
        except OSError:
            continue
        for e in entries:
            if stop_flag and stop_flag[0]:
                return items
            if not e.is_file(follow_symlinks=False):
                continue
            if e.name.lower() == "desktop.ini":
                continue
            target = e.path
            company = file_company(e.path) if e.path.lower().endswith(".exe") else ""
            items.append(StartupItem(
                uid=f"file::{e.path}", name=e.name, command=e.path, kind=kind,
                location=folder, enabled=True, can_toggle=True,
                publisher=company, target=target, exists=True,
                file_path=e.path))
    return items


# --- 计划任务
_TASK_FIELDS = {
    "taskname": "name", "任务名": "name",
    "scheduled task state": "state", "计划任务状态": "state",
    "schedule type": "type", "计划类型": "type",
    "task to run": "run", "要运行的任务": "run",
    "status": "status", "状态": "status",
    "run as user": "user", "作为用户运行": "user",
}
_LOGON_TYPES = ("登录时", "启动时", "at logon", "at startup", "on logon",
                "on startup", "登录时/启动时", "at logon or startup")


def parse_schtasks_list(text: str) -> list[dict]:
    """解析 `schtasks /query /fo LIST /v` 的输出（中英文字段都认）。"""
    records: list[dict] = []
    cur: dict[str, str] = {}
    for line in (text or "").splitlines():
        if not line.strip():
            if cur:
                records.append(cur)
                cur = {}
            continue
        if ":" not in line:
            continue
        key, _, val = line.partition(":")
        field = _TASK_FIELDS.get(key.strip().lower())
        if not field:
            continue
        # 值后面可能带一长串空格 + 折行，先合并再 trim
        val = val.strip()
        if field in cur and val:
            continue
        cur[field] = val
    if cur:
        records.append(cur)
    return records


def scan_tasks(stop_flag=None) -> list[StartupItem]:
    """扫描计划任务：优先直接解析 System32\\Tasks 下的 XML。

    不用 `schtasks /query` 做扫描：它慢（1~2s）且在某些受管环境下会被拦截；
    XML 是任务定义的权威来源，读文件又快又稳。启停动作仍然走 schtasks，
    因为改 XML 需要 TrustedInstaller 权限。
    """
    items = _scan_tasks_xml(stop_flag)
    if items:
        # 第三方任务排前面：微软自带的几十个默认都启用，堆在顶部没有价值
        items.sort(key=lambda i: (i.is_ms, i.location.lower(), i.name.lower()))
        return items
    return _scan_tasks_cli(stop_flag)


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _tasks_dir() -> str:
    root = os.environ.get("SystemRoot", r"C:\Windows")
    return os.path.join(root, "System32", "Tasks")


def _iter_task_files(root: str):
    for dirpath, _dirnames, filenames in os.walk(root):
        for fn in filenames:
            if fn.lower() == "desktop.ini":
                continue
            if "." in fn:                 # 任务文件没有扩展名
                continue
            yield os.path.join(dirpath, fn)


def _task_name_from_path(root: str, path: str) -> str:
    rel = os.path.relpath(path, root)
    return "\\" + rel.replace(os.sep, "\\")


def _scan_tasks_xml(stop_flag=None) -> list[StartupItem]:
    import xml.etree.ElementTree as ET

    root_dir = _tasks_dir()
    if not os.path.isdir(root_dir):
        return []
    items: list[StartupItem] = []
    for path in _iter_task_files(root_dir):
        if stop_flag and stop_flag[0]:
            break
        try:
            tree = ET.parse(path)
        except (ET.ParseError, OSError):
            continue
        root = tree.getroot()
        triggers: list[str] = []
        command = ""
        arguments = ""
        enabled = True
        hidden = False
        for el in root.iter():
            tag = _local(el.tag)
            if tag in ("LogonTrigger", "BootTrigger"):
                triggers.append(tag)
            elif tag == "Settings":
                # 任务级开关在 <Settings><Enabled>；各 Trigger 里的 Enabled
                # 只管那个触发器，不改变任务本身状态
                for child in el:
                    if _local(child.tag) == "Enabled":
                        enabled = (child.text or "").strip().lower() != "false"
                    elif _local(child.tag) == "Hidden":
                        hidden = (child.text or "").strip().lower() == "true"
            elif tag == "Exec":
                for child in el:
                    sub = _local(child.tag)
                    if sub == "Command":
                        command = (child.text or "").strip()
                    elif sub == "Arguments":
                        arguments = (child.text or "").strip()
        if not triggers:
            continue
        full = _task_name_from_path(root_dir, path)
        cmd = f'"{command}" {arguments}'.strip() if command else ""
        target = expand_target(parse_target(cmd)) if cmd else ""
        # 无命令行的任务由系统服务调用，不存在「程序丢失」，不能算残留
        exists = (os.path.isfile(target) if target else bool(not command))
        folder = full.rsplit("\\", 1)[0] or "\\"
        name = full.rsplit("\\", 1)[-1]
        note = ""
        if not command:
            note = "由系统服务触发，无命令行"
        elif not exists:
            note = "指向的程序已不存在"
        elif is_temp_path(target):
            note = "位于临时目录，多半是残留"
        if hidden:
            note = (note + " · " if note else "") + "隐藏任务"
        items.append(StartupItem(
            uid=f"task::{full}", name=name, command=cmd, kind="计划任务",
            location=folder or "\\", enabled=enabled, can_toggle=True,
            note=note, publisher=file_company(target) if exists else "",
            target=target, exists=exists, task_name=full))
    return items


def _scan_tasks_cli(stop_flag=None) -> list[StartupItem]:
    """兜底：解析 schtasks 输出（XML 不可读时才会走到这里）。"""
    rc, out = _run(["schtasks", "/query", "/fo", "LIST", "/v"], timeout=40)
    if rc != 0 or not out:
        return []
    items: list[StartupItem] = []
    for rec in parse_schtasks_list(out):
        if stop_flag and stop_flag[0]:
            break
        name = rec.get("name", "")
        stype = rec.get("type", "")
        if not name or name.upper() == "TASKNAME":
            continue
        if not any(t in stype.lower() for t in _LOGON_TYPES):
            continue
        run = rec.get("run", "")
        target = parse_target(run) if run else ""
        state = rec.get("state", "")
        enabled = "禁用" not in state and "disabled" not in state.lower()
        folder = name.rsplit("\\", 1)[0] if "\\" in name else "\\"
        items.append(StartupItem(
            uid=f"task::{name}", name=name.rsplit("\\", 1)[-1] or name,
            command=run, kind="计划任务", location=folder,
            enabled=enabled, can_toggle=True,
            note="" if run else "该任务由系统服务触发，无命令行",
            publisher=file_company(target) if target else "",
            target=target, exists=bool(target) and os.path.isfile(target),
            task_name=name))
    items.sort(key=lambda i: (not i.exists, i.name.lower()))
    return items


def scan_all(stop_flag=None, progress=None) -> list[StartupItem]:
    items: list[StartupItem] = []
    steps = (("注册表启动项", scan_registry), ("启动文件夹", scan_folder),
             ("计划任务", scan_tasks))
    for i, (label, fn) in enumerate(steps):
        if progress:
            progress(i, len(steps), label)
        if stop_flag and stop_flag[0]:
            break
        try:
            items.extend(fn(stop_flag))
        except Exception:                      # noqa: BLE001
            continue
    if progress:
        progress(len(steps), len(steps), "完成")
    return items


# ------------------------------------------------------------------ 操作
def backup_root() -> str:
    base = os.path.expandvars(r"%LOCALAPPDATA%\SystemToolbox\backup")
    try:
        os.makedirs(base, exist_ok=True)
        return base
    except OSError:
        return os.path.expandvars("%TEMP%")


def _log_action(record: dict) -> None:
    """把每次开关动作追加到 jsonl，出问题可照原始数据还原。"""
    path = os.path.join(backup_root(), "startup_actions.jsonl")
    record = dict(record)
    record["time"] = time.strftime("%Y-%m-%d %H:%M:%S")
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError:
        pass


def set_enabled(item: StartupItem, on: bool) -> tuple[bool, str]:
    """切换启用状态。所有路径都可逆，不删除启动项本身。"""
    if item.kind.startswith("注册表") or item.hive:
        path = item.approval_path
        if on:
            ok, msg = _delete_approval(item.hive, path, item.value_name)
            if not ok:
                return (ok, msg)
        else:
            ok, msg = _write_approval(item.hive, path, item.value_name, disabled_data())
            if not ok:
                return (ok, msg)
        _log_action({"action": "enable" if on else "disable", "type": "registry",
                     "hive": item.hive, "path": item.reg_path,
                     "approval": path, "name": item.value_name,
                     "command": item.command})
        item.enabled = on
        return (True, "")

    if item.kind.startswith("启动文件夹"):
        hold = os.path.join(backup_root(), "startup_disabled")
        try:
            os.makedirs(hold, exist_ok=True)
        except OSError as exc:
            return (False, f"创建备份目录失败：{exc}")
        src = item.file_path
        dst = os.path.join(hold, f"{int(time.time())}_{os.path.basename(src)}")
        try:
            if on:
                if os.path.isfile(src):
                    return (True, "")
                if os.path.isfile(dst):
                    shutil.move(dst, src)
                else:
                    return (False, "备份里找不到这个文件，无法还原")
            else:
                if not os.path.isfile(src):
                    return (True, "")
                shutil.move(src, dst)
        except OSError as exc:
            return (False, f"移动文件失败（可能被占用）：{exc}")
        _log_action({"action": "enable" if on else "disable", "type": "folder",
                     "original": item.file_path, "backup": dst})
        item.enabled = on
        return (True, "")

    if item.kind == "计划任务":
        args = ["schtasks", "/change", "/tn", item.task_name,
                "/enable" if on else "/disable"]
        rc, out = _run(args, timeout=25)
        if rc != 0:
            tail = (out or "").strip().splitlines()
            return (False, tail[0] if tail else "schtasks 执行失败（可能需要管理员）")
        _log_action({"action": "enable" if on else "disable", "type": "task",
                     "task": item.task_name})
        item.enabled = on
        return (True, "")

    return (False, "不支持的类型")


def set_many(items, on: bool, progress=None) -> tuple[int, list[str]]:
    ok = 0
    errors: list[str] = []
    for i, it in enumerate(items):
        if progress:
            progress(i, len(items), it.name)
        good, msg = set_enabled(it, on)
        if good:
            ok += 1
        else:
            errors.append(f"{it.name}: {msg}")
    if progress:
        progress(len(items), len(items), "完成")
    return (ok, errors)
