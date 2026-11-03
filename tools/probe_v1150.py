# -*- coding: utf-8 -*-
"""v1.15.0 驱动体检可行性实测。

四件事：
1. `HKLM\\SYSTEM\\DriverDatabase\\DriverPackages` 能否直接给出「驱动包清单」
   （oemNN.inf → OriginalInf / Provider / Class / Version / Date / Active）
2. `psapi.EnumDeviceDrivers` 能否给出「此刻已加载的内核驱动」
3. `Services` 里 Type=1/2 的驱动服务：数量、Start 分布、ImagePath 形态
4. `sign.verify_many` 对 System32\\drivers 全量的成本与被判「未签名」的清单

结果写 probe_v1150.log（stdout 在 Bash 里不可靠）。
"""
import ctypes
import os
import sys
import time
from ctypes import wintypes as wt

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
LOG = os.path.join(ROOT, "probe_v1150.log")
SR = os.environ.get("SystemRoot", r"C:\Windows")
DRV = os.path.join(SR, "System32", "drivers")

_PK = r"SYSTEM\CurrentControlSet\Control\Class"
_DB = r"SYSTEM\DriverDatabase"


def main():
    import winreg

    from app.core import sign

    out = []

    def w(m=""):
        out.append(m)

    # ---------------------------------------------------------------- 1. 驱动包
    t0 = time.perf_counter()
    keys = []
    try:
        base = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, _DB + "\\DriverPackages")
        i = 0
        while True:
            try:
                name = winreg.EnumKey(base, i)
            except OSError:
                break
            keys.append(name)
            i += 1
        winreg.CloseKey(base)
    except OSError as exc:
        w("DriverPackages 打不开: %s" % exc)
    w("DriverPackages: %d 个键，%.0fms" % (len(keys), (time.perf_counter() - t0) * 1000))

    def vals(path):
        d = {}
        try:
            k = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path)
        except OSError:
            return d
        try:
            i = 0
            while True:
                try:
                    n, v, _t = winreg.EnumValue(k, i)
                except OSError:
                    break
                d[n] = v
                i += 1
        finally:
            winreg.CloseKey(k)
        return d

    # 抽样看字段
    for name in keys[:3]:
        w("  %-46s %s" % (name, vals(_DB + "\\DriverPackages\\" + name)))
    # oemNN.inf 形态的键有多少，能不能拿到 OriginalInf
    oem = [k for k in keys if k.lower().startswith("oem") and k.lower().endswith(".inf")]
    w("  oemNN.inf 形态: %d" % len(oem))
    for name in oem[:3]:
        v = vals(_DB + "\\DriverPackages\\" + name)
        w("    %-16s OriginalInf=%s Provider=%s Class=%s Ver=%s"
          % (name, v.get("OriginalInf"), v.get("Provider"), v.get("Class"),
             v.get("DriverVersion")))

    t0 = time.perf_counter()
    act = {}
    try:
        base = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, _DB + "\\DriverInfFiles")
        i = 0
        while True:
            try:
                name = winreg.EnumKey(base, i)
            except OSError:
                break
            act[name] = vals(_DB + "\\DriverInfFiles\\" + name)
            i += 1
        winreg.CloseKey(base)
    except OSError as exc:
        w("DriverInfFiles 打不开: %s" % exc)
    w("DriverInfFiles: %d 个，%.0fms" % (len(act), (time.perf_counter() - t0) * 1000))
    for name in list(act)[:3]:
        w("  %-40s %s" % (name, act[name]))

    # ---------------------------------------------------------------- 2. 已加载
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    psapi.EnumDeviceDrivers.argtypes = [ctypes.POINTER(ctypes.c_void_p), wt.DWORD,
                                        ctypes.POINTER(wt.DWORD)]
    psapi.GetDeviceDriverBaseNameW.argtypes = [ctypes.c_void_p, wt.LPWSTR, wt.DWORD]
    need = wt.DWORD(0)
    t0 = time.perf_counter()
    psapi.EnumDeviceDrivers(None, 0, ctypes.byref(need))
    n = need.value // ctypes.sizeof(ctypes.c_void_p)
    arr = (ctypes.c_void_p * max(n, 1))()
    ok = psapi.EnumDeviceDrivers(arr, ctypes.sizeof(arr), ctypes.byref(need))
    cost = (time.perf_counter() - t0) * 1000
    names = []
    for i in range(need.value // ctypes.sizeof(ctypes.c_void_p)):
        buf = ctypes.create_unicode_buffer(260)
        if psapi.GetDeviceDriverBaseNameW(arr[i], buf, 260):
            names.append(buf.value)
    w("")
    w("EnumDeviceDrivers: ok=%d 装载模块 %d 个，%.1fms" % (ok, len(names), cost))
    w("  样例: %s" % names[:8])
    low = {x.lower() for x in names}
    w("  含 ntoskrnl: %s / 含 tcpip: %s" % ("ntoskrnl.exe" in low, "tcpip.sys" in low))

    # ---------------------------------------------------------------- 3. 驱动服务
    t0 = time.perf_counter()
    svcs = []
    try:
        base = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                              r"SYSTEM\CurrentControlSet\Services")
        i = 0
        while True:
            try:
                name = winreg.EnumKey(base, i)
            except OSError:
                break
            i += 1
            v = vals("SYSTEM\\CurrentControlSet\\Services\\" + name)
            t = v.get("Type")
            if isinstance(t, int) and t & 0x0F in (1, 2):        # 内核驱动 / 文件系统驱动
                svcs.append((name, t, v.get("Start"), v.get("ImagePath", "")))
        winreg.CloseKey(base)
    except OSError as exc:
        w("Services 打不开: %s" % exc)
    w("")
    w("驱动服务(Type&0xF in 1,2): %d 个，%.0fms" % (len(svcs), (time.perf_counter() - t0) * 1000))
    from collections import Counter
    w("  Start 分布: %s" % dict(Counter(s[2] for s in svcs)))
    w("  Type 分布: %s" % dict(Counter(s[1] for s in svcs)))
    shapes = Counter()
    for _n, _t, _s, ip in svcs:
        lowip = (ip or "").lower()
        if lowip.startswith("\\systemroot\\"):
            shapes["\\SystemRoot\\..."] += 1
        elif lowip.startswith("system32\\"):
            shapes["System32\\..."] += 1
        elif lowip.startswith("\\??\\"):
            shapes["\\??\\..."] += 1
        elif lowip:
            shapes["其他"] += 1
        else:
            shapes["空"] += 1
    w("  ImagePath 形态: %s" % dict(shapes))
    running = sum(1 for n, _t, _s, _ip in svcs if (n.lower() + ".sys") in low
                  or n.lower() in low)
    w("  名字能对上已加载模块: %d" % running)
    w("  样例: %s" % [(n, s) for n, _t, s, _ip in svcs[:6]])

    # ---------------------------------------------------------------- 4. 签名
    files = []
    for n in sorted(os.listdir(DRV)):
        p = os.path.join(DRV, n)
        if os.path.isfile(p) and n.lower().endswith(".sys"):
            files.append(p)
    w("")
    w("drivers 目录 .sys: %d 个" % len(files))
    t0 = time.perf_counter()
    infos = sign.verify_many(files, None, None)
    cost = (time.perf_counter() - t0) * 1000
    bad = [i for i in infos if i.suspicious]
    w("  verify_many 全量: %.1fs（%.1fms/个）" % (cost / 1000.0, cost / max(len(infos), 1)))
    w("  未签名/无效: %d 个" % len(bad))
    for i in bad[:20]:
        nm = os.path.basename(i.path)
        w("    %-26s %-10s %s loaded=%s"
          % (nm, i.level_text, i.rc_text[:28], nm.lower() in low))

    with open(LOG, "w", encoding="utf-8") as fh:
        fh.write("\n".join(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
