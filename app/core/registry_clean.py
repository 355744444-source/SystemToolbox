# -*- coding: utf-8 -*-
"""注册表冗余检测。

只做五类「有客观证据」的判定——所谓证据是：注册表里登记的路径在磁盘上
确实不存在。不去猜"这个键看起来没用"，所以不会误伤正常软件。

五类：
  1. 卸载项残留      Uninstall 子键里登记的反安装程序与安装目录都不存在
  2. 启动项失效      Run / RunOnce 指向的文件已不存在
  3. 右键菜单失效    ContextMenuHandlers 引用的 COM DLL 不存在
  4. COM 组件失效    CLSID\\InprocServer32 指向第三方目录的 DLL 不存在
  5. 应用路径失效    App Paths 登记的 exe 不存在

**绝不触碰**：SYSTEM 分支、Services、Winsock、安全策略、字体、驱动。
删除前统一导出 .reg 备份，删错可直接双击还原。
"""
from __future__ import annotations

import os
import re
import subprocess
import tempfile
from dataclasses import dataclass

try:
    import winreg
except ImportError:            # 非 Windows 下仅用于导入不报错
    winreg = None

IS_WIN = os.name == "nt"
CREATE_NO_WINDOW = 0x08000000

HIVES = {"HKCU": "HKEY_CURRENT_USER", "HKLM": "HKEY_LOCAL_MACHINE",
         "HKCR": "HKEY_CLASSES_ROOT"}

_THIRD_PARTY_ROOTS = tuple(
    os.path.expandvars(p).lower().rstrip("\\")
    for p in ("%ProgramFiles%", "%ProgramFiles(x86)%", "%ProgramData%",
              "%LOCALAPPDATA%", "%APPDATA%")
    if "%" not in os.path.expandvars(p))


@dataclass
class RegIssue:
    hive: str                  # HKCU / HKLM
    subkey: str                # 不含 hive 的完整子键
    label: str                 # 给人看的名字
    category: str
    reason: str
    target: str = ""
    risk: str = "低"
    delete_key: bool = True    # False 表示只删 value_name 这个值
    value_name: str = ""

    @property
    def full_path(self) -> str:
        return f"{self.hive}\\{self.subkey}"

    @property
    def can_fix(self) -> bool:
        return True


def _hive_const(hive: str):
    return {"HKCU": winreg.HKEY_CURRENT_USER,
            "HKLM": winreg.HKEY_LOCAL_MACHINE,
            "HKCR": winreg.HKEY_CLASSES_ROOT}[hive]


def _open(hive: str, subkey: str, write: bool = False):
    access = winreg.KEY_READ | winreg.KEY_WOW64_64KEY
    if write:
        access = winreg.KEY_ALL_ACCESS | winreg.KEY_WOW64_64KEY
    return winreg.OpenKey(_hive_const(hive), subkey, 0, access)


def _get_str(key, name: str) -> str:
    try:
        v, _ = winreg.QueryValueEx(key, name)
        return v if isinstance(v, str) else ""
    except OSError:
        return ""


def _get_int(key, name: str) -> int | None:
    try:
        v, _ = winreg.QueryValueEx(key, name)
        return int(v)
    except (OSError, TypeError, ValueError):
        return None


def _subkeys(hive: str, subkey: str) -> list[str]:
    out = []
    try:
        k = _open(hive, subkey)
    except OSError:
        return out
    with k:
        i = 0
        while True:
            try:
                out.append(winreg.EnumKey(k, i))
            except OSError:
                break
            i += 1
    return out


def expand_path(raw: str) -> str:
    """从命令行里抠出可执行文件路径，处理引号、参数、环境变量。"""
    if not raw:
        return ""
    s = raw.strip()
    if s.startswith('"'):
        end = s.find('"', 1)
        s = s[1:end] if end > 0 else s[1:]
    else:
        # 贪婪匹配：无引号的路径可能带空格，取最后一个像可执行文件的片段
        m = re.match(r"^(.+\.(?:exe|dll|sys|ico|scr|cpl|cmd|bat|vbs|ps1|msi|jar))"
                     r"(?:\s|,|$)", s, re.I)
        if m:
            s = m.group(1)
        else:
            s = s.split(" ")[0]
    s = s.strip().strip(",")
    s = os.path.expandvars(s)
    return s


