# -*- coding: utf-8 -*-
"""空间占用分析：大文件检索 + 目录占用排行。

全部只读。遍历时跳过 reparse point（junction / 符号链接）避免绕圈，
跳过系统与开发缓存目录减少噪声与耗时，并对遍历量设上限，
保证在几个 TB 的盘上也不会跑到天荒地老。
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

# 大文件扫描跳过：系统目录、包管理缓存、虚拟环境
SKIP_DIRS_LARGE = {
    "$recycle.bin", "system volume information", "recovery", "perflogs",
    "windows", "winsxs", "installer", "$windows.~bt", "$windows.~ws",
    "node_modules", ".git", ".svn", "__pycache__", "venv", ".venv", "site-packages",
}

# 目录分析跳过：这些目录要么是系统自身，要么不含用户数据，扫了也没意义
SKIP_DIRS_TREE = {"$recycle.bin", "system volume information", "recovery"}


def _is_reparse(entry) -> bool:
    try:
        return bool(entry.stat(follow_symlinks=False).st_file_attributes & 0x400)
    except (OSError, AttributeError):
        return False


def find_large_files(root: str, min_size: int = 200 * 1024 * 1024, limit: int = 300,
                     stop_flag=None, progress=None,
                     max_files: int = 600_000) -> list[tuple[str, int, float]]:
    """在 root 下找大于阈值的文件，按体积降序。"""
    found: list[tuple[str, int, float]] = []
    if not os.path.isdir(root):
        return found
    seen = 0
    root_depth = os.path.normpath(root).count(os.sep)
    stack = [os.path.normpath(root)]
    while stack:
        if stop_flag and stop_flag[0]:
            break
        cur = stack.pop()
        try:
            it = os.scandir(cur)
        except OSError:
            continue
        with it:
            for e in it:
                seen += 1
                if seen > max_files:
                    stack.clear()
                    break
                if progress and seen % 1000 == 0:
                    progress(seen, cur)
                try:
                    if e.is_dir(follow_symlinks=False):
                        if e.name.lower() in SKIP_DIRS_LARGE or _is_reparse(e):
                            continue
                        if e.path.count(os.sep) - root_depth > 12:
                            continue
                        stack.append(e.path)
                        continue
                    st = e.stat(follow_symlinks=False)
                except OSError:
                    continue
                if st.st_size >= min_size:
                    found.append((e.path, st.st_size, st.st_mtime))
    found.sort(key=lambda x: -x[1])
    return found[:limit]


# ------------------------------------------------------------------ 目录分析
@dataclass
class DirNode:
    path: str
    name: str
    size: int = 0
    files: int = 0
    mtime: float = 0.0
    children: list["DirNode"] = field(default_factory=list)
    error: bool = False

    @property
    def depth(self) -> int:
        return self.path.count(os.sep)


def _dir_size(path: str, stop_flag=None, counter=None,
              max_files: int = 2_000_000) -> tuple[int, int, float, bool]:
    """递归统计目录总大小 / 文件数 / 最新修改时间。"""
    total = 0
    count = 0
    newest = 0.0
    failed = False
    stack = [path]
    while stack:
        if stop_flag and stop_flag[0]:
            break
        cur = stack.pop()
        try:
            it = os.scandir(cur)
        except OSError:
            failed = True
            continue
        with it:
            for e in it:
                try:
                    if e.is_dir(follow_symlinks=False):
                        if _is_reparse(e):
                            continue
                        stack.append(e.path)
                        continue
                    st = e.stat(follow_symlinks=False)
                except OSError:
                    continue
                total += st.st_size
                count += 1
                if st.st_mtime > newest:
                    newest = st.st_mtime
                if counter is not None:
                    counter[0] += 1
                    if counter[0] > max_files:
                        return (total, count, newest, failed)
    return (total, count, newest, failed)


def analyze_dir(path: str, stop_flag=None, progress=None, counter=None,
                min_child_size: int = 1 * 1024 * 1024) -> DirNode:
    """统计 path 下每个一级子目录的大小，返回按体积降序的根节点。

    只算一级子项 + 每个子项的总量，不建整棵树（几百万文件的树会撑爆内存）。
    """
    path = os.path.normpath(path)
    root = DirNode(path=path, name=path)
    counter = counter if counter is not None else [0]
    direct = 0
    direct_files = 0
    root_newest = 0.0
    try:
        it = os.scandir(path)
    except OSError:
        root.error = True
        return root
    entries = []
    with it:
        for e in it:
            if stop_flag and stop_flag[0]:
                break
            try:
                if e.is_dir(follow_symlinks=False):
                    if e.name.lower() in SKIP_DIRS_TREE or _is_reparse(e):
                        continue
                    entries.append(e.path)
                else:
                    st = e.stat(follow_symlinks=False)
                    direct += st.st_size
                    direct_files += 1
                    root_newest = max(root_newest, st.st_mtime)
            except OSError:
                continue
    for i, sub in enumerate(entries):
        if stop_flag and stop_flag[0]:
            break
        if progress:
            progress(i, len(entries), os.path.basename(sub))
        size, files, newest, failed = _dir_size(sub, stop_flag, counter)
        node = DirNode(path=sub, name=os.path.basename(sub), size=size,
                       files=files, mtime=newest, error=failed)
        root.children.append(node)
    root.size = direct + sum(c.size for c in root.children)
    root.files = direct_files + sum(c.files for c in root.children)
    root.mtime = max([root_newest] + [c.mtime for c in root.children]) if root.children else root_newest
    root.children.sort(key=lambda c: -c.size)
    return root


def top_dirs(root: DirNode, n: int = 40, min_size: int = 0) -> list[DirNode]:
    """把树里所有节点摊平取前 n（含多层，方便一眼看到深层元凶）。"""
    flat: list[DirNode] = []

    def walk(node: DirNode, depth: int) -> None:
        for c in node.children:
            if c.size >= min_size:
                flat.append(c)
            if depth < 4:
                walk(c, depth + 1)

    walk(root, 0)
    flat.sort(key=lambda x: -x.size)
    return flat[:n]


def used_percent(size: int, total: int) -> float:
    return (size / total * 100.0) if total else 0.0
