"""验证 WinVerifyTrust 签名校验 + HTTP 时钟偏差，判断能否给启动项/驱动加证据。"""
import ctypes
import ctypes.wintypes as wt
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "probe_ideas5.log")
log = []


def p(s=""):
    log.append(str(s))


# ---------------- WinVerifyTrust ----------------
wintrust = ctypes.WinDLL("wintrust", use_last_error=True)


class GUID(ctypes.Structure):
    _fields_ = [("Data1", wt.DWORD), ("Data2", wt.WORD), ("Data3", wt.WORD),
                ("Data4", ctypes.c_ubyte * 8)]


class WINTRUST_FILE_INFO(ctypes.Structure):
    _fields_ = [("cbStruct", wt.DWORD), ("pcwszFilePath", ctypes.c_wchar_p),
                ("hFile", wt.HANDLE), ("pgKnownSubject", ctypes.POINTER(GUID))]


class WINTRUST_DATA(ctypes.Structure):
    _fields_ = [
        ("cbStruct", wt.DWORD),
        ("pPolicyCallbackData", ctypes.c_void_p),
        ("pSIPClientData", ctypes.c_void_p),
        ("dwUIChoice", wt.DWORD),
        ("fdwRevocationChecks", wt.DWORD),
        ("dwUnionChoice", wt.DWORD),
        ("pFile", ctypes.POINTER(WINTRUST_FILE_INFO)),
        ("dwStateAction", wt.DWORD),
        ("hWVTStateData", wt.HANDLE),
        ("pwszURLReference", ctypes.c_wchar_p),
        ("dwProvFlags", wt.DWORD),
        ("dwUIContext", wt.DWORD),
        ("pSignatureSettings", ctypes.c_void_p),
    ]


# WINTRUST_ACTION_GENERIC_VERIFY_V2
ACTION = GUID(0x00AAC56B, 0xCD44, 0x11D0,
              (ctypes.c_ubyte * 8)(0x8C, 0xC2, 0x00, 0xC0, 0x4F, 0xC2, 0x95, 0xEE))
WTD_UI_NONE = 2
WTD_REVOKE_NONE = 0
WTD_CHOICE_FILE = 1
WTD_STATEACTION_VERIFY = 1
WTD_STATEACTION_CLOSE = 2
WTD_SAFER_FLAG = 0x100
WTD_CACHE_ONLY_URL_RETRIEVAL = 0x1000


def verify(path):
    t0 = time.perf_counter()
    fi = WINTRUST_FILE_INFO()
    fi.cbStruct = ctypes.sizeof(fi)
    fi.pcwszFilePath = path
    fi.hFile = None
    fi.pgKnownSubject = None
    wd = WINTRUST_DATA()
    wd.cbStruct = ctypes.sizeof(wd)
    wd.dwUIChoice = WTD_UI_NONE
    wd.fdwRevocationChecks = WTD_REVOKE_NONE
    wd.dwUnionChoice = WTD_CHOICE_FILE
    wd.pFile = ctypes.pointer(fi)
    wd.dwStateAction = WTD_STATEACTION_VERIFY
    wd.dwProvFlags = WTD_SAFER_FLAG | WTD_CACHE_ONLY_URL_RETRIEVAL
    rc = wintrust.WinVerifyTrust(None, ctypes.byref(ACTION), ctypes.byref(wd))
    wd.dwStateAction = WTD_STATEACTION_CLOSE
    wintrust.WinVerifyTrust(None, ctypes.byref(ACTION), ctypes.byref(wd))
    return rc, (time.perf_counter() - t0) * 1000


def signer(path):
    """取签名者名称：WinVerifyTrust 只给结论，名字得走 CryptQueryObject。"""
    try:
        import subprocess
        r = subprocess.run(["powershell", "-NoProfile", "-Command",
                            "(Get-AuthenticodeSignature -LiteralPath '%s').SignerCertificate.Subject"
                            % path.replace("'", "''")],
                           capture_output=True, text=True, creationflags=0x08000000,
                           timeout=20)
        return (r.stdout or "").strip()[:90]
    except Exception as exc:  # noqa: BLE001
        return "ERR %s" % exc


