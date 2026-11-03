# -*- coding: utf-8 -*-
"""v1.15.0 驱动体检 · 第二轮：把第一轮留下的 5 个问号逐个落实。

1. `oemNN.inf` 到底在不在 —— 不在 `DriverPackages`，那 `DriverInfFiles` 里有吗？
   第三方驱动包用什么标识？（这决定「同一 INF 多版本」怎么判）
2. `DriverPackages` 的 `Version` 40 字节能否解出日期/版本；
   `StatusFlags` / `SignerScore` 是否有可用含义。
3. v1.14 探针里那 5 个「真未签名」的第三方驱动还在不在（AliPaladinEx64 等）。
4. `INF` 目录读 DriverVer 的成本（决定版本号从注册表取还是从 INF 取）。
5. 服务 ImagePath 为空的 12 个是什么；服务名 → 已加载模块名的映射该怎么归一。
"""
import ctypes
import os
import struct
import sys
import time
from ctypes import wintypes as wt

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
LOG = os.path.join(ROOT, "probe_v1150b.log")
SR = os.environ.get("SystemRoot", r"C:\Windows")
DB = r"SYSTEM\DriverDatabase"


def main():
    import winreg

    out = []

    def w(m=""):
        out.append(m)

    def keys_of(path):
        try:
            base = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path)
        except OSError:
            return []
        got = []
        try:
            while True:
                try:
                    got.append(winreg.EnumKey(base, len(got)))
                except OSError:
                    break
        finally:
            winreg.CloseKey(base)
        return got

    def vals(path):
        d = {}
        try:
            k = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path)
        except OSError:
            return d
        try:
            while True:
                try:
                    n, v, _t = winreg.EnumValue(k, len(d))
                except OSError:
                    break
                d[n] = v
        finally:
            winreg.CloseKey(k)
        return d

    # ------------------------------------------------------------ 1. oemNN.inf
    inf_files = keys_of(DB + r"\DriverInfFiles")
    pkgs = keys_of(DB + r"\DriverPackages")
    oem_inf = [k for k in inf_files if k.lower().startswith("oem")]
    w("DriverInfFiles=%d  DriverPackages=%d" % (len(inf_files), len(pkgs)))
    w("DriverInfFiles 里 oemNN.inf: %d 个  样例=%s" % (len(oem_inf), oem_inf[:5]))
    if oem_inf:
        for k in oem_inf[:3]:
            w("  %-18s %s" % (k, vals(DB + r"\DriverInfFiles\\" + k)))
            for p in vals(DB + r"\DriverInfFiles\\" + k).get("", []) or []:
                w("    包 %-46s %s" % (p, vals(DB + r"\DriverPackages\\" + p)))

    # 同一原始 INF 多个包的情况（跨版本残留）
    multi = {}
    for inf in inf_files:
        v = vals(DB + r"\DriverInfFiles\\" + inf)
        lst = v.get("", [])
        if isinstance(lst, str):
            lst = [lst]
        if len(lst) > 1:
            multi[inf] = (v.get("Active"), list(lst))
    w("同一 INF 有多个包（可能残留旧版本）: %d 个" % len(multi))
    for k in list(multi)[:5]:
        w("  %-34s active=%s 包=%d" % (k, multi[k][0], len(multi[k][1])))

    # ------------------------------------------------------------ 2. 二进制字段
    w()
    if pkgs:
        v = vals(DB + r"\DriverPackages\\" + pkgs[0])
        ver = v.get("Version")
        w("Version 字段 %d 字节: %s" % (len(ver or b""), (ver or b"").hex()))
        if isinstance(ver, bytes) and len(ver) >= 40:
            # 前 8 字节像 FILETIME（0x01D... 是 1601 起的 100ns）
            raw = struct.unpack("<Q", ver[:8])[0]
            w("  前 8 字节当 FILETIME: 0x%016X" % raw)
            try:
                import datetime
                ts = datetime.datetime(1601, 1, 1) + datetime.timedelta(
                    microseconds=raw / 10)
                w("  → %s" % ts.strftime("%Y-%m-%d %H:%M:%S"))
            except Exception as exc:
                w("  → 解析失败 %s" % exc)
            tail = ver[8:]
            w("  后续 32 字节: %s" % tail.hex())
        w("StatusFlags=0x%X SignerScore=0x%X FileSize=%d"
          % (v.get("StatusFlags", 0), v.get("SignerScore", 0), v.get("FileSize", 0)))
        # 统计一下这两个字段的取值分布，看能否当「签名等级」
        from collections import Counter
        sf, ss = Counter(), Counter()
        for p in pkgs[:400]:
            d = vals(DB + r"\DriverPackages\\" + p)
            sf[d.get("StatusFlags")] += 1
            ss[d.get("SignerScore")] += 1
        w("  StatusFlags 分布: %s" % dict(sf))
        w("  SignerScore 分布: %s" % dict(ss))

    # ------------------------------------------------------------ 3. 第三方未签名
    w()
    drv = os.path.join(SR, "System32", "drivers")
    watch = ["AliPaladinEx64.sys", "IOMap64.sys", "MsIo64.sys", "PHIPS.sys",
             "PROCEXP152.sys", "bthmodem.sys"]
    for name in watch:
        p = os.path.join(drv, name)
        w("  %-20s 存在=%s 大小=%s" % (name, os.path.isfile(p),
                                      os.path.getsize(p) if os.path.isfile(p) else "-"))

    # ------------------------------------------------------------ 4. INF 成本
    w()
    inf_dir = os.path.join(SR, "INF")
    names = [n for n in os.listdir(inf_dir) if n.lower().endswith(".inf")]
    t0 = time.perf_counter()
    hit = 0
    for n in names[:200]:
        try:
            with open(os.path.join(inf_dir, n), "r", encoding="utf-8",
                      errors="ignore") as fh:
                txt = fh.read(20000)
        except OSError:
            continue
        if "DriverVer" in txt:
            hit += 1
    cost = (time.perf_counter() - t0) * 1000
    w("INF 目录 %d 个 .inf；读前 200 个找 DriverVer：%.0fms（%.2fms/个，命中 %d）"
      % (len(names), cost, cost / 200.0, hit))

    # ------------------------------------------------------------ 5. 空 ImagePath
    w()
    empty = []
    svc_keys = keys_of(r"SYSTEM\CurrentControlSet\Services")
    for name in svc_keys:
        v = vals(r"SYSTEM\CurrentControlSet\Services\\" + name)
        t = v.get("Type")
        if isinstance(t, int) and (t & 0x0F) in (1, 2):
            ip = v.get("ImagePath", "")
            if not ip:
                empty.append((name, t, v.get("Start"), v.get("Group", ""),
                              v.get("Owners", "")))
    w("ImagePath 为空的驱动服务: %d" % len(empty))
    for row in empty[:14]:
        w("  %-28s type=%s start=%s group=%s owners=%s" % row)

    # 服务名 → 模块名的归一：psapi 列表对比服务名
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    psapi.EnumDeviceDrivers.argtypes = [ctypes.POINTER(ctypes.c_void_p), wt.DWORD,
                                        ctypes.POINTER(wt.DWORD)]
    psapi.GetDeviceDriverBaseNameW.argtypes = [ctypes.c_void_p, wt.LPWSTR, wt.DWORD]
    need = wt.DWORD(0)
    psapi.EnumDeviceDrivers(None, 0, ctypes.byref(need))
    n = need.value // ctypes.sizeof(ctypes.c_void_p)
    arr = (ctypes.c_void_p * max(n, 1))()
    psapi.EnumDeviceDrivers(arr, ctypes.sizeof(arr), ctypes.byref(need))
    mods = []
    for i in range(need.value // ctypes.sizeof(ctypes.c_void_p)):
        buf = ctypes.create_unicode_buffer(260)
        if psapi.GetDeviceDriverBaseNameW(arr[i], buf, 260):
            mods.append(buf.value)
    stem = {m.lower().rsplit(".", 1)[0] for m in mods}
    names = []
    for name in svc_keys:
        v = vals(r"SYSTEM\CurrentControlSet\Services\\" + name)
        t = v.get("Type")
        if isinstance(t, int) and (t & 0x0F) in (1, 2):
            names.append(name)
    matched = [s for s in names if s.lower() in stem]
    w("驱动服务 %d 个，其中服务名 == 已加载模块名(去扩展名) 的 %d 个"
      % (len(names), len(matched)))
    # ImagePath 文件名也能对
    byfile = 0
    for name in names:
        v = vals(r"SYSTEM\CurrentControlSet\Services\\" + name)
        ip = os.path.basename((v.get("ImagePath") or "").replace("\\", "/")).lower()
        if ip and ip.rsplit(".", 1)[0] in stem:
            byfile += 1
    w("按 ImagePath 文件名匹配: %d 个" % byfile)

    with open(LOG, "w", encoding="utf-8") as fh:
        fh.write("\n".join(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
