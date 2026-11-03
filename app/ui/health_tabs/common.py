# -*- coding: utf-8 -*-
"""体检页的公共常量与零状态工具函数。

抽出来是因为十个小页签都要用（表格着色、对齐、打开目录），
放在任何一个 mixin 里都会让另外九个反向依赖它。
"""
from __future__ import annotations

import os

from PyQt5.QtCore import QUrl, Qt
from PyQt5.QtGui import QDesktopServices


RIGHT = int(Qt.AlignRight | Qt.AlignVCenter)


LEVEL_COLOR = {"crit": "crit", "warn": "warn", "info": "dim", "ok": "ok"}


DISM_CN = {"ok": "正常", "repairable": "存在可修复损坏", "corrupt": "已损坏",
           "needs_admin": "需管理员", "unknown": "未判定"}


MAX_POINTS = 180                       # 3 分钟（1 秒 1 点）


def _level_color(r) -> str:
    return LEVEL_COLOR.get(getattr(r, "level", "ok"), "")


def _cpu_color(p) -> str:
    """进程 CPU 上色。全机口径下 20% 已经是 3 个核满载，别按 80 判。"""
    if p.cpu >= 50:
        return "crit"
    if p.cpu >= 20:
        return "warn"
    return ""


def _open(path: str) -> None:
    if not path:
        return
    if os.path.isfile(path):
        path = os.path.dirname(path)
    if os.path.isdir(path):
        QDesktopServices.openUrl(QUrl.fromLocalFile(path))
