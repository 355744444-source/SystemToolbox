# -*- coding: utf-8 -*-
"""拆页体检：确认十个 mixin 拼起来之后方法齐全、没有互相盖掉。"""
import ast
import collections
import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app.ui.health_tabs import (  # noqa: E402
    ComponentTabMixin, DevTabMixin, DiskTabMixin, DriversTabMixin,
    DumpCfgTabMixin, HwTabMixin, PathEnvTabMixin, PerfTabMixin,
    SecurityTabMixin, UpgradeTabMixin,
)

MIXINS = [DevTabMixin, UpgradeTabMixin, PerfTabMixin, SecurityTabMixin,
          ComponentTabMixin, DiskTabMixin, HwTabMixin, DumpCfgTabMixin,
          PathEnvTabMixin, DriversTabMixin]

owner = collections.defaultdict(list)
total = 0
lines = []
for m in MIXINS:
    src = io.open(os.path.join(ROOT, *m.__module__.split(".")) + ".py",
                  encoding="utf-8").read()
    funcs = [n.name for n in ast.parse(src).body
             if isinstance(n, ast.ClassDef) for n in n.body
             if isinstance(n, ast.FunctionDef)]
    total += len(funcs)
    lines.append("%-28s %2d 个方法  %s" % (m.__name__, len(funcs), ", ".join(funcs)))
    for f in funcs:
        owner[f].append(m.__name__)

shell = io.open(os.path.join(ROOT, "app", "ui", "health_page.py"),
                encoding="utf-8").read()
shell_funcs = [n.name for n in ast.parse(shell).body
               if isinstance(n, ast.ClassDef) and n.name == "HealthPage"
               for n in n.body if isinstance(n, ast.FunctionDef)]
lines.append("%-28s %2d 个方法  %s" % ("HealthPage(骨架)", len(shell_funcs),
                                      ", ".join(shell_funcs)))
for f in shell_funcs:
    owner[f].append("HealthPage")

dup = {k: v for k, v in owner.items() if len(v) > 1}
lines.append("")
lines.append("方法总数 = %d（mixin %d + 骨架 %d）"
             % (total + len(shell_funcs), total, len(shell_funcs)))
lines.append("重名（会被 MRO 前面那个盖掉）: %s" % (dup if dup else "无"))
lines.append("RESULT: %s" % ("OK" if not dup else "DUP"))

out = "\n".join(lines)
io.open(os.path.join(ROOT, "probe_split.txt"), "w", encoding="utf-8",
        newline="").write(out + "\n")
print(out)
