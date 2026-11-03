# -*- coding: utf-8 -*-
"""把 health_page.py 按页签切成 mixin（AST 取行区间，代码体逐字节搬走）。

**为什么用 mixin 而不是十个独立 QWidget**：`HealthPage` 里 `_busy` / `status` /
八个 KPI 卡 / 十个 models 被大量方法共享，横向切出独立窗口会把几十个 `self.xxx`
变成跨模块穿针，风险远高于收益。mixin 只是**换个文件放同一批方法**，
状态仍然全在 `self` 上，行为逐字节不变。

用法：python tools/split_health.py   （一次性的搬家工具，跑完即可删）
"""
from __future__ import annotations

import ast
import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "app", "ui", "health_page.py")
PKG = os.path.join(ROOT, "app", "ui", "health_tabs")

# 每个文件：(mixin 类名, 方法清单, 文档串)
TABS = {
    "dev": ("DevTabMixin", ["_build_dev", "_fill_dev", "load_devices"],
            "设备与驱动页签：设备清单 + 驱动清单 + 四档筛选。"),
    "upgrade": ("UpgradeTabMixin", ["_build_up", "load_upgrade"],
                "Win11 升级体检页签：逐项判定 + 阻塞项说明。"),
    "perf": ("PerfTabMixin", ["_build_perf", "_open_proc", "_fill_procs"],
             "性能采样页签：CPU/内存/磁盘/网络曲线 + 进程 TOP。"),
    "security": ("SecurityTabMixin", ["_build_sec", "load_security"],
                 "安全基线页签：Defender / 防火墙 / UAC / SMBv1 / RDP 等只读检查。"),
    "component": ("ComponentTabMixin",
                  ["_build_file", "load_component", "_fill_file",
                   "measure_winsxs", "_fail_size"],
                  "系统文件页签：DISM CheckHealth + CBS.log + 补丁级别 + WinSxS 体积。"),
    "disk": ("DiskTabMixin", ["_build_disk", "load_smart", "_fill_disk"],
             "磁盘 SMART 页签：NVMe 寿命/温度/读写量 + 磁盘 IO 告警。"),
    "hwevent": ("HwTabMixin", ["_build_hw", "load_hw", "_fill_hw"],
                "硬件错误页签：WHEA CPER 归类 + 蓝屏/内存诊断/IO 汇总。"),
    "dumpcfg": ("DumpCfgTabMixin",
                ["_build_dumpcfg", "load_dumpcfg", "_fill_dumpcfg"],
                "崩溃转储体检页签：转储类型 × 页面文件容量核对。"),
    "pathenv": ("PathEnvTabMixin",
                ["_build_pathenv", "load_pathenv", "_fill_pathenv"],
                "环境变量页签：PATH 逐条核对失效 / 重复 / 变量未定义。"),
    "drivers": ("DriversTabMixin",
                ["_build_drvtab", "load_drivers", "_fill_drv", "_fill_drv_files"],
                "驱动体检页签：驱动包清单 + 逐个文件验签 + 已加载判定。"),
}

# 留在 health_page.py 里的方法（窗口骨架、结论条、采样节奏）
SHELL = ["__init__", "_build", "showEvent", "shutdown", "_ensure_sampler",
         "run_all", "_section_done", "_raw_parts", "_compose_report",
         "_show_verdict", "_export_report", "_fail", "_toggle_pause",
         "_reset_chart", "_tick"]

# alias → 导入模板。{core}/{ui}/{common}/{chart} 是相对层级前缀：
# 页签在 app/ui/health_tabs/ 下（...core、..line_chart），骨架在 app/ui/ 下（..core、.line_chart）
MOD_IMPORTS = {
    "dv": "from {core}core import devices as dv",
    "drv": "from {core}core import drivers as drv",
    "dcfg": "from {core}core import dumpcfg as dcfg",
    "pe": "from {core}core import pathenv as pe",
    "hwevent": "from {core}core import hwevent",
    "perf": "from {core}core import perf",
    "procs": "from {core}core import procs",
    "security": "from {core}core import security",
    "smartm": "from {core}core import smart as smartm",
    "up": "from {core}core import upgrade as up",
    "component": "from {core}core import component",
    "health": "from {core}core import health",
    "report": "from {core}core import report",
    "theme": "from {core} import theme",
    "notice": "from {ui} import notice",
}
PREFIX_TABS = {"core": "...", "ui": "..", "common": ".common", "chart": "..line_chart"}
PREFIX_PAGE = {"core": "..", "ui": ".", "common": ".health_tabs.common",
               "chart": ".line_chart"}
