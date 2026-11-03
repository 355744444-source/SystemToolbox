# -*- coding: utf-8 -*-
"""临时：看设备注册表结构与 CM 状态取数。"""
import winreg

HK = 0x80000002
K = r"SYSTEM\CurrentControlSet\Enum"
C = r"SYSTEM\CurrentControlSet\Control\Class"


def dump(path, name=""):
    try:
        k = winreg.OpenKey(HK, path, 0, winreg.KEY_READ)
    except OSError as e:
        print("  OPEN FAIL", name, e)
        return None
    try:
        return winreg.QueryValueEx(k, name)[0]
    except OSError as e:
        print("  VALUE MISS", name, e)
        return None
    finally:
        k.Close()


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


lines = []
p = lines.append
p("ENUM: " + repr(sub(K)[:24]))
p("CLASS sample: " + repr(sub(C)[:4]))
g = sub(C)[0]
p("  class default(%s) = %r" % (g, dump(C + "\\" + g)))

ents = sub(K + r"\ACPI")
p("ACPI hwid sample: " + repr(ents[:3]))
h = ents[0]
insts = sub(K + r"\ACPI" + "\\" + h)
p("  insts: " + repr(insts[:4]))
base = K + r"\ACPI" + "\\" + h + "\\" + insts[0]
for n in ("DeviceDesc", "FriendlyName", "Mfg", "Service", "Driver",
          "ClassGUID", "LocationInformation"):
    p("  %-20s = %r" % (n, dump(base, n)))
p("  instance subkeys: " + repr(sub(base)[:8]))

# machine.inf 到底在哪个枚举器下
for enum in sub(K):
    for hw in sub(K + "\\" + enum):
        if "machine" in hw.lower():
            p("machine found: enum=%s hw=%s insts=%r"
              % (enum, hw, sub(K + "\\" + enum + "\\" + hw)[:3]))
        for inst in sub(K + "\\" + enum + "\\" + hw)[:6]:
            d = dump(K + "\\" + enum + "\\" + hw + "\\" + inst, "DeviceDesc")
            if d and ("machine.inf" in str(d).lower() or "@" in str(d)[:1]):
                p("oddbesc: %s\\%s\\%s -> %r" % (enum, hw, inst, d))

open("probe_enum.log", "w", encoding="utf-8").write("\n".join(lines))
print("\n".join(lines))
