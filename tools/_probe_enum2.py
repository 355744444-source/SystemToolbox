# -*- coding: utf-8 -*-
"""临时：验证类名来源与 CM 状态取数。"""
import ctypes
import winreg
from ctypes import wintypes

HK = 0x80000002
K = r"SYSTEM\CurrentControlSet\Enum"
C = r"SYSTEM\CurrentControlSet\Control\Class"
G = "{4d36e97d-e325-11ce-bfc1-08002be10318}"

lines = []
p = lines.append


def values(path):
    out = {}
    try:
        k = winreg.OpenKey(HK, path, 0, winreg.KEY_READ)
    except OSError as e:
        return {"__error__": str(e)}
    try:
        i = 0
        while True:
            try:
                n, v, _t = winreg.EnumValue(k, i)
            except OSError:
                break
            out[n] = v
            i += 1
    finally:
        k.Close()
    return out


p("Control\\Class\\%s values: %r" % (G, values(C + "\\" + G)))

cm = ctypes.WinDLL("cfgmgr32", use_last_error=True)
cm.CM_Locate_DevNodeW.argtypes = [ctypes.POINTER(wintypes.DWORD),
                                  wintypes.LPCWSTR, wintypes.DWORD]
cm.CM_Locate_DevNodeW.restype = ctypes.c_long
cm.CM_Get_DevNode_Status.argtypes = [ctypes.POINTER(wintypes.DWORD),
                                     ctypes.POINTER(wintypes.DWORD),
                                     wintypes.DWORD, wintypes.DWORD]
cm.CM_Get_DevNode_Status.restype = ctypes.c_long


def status(inst):
    dev = wintypes.DWORD(0)
    rc = cm.CM_Locate_DevNodeW(ctypes.byref(dev), inst, 0)
    flag = "normal"
    if rc != 0:
        rc = cm.CM_Locate_DevNodeW(ctypes.byref(dev), inst, 1)
        flag = "phantom"
    if rc != 0:
        return (f"LOCATE_FAIL rc={rc}", 0, 0, flag)
    st = wintypes.DWORD(0)
    pr = wintypes.DWORD(0)
    rc = cm.CM_Get_DevNode_Status(ctypes.byref(st), ctypes.byref(pr), dev, 0)
    return (f"rc={rc}", int(st.value), int(pr.value), flag)


def sub(path):
    out = []
    try:
        k = winreg.OpenKey(HK, path, 0, winreg.KEY_READ)
    except OSError:
        return out
    i = 0
    while True:
        try:
            out.append(winreg.EnumKey(k, i))
        except OSError:
            break
        i += 1
    k.Close()
    return out


tests = ["ACPI\\ACPI000E\\2&daba3ff&0", "ACPI\\ACPI0010\\1",
         "PCI\\VEN_1022&DEV_14E8&SUBSYS_7E261462&REV_00\\3&11583659&0&20",
         "ROOT\\MACHINE\\0000", "USB\\VID_373B&PID_1177\\00000026E4202219"]

# 自动找 3 个 PCI / USB / STORAGE 实例
for enum in ("PCI", "USB", "STORAGE", "HID", "DISPLAY"):
    hws = sub(K + "\\" + enum)[:1]
    for hw in hws:
        insts = sub(K + "\\" + enum + "\\" + hw)[:1]
        for inst in insts:
            tests.append(enum + "\\" + hw + "\\" + inst)

for t in tests:
    p("status(%-62s) = %r" % (t[:62], status(t)))

# 统计：全部实例里有多少定位失败
tot = fail = 0
for enum in sub(K):
    for hw in sub(K + "\\" + enum):
        for inst in sub(K + "\\" + enum + "\\" + hw):
            tot += 1
            r = status(enum + "\\" + hw + "\\" + inst)
            if "LOCATE_FAIL" in r[0]:
                fail += 1
p("total instances=%d locate_fail=%d" % (tot, fail))

open("probe_enum2.log", "w", encoding="utf-8").write("\n".join(lines))
print("\n".join(lines))
