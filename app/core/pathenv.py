# -*- coding: utf-8 -*-
"""环境变量 / PATH 体检：为什么某个命令「明明装了却用不了」。

## 实测背景（Win11 26H2，2026-10-26）

本机 Machine PATH 19 项里 **7 项指向已删除的目录**、**1 项重复**
（`...\\cursor\\resources\\app\\bin` 既重复又不存在）。用户侧的典型症状是：
装过又卸载的软件把目录留在 PATH 里，命令解析时每次都要 stat 一遍不存在的路径，
既拖慢 `cmd` / `CreateProcess`，也让「command not found」变得难以判断 ——
到底是没装，还是装了但 PATH 里的那条是坏的。

另一类更隐蔽：条目里写着 `%SOME_VAR%` 而该变量已不存在。Windows 不会报错，
直接把字面量当路径，于是永远命中不了。

## 判定口径

- **失效**：`os.path.isdir(展开后)` 为假。文件型条目（很少见）用 `isfile`。
- **重复**：展开后去掉尾部反斜杠、转小写后相同。**后出现的才是重复项** ——
  有效 PATH 是「系统在前、用户在后」，靠后的同名项永远轮不到。
- **未展开**：`%NAME%` 里的 NAME 在当前进程环境里不存在。
- **带引号**：`"C:\\Program Files\\x"` 这种写法 Windows 会连引号一起当路径，
  是经典的失效原因。
- **相对路径**：不以盘符或 UNC 开头，会相对当前工作目录解析，几乎必然失效。

只读：本模块不写注册表、不改 PATH，建议只给「去哪儿改」。
"""
from __future__ import annotations

import os
import re
import winreg
from dataclasses import dataclass, field

from .analysis import Finding

__all__ = ["PathEntry", "Report", "audit", "parse", "scopes", "SEP"]

SEP = ";"
_VAR_RE = re.compile(r"%([^%]+)%")
_SEP_RE = re.compile(r"[\\/]+")        # 连续的目录分隔符，用于 _norm 归一

#: 这些变量缺了会影响大量程序，单独查
KEY_VARS = ("SystemRoot", "windir", "ComSpec", "PATHEXT", "TEMP", "TMP",
            "USERPROFILE", "SystemDrive")

SCOPE_CN = {"machine": "系统", "user": "用户"}


@dataclass
class PathEntry:
    raw: str
    scope: str = "machine"          # machine / user
    expanded: str = ""
    exists: bool = False
    duplicate: bool = False
    dup_scope: str = ""             # 与哪一级重复（决定「默认情况」还是「真冗余」）
    unresolved: list[str] = field(default_factory=list)
    quoted: bool = False
    relative: bool = False

    @property
    def scope_text(self) -> str:
        return SCOPE_CN.get(self.scope, self.scope)

    @property
    def kind(self) -> str:
        """表格里只报最主要的一个问题，避免一行堆四五个红字。

        「重复」排在失效之后：同一个目录既重复又不存在时，用户该看的是
        「目录不存在」——但它在重复统计里仍然算一项（见 Report.dups）。
        """
        if not self.raw.strip():
            return "空条目"
        if self.unresolved:
            return "变量未定义"
        if self.quoted:
            return "带引号"
        if self.relative:
            return "相对路径"
        if not self.exists:
            return "目录不存在"
        if self.duplicate:
            return "重复"
        return "正常"

    @property
    def level(self) -> str:
        return {"正常": "ok", "重复": "info", "空条目": "info"}.get(self.kind, "warn")

    @property
    def note(self) -> str:
        """补充说明，进表格最后一列。"""
        if self.duplicate and self.dup_scope and self.dup_scope != self.scope:
            return "与%s级重复（多为系统默认，可忽略）" % SCOPE_CN.get(self.dup_scope,
                                                                      self.dup_scope)
        if self.duplicate:
            return "同级重复，靠后的不生效"
        return ""

    @property
    def show(self) -> str:
        """表格里显示哪一份：展开成功就显示展开后的，否则显示原文。"""
        if self.unresolved or self.quoted or self.relative:
            return self.raw
        return self.expanded or self.raw


