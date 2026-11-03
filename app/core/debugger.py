# -*- coding: utf-8 -*-
"""可选的 cdb 符号增强。

本工具的核心价值是不装任何东西也能出结论；但如果机器上恰好有
Windows SDK 的调试工具，就顺带把符号化堆栈也取回来，白赚。
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field

CANDIDATES = [
    r"C:\Program Files (x86)\Windows Kits\10\Debuggers\x64\cdb.exe",
    r"C:\Program Files\Windows Kits\10\Debuggers\x64\cdb.exe",
    r"C:\Program Files (x86)\Windows Kits\10\Debuggers\x86\cdb.exe",
    r"C:\Program Files\Windows Kits\10\Debuggers\x86\cdb.exe",
    r"C:\Program Files\Windows Kits\10\Debuggers\x64\cdb.exe",
]

KEY_PATTERNS = [
    "BUGCHECK_CODE", "BUGCHECK_P1", "BUGCHECK_P2", "BUGCHECK_P3", "BUGCHECK_P4",
    "MODULE_NAME", "IMAGE_NAME", "IMAGE_VERSION", "PROCESS_NAME", "FAILURE_BUCKET_ID",
    "EXCEPTION_CODE", "FAULTING_IP", "STACK_COMMAND", "OS_VERSION", "SYMBOL_NAME",
    "BUCKET_ID", "PRIMARY_PROBLEM_CLASS",
]


@dataclass
class CdbResult:
    ok: bool = False
    cdb_path: str = ""
    text: str = ""
    keys: dict = field(default_factory=dict)
    stack: list = field(default_factory=list)
    blamed: str = ""
    error: str = ""

    @property
    def summary(self) -> str:
        if not self.ok:
            return self.error or "未执行"
        bits = []
        if self.keys.get("MODULE_NAME"):
            bits.append("模块 " + self.keys["MODULE_NAME"])
        if self.keys.get("FAILURE_BUCKET_ID"):
            bits.append(self.keys["FAILURE_BUCKET_ID"])
        return " · ".join(bits) or "已取得调试器输出"


def find_cdb() -> str:
    env = os.environ.get("CDB_PATH") or os.environ.get("CLS_CDB")
    if env and os.path.isfile(env):
        return env
    for p in CANDIDATES:
        if os.path.isfile(p):
            return p
    return shutil.which("cdb") or ""


def available() -> bool:
    return bool(find_cdb())


def run(path: str, timeout: int = 240) -> CdbResult:
    cdb = find_cdb()
    res = CdbResult(cdb_path=cdb)
    if not cdb:
        res.error = ("未检测到 cdb.exe。安装「Windows SDK → Debugging Tools for Windows」"
                     "（或 Microsoft Store 的 WinDbg）后重新分析即可自动启用。")
        return res
    if not os.path.isfile(path):
        res.error = "转储文件不存在"
        return res

    cmd = [cdb, "-z", path, "-c", ".symfix; .reload; !analyze -v; q"]
    env = dict(os.environ)
    env.setdefault("_NT_SYMBOL_PATH",
                   r"srv*%s*https://msdl.microsoft.com/download/symbols"
                   % os.path.join(os.path.expanduser("~"), "AppData", "Local", "SystemToolbox", "Symbols"))
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=timeout, env=env,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except subprocess.TimeoutExpired:
        res.error = f"调试器执行超时（{timeout}s），符号首次下载较慢时可重试"
        return res
    except Exception as exc:  # noqa: BLE001
        res.error = f"调用调试器失败：{exc}"
        return res

    raw = proc.stdout or b""
    try:
        text = raw.decode("utf-8", "replace")
    except Exception:
        text = str(raw)
    res.text = text
    if not text.strip():
        res.error = "调试器没有输出（可能符号下载失败或文件损坏）"
        return res

    res.keys = _parse_keys(text)
    res.stack = _parse_stack(text)
    res.blamed = _parse_blamed(text)
    res.ok = bool(res.keys or res.stack)
    if not res.ok:
        res.error = "调试器输出中未识别到 !analyze 结论"
    return res


def _parse_keys(text: str) -> dict:
    keys: dict = {}
    for line in text.splitlines():
        stripped = line.strip()
        for k in KEY_PATTERNS:
            if stripped.startswith(k + ":"):
                v = stripped[len(k) + 1:].strip()
                if v:
                    keys[k] = v
    return keys


def _parse_blamed(text: str) -> str:
    m = re.search(r"Probably caused by\s*:\s*(.+)", text)
    return m.group(1).strip() if m else ""


def _parse_stack(text: str) -> list:
    """抽取 STACK_TEXT 段里的符号化帧。"""
    lines = text.splitlines()
    out: list[str] = []
    started = False
    for line in lines:
        s = line.rstrip()
        if s.strip().startswith("STACK_TEXT"):
            started = True
            continue
        if started:
            if not s.strip():
                if out:
                    break
                continue
            if re.match(r"^[A-Z_]{4,}\s*:", s.strip()):
                break
            out.append(s.strip())
            if len(out) >= 60:
                break
    # 只保留能被解读的帧（含模块!符号 或 地址 + 模块）
    pretty = []
    for ln in out:
        m = re.search(r"([0-9a-f`]{8,})\s+([0-9a-f`]{8,})\s+([0-9a-f`]{8,})\s+([0-9a-f`]{8,})\s+(.*)", ln)
        if m:
            body = m.group(5).strip()
            pretty.append(body)
        elif ln.strip():
            pretty.append(ln.strip())
    return pretty[:40]