def _exists(path: str) -> bool:
    if not path:
        return False
    if not (os.path.isabs(path) or path[:2].endswith(":")):
        return False
    try:
        return os.path.exists(path)
    except (OSError, ValueError):
        return False


def _is_system_cmd(path: str) -> bool:
    """系统组件路径不作为失效证据（可能被 WOW64 重定向、被 App-V 虚拟化）。"""
    low = path.lower()
    return ("\\windows\\system32" in low or "\\windows\\syswow64" in low
            or "\\windows\\winsxs" in low)


def _is_third_party(path: str) -> bool:
    low = path.lower()
    # Microsoft / Windows 自身的组件会被更新程序重新注册，删了徒增风险
    if "\\microsoft\\" in low or "\\windows\\" in low or "\\common files\\" in low:
        return False
    return any(low.startswith(r) for r in _THIRD_PARTY_ROOTS)


# ------------------------------------------------------------------ 1 卸载项
UNINSTALL_KEYS = (
    ("HKCU", r"Software\Microsoft\Windows\CurrentVersion\Uninstall"),
    ("HKLM", r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
    ("HKLM", r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
)


def scan_uninstall(stop_flag=None) -> list[RegIssue]:
    out = []
    for hive, base in UNINSTALL_KEYS:
        for name in _subkeys(hive, base):
            if stop_flag and stop_flag[0]:
                return out
            sub = f"{base}\\{name}"
            try:
                k = _open(hive, sub)
            except OSError:
                continue
            with k:
                display = _get_str(k, "DisplayName")
                if not display:
                    continue
                if (_get_int(k, "SystemComponent") or 0) == 1:
                    continue
                if _get_str(k, "ParentKeyName"):        # 补丁/子组件，不算残留
                    continue
                uninst = _get_str(k, "UninstallString") or _get_str(k, "QuietUninstallString")
                loc = _get_str(k, "InstallLocation")
                icon = _get_str(k, "DisplayIcon")
            is_msi = "msiexec" in uninst.lower()
            targets = []
            if uninst and not is_msi:
                targets.append(expand_path(uninst))
            if loc:
                targets.append(loc.strip().strip('"'))
            if icon:
                targets.append(expand_path(icon))
            targets = [t for t in targets if t and not _is_system_cmd(t)]
            if not targets:
                continue
            if any(_exists(t) for t in targets):
                continue
            # 一个有效路径都没有：整条卸载记录已经没有可执行实体
            risk = "低" if len(targets) >= 2 else "中"
            missing = "、".join(os.path.basename(t) or t for t in targets[:3])
            out.append(RegIssue(
                hive=hive, subkey=sub, label=display, category="卸载项残留",
                reason=f"登记的反安装程序与安装目录均不存在（{missing}）",
                target=targets[0], risk=risk, delete_key=True))
    return out


# ------------------------------------------------------------------ 2 启动项
RUN_KEYS = (
    ("HKCU", r"Software\Microsoft\Windows\CurrentVersion\Run"),
    ("HKCU", r"Software\Microsoft\Windows\CurrentVersion\RunOnce"),
    ("HKLM", r"SOFTWARE\Microsoft\Windows\CurrentVersion\Run"),
    ("HKLM", r"SOFTWARE\Microsoft\Windows\CurrentVersion\RunOnce"),
    ("HKLM", r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Run"),
)

_HANDLED_BY_SYSTEM = ("rundll32", "svchost", "msiexec", "cmd.exe", "powershell",
                      "explorer.exe", "regsvr32", "schtasks", "wscript", "cscript")


def scan_run(stop_flag=None) -> list[RegIssue]:
    out = []
    for hive, sub in RUN_KEYS:
        try:
            k = _open(hive, sub)
        except OSError:
            continue
        with k:
            i = 0
            while True:
                if stop_flag and stop_flag[0]:
                    return out
                try:
                    vname, vdata, _ = winreg.EnumValue(k, i)
                except OSError:
                    break
                i += 1
                if not isinstance(vdata, str) or not vdata.strip():
                    continue
                low = vdata.lower()
                if any(h in low for h in _HANDLED_BY_SYSTEM):
                    continue
                path = expand_path(vdata)
                if not path or _is_system_cmd(path):
                    continue
                if _exists(path):
                    continue
                out.append(RegIssue(
                    hive=hive, subkey=sub, label=vname, category="启动项失效",
                    reason=f"启动命令指向的文件不存在：{path}",
                    target=path, risk="低", delete_key=False, value_name=vname))
    return out


# ------------------------------------------------------------------ 3 右键菜单
SHELLEX_KEYS = (
    r"*\shellex\ContextMenuHandlers",
    r"Directory\shellex\ContextMenuHandlers",
    r"Directory\Background\shellex\ContextMenuHandlers",
    r"Folder\shellex\ContextMenuHandlers",
    r"AllFilesystemObjects\shellex\ContextMenuHandlers",
    r"Drive\shellex\ContextMenuHandlers",
    r"Directory\shellex\PropertySheetHandlers",
)

_CLSID_RE = re.compile(r"\{[0-9A-Fa-f\-]{36}\}")


def _clsid_dll(clsid: str) -> str:
    for sub in (rf"SOFTWARE\Classes\CLSID\{clsid}\InprocServer32",
                rf"SOFTWARE\Classes\WOW6432Node\CLSID\{clsid}\InprocServer32"):
        try:
            k = _open("HKLM", sub)
        except OSError:
            continue
        with k:
            dll = _get_str(k, "")
        if dll:
            return expand_path(dll)
    return ""


def scan_shellex(stop_flag=None) -> list[RegIssue]:
    out = []
    for rel in SHELLEX_KEYS:
        base = rf"SOFTWARE\Classes\{rel}"
        for name in _subkeys("HKLM", base):
            if stop_flag and stop_flag[0]:
                return out
            sub = f"{base}\\{name}"
            try:
                k = _open("HKLM", sub)
            except OSError:
                continue
            with k:
                val = _get_str(k, "")
            clsid = ""
            m = _CLSID_RE.search(val or "")
            if m:
                clsid = m.group(0)
            if not clsid:
                continue
            dll = _clsid_dll(clsid)
            if not dll or _is_system_cmd(dll) or _exists(dll):
                continue
            out.append(RegIssue(
                hive="HKLM", subkey=sub, label=name, category="右键菜单失效",
                reason=f"菜单处理程序 DLL 不存在：{dll}",
                target=dll, risk="中", delete_key=True))
    return out


# ------------------------------------------------------------------ 4 COM 组件
def scan_com(stop_flag=None, limit: int = 400) -> list[RegIssue]:
    out = []
    base = r"SOFTWARE\Classes\CLSID"
    for clsid in _subkeys("HKLM", base):
        if stop_flag and stop_flag[0] or len(out) >= limit:
            return out
        dll = _clsid_dll(clsid)
        if not dll or not _is_third_party(dll) or _exists(dll):
            continue
        try:
            k = _open("HKLM", rf"{base}\{clsid}")
        except OSError:
            continue
        with k:
            label = _get_str(k, "")
        out.append(RegIssue(
            hive="HKLM", subkey=rf"{base}\{clsid}",
            label=label or clsid, category="COM 组件失效",
            reason=f"组件 DLL 已被删除，注册信息成为孤立引用：{dll}",
            target=dll, risk="中", delete_key=True))
    return out


# ------------------------------------------------------------------ 5 App Paths
def scan_app_paths(stop_flag=None) -> list[RegIssue]:
    out = []
    base = r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths"
    for name in _subkeys("HKLM", base):
        if stop_flag and stop_flag[0]:
            return out
        sub = f"{base}\\{name}"
        try:
            k = _open("HKLM", sub)
        except OSError:
            continue
        with k:
            exe = expand_path(_get_str(k, ""))
        if not exe or _exists(exe):
            continue
        out.append(RegIssue(
            hive="HKLM", subkey=sub, label=name, category="应用路径失效",
            reason=f"登记的启动路径不存在：{exe}",
            target=exe, risk="低", delete_key=True))
    return out


CATEGORIES = ("卸载项残留", "启动项失效", "右键菜单失效", "COM 组件失效", "应用路径失效")


def scan_all(stop_flag=None, progress=None,
             categories=None, include_com: bool = True) -> list[RegIssue]:
    if not IS_WIN or winreg is None:
        return []
    cats = set(categories or CATEGORIES)
    steps = [("卸载项残留", scan_uninstall), ("启动项失效", scan_run),
             ("右键菜单失效", scan_shellex)]
    if include_com:
        steps.append(("COM 组件失效", scan_com))
    steps.append(("应用路径失效", scan_app_paths))
    out: list[RegIssue] = []
    done = 0
    for cat, fn in steps:
        if cat not in cats:
            continue
        if progress:
            progress(done, len(steps), cat)
        try:
            out.extend(fn(stop_flag))
        except Exception:       # noqa: BLE001  —— 单类失败不影响其余
            pass
        done += 1
    if progress:
        progress(len(steps), len(steps), "")
    return out


# ------------------------------------------------------------------ 备份与删除
def export_backup(issues, out_path: str) -> tuple[bool, str]:
    """把待删的键导出成一个 .reg，删错可双击还原。"""
    if not IS_WIN or not issues:
        return (False, "没有需要备份的项目")
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    chunks: list[str] = []
    header = "Windows Registry Editor Version 5.00"
    for it in issues:
        key_path = it.full_path
        tmp = os.path.join(tempfile.gettempdir(), f"cls_reg_{abs(hash(key_path))}.reg")
        try:
            p = subprocess.run(
                ["reg.exe", "export", key_path, tmp, "/y"],
                capture_output=True, creationflags=CREATE_NO_WINDOW, timeout=20)
            if p.returncode != 0 or not os.path.isfile(tmp):
                continue
            with open(tmp, "r", encoding="utf-16", errors="replace") as f:
                text = f.read()
        except Exception:       # noqa: BLE001
            continue
        finally:
            try:
                if os.path.isfile(tmp):
                    os.remove(tmp)
            except OSError:
                pass
        lines = [ln for ln in text.splitlines() if ln.strip()]
        if not lines:
            continue
        body = "\n".join(lines[2:]) if lines[0].lower().startswith("windows registry") else "\n".join(lines)
        if body.strip():
            chunks.append(body)
    if not chunks:
        return (False, "导出失败（可能需要管理员权限）")
    try:
        with open(out_path, "w", encoding="utf-16-le", newline="\r\n") as f:
            f.write(header + "\r\n\r\n")
            f.write("\r\n\r\n".join(chunks))
            f.write("\r\n")
    except OSError as exc:
        return (False, f"写入备份失败：{exc}")
    return (True, out_path)


def _delete_tree(hive: str, subkey: str, depth: int = 0) -> None:
    """递归清空子键后删除自身（winreg 不允许删非空键）。"""
    if depth > 24:
        return
    for child in _subkeys(hive, subkey):
        try:
            _delete_tree(hive, f"{subkey}\\{child}", depth + 1)
        except OSError:
            pass
    k = _open(hive, subkey, write=True)
    k.Close()
    winreg.DeleteKey(_hive_const(hive), subkey)


def delete_issue(issue: RegIssue) -> tuple[bool, str]:
    if not IS_WIN or winreg is None:
        return (False, "当前系统不支持注册表操作")
    try:
        if issue.delete_key:
            _delete_tree(issue.hive, issue.subkey)
        else:
            k = _open(issue.hive, issue.subkey, write=True)
            with k:
                winreg.DeleteValue(k, issue.value_name)
        return (True, "")
    except PermissionError:
        return (False, "权限不足（该项属于系统级，需要管理员）")
    except FileNotFoundError:
        return (True, "该项已不存在")
    except OSError as exc:
        return (False, str(exc))


def delete_issues(issues, progress=None) -> tuple[int, int, list[str]]:
    ok = fail = 0
    problems: list[str] = []
    for i, it in enumerate(issues):
        good, msg = delete_issue(it)
        if good:
            ok += 1
        else:
            fail += 1
            if len(problems) < 10:
                problems.append(f"{it.label}：{msg}")
        if progress:
            progress(i + 1, len(issues))
    return (ok, fail, problems)