STDLIB = {"os": "import os", "sys": "import sys", "time": "import time",
          "re": "import re", "glob": "import glob", "json": "import json",
          "winreg": "import winreg",
          "datetime": "from datetime import datetime"}
QT_CORE = ["QPointF", "QRectF", "Qt", "QTimer", "QUrl", "pyqtSignal"]
QT_GUI = ["QColor", "QDesktopServices", "QPainter", "QPainterPath", "QPen"]
QT_WIDGETS = ["QFileDialog", "QFrame", "QGridLayout", "QHBoxLayout", "QLabel",
              "QPlainTextEdit", "QSplitter", "QStackedWidget", "QTabWidget",
              "QVBoxLayout", "QWidget"]
LOCAL_IMPORTS = {
    "Col": "from {ui}tables import Col",
    "RowModel": "from {ui}tables import RowModel",
    "make_table": "from {ui}tables import make_table",
    "run_bg": "from {ui}tables import run_bg",
    "stop_all": "from {ui}tables import stop_all",
    "EmptyState": "from {ui}widgets import EmptyState",
    "FindingCard": "from {ui}widgets import FindingCard",
    "KpiCard": "from {ui}widgets import KpiCard",
    "ScrollColumn": "from {ui}widgets import ScrollColumn",
    "ghost_button": "from {ui}widgets import ghost_button",
    "mono_font": "from {ui}widgets import mono_font",
    "primary_button": "from {ui}widgets import primary_button",
    "ChipBar": "from {ui}optimize_page import ChipBar",
    "LineChart": "from {chart} import LineChart",
    "RIGHT": "from {common} import RIGHT",
    "LEVEL_COLOR": "from {common} import LEVEL_COLOR",
    "DISM_CN": "from {common} import DISM_CN",
    "MAX_POINTS": "from {common} import MAX_POINTS",
    "_level_color": "from {common} import _level_color",
    "_cpu_color": "from {common} import _cpu_color",
    "_open": "from {common} import _open",
}


def _wrap(names, width=58, indent=29):
    """按宽度折行，续行缩进由调用方给 —— 缩进硬编码会让拼进 `(%s)` 后错位。"""
    out, line = [], ""
    for n in names:
        if len(line) + len(n) + 2 > width:
            out.append(line.rstrip(", "))
            line = ""
        line += n + ", "
    out.append(line.rstrip(", "))
    return (",\n" + " " * indent).join(out)


def _used_names(text: str) -> set:
    """按 AST 取「真正被用到的名字」。

    不能用 `"os." in text` 这种子串判断：文案里写一句「按 os.stat 算」
    就会多一条没用的 import，pyflakes 立刻报出来。AST 只认代码里的名字。
    """
    tree = ast.parse("class _X:\n" + text)
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            out.add(node.id)
        elif isinstance(node, ast.Attribute):
            root = node
            while isinstance(root, ast.Attribute):
                root = root.value
            if isinstance(root, ast.Name):
                out.add(root.id)
    return out


