# -*- coding: utf-8 -*-
"""变更记录：把三处备份折成一条时间线，并支持一键全部还原。

本工具的改动都是可逆的，但原始记录散在三个地方：

- `startup_actions.jsonl`  启动项每次开关的原始数据（追加式，含历史动作）
- `services.json`          服务**首次**改动的原始启动类型（同名不覆盖）
- `tweaks.json`            微调**首次**改动前的原值（含 exists 标志）

单看任何一份都不知道「这台机器现在被改成了什么样」。这一页把三份折成
一张按时间倒序的表，并且区分**仍然生效**与**已经改回去了**——后者留着是
为了保留操作痕迹，但不计入「待还原」的统计。

一键还原的原则：
1. 只处理**本工具记录过的**目标，不碰用户自己改的东西
2. 启动项走「重新启用」，服务走「写回原始启动类型」，微调走 `tweaks.revert_all()`
3. 已经在原状态的目标直接跳过，不重复写注册表
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass

from . import services as svc_mod
from . import startup, tweaks

# 单测会往真实备份目录写自检记录，展示时过滤掉，别让用户看到 "__selftest__"
SELFTEST_MARK = "SystemToolboxSelfTest"
MAX_RECORDS = 800

MODULE_CN = {"startup": "启动项", "service": "服务", "tweak": "微调"}


@dataclass
class Change:
    ts: float                    # 排序用时间戳，解析失败为 0
    when: str                    # 显示文本
    module: str                  # 启动项 / 服务 / 微调
    action: str                  # 已禁用 / 已启用 / 已优化 / 已还原
    target: str                  # 显示名
    detail: str = ""             # 原始值 → 现值
    active: bool = True          # 当前是否仍处于改动后的状态
    kind: str = ""               # startup / service / tweak
    key: str = ""                # 回滚标识（服务名 / 微调 key / 启动项定位）

    @property
    def state_text(self) -> str:
        if self.active:
            return "生效中"
        return "已还原"


@dataclass
class Summary:
    active_startup: int = 0
    active_service: int = 0
    active_tweak: int = 0
    restored: int = 0
    total: int = 0

    @property
    def active_total(self) -> int:
        return self.active_startup + self.active_service + self.active_tweak

    @property
    def pending(self) -> bool:
        return self.active_total > 0


def _ts(text: str) -> float:
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return time.mktime(time.strptime(text, fmt))
        except (ValueError, OverflowError, TypeError):
            continue
    return 0.0


# ------------------------------------------------------------------ 启动项
def _actions_path() -> str:
    return os.path.join(startup.backup_root(), "startup_actions.jsonl")


def _read_actions() -> list[dict]:
    path = _actions_path()
    if not os.path.isfile(path):
        return []
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            lines = f.readlines()[-MAX_RECORDS:]
    except OSError:
        return []
    out: list[dict] = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if not isinstance(rec, dict):
            continue
        if SELFTEST_MARK in json.dumps(rec, ensure_ascii=False):
            continue
        out.append(rec)
    return out


def _rec_key(rec: dict) -> str:
    """一条动作记录对应哪个启动项。"""
    typ = rec.get("type") or ""
    if typ == "registry":
        return "reg:%s\\%s\\%s" % (rec.get("hive", ""), rec.get("path", ""),
                                   rec.get("name", ""))
    if typ == "folder":
        return "folder:%s" % (rec.get("original", ""),)
    if typ == "task":
        return "task:%s" % (rec.get("task", ""),)
    return ""


def _rec_target(rec: dict) -> str:
    typ = rec.get("type") or ""
    if typ == "registry":
        return rec.get("name") or "(未命名)"
    if typ == "folder":
        p = (rec.get("original") or "").replace("/", "\\")
        return p.rsplit("\\", 1)[-1] or p
    if typ == "task":
        return (rec.get("task") or "").lstrip("\\") or "(未命名任务)"
    return "(未知)"


def _rec_detail(rec: dict) -> str:
    typ = rec.get("type") or ""
    if typ == "registry":
        return "%s\\%s" % (rec.get("hive", ""), rec.get("path", ""))
    if typ == "folder":
        return rec.get("original") or ""
    if typ == "task":
        return "计划任务"
    return ""


def _startup_changes() -> list[Change]:
    """同一项只保留最后一次动作 —— 中间来回切的不值得占一行。"""
    last: dict[str, dict] = {}
    for rec in _read_actions():
        k = _rec_key(rec)
        if k:
            last[k] = rec                # 文件按时间追加，后面的覆盖前面的
    out: list[Change] = []
    for k, rec in last.items():
        disabled = (rec.get("action") == "disable")
        when = str(rec.get("time") or "")
        out.append(Change(
            ts=_ts(when), when=when, module=MODULE_CN["startup"],
            action="已禁用" if disabled else "已启用",
            target=_rec_target(rec), detail=_rec_detail(rec),
            active=disabled,             # 最后一次是「禁用」才算还需要还原
            kind="startup", key=k))
    return out


# ------------------------------------------------------------------ 服务
_SERVICES_KEY = r"SYSTEM\CurrentControlSet\Services"
_HKLM = 0x80000002


def _svc_start(name: str) -> int | None:
    if os.name != "nt":
        return None
    try:
        import winreg
        k = winreg.OpenKey(_HKLM, _SERVICES_KEY + "\\" + name, 0, winreg.KEY_READ)
    except OSError:
        return None
    try:
        v = winreg.QueryValueEx(k, "Start")[0]
        return int(v)
    except (OSError, TypeError, ValueError):
        return None
    finally:
        k.Close()


def _start_text(start: int | None) -> str:
    return svc_mod.START_TYPES.get(start, "未知") if start is not None else "未知"


def _service_changes() -> list[Change]:
    data = svc_mod._load_backup()
    out: list[Change] = []
    for name, rec in data.items():
        if SELFTEST_MARK in name:
            continue
        original = rec.get("start")
        cur = _svc_start(name)
        active = cur is not None and original is not None and cur != original
        when = str(rec.get("saved") or "")
        out.append(Change(
            ts=_ts(when), when=when, module=MODULE_CN["service"],
            action="已改启动类型" if active else "已还原",
            target=str(rec.get("display") or name),
            detail="原始 %s → 当前 %s" % (_start_text(original), _start_text(cur)),
            active=active, kind="service", key=name))
    return out


# ------------------------------------------------------------------ 微调
def _tweak_changes() -> list[Change]:
    data = tweaks.load_backup()
    out: list[Change] = []
    for key, rec in data.items():
        if SELFTEST_MARK in key or key.startswith("__"):
            continue
        t = tweaks.by_key(key)
        if t is None:
            continue
        try:
            active = t.state() == "已优化"
        except Exception:                # noqa: BLE001
            active = True
        when = str(rec.get("saved") or "")
        if rec.get("kind") == "power":
            orig = "原始电源计划 %s" % (rec.get("guid") or "未知")
        elif rec.get("exists"):
            orig = "原始值 %s" % (rec.get("value"),)
        else:
            orig = "原始为「未设置」"
        out.append(Change(
            ts=_ts(when), when=when, module=MODULE_CN["tweak"],
            action="已优化" if active else "已还原",
            target=t.name,
            detail="%s → 当前 %s" % (orig, t.current_text()),
            active=active, kind="tweak", key=key))
    return out


# ------------------------------------------------------------------ 汇总
def collect(include_restored: bool = True) -> tuple[list[Change], Summary]:
    """返回 (按时间倒序的变更列表, 统计)。"""
    rows = _startup_changes() + _service_changes() + _tweak_changes()
    summary = Summary(total=len(rows))
    for c in rows:
        if c.active:
            if c.kind == "startup":
                summary.active_startup += 1
            elif c.kind == "service":
                summary.active_service += 1
            else:
                summary.active_tweak += 1
        else:
            summary.restored += 1
    rows.sort(key=lambda c: (-c.ts, c.module, c.target.lower()))
    if not include_restored:
        rows = [c for c in rows if c.active]
    return (rows, summary)


# ------------------------------------------------------------------ 还原
def _matches(item, rec: dict) -> bool:
    """扫描出来的启动项与 jsonl 记录是否是同一个。"""
    typ = rec.get("type") or ""
    if typ == "registry":
        return (item.hive == rec.get("hive")
                and (item.reg_path or "").lower() == (rec.get("path") or "").lower()
                and item.value_name == rec.get("name"))
    if typ == "folder":
        a = (item.file_path or "").replace("/", "\\").lower()
        b = (rec.get("original") or "").replace("/", "\\").lower()
        return bool(a) and a == b
    if typ == "task":
        return item.task_name == rec.get("task")
    return False


def revert_all(progress=None) -> tuple[int, list[str]]:
    """把三类改动全部退回原始状态。

    返回 (成功项数, 失败说明)。已在原状态的目标会被跳过，不计入成功数。
    """
    ok = 0
    errs: list[str] = []

    # ---- 1. 启动项：只恢复「最后动作是禁用」的那些
    pend = [r for r in _read_actions() if r.get("action") == "disable"]
    keys = {_rec_key(r) for r in pend}
    last: dict[str, dict] = {}
    for r in pend:
        k = _rec_key(r)
        if k in keys:
            last[k] = r
    if last:
        try:
            items = startup.scan_all()
        except Exception as exc:         # noqa: BLE001
            items = []
            errs.append("扫描启动项失败：%s" % (exc,))
        targets = []
        for it in items:
            if it.enabled or not it.can_toggle:
                continue
            if any(_matches(it, rec) for rec in last.values()):
                targets.append(it)
        if targets:
            if progress:
                progress(0, len(targets), "恢复启动项")
            n, es = startup.set_many(targets, True, progress=progress)
            ok += n
            errs.extend(es)

    # ---- 2. 服务：写回原始启动类型
    svc_data = {k: v for k, v in svc_mod._load_backup().items()
                if SELFTEST_MARK not in k}
    if svc_data:
        if progress:
            progress(0, len(svc_data), "恢复服务")
        missing_admin = False
        for i, (name, rec) in enumerate(svc_data.items()):
            original = rec.get("start")
            if original is None:
                continue
            if _svc_start(name) == original:
                continue                 # 已经是原状态
            good, msg = svc_mod.set_start_type(name, int(original))
            if good:
                ok += 1
            else:
                if "管理员" in (msg or ""):
                    missing_admin = True
                errs.append("%s：%s" % (name, msg))
            if progress:
                progress(i + 1, len(svc_data), name)
        if missing_admin and errs:
            errs.append("部分服务需要管理员权限，请以管理员身份重启后重试")

    # ---- 3. 微调：走已有的 revert_all（它按备份里的原值写回）
    if tweaks.load_backup():
        n, es = tweaks.revert_all(progress=progress)
        ok += n
        errs.extend(es)

    return (ok, errs)
