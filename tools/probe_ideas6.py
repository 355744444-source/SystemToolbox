"""验证目录签名（catalog）补判：WinVerifyTrust 文件签名失败后再查 .cat。"""
import ctypes
import ctypes.wintypes as wt
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "probe_ideas6.log")
log = []


def p(s=""):
    log.append(str(s))


k32 = ctypes.WinDLL("kernel32", use_last_error=True)
wintrust = ctypes.WinDLL("wintrust", use_last_error=True)


class GUID(ctypes.Structure):
    _fields_ = [("Data1", wt.DWORD), ("Data2", wt.WORD), ("Data3", wt.WORD),
                ("Data4", ctypes.c_ubyte * 8)]


GENERIC_VERIFY_V2 = GUID(0x00AAC56B, 0xCD44, 0x11D0,
                         (ctypes.c_ubyte * 8)(0x8C, 0xC2, 0x00, 0xC0, 0x4F, 0xC2, 0x95, 0xEE))


class CATALOG_INFO(ctypes.Structure):
    _fields_ = [("cbStruct", wt.DWORD), ("wszCatalogFile", ctypes.c_wchar * 260)]


DRIVER_ACTION = GUID(0xF750E6C3, 0x38EE, 0x11D1,
                     (ctypes.c_ubyte * 8)(0x85, 0xE5, 0x00, 0xC0, 0x4F, 0xC2, 0x95, 0xEE))

# 计算文件哈希 + 找目录
wintrust.CryptCATAdminAcquireContext.argtypes = [ctypes.POINTER(wt.HANDLE),
                                                 ctypes.POINTER(GUID), wt.DWORD]
wintrust.CryptCATAdminCalcHashFromFileHandle.argtypes = [
    wt.HANDLE, ctypes.POINTER(wt.DWORD), ctypes.POINTER(ctypes.c_ubyte), wt.DWORD]
wintrust.CryptCATAdminEnumCatalogFromHash.argtypes = [
    wt.HANDLE, ctypes.POINTER(ctypes.c_ubyte), wt.DWORD, wt.DWORD,
    ctypes.POINTER(wt.HANDLE)]
wintrust.CryptCATCatalogInfoFromContext.argtypes = [
    wt.HANDLE, ctypes.POINTER(CATALOG_INFO), wt.DWORD]
wintrust.CryptCATAdminReleaseCatalogContext.argtypes = [wt.HANDLE, wt.HANDLE, wt.DWORD]
wintrust.CryptCATAdminEnumCatalogFromHash.restype = wt.HANDLE
wintrust.CryptCATAdminAcquireContext.restype = wt.BOOL
wintrust.CryptCATAdminCalcHashFromFileHandle.restype = wt.BOOL
wintrust.CryptCATAdminReleaseCatalogContext.restype = wt.BOOL
wintrust.CryptCATAdminReleaseContext.restype = wt.BOOL
wintrust.WinVerifyTrust.restype = ctypes.c_long
wintrust.CryptCATAdminReleaseContext.argtypes = [wt.HANDLE, wt.DWORD]


class CATALOG_INFO_EX(ctypes.Structure):
    """带目录文件路径 + 成员标签，用于 WinVerifyTrust 的目录校验。"""
    _fields_ = [("cbStruct", wt.DWORD), ("wszCatalogFile", ctypes.c_wchar * 260)]


class WINTRUST_CATALOG_INFO(ctypes.Structure):
    _fields_ = [
        ("cbStruct", wt.DWORD),
        ("dwCatalogVersion", wt.DWORD),
        ("pcwszCatalogFilePath", ctypes.c_wchar_p),
        ("pcwszMemberTag", ctypes.c_wchar_p),
        ("pcwszMemberFilePath", ctypes.c_wchar_p),
        ("hMemberFile", wt.HANDLE),
        ("pbCalculatedFileHash", ctypes.POINTER(ctypes.c_ubyte)),
        ("cbCalculatedFileHash", wt.DWORD),
        ("pcCatalogContext", wt.HANDLE),
        ("hCatAdmin", wt.HANDLE),
    ]


class WINTRUST_DATA(ctypes.Structure):
    _fields_ = [
        ("cbStruct", wt.DWORD),
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
        ("pSignatureSettings", ctypes.c_void_p),
    ]


