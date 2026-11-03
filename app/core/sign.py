# -*- coding: utf-8 -*-
"""Authenticode 签名校验：内嵌签名 + 目录（catalog）签名。

## 为什么不能只调 WinVerifyTrust

`WinVerifyTrust` 配 `WTD_CHOICE_FILE` **只查文件里内嵌的 PKCS#7 签名**。
Windows 自带组件大量使用**目录签名**：签名不在 exe/sys 里，而在
`C:\\Windows\\System32\\CatRoot\\...` 下的 `.cat` 文件里。只查内嵌签名，
这些文件会全部被判 `TRUST_E_NOSIGNATURE (0x800B0100)`。

本机实测（Win11 26H2，2026-10-26）：

    只查内嵌签名          notepad.exe        → 0x800B0100（假阴性）
                          cdfs.sys           → 0x800B0100（假阴性）
                          acpiex.sys         → 0x800B0100（假阴性）
    System32\\drivers 前 60 个 .sys：25 个被判「未签名」

补上目录签名后：

    notepad.exe / cdfs.sys / acpiex.sys  → 0（通过，各自命中 .cat）
    60 个 .sys：只剩 5 个「确实没有签名」
    （AliPaladinEx64 / IOMap64 / MsIo64 / PHIPS / PROCEXP152 —— 全是第三方驱动）

## 实现要点

- 目录签名的流程是 `CryptCATAdminCalcHashFromFileHandle` 算哈希 →
  `CryptCATAdminEnumCatalogFromHash` 找目录 → `WinVerifyTrust` 用
  `WTD_CHOICE_CATALOG(2)` 校验。
- **`CryptCATAdminEnumCatalogFromHash` 必须显式 `restype = HANDLE`**，
  否则 64 位句柄被截成 int，`CryptCATAdminReleaseCatalogContext` 直接
  access violation（本机踩过）。
- `fdwRevocationChecks` 一律 `WTD_REVOKE_NONE`、`dwProvFlags` 带
  `WTD_CACHE_ONLY_URL_RETRIEVAL`：工具要离线可用，不能因为查不到吊销列表
  就报「签名无效」。这也意味着**吊销状态不判**，文案里不承诺。
- 单个文件约 13 ms（内嵌）/ 16 ms（走目录），52 个驱动 ≈ 1 秒，可以逐条查。
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import os
from dataclasses import dataclass

__all__ = ["SignInfo", "verify", "LEVEL_CN", "verify_many"]

# ---------------------------------------------------------------- 结果码
TRUST_E_NOSIGNATURE = 0x800B0100
TRUST_E_SUBJECT_FORM_UNKNOWN = 0x800B0003
TRUST_E_PROVIDER_UNKNOWN = 0x800B0001
TRUST_E_EXPLICIT_DISTRUST = 0x800B0111
CERT_E_UNTRUSTEDROOT = 0x800B0109
CERT_E_CHAINING = 0x800B010A
CERT_E_EXPIRED = 0x800B0101
TRUST_E_BAD_DIGEST = 0x80096010
CRYPT_E_SECURITY_SETTINGS = 0x80092026
NO_CATALOG = -4          # 内部：没找到对应 .cat

#: kind → 中文
LEVEL_CN = {
    "ok": "签名有效",
    "uncatalogued": "未签名",
    "bad": "签名无效",
    "warn": "签名可疑",
    "missing": "文件不存在",
    "error": "无法校验",
}

#: 结果码 → (kind, 说明)。没列出的走默认「签名无效」。
_CODE_MAP = {
    0: ("ok", "证书链完整，文件未被改动"),
    TRUST_E_NOSIGNATURE: ("uncatalogued",
                          "文件里没有内嵌签名，系统目录里也没有它的 .cat 记录"),
    TRUST_E_SUBJECT_FORM_UNKNOWN: ("uncatalogued", "签名格式不是 Authenticode"),
    TRUST_E_PROVIDER_UNKNOWN: ("uncatalogued", "没有可用的签名提供程序"),
    TRUST_E_BAD_DIGEST: ("bad", "签名存在，但文件内容与签名不符（被改过）"),
    TRUST_E_EXPLICIT_DISTRUST: ("bad", "该证书被本机显式标记为不信任"),
    CERT_E_UNTRUSTEDROOT: ("warn", "签名者不在本机受信任根证书里"),
    CERT_E_CHAINING: ("warn", "证书链不完整，无法追到受信任根"),
    CERT_E_EXPIRED: ("warn", "签名证书已过期（旧程序常见，不一定有问题）"),
    CRYPT_E_SECURITY_SETTINGS: ("warn", "本机安全策略禁止校验该签名"),
}

# ---------------------------------------------------------------- Win32 结构
_wintrust = ctypes.WinDLL("wintrust", use_last_error=True)
_k32 = ctypes.WinDLL("kernel32", use_last_error=True)


class _GUID(ctypes.Structure):
    _fields_ = [("Data1", wt.DWORD), ("Data2", wt.WORD), ("Data3", wt.WORD),
                ("Data4", ctypes.c_ubyte * 8)]


def _guid(d1, d2, d3, rest):
    return _GUID(d1, d2, d3, (ctypes.c_ubyte * 8)(*rest))


WINTRUST_ACTION_GENERIC_VERIFY_V2 = _guid(
    0x00AAC56B, 0xCD44, 0x11D0, (0x8C, 0xC2, 0x00, 0xC0, 0x4F, 0xC2, 0x95, 0xEE))

WTD_UI_NONE = 2
WTD_REVOKE_NONE = 0
WTD_CHOICE_FILE = 1
WTD_CHOICE_CATALOG = 2
WTD_STATEACTION_VERIFY = 1
WTD_STATEACTION_CLOSE = 2
WTD_SAFER_FLAG = 0x00000100
WTD_CACHE_ONLY_URL_RETRIEVAL = 0x00001000
WTD_REVOKE_WHOLECHAIN = 0x00000001


class _WINTRUST_FILE_INFO(ctypes.Structure):
    _fields_ = [("cbStruct", wt.DWORD),
                ("pcwszFilePath", ctypes.c_wchar_p),
                ("hFile", wt.HANDLE),
                ("pgKnownSubject", ctypes.POINTER(_GUID))]


class _CATALOG_INFO(ctypes.Structure):
    _fields_ = [("cbStruct", wt.DWORD),
                ("wszCatalogFile", ctypes.c_wchar * 260)]


class _WINTRUST_CATALOG_INFO(ctypes.Structure):
    _fields_ = [("cbStruct", wt.DWORD),
                ("dwCatalogVersion", wt.DWORD),
                ("pcwszCatalogFilePath", ctypes.c_wchar_p),
                ("pcwszMemberTag", ctypes.c_wchar_p),
                ("pcwszMemberFilePath", ctypes.c_wchar_p),
                ("hMemberFile", wt.HANDLE),
                ("pbCalculatedFileHash", ctypes.POINTER(ctypes.c_ubyte)),
                ("cbCalculatedFileHash", wt.DWORD),
                ("pcCatalogContext", wt.HANDLE),
                ("hCatAdmin", wt.HANDLE)]


class _WINTRUST_DATA(ctypes.Structure):
    _fields_ = [("cbStruct", wt.DWORD),
                ("pPolicyCallbackData", ctypes.c_void_p),
                ("pSIPClientData", ctypes.c_void_p),
                ("dwUIChoice", wt.DWORD),
                ("fdwRevocationChecks", wt.DWORD),
                ("dwUnionChoice", wt.DWORD),
                ("pUnion", ctypes.c_void_p),
                ("dwStateAction", wt.DWORD),
                ("hWVTStateData", wt.HANDLE),
                ("pwszURLReference", ctypes.c_wchar_p),
                ("dwProvFlags", wt.DWORD),
                ("dwUIContext", wt.DWORD),
                ("pSignatureSettings", ctypes.c_void_p)]


_wintrust.WinVerifyTrust.argtypes = [wt.HANDLE, ctypes.POINTER(_GUID), ctypes.c_void_p]
_wintrust.WinVerifyTrust.restype = ctypes.c_long
_wintrust.CryptCATAdminAcquireContext.argtypes = [ctypes.POINTER(wt.HANDLE),
                                                  ctypes.POINTER(_GUID), wt.DWORD]
_wintrust.CryptCATAdminAcquireContext.restype = wt.BOOL
_wintrust.CryptCATAdminCalcHashFromFileHandle.argtypes = [
    wt.HANDLE, ctypes.POINTER(wt.DWORD), ctypes.POINTER(ctypes.c_ubyte), wt.DWORD]
_wintrust.CryptCATAdminCalcHashFromFileHandle.restype = wt.BOOL
_wintrust.CryptCATAdminEnumCatalogFromHash.argtypes = [
    wt.HANDLE, ctypes.POINTER(ctypes.c_ubyte), wt.DWORD, wt.DWORD,
    ctypes.POINTER(wt.HANDLE)]
# 这一行不能少：默认 c_int 会把 64 位目录句柄截断 → access violation
_wintrust.CryptCATAdminEnumCatalogFromHash.restype = wt.HANDLE
_wintrust.CryptCATAdminReleaseCatalogContext.argtypes = [wt.HANDLE, wt.HANDLE, wt.DWORD]
_wintrust.CryptCATAdminReleaseCatalogContext.restype = wt.BOOL
_wintrust.CryptCATAdminReleaseContext.argtypes = [wt.HANDLE, wt.DWORD]
_wintrust.CryptCATAdminReleaseContext.restype = wt.BOOL
_wintrust.CryptCATCatalogInfoFromContext.argtypes = [
    wt.HANDLE, ctypes.POINTER(_CATALOG_INFO), wt.DWORD]
_wintrust.CryptCATCatalogInfoFromContext.restype = wt.BOOL

_INVALID_HANDLE = ctypes.c_void_p(-1).value
FILE_SHARE_ALL = 0x07
OPEN_EXISTING = 3
GENERIC_READ = 0x80000000


@dataclass
class SignInfo:
    """一个文件的签名结论。"""
    path: str
    rc: int = 0
    kind: str = "ok"                  # 见 LEVEL_CN 的 key
    method: str = ""                  # embedded / catalog / none
    catalog: str = ""                 # 命中的 .cat 文件名
    note: str = ""                    # 结果码解释
    elapsed_ms: float = 0.0

    @property
    def ok(self) -> bool:
        return self.kind == "ok"

    @property
    def suspicious(self) -> bool:
        """真正值得报的：没签名或签名坏了。证书过期一类只算提示。"""
        return self.kind in ("uncatalogued", "bad")

    @property
    def level_text(self) -> str:
        return LEVEL_CN.get(self.kind, self.kind)

    @property
    def rc_text(self) -> str:
        return "0x%08X" % (self.rc & 0xFFFFFFFF)

    @property
    def method_text(self) -> str:
        return {"embedded": "内嵌签名", "catalog": "目录签名",
                "none": "无", "": "-"}.get(self.method, self.method)


def _decode(rc: int) -> tuple[str, str]:
    if rc == 0:
        return _CODE_MAP[0]
    return _CODE_MAP.get(rc & 0xFFFFFFFF,
                         ("bad", "校验未通过（结果码 %s），可能文件被替换或签名损坏"
                          % ("0x%08X" % (rc & 0xFFFFFFFF))))


def _verify_embedded(path: str) -> int:
    fi = _WINTRUST_FILE_INFO()
    fi.cbStruct = ctypes.sizeof(fi)
    fi.pcwszFilePath = path
    fi.hFile = None
    fi.pgKnownSubject = None
    wd = _WINTRUST_DATA()
    wd.cbStruct = ctypes.sizeof(wd)
    wd.dwUIChoice = WTD_UI_NONE
    wd.fdwRevocationChecks = WTD_REVOKE_NONE
    wd.dwUnionChoice = WTD_CHOICE_FILE
    wd.pUnion = ctypes.cast(ctypes.pointer(fi), ctypes.c_void_p)
    wd.dwStateAction = WTD_STATEACTION_VERIFY
    wd.dwProvFlags = WTD_SAFER_FLAG | WTD_CACHE_ONLY_URL_RETRIEVAL
    rc = _wintrust.WinVerifyTrust(None, ctypes.byref(WINTRUST_ACTION_GENERIC_VERIFY_V2),
                                  ctypes.byref(wd))
    wd.dwStateAction = WTD_STATEACTION_CLOSE
    _wintrust.WinVerifyTrust(None, ctypes.byref(WINTRUST_ACTION_GENERIC_VERIFY_V2),
                             ctypes.byref(wd))
    return int(rc)


def _verify_catalog(path: str) -> tuple[int, str]:
    """走 .cat 目录签名。返回 (rc, 目录文件名)；找不到目录回 (NO_CATALOG, "")。"""
    h_admin = wt.HANDLE()
    if not _wintrust.CryptCATAdminAcquireContext(ctypes.byref(h_admin), None, 0):
        return NO_CATALOG, ""
    h_file = _k32.CreateFileW(path, GENERIC_READ, FILE_SHARE_ALL, None,
                              OPEN_EXISTING, 0, None)
    if not h_file or h_file == _INVALID_HANDLE:
        _wintrust.CryptCATAdminReleaseContext(h_admin, 0)
        return NO_CATALOG, ""
    try:
        size = wt.DWORD(0)
        _wintrust.CryptCATAdminCalcHashFromFileHandle(h_file, ctypes.byref(size),
                                                      None, 0)
        if not size.value:
            return NO_CATALOG, ""
        buf = (ctypes.c_ubyte * size.value)()
        if not _wintrust.CryptCATAdminCalcHashFromFileHandle(
                h_file, ctypes.byref(size), buf, 0):
            return NO_CATALOG, ""
        h_cat = _wintrust.CryptCATAdminEnumCatalogFromHash(
            h_admin, buf, size.value, 0, None)
        if not h_cat:
            return NO_CATALOG, ""
        try:
            info = _CATALOG_INFO()
            info.cbStruct = ctypes.sizeof(info)
            _wintrust.CryptCATCatalogInfoFromContext(h_cat, ctypes.byref(info), 0)
            cat_name = os.path.basename(info.wszCatalogFile)

            ci = _WINTRUST_CATALOG_INFO()
            ci.cbStruct = ctypes.sizeof(ci)
            ci.pcwszCatalogFilePath = info.wszCatalogFile
            ci.pcwszMemberTag = os.path.basename(path)   # 成员标签就是文件名
            ci.pcwszMemberFilePath = path
            ci.hMemberFile = h_file
            ci.pbCalculatedFileHash = buf
            ci.cbCalculatedFileHash = size.value
            ci.pcCatalogContext = None
            ci.hCatAdmin = None
            wd = _WINTRUST_DATA()
            wd.cbStruct = ctypes.sizeof(wd)
            wd.dwUIChoice = WTD_UI_NONE
            wd.fdwRevocationChecks = WTD_REVOKE_NONE
            wd.dwUnionChoice = WTD_CHOICE_CATALOG
            wd.pUnion = ctypes.cast(ctypes.pointer(ci), ctypes.c_void_p)
            wd.dwStateAction = WTD_STATEACTION_VERIFY
            wd.dwProvFlags = WTD_SAFER_FLAG | WTD_CACHE_ONLY_URL_RETRIEVAL
            rc = int(_wintrust.WinVerifyTrust(
                None, ctypes.byref(WINTRUST_ACTION_GENERIC_VERIFY_V2), ctypes.byref(wd)))
            wd.dwStateAction = WTD_STATEACTION_CLOSE
            _wintrust.WinVerifyTrust(None, ctypes.byref(WINTRUST_ACTION_GENERIC_VERIFY_V2),
                                     ctypes.byref(wd))
            return rc, cat_name
        finally:
            _wintrust.CryptCATAdminReleaseCatalogContext(h_admin, h_cat, 0)
    finally:
        _k32.CloseHandle(h_file)
        _wintrust.CryptCATAdminReleaseContext(h_admin, 0)


def verify(path: str) -> SignInfo:
    """校验一个文件。先看内嵌签名，失败再看目录签名。

    目录签名只在「内嵌签名不存在」时才查 —— 内嵌签名明确「被改过」
    （`TRUST_E_BAD_DIGEST`）时不该再用目录签名洗白。
    """
    import time
    t0 = time.perf_counter()
    info = SignInfo(path=path)
    try:
        if not os.path.isfile(path):
            info.kind, info.note, info.method = "missing", "文件不存在或不是文件", "none"
            return info
        rc = _verify_embedded(path)
        info.rc = rc
        if rc == 0:
            info.kind, info.note, info.method = "ok", _CODE_MAP[0][1], "embedded"
            return info
        no_sig = (rc & 0xFFFFFFFF) in (TRUST_E_NOSIGNATURE,
                                       TRUST_E_SUBJECT_FORM_UNKNOWN,
                                       TRUST_E_PROVIDER_UNKNOWN)
        if no_sig:
            rc2, cat = _verify_catalog(path)
            if rc2 != NO_CATALOG:
                info.rc = rc2
                info.catalog = cat
                info.kind, info.note = _decode(rc2)
                info.method = "catalog" if rc2 == 0 else "none"
                if rc2 != 0 and not info.note:
                    info.note = "目录签名校验未通过"
                return info
            info.kind = "uncatalogued"
            info.method = "none"
            info.note = _CODE_MAP[TRUST_E_NOSIGNATURE][1]
            return info
        info.kind, info.note, info.method = (*_decode(rc), "none")
        return info
    except OSError as exc:
        info.kind, info.note = "error", "读取文件失败：%s" % exc
        return info
    finally:
        info.elapsed_ms = (time.perf_counter() - t0) * 1000


def verify_many(paths, stop_flag=None, progress=None) -> list[SignInfo]:
    """批量校验。`progress(done, total)` 用于进度条。

    `stop_flag` 兼容两种约定：`run_bg` 传进来的 `[bool]` 列表，以及直接传函数。
    （v1.14 只写了 `stop_flag()` 一种，接到 QThread 的列表会 TypeError。）
    """
    total = len(paths)
    out: list[SignInfo] = []
    for i, p in enumerate(paths):
        if stop_flag is not None:
            stopped = stop_flag() if callable(stop_flag) else bool(stop_flag[0])
            if stopped:
                break
        out.append(verify(p))
        if progress is not None:
            progress(i + 1, total)
    return out


def describe(info: SignInfo) -> str:
    """给表格/详情用的一行说明。"""
    bits = [info.level_text]
    if info.catalog:
        bits.append("命中 %s" % info.catalog)
    if info.note:
        bits.append(info.note)
    return " · ".join(bits)