@dataclass
class Report:
    machine_raw: str = ""
    user_raw: str = ""
    machine_kind: int = 0           # 注册表值类型：1=REG_SZ 2=REG_EXPAND_SZ
    user_kind: int = 0
    entries: list[PathEntry] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    env: dict = field(default_factory=dict)
    elapsed: float = 0.0

    # ---- 统计
    @property
    def total(self) -> int:
        return len(self.entries)

    def _count(self, kind: str) -> int:
        return sum(1 for e in self.entries if e.kind == kind)

    @property
    def missing(self) -> int:
        return self._count("目录不存在")

    @property
    def dups(self) -> int:
        return sum(1 for e in self.entries if e.duplicate)

    @property
    def dups_same_scope(self) -> int:
        return sum(1 for e in self.entries if e.duplicate and e.dup_scope == e.scope)

    @property
    def broken_other(self) -> int:
        return sum(1 for e in self.entries if e.kind not in
                   ("正常", "重复", "目录不存在", "空条目"))

    @property
    def machine_count(self) -> int:
        return sum(1 for e in self.entries if e.scope == "machine")

    @property
    def user_count(self) -> int:
        return sum(1 for e in self.entries if e.scope == "user")

    @property
    def level(self) -> str:
        if any(f.severity == "crit" for f in self.findings):
            return "crit"
        if self.missing or self.broken_other:
            return "warn"
        if self.dups:
            return "info"
        return "ok"

    @property
    def headline(self) -> str:
        if not self.entries:
            return "读不到 PATH"
        bad = self.missing + self.broken_other
        if not bad and not self.dups:
            return "%d 项全部有效，没有重复" % self.total
        bits = []
        if bad:
            bits.append("%d 项无效" % bad)
        if self.dups:
            bits.append("%d 项重复" % self.dups)
        return "%s（共 %d 项）" % ("、".join(bits), self.total)


# ---------------------------------------------------------------- 读取
def _read(scope: str) -> tuple[str, int]:
    if scope == "machine":
        path = r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"
        root = winreg.HKEY_LOCAL_MACHINE
    else:
        path = "Environment"
        root = winreg.HKEY_CURRENT_USER
    try:
        with winreg.OpenKey(root, path) as k:
            val, kind = winreg.QueryValueEx(k, "Path")
            return str(val or ""), int(kind)
    except FileNotFoundError:
        return "", 0
    except OSError:
        return "", -1


def _expand(raw: str, env: dict) -> tuple[str, list[str]]:
    """展开 %VAR%，并把找不到的变量名带回来。"""
    missing: list[str] = []

    def sub(m):
        name = m.group(1)
        if name in env:
            return env[name]
        missing.append(name)
        return m.group(0)

    return _VAR_RE.sub(sub, raw), missing


def _is_abs(p: str) -> bool:
    if p.startswith("\\\\") or p.startswith("//"):
        return True
    return len(p) >= 3 and p[1] == ":" and p[2] in "\\/"


def _norm(p: str) -> str:
    """比较用的规范形：合并多余分隔符、统一成反斜杠、去尾斜杠、转小写。

    Windows 两种分隔符都认，开发工具写进 PATH 的 `C:/tool/bin`、
    `%SystemRoot%` 展开后偶然出现的双分隔符都很常见。
    不统一的话同一个目录会被算成两个（重复检测漏报），
    System32 也可能因为写法不同被误判成「不在 PATH 里」。
    """
    # 用 lambda 而不是直接给替换串：re 的替换模板里单个反斜杠是转义字符，
    # 传 "\" 会抛 "bad escape (end of pattern)"
    return _SEP_RE.sub(lambda m: "\\", p).rstrip("\\").lower()


def scopes() -> dict:
    """当前进程里与路径解析相关的关键变量。"""
    out = {}
    for name in KEY_VARS:
        out[name] = os.environ.get(name, "")
    return out


def parse(machine_raw: str, user_raw: str, env: dict | None = None,
          check_exists: bool = True) -> list[PathEntry]:
    """把两级 PATH 原文解析成条目。抽出来是为了能拿构造数据跑单测。"""
    env = dict(os.environ if env is None else env)
    out: list[PathEntry] = []
    seen: dict[str, PathEntry] = {}
    for scope, raw in (("machine", machine_raw or ""), ("user", user_raw or "")):
        for piece in raw.split(SEP):
            e = PathEntry(raw=piece, scope=scope)
            if not piece.strip():
                out.append(e)
                continue
            stripped = piece.strip()
            e.quoted = stripped.startswith('"') and stripped.endswith('"')
            body = stripped.strip('"') if e.quoted else stripped
            e.expanded, e.unresolved = _expand(body, env)
            e.relative = not _is_abs(e.expanded) and not e.unresolved
            if check_exists:
                try:
                    e.exists = (os.path.isdir(e.expanded)
                                or os.path.isfile(e.expanded))
                except OSError:
                    e.exists = False
            else:
                e.exists = True
            key = _norm(e.expanded)
            if key and key in seen:
                e.duplicate = True
                e.dup_scope = seen[key].scope
            elif key:
                seen[key] = e
            out.append(e)
    return out


def audit() -> Report:
    import time
    t0 = time.perf_counter()
    rep = Report(env=scopes())
    rep.machine_raw, rep.machine_kind = _read("machine")
    rep.user_raw, rep.user_kind = _read("user")
    rep.entries = parse(rep.machine_raw, rep.user_raw)
    rep.findings = _findings(rep)
    rep.elapsed = time.perf_counter() - t0
    return rep