def verify_catalog(path):
    """返回 (rc, catalog 名, 成员标签, ms)"""
    t0 = time.perf_counter()
    hAdmin = wt.HANDLE()
    if not wintrust.CryptCATAdminAcquireContext(ctypes.byref(hAdmin), None, 0):
        return -1, "", "", 0.0
    hFile = k32.CreateFileW(path, 0x80000000, 7, None, 3, 0, None)
    if not hFile or hFile == ctypes.c_void_p(-1).value:
        wintrust.CryptCATAdminReleaseContext(hAdmin, 0)
        return -2, "", "", 0.0
    try:
        size = wt.DWORD(0)
        wintrust.CryptCATAdminCalcHashFromFileHandle(hFile, ctypes.byref(size), None, 0)
        size = wt.DWORD(size.value or 0)
        buf = (ctypes.c_ubyte * max(size.value, 1))()
        if not wintrust.CryptCATAdminCalcHashFromFileHandle(hFile, ctypes.byref(size), buf, 0):
            return -3, "", "", 0.0
        hCat = wintrust.CryptCATAdminEnumCatalogFromHash(hAdmin, buf, size.value, 0, None)
        if not hCat:
            return -4, "", "", (time.perf_counter() - t0) * 1000
        try:
            info = CATALOG_INFO()
            info.cbStruct = ctypes.sizeof(info)
            wintrust.CryptCATCatalogInfoFromContext(hCat, ctypes.byref(info), 0)
            tag = os.path.basename(path)
            ci = WINTRUST_CATALOG_INFO()
            ci.cbStruct = ctypes.sizeof(ci)
            ci.pcwszCatalogFilePath = info.wszCatalogFile
            ci.pcwszMemberTag = tag
            ci.pcwszMemberFilePath = path
            ci.hMemberFile = hFile
            ci.pbCalculatedFileHash = buf
            ci.cbCalculatedFileHash = size.value
            ci.pcCatalogContext = None
            ci.hCatAdmin = None
            wd = WINTRUST_DATA()
            wd.cbStruct = ctypes.sizeof(wd)
            wd.dwUIChoice = 2
            wd.fdwRevocationChecks = 0
            wd.dwUnionChoice = 2          # WTD_CHOICE_CATALOG
            wd.pUnion = ctypes.cast(ctypes.pointer(ci), ctypes.c_void_p)
            wd.dwStateAction = 1
            wd.dwProvFlags = 0x100 | 0x1000
            rc = wintrust.WinVerifyTrust(None, ctypes.byref(GENERIC_VERIFY_V2),
                                         ctypes.byref(wd))
            wd.dwStateAction = 2
            wintrust.WinVerifyTrust(None, ctypes.byref(GENERIC_VERIFY_V2),
                                    ctypes.byref(wd))
            return rc, os.path.basename(info.wszCatalogFile), tag, \
                (time.perf_counter() - t0) * 1000
        finally:
            wintrust.CryptCATAdminReleaseCatalogContext(hAdmin, hCat, 0)
    finally:
        k32.CloseHandle(hFile)
        wintrust.CryptCATAdminReleaseContext(hAdmin, 0)


p("=" * 70)
p("目录签名补判（对文件签名判为「无签名」的样本）")
p("=" * 70)
for f in [r"C:\Windows\System32\notepad.exe",
          r"C:\Windows\System32\drivers\cdfs.sys",
          r"C:\Windows\System32\drivers\acpiex.sys",
          r"C:\Users\MSI\.workbuddy\binaries\python\versions\3.13.12\python.exe",
          r"C:\Users\MSI\WorkBuddy\2026-10-21-09-08-23\CrashLogSuite\dist\SystemToolbox.exe"]:
    if not os.path.isfile(f):
        p("  跳过 %s" % f)
        continue
    rc, cat, tag, ms = verify_catalog(f)
    p("  rc=0x%08X %6.1f ms cat=%-24s %s" % (rc & 0xFFFFFFFF, ms, cat or "-",
                                             os.path.basename(f)))

p()
p("批量：System32\\drivers 前 60 个 .sys 走 catalog 判定的代价")
sysdir = os.path.join(os.environ["WINDIR"], "System32", "drivers")
files = [os.path.join(sysdir, n) for n in sorted(os.listdir(sysdir))
         if n.lower().endswith(".sys")][:60]
t0 = time.perf_counter()
ok = bad = 0
uncat = []
for f in files:
    rc, cat, tag, _ = verify_catalog(f)
    if rc == 0:
        ok += 1
    else:
        bad += 1
        uncat.append((os.path.basename(f), rc))
dt = time.perf_counter() - t0
p("  %d 个文件 共 %.2f s（%.1f ms/个）通过 %d 未通过 %d"
  % (len(files), dt, dt * 1000 / len(files), ok, bad))
for n, rc in uncat[:8]:
    p("    未通过 %-20s rc=0x%08X" % (n, rc & 0xFFFFFFFF))

with open(OUT, "w", encoding="utf-8") as fh:
    fh.write("\n".join(log) + "\n")
print("ok", len(log))
