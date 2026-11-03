# -*- coding: utf-8 -*-
"""读 exe 的版本资源（右键 → 属性 → 详细信息 里那一栏）。

只用 `version.dll` 的四个 API，不依赖 pywin32：
`GetFileVersionInfoSizeW` → `GetFileVersionInfoW` → `VerQueryValueW`。

打包后自检要靠它确认「版本资源真的写进 exe 了」—— 这一步最容易出事：
spec 里 `version=None`（v1.17.0 之前就是这样）打出来的 exe 属性全是空的，
程序自己却毫不知情，因为代码里的 `VERSION` 常量跟资源是两回事。
"""
from __future__ import annotations

import ctypes
import os
import sys
from ctypes import wintypes

IS_WIN = sys.platform == "win32"

# 简体中文 + Unicode(1200)。翻译表里没有 0804 时退回 0409（英文）。
_LANG_ORDER = (0x0804, 0x0409)


def _query(buf, sub: str) -> str:
    size = wintypes.UINT(0)
    ptr = ctypes.c_void_p(0)
    if not ctypes.windll.version.VerQueryValueW(
            buf, sub, ctypes.byref(ptr), ctypes.byref(size)):
        return ""
    if not size.value or not ptr.value:
        return ""
    return ctypes.wstring_at(ptr.value, size.value).rstrip("\x00")


def read(path: str) -> dict:
    """返回 `{FileVersion, ProductVersion, CompanyName, ...}` 与 `filevers`。

    读不到就返回空 dict —— 调用方据此判断「没有版本资源」，
    不要抛异常：这个函数在自检里用，读失败本身就是要报告的结果。
    """
    out = {}
    if not IS_WIN or not path or not os.path.isfile(path):
        return out
    try:
        ver = ctypes.windll.version
        size = ver.GetFileVersionInfoSizeW(path, None)
    except Exception:       # noqa: BLE001
        return out
    if not size:
        return out
    buf = ctypes.create_string_buffer(size)
    try:
        if not ctypes.windll.version.GetFileVersionInfoW(path, 0, size, buf):
            return out
    except Exception:       # noqa: BLE001
        return out

    # 固定信息：四段数字，打包版本号对不对看这里最直接
    fixed = ctypes.c_void_p(0)
    flen = wintypes.UINT(0)
    if ctypes.windll.version.VerQueryValueW(
            buf, "\\", ctypes.byref(fixed), ctypes.byref(flen)) and fixed.value:
        # VS_FIXEDFILEINFO：前 8 个 DWORD 依次是签名/结构版本/文件版本MS-LS/产品版本MS-LS
        raw = ctypes.cast(fixed, ctypes.POINTER(wintypes.DWORD * 13)).contents
        out["filevers"] = (raw[2] >> 16, raw[2] & 0xFFFF,
                           raw[3] >> 16, raw[3] & 0xFFFF)
        out["prodvers"] = (raw[4] >> 16, raw[4] & 0xFFFF,
                           raw[5] >> 16, raw[5] & 0xFFFF)

    for lang in _LANG_ORDER:
        head = "\\StringFileInfo\\%04x04b0\\" % lang
        got = {}
        for key in ("CompanyName", "FileDescription", "FileVersion",
                    "InternalName", "LegalCopyright", "OriginalFilename",
                    "ProductName", "ProductVersion"):
            val = _query(buf, head + key)
            if val:
                got[key] = val
        if got:
            out.update(got)
            out["lang"] = "%04x" % lang
            break
    return out


def describe(path: str) -> str:
    """一行摘要，给 `--selftest` 打日志用。"""
    info = read(path)
    if not info:
        return "无版本资源"
    ver = ".".join(str(x) for x in info.get("filevers", ())) or "?"
    return "%s | 文件版本 %s | %s" % (
        info.get("ProductName", "?"), ver, info.get("CompanyName", "?"))