def _f(severity: str, topic: str, title: str, detail: str = "",
       advice=None, evidence=None, count: int = 0) -> Finding:
    return Finding(severity=severity, topic=topic, title=title, detail=detail,
                   advice=list(advice or []), count=count,
                   evidence=list(evidence or []))


def _findings(rep: Report) -> list[Finding]:
    out: list[Finding] = []
    entries = rep.entries

    # ---- 1. 失效目录
    bad = [e for e in entries if e.kind == "目录不存在"]
    if bad:
        scopes_hit = sorted({e.scope for e in bad})
        out.append(_f(
            "warn", "path-missing",
            "%d 项 PATH 指向已不存在的目录" % len(bad),
            "这些条目几乎都来自「装过又卸载」的软件。它们不会报错，只是每次解析命令时"
            "多 stat 一次空路径；副作用是排查「命令找不到」时容易误判 —— "
            "看着 PATH 里有，其实那条是坏的。%s"
            % ("其中系统级 %d 项会影响所有用户。" % sum(1 for e in bad if e.scope == "machine")
               if bad and "machine" in scopes_hit else ""),
            ["系统属性 → 高级 → 环境变量",
             "在「系统变量」和「用户变量」里分别选中 Path → 编辑",
             "删掉下面这些已不存在的条目（建议先导出一份文本备份）",
             "改完要重开命令行窗口，老窗口读的还是旧 PATH"],
            ["%s：%s" % (e.scope_text, e.show) for e in bad[:6]]
            + (["…还有 %d 项" % (len(bad) - 6)] if len(bad) > 6 else []),
            count=len(bad)))

    # ---- 2. 重复项（同级重复才算冗余；跨级重复多半是 Windows 默认）
    dup_same = [e for e in entries if e.duplicate and e.dup_scope == e.scope]
    if dup_same:
        out.append(_f(
            "warn", "path-dup",
            "%d 项同级重复（靠后的永远轮不到）" % len(dup_same),
            "有效 PATH 是「系统在前、用户在后」，同一个目录写两遍不会生效，"
            "只会让 PATH 变长、解析变慢。同级重复基本都是后来追加时没先看一眼。",
            ["在环境变量里保留靠前的那一条，删掉重复的"],
            ["%s：%s" % (e.scope_text, e.show) for e in dup_same[:6]],
            count=len(dup_same)))

    dup_cross = [e for e in entries if e.duplicate and e.dup_scope != e.scope]
    if dup_cross:
        out.append(_f(
            "info", "path-dup-cross",
            "%d 项在系统与用户里各写了一份" % len(dup_cross),
            "系统 PATH 和用户 PATH 里有同一个目录。这不一定是别人改坏的 —— "
            "Windows 自己就常把 WindowsApps 两边各放一份，超集是同一个目录，"
            "不会造成错误，只是白占长度。除非你正在清理 PATH，否则可以忽略。",
            ["如果顺手整理，删掉用户级那一份即可"],
            ["%s：%s" % (e.scope_text, e.show) for e in dup_cross[:6]],
            count=len(dup_cross)))

    # ---- 3. 变量未定义
    undef = [e for e in entries if e.unresolved]
    if undef:
        names = sorted({n for e in undef for n in e.unresolved})
        out.append(_f(
            "warn", "path-undef",
            "%d 项里的变量 %s 已不存在" % (len(undef), "、".join(names[:3])),
            "PATH 里写着 %%%s%%，但这个环境变量已经不在了。Windows 不会报错，"
            "会把 %%…%% 当字面量拼进路径，于是这个目录永远命中不了。"
            % names[0],
            ["把这条改成绝对路径，或先把这个环境变量补回来"],
            ["%s：%s" % (e.scope_text, e.raw) for e in undef[:5]],
            count=len(undef)))

    # ---- 4. 带引号 / 相对路径（经典坑）
    quoted = [e for e in entries if e.quoted]
    if quoted:
        out.append(_f(
            "warn", "path-quoted", "%d 项被引号包住，实际会失效" % len(quoted),
            "PATH 条目里的双引号会被当成路径的一部分（PATH 本身就是分号分隔，"
            "不需要给空格加引号）。这是「路径看起来完全正确却用不了」的常见原因。",
            ["去掉条目两端的双引号，直接写 C:\\Program Files\\… 即可"],
            ["%s：%s" % (e.scope_text, e.raw) for e in quoted[:5]],
            count=len(quoted)))
    rel = [e for e in entries if e.kind == "相对路径"]
    if rel:
        out.append(_f(
            "warn", "path-relative", "%d 项是相对路径" % len(rel),
            "相对路径会按「当前工作目录」解析，等于换个目录就失效。"
            "PATH 里应当只放绝对路径。",
            ["改成绝对路径，或删掉"],
            ["%s：%s" % (e.scope_text, e.raw) for e in rel[:5]],
            count=len(rel)))

    # ---- 5. 关键变量
    env = rep.env
    core_missing = [n for n in ("SystemRoot", "ComSpec") if not env.get(n)]
    if core_missing:
        out.append(_f(
            "crit", "env-core", "缺少 %s 这类核心变量" % "、".join(core_missing),
            "这些是系统级变量，缺了会让大量程序（含安装包、脚本）行为异常。"
            "正常情况下不可能为空，除非被手工删过或注册表被改坏。",
            ["系统属性 → 高级 → 环境变量，确认系统变量里存在这些项",
             "可以在一台正常的同版本机器上对照取值"],
            ["%s = %r" % (n, env.get(n)) for n in KEY_VARS if n in core_missing]))

    temp_problems = []
    for name in ("TEMP", "TMP"):
        val = env.get(name, "")
        if not val:
            temp_problems.append("%s 为空" % name)
        elif not os.path.isdir(val):
            temp_problems.append("%s 指向不存在的目录" % name)
        elif not os.access(val, os.W_OK):
            temp_problems.append("%s 不可写" % name)
    if temp_problems:
        out.append(_f(
            "crit", "env-temp", "临时目录有问题：%s" % "；".join(temp_problems[:2]),
            "大量安装包、解压工具、编译器都要写临时目录。TEMP 不可用时典型症状是"
            "「安装到一半报错」「解压失败」，而错误信息往往完全不提 TEMP。",
            ["把 TEMP 和 TMP 都指向一个存在且可写的目录，例如 %LOCALAPPDATA%\\Temp",
             "确认该目录没有被安全软件锁死"],
            ["TEMP = %r" % env.get("TEMP", ""), "TMP = %r" % env.get("TMP", "")]))

    pathext = env.get("PATHEXT", "")
    if pathext and ".EXE" not in pathext.upper():
        out.append(_f(
            "warn", "env-pathext", "PATHEXT 里没有 .EXE",
            "PATHEXT 决定「敲一个不带扩展名的命令」时按什么顺序找文件。"
            "缺了 .EXE，绝大部分命令都会提示找不到。",
            ["把 PATHEXT 恢复为系统默认值（.COM;.EXE;.BAT;.CMD;…）"],
            ["PATHEXT = %s" % pathext]))

    # ---- 6. 长度（只做提示，不吓人）
    merged = env.get("PATH", "") or (rep.machine_raw + SEP + rep.user_raw)
    if len(merged) > 8000:
        out.append(_f(
            "info", "path-long", "合并后 PATH 有 %s 字符" % f"{len(merged):,}",
            "环境变量块上限是 32767 字符，当前还远没到。只是 PATH 越长，"
            "每次启动进程解析命令都要多查几个目录，属于「慢」而不是「错」。",
            ["如果确实不需要，顺手删掉不用的开发工具目录"],
            ["系统 %d 字符 + 用户 %d 字符" % (len(rep.machine_raw),
                                              len(rep.user_raw))]))

    # ---- 7. System32 缺席（很少见，但会让命令行全废）
    dirs = {_norm(e.expanded) for e in entries if e.exists}
    sysroot = env.get("SystemRoot") or r"C:\Windows"
    sys32 = _norm(os.path.join(sysroot, "System32"))
    if rep.entries and sys32 not in dirs:
        out.append(_f(
            "crit", "path-sys32", "PATH 里找不到 System32",
            "System32 不在 PATH 里时，cmd 里的 ipconfig / ping / sc 等基础命令都会找不到。"
            "通常是有人为了「安全」把它删掉了。",
            ["把 %SystemRoot%\\System32 加回系统 PATH 最前面"],
            ["期望存在：%s" % os.path.join(sysroot, "System32")]))

    if not out:
        out.append(_f(
            "info", "path-ok", "PATH 与环境变量没有发现问题",
            "%d 项全部指向存在的目录，没有重复、没有未定义变量。" % len(entries),
            ["这一页不用动"]))
    return out


def verdict(findings: list[Finding]) -> tuple[str, str]:
    """给页面顶部的一句话结论。"""
    order = {"crit": 0, "warn": 1, "info": 2, "ok": 3}
    worst = "ok"
    for f in findings:
        if order.get(f.severity, 9) < order.get(worst, 9):
            worst = f.severity
    text = {"crit": "环境变量有明显问题，先修这一页",
            "warn": "PATH 有可清理的失效项",
            "info": "配置正常，只有可选的整理建议",
            "ok": "配置正常"}.get(worst, "检查完成")
    return worst, text
