# -*- coding: utf-8 -*-
"""数值 → 界面文本的统一格式化。

为什么单独成模块：同一件事（字节数转可读）在本项目里曾经有 5 份实现，
小数位、进位门槛、「0 字节怎么写」各不相同 —— 同一个 512 字节的目录，
一处显示 `512 B`，另一处显示 `0 KB`（`component._fmt_size`），
还有一处显示 `0.00 GB`。**用户看到的是「这目录是空的」**，
而这正是只读取证工具最不能给的结论。

现在只留一条规则。需要别的口味时加参数，不再另写一份。
"""
from __future__ import annotations

KB = 1024.0
MB = KB * 1024
GB = MB * 1024


def human_size(n: float) -> str:
    """字节数转 `1.5 KB` / `30.0 GB` / `456 MB`。

    两条规则：
    - 小于 1024 字节用整数 `B`，**不四舍五入成 0 KB**（512 B 就是 512 B）
    - 三位数以上不带小数（`456 MB` 比 `456.3 MB` 好读，界面列也窄）
    """
    n = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024.0 or unit == "TB":
            if unit == "B":
                return f"{int(n)} B"
            return f"{n:.1f} {unit}" if abs(n) < 100 else f"{n:.0f} {unit}"
        n /= 1024.0
    return f"{n:.1f} TB"


def gb_text(gb: float) -> str:
    """已按 GB 计的容量（硬盘、内存条）→ `512 GB` / `2.00 TB`。"""
    gb = float(gb)
    if gb >= 1024:
        return f"{gb / 1024:.2f} TB"
    return f"{gb:.0f} GB"


def bytes_to_gb(n: float) -> float:
    """字节 → GB（保留原值，不做舍入）。"""
    return float(n) / GB
