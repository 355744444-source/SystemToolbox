# -*- coding: utf-8 -*-
"""v1.15.0 第三轮：复核签名结论、解 ImportDate、看 SignerName 覆盖面。"""
import datetime
import os
import struct
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
LOG = os.path.join(ROOT, "probe_v1150c.log")
SR = os.environ.get("SystemRoot", r"C:\Windows")
DRV = os.path.join(SR, "System32", "drivers")
DB = r"SYSTEM\DriverDatabase"


def main():
    import winreg

    from app.core import sign

    out = []

    def w(m=""):
        out.append(m)

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

    # -------------------------------------------------- 1. 那几个第三方驱动
    w("== 逐个签名（v1.14 曾判未签名的 5 个 + bthmodem）==")
    for name in ["AliPaladinEx64.sys", "IOMap64.sys", "MsIo64.sys", "PHIPS.sys",
                 "PROCEXP152.sys", "bthmodem.sys", "nvlddmkm.sys", "kbdclass.sys"]:
        p = os.path.join(DRV, name)
        if not os.path.isfile(p):
            w("  %-20s 不存在" % name)
            continue
        t0 = time.perf_counter()
        info = sign.verify(p)
        w("  %-20s %-10s method=%-10s rc=%-12s cat=%-28s %.0fms"
          % (name, info.level_text, info.method_text, info.rc_text,
             (info.catalog or "-")[:28], (time.perf_counter() - t0) * 1000))

    # -------------------------------------------------- 2. 全量再跑一次（对照）
    files = [os.path.join(DRV, n) for n in sorted(os.listdir(DRV))
             if n.lower().endswith(".sys") and os.path.isfile(os.path.join(DRV, n))]
    t0 = time.perf_counter()
    infos = sign.verify_many(files, None, None)
    w()
    w("== verify_many 全量复跑：%d 个 %.1fs ==" % (len(infos), time.perf_counter() - t0))
    from collections import Counter
    w("  method 分布: %s" % dict(Counter(i.method_text for i in infos)))
    w("  level 分布: %s" % dict(Counter(i.level_text for i in infos)))
    bad = [i for i in infos if i.suspicious]
    w("  可疑 %d 个: %s" % (len(bad), [os.path.basename(i.path) for i in bad]))

    # -------------------------------------------------- 3. ImportDate / SignerName
    w()
    pkgs = keys_of(DB + r"\DriverPackages")
    dates, signers, no_sign = [], Counter(), 0
    for p in pkgs:
        d = vals(DB + r"\DriverPackages\\" + p)
        raw = d.get("ImportDate")
        if isinstance(raw, bytes) and len(raw) == 8:
            ft = struct.unpack("<Q", raw)[0]
            if ft:
                dates.append(datetime.datetime(1601, 1, 1)
                             + datetime.timedelta(microseconds=ft / 10))
        sn = d.get("SignerName")
        if sn:
            signers[sn] += 1
        else:
            no_sign += 1
    w("ImportDate 可解: %d / %d 个包" % (len(dates), len(pkgs)))
    if dates:
        w("  范围: %s ~ %s"
          % (min(dates).strftime("%Y-%m-%d"), max(dates).strftime("%Y-%m-%d")))
    w("SignerName 覆盖: %d 个不同签名者，缺 %d 个包" % (len(signers), no_sign))
    for k, v in signers.most_common(8):
        w("  %-58s %d" % (k[:58], v))

    # SignerScore 与 SignerName 的对应（看能不能当等级用）
    w()
    pairs = {}
    for p in pkgs:
        d = vals(DB + r"\DriverPackages\\" + p)
        pairs.setdefault((d.get("SignerScore"), (d.get("SignerName") or "（无）")[:40]),
                         []).append(p)
    for (score, name), lst in sorted(pairs.items(), key=lambda x: -len(x[1]))[:10]:
        w("  score=0x%-9X %-42s %d 个  e.g. %s"
          % (score or 0, name, len(lst), lst[0][:40]))

    # -------------------------------------------------- 4. oemNN 包的整体样子
    w()
    inf_files = keys_of(DB + r"\DriverInfFiles")
    oem = [k for k in inf_files if k.lower().startswith("oem")]
    w("== 本机 23 个 oemNN.inf（第三方驱动包）==")
    for k in oem:
        v = vals(DB + r"\DriverInfFiles\\" + k)
        pkg = (v.get("") or [""])[0]
        d = vals(DB + r"\DriverPackages\\" + pkg)
        w("  %-10s %-26s %-34s %s"
          % (k, str(d.get("InfName"))[:26], str(d.get("SignerName"))[:34],
             "active" if v.get("Active") == pkg else "INACTIVE"))

    with open(LOG, "w", encoding="utf-8") as fh:
        fh.write("\n".join(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
