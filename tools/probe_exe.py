# -*- coding: utf-8 -*-
"""列出单文件 exe 内部条目体积 TOP。"""
import os
import sys

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "probe_exe.log")
exe = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "dist", "SystemToolbox.exe")
rows = []
try:
    from PyInstaller.archive.readers import CArchiveReader
    r = CArchiveReader(exe)
    for name, (pos, clen, ulen, flag, typ) in r.toc.items():
        rows.append((clen, ulen, typ, name))
except Exception as exc:  # noqa: BLE001
    with open(OUT, "w", encoding="utf-8") as f:
        f.write("ERR %r\n" % (exc,))
    raise

rows.sort(reverse=True)
lines = ["exe=%s size=%.1f MB entries=%d" % (exe, os.path.getsize(exe) / 1048576, len(rows)), ""]
lines.append("%10s %10s %-6s %s" % ("packed", "unpacked", "type", "name"))
for clen, ulen, typ, name in rows[:45]:
    lines.append("%9.2fM %9.2fM %-6s %s" % (clen / 1048576, ulen / 1048576, typ, name))

# 按顶层目录汇总
agg = {}
for clen, ulen, typ, name in rows:
    key = name.split("/")[0].split("\\")[0]
    agg[key] = agg.get(key, 0) + clen
lines.append("")
lines.append("按顶层汇总：")
for k, v in sorted(agg.items(), key=lambda kv: -kv[1])[:15]:
    lines.append("%9.2fM %s" % (v / 1048576, k))

with open(OUT, "w", encoding="utf-8") as f:
    f.write("\n".join(lines))
print("done")
