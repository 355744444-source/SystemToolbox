# -*- coding: utf-8 -*-
"""轻量偏好存储（主题、窗口位置、上次停在哪一页）。

## 为什么不用 QSettings

QSettings 在注册表里留一堆键，卸载后还在；这个工具是便携型 exe，
用户期望「拷走就干净」。所以只写一个 JSON 到
`%LOCALAPPDATA%\\SystemToolbox\\ui.json`，跟着工具箱其它数据放一起
（`startup_backup/`、`drvsign.json` 也在那儿）。

## 为什么要容错

偏好文件是最不该影响主流程的东西：读失败（被别的版本写坏、权限异常）
就当没设置过，写失败就当没记住 —— 绝不抛异常。
"""
from __future__ import annotations

import json
import os

__all__ = ["path", "load", "save", "get", "put", "geometry", "set_geometry",
           "DEFAULTS"]

#: 认识的键与默认值。多出来的键会被忽略（旧版本写的、手改错的都不影响）。
DEFAULTS = {
    # v2.0.0 起只有深色，不再存 "theme"。老配置文件里残留的 theme 键会被
    # 这里「不认识的键直接忽略」的规则丢掉，不用写迁移代码。
    "win": "",                # "x,y,w,h"，空 = 用默认尺寸
}

FILE_NAME = "ui.json"


def path() -> str:
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return os.path.join(base, "SystemToolbox", FILE_NAME)


def load(file_path: str | None = None) -> dict:
    """读偏好。任何异常都退化成空表（调用方自己用 DEFAULTS 兜）。"""
    p = file_path or path()
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {k: v for k, v in data.items() if k in DEFAULTS}


def save(data: dict, file_path: str | None = None) -> bool:
    """写偏好。返回是否写成功 —— 调用方通常不关心，失败也不该打断退出。"""
    p = file_path or path()
    clean = {k: data[k] for k in DEFAULTS if k in data}
    try:
        os.makedirs(os.path.dirname(p), exist_ok=True)
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(clean, f, ensure_ascii=False, indent=1)
        os.replace(tmp, p)      # 原子替换：写一半断电不会留下坏文件
        return True
    except OSError:
        return False


def get(key: str, default=None, file_path: str | None = None) -> object:
    d = DEFAULTS.get(key, default)
    v = load(file_path).get(key, d)
    if isinstance(d, int) and not isinstance(v, bool):
        try:
            return int(v)
        except (TypeError, ValueError):
            return d
    if isinstance(d, str):
        return str(v) if v is not None else d
    return v


def put(key: str, value) -> None:
    """改一个键。读-改-写，几个键的调用点很少，不值得做内存缓存。"""
    data = load()
    data[key] = value
    save(data)


def _parse_win(raw: str) -> tuple:
    """把 "x,y,w,h" 解析成四元组。格式不对或尺寸离谱都返回空元组。"""
    parts = str(raw or "").split(",")
    if len(parts) != 4:
        return ()
    try:
        x, y, w, h = (int(p) for p in parts)
    except ValueError:
        return ()
    if w < 800 or h < 600:      # 明显不合理（旧版本写坏 / 换了分辨率），当没设置
        return ()
    return (x, y, w, h)


def geometry(file_path: str | None = None) -> tuple:
    """上次的窗口位置与尺寸 `(x, y, w, h)`；没有或格式不对返回空元组。"""
    return _parse_win(str(load(file_path).get("win") or ""))


def set_geometry(rect: tuple) -> None:
    x, y, w, h = (int(v) for v in rect)
    data = load()
    data["win"] = "%d,%d,%d,%d" % (x, y, w, h)
    save(data)