def _header(body: str, pre: dict, extra=()) -> str:
    """按实际用到的名字拼导入头。"""
    used = _used_names(body) | set(extra)
    imp_std, seen = [], set()
    for alias, stmt in STDLIB.items():
        if alias in used and alias not in seen:
            seen.add(alias)
            imp_std.append(stmt)
    imp_mod = [tpl.format(**pre) for alias, tpl in MOD_IMPORTS.items()
               if alias in used]
    imp_core = [n for n in QT_CORE if n in used]
    imp_gui = [n for n in QT_GUI if n in used]
    imp_wid = [n for n in QT_WIDGETS if n in used]
    imp_local = [(name, tpl.format(**pre)) for name, tpl in LOCAL_IMPORTS.items()
                 if name in used]

    out = list(imp_std)
    if imp_std and (imp_core or imp_gui or imp_wid):
        out.append("")
    if imp_core:
        out.append("from PyQt5.QtCore import %s" % ", ".join(imp_core))
    if imp_gui:
        out.append("from PyQt5.QtGui import (%s)" % _wrap(imp_gui))
    if imp_wid:
        out.append("from PyQt5.QtWidgets import (%s)" % _wrap(imp_wid))
    if imp_mod or imp_local:
        out.append("")
        out.extend(imp_mod)
        merged = {}
        for name, stmt in imp_local:
            mod, _n = stmt.rsplit(" import ", 1)
            merged.setdefault(mod, []).append(name)
        for mod, names in merged.items():
            out.append("%s import %s" % (mod, ", ".join(names)))
    return "\n".join(out)


