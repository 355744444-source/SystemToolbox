# -*- coding: utf-8 -*-
"""临时：每个枚举器下有多少设备实例。"""
import winreg

HK = 0x80000002
K = r"SYSTEM\CurrentControlSet\Enum"


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


def count(enum):
    n = 0
    for hw in sub(K + "\\" + enum):
        n += len(sub(K + "\\" + enum + "\\" + hw))
    return n


lines = []
for enum in sub(K):
    lines.append("%-42s %4d" % (enum, count(enum)))
lines.append("TOTAL %d" % sum(int(x.rsplit(None, 1)[-1]) for x in lines))
open("probe_enum3.log", "w", encoding="utf-8").write("\n".join(lines))
print("\n".join(lines))