p("=" * 68)
p("A. WinVerifyTrust 校验（0=通过，0x800B0100=无签名，0x800B0109=链不受信）")
p("=" * 68)
samples = [
    r"C:\Windows\System32\notepad.exe",
    r"C:\Windows\System32\drivers\tcpip.sys",
    r"C:\Users\MSI\.workbuddy\binaries\python\versions\3.13.12\python.exe",
    r"C:\Users\MSI\WorkBuddy\2026-10-21-09-08-23\CrashLogSuite\dist\SystemToolbox.exe",
    r"C:\Windows\System32\drivers\ACPI.sys",
    r"C:\Windows\System32\kernel32.dll",
    r"C:\Program Files\CatsxpSoftware\Catsxp-Browser\Application\chrome.exe",
]
for f in samples:
    if not os.path.isfile(f):
        p("  跳过（不存在）%s" % f)
        continue
    rc, ms = verify(f)
    p("  rc=0x%08X %7.1f ms  %s" % (rc & 0xFFFFFFFF, ms, f))
    if rc != 0:
        p("      签名者? %s" % signer(f))

p()
p("  批量代价：对 System32 下 60 个 .sys 逐一校验")
sysdir = os.path.join(os.environ["WINDIR"], "System32", "drivers")
files = [os.path.join(sysdir, n) for n in sorted(os.listdir(sysdir))
         if n.lower().endswith(".sys")][:60]
t0 = time.perf_counter()
bad = 0
for f in files:
    rc, _ = verify(f)
    if rc != 0:
        bad += 1
dt = time.perf_counter() - t0
p("  %d 个文件 共 %.2f s（平均 %.1f ms/个），未通过 %d 个"
  % (len(files), dt, dt * 1000 / max(len(files), 1), bad))

# ---------------- 时钟偏差 ----------------
p()
p("=" * 68)
p("B. 时钟偏差（HTTP Date 头，不依赖 NTP 权限）")
p("=" * 68)
try:
    import email.utils
    import ssl
    import urllib.request
    t0 = time.perf_counter()
    req = urllib.request.Request("https://www.baidu.com", method="HEAD",
                                 headers={"User-Agent": "Mozilla/5.0"})
    ctx = ssl.create_default_context()
    with urllib.request.urlopen(req, timeout=6, context=ctx) as r:
        date = r.headers.get("Date")
    dt = time.perf_counter() - t0
    p("  Date=%r  用时 %.0f ms" % (date, dt * 1000))
    if date:
        remote = email.utils.parsedate_to_datetime(date)
        now = time.time()
        off = remote.timestamp() - now + dt / 2  # 粗扣单程
        p("  本地与源偏差 ≈ %+.1f s" % off)
    # 第二个源
    req = urllib.request.Request("https://www.qq.com", method="HEAD",
                                 headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=6, context=ctx) as r:
        p("  qq Date=%r" % r.headers.get("Date"))
except Exception as exc:  # noqa: BLE001
    p("  EXC %s" % exc)

p()
p("  w32time 服务状态（sc.exe 被沙箱拦，改读注册表）:")
try:
    import winreg
    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                        r"SYSTEM\CurrentControlSet\Services\w32time") as k:
        p("    Start=%s  （2=自动 3=手动 4=禁用）" % winreg.QueryValueEx(k, "Start")[0])
    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                        r"SYSTEM\CurrentControlSet\Services\W32Time\Parameters") as k:
        p("    Type=%s  NtpServer=%s"
          % (winreg.QueryValueEx(k, "Type")[0], winreg.QueryValueEx(k, "NtpServer")[0]))
except Exception as exc:  # noqa: BLE001
    p("    EXC %s" % exc)

with open(OUT, "w", encoding="utf-8") as fh:
    fh.write("\n".join(log) + "\n")
print("ok", len(log))