def main() -> int:
    src = io.open(SRC, encoding="utf-8").read()
    lines = src.splitlines(keepends=True)
    tree = ast.parse(src)

    def cut(a, b):
        return "".join(lines[a - 1:b])

    cls = next(n for n in tree.body
               if isinstance(n, ast.ClassDef) and n.name == "HealthPage")
    seg = {n.name: (n.lineno, n.end_lineno) for n in cls.body
           if isinstance(n, ast.FunctionDef)}
    chart = next(n for n in tree.body
                 if isinstance(n, ast.ClassDef) and n.name == "LineChart")

    assigned = {m for _c, ms, _d in TABS.values() for m in ms} | set(SHELL)
    missing = set(seg) - assigned
    if missing:
        print("!! 有方法没分配：", sorted(missing))
        return 2
    lost = assigned - set(seg)
    if lost:
        print("!! 分配了但不存在：", sorted(lost))
        return 2

    os.makedirs(PKG, exist_ok=True)

    # ---- 1. 公共常量与工具
    common_body = []
    for n in tree.body:
        if isinstance(n, ast.Assign):
            tgt = getattr(n.targets[0], "id", "")
            if tgt in ("RIGHT", "LEVEL_COLOR", "DISM_CN", "MAX_POINTS"):
                common_body.append(cut(n.lineno, n.end_lineno))
        elif isinstance(n, ast.FunctionDef) and n.name in (
                "_level_color", "_cpu_color", "_open"):
            common_body.append(cut(n.lineno, n.end_lineno))
    assert len(common_body) == 7, len(common_body)
    io.open(os.path.join(PKG, "common.py"), "w", encoding="utf-8",
            newline="").write(
        '# -*- coding: utf-8 -*-\n'
        '"""体检页的公共常量与零状态工具函数。\n\n'
        '抽出来是因为十个小页签都要用（表格着色、对齐、打开目录），\n'
        '放在任何一个 mixin 里都会让另外九个反向依赖它。\n"""\n'
        "from __future__ import annotations\n\n"
        "import os\n\n"
        "from PyQt5.QtCore import QUrl, Qt\n"
        "from PyQt5.QtGui import QDesktopServices\n\n\n"
        + "\n\n".join(x.rstrip("\n") + "\n" for x in common_body))

    # ---- 2. 折线图控件
    io.open(os.path.join(ROOT, "app", "ui", "line_chart.py"), "w",
            encoding="utf-8", newline="").write(
        '# -*- coding: utf-8 -*-\n'
        '"""极简折线图控件（体检页的实时曲线用）。\n\n'
        '没上 matplotlib / Chart.js：这一页只要「最近三分钟的趋势」，\n'
        '引入依赖换不来什么，反而拖慢启动、还要处理打包。\n"""\n'
        "from __future__ import annotations\n\n"
        "from PyQt5.QtCore import QPointF, QRectF, Qt\n"
        "from PyQt5.QtGui import QColor, QPainter, QPainterPath, QPen\n"
        "from PyQt5.QtWidgets import QWidget\n\n"
        "from .. import theme\n"
        "from .health_tabs.common import MAX_POINTS\n\n\n"
        + cut(chart.lineno, chart.end_lineno).rstrip("\n") + "\n")

    # ---- 3. 十个 mixin（代码体逐字节照搬）
    for mod, (klass, methods, doc) in TABS.items():
        body = "\n".join(cut(*seg[m]).rstrip("\n") + "\n" for m in methods)
        io.open(os.path.join(PKG, mod + ".py"), "w", encoding="utf-8",
                newline="").write(
            '# -*- coding: utf-8 -*-\n'
            '"""%s\n\n'
            '代码体与拆分前逐字节一致（`tools/split_health.py` 按 AST 行区间搬的）；\n'
            '状态全在 `self` 上 —— 这里只换文件，不改结构。\n"""\n'
            "from __future__ import annotations\n\n%s\n\n\nclass %s:\n%s"
            % (doc, _header(body, PREFIX_TABS), klass, body))

    io.open(os.path.join(PKG, "__init__.py"), "w", encoding="utf-8",
            newline="").write(
        '# -*- coding: utf-8 -*-\n'
        '"""体检工作台的十个页签，一个页签一个 mixin。\n\n'
        '`HealthPage` 继承它们，方法仍是同一个 `self` 上的方法 ——\n'
        '拆分只为「一个文件别一千七百行」，不是重新设计对象关系。\n"""\n'
        + "\n".join("from .%s import %s  # noqa: F401"
                    % (m, TABS[m][0]) for m in TABS)
        + "\n\n__all__ = [\n"
        + "".join('    "%s",\n' % TABS[m][0] for m in TABS)
        + "]\n")

    # ---- 4. 新的 health_page.py：只剩窗口骨架
    shell_body = "\n".join(cut(*seg[m]).rstrip("\n") + "\n" for m in SHELL)
    head = _header(shell_body, PREFIX_PAGE,
                   extra=["pyqtSignal", "QWidget"] + [TABS[m][0] for m in TABS])
    bases = [TABS[m][0] for m in TABS] + ["QWidget"]
    # 续行缩进 = "from .health_tabs import (" 的长度，正好对齐左括号后
    imp_mixins = _wrap(bases[:-1], 54, len("from .health_tabs import ("))
    # 类头续行缩进 = "class HealthPage(" 的长度 - 1
    cls_mixins = _wrap(bases, 60, len("class HealthPage(") - 1)
    new_src = (
        '# -*- coding: utf-8 -*-\n'
        '"""系统体检工作台（第七）：十个页签，全部**只读**。\n\n'
        '不装驱动、不改安全策略、不动系统设置、不修组件、不碰硬盘、不改 PATH。\n\n'
        '结构：窗口骨架留在这里（KPI、页签容器、采样节奏、整机结论、报告导出），\n'
        '十个页签的构建与填充在 `health_tabs/` 下按页签一个 mixin —— 一个文件\n'
        '一千七百行没人看得下去，但**状态仍在同一个 `self` 上**，不做跨模块穿针。\n\n'
        '九项静态检查跑完后用 `core.health.fold()` 折出整机结论（不重新扫描），\n'
        '命令行 `--scan` 用的是同一套判断。\n'
        '"""\n'
        "from __future__ import annotations\n\n"
        "%s\n"
        "from .health_tabs import (%s)\n\n\n"
        # mixin 只是「换个文件放同一批方法」，真正的 QWidget 底必须排在最后：
        # MRO 走到 QWidget 才会接住 __init__ 里的 super().__init__(parent)
        "class HealthPage(%s):\n"
        "    status = pyqtSignal(str)\n\n"
        "%s" % (head, imp_mixins, cls_mixins, shell_body))
    io.open(SRC, "w", encoding="utf-8", newline="").write(new_src)

    print("mixin:", ", ".join(TABS))
    print("health_page.py 行数:", new_src.count("\n"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
