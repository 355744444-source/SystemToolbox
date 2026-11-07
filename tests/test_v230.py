# -*- coding: utf-8 -*-
"""v2.3.0：把「重复实现」与「整读兜底」这两类问题钉死在测试里。

这一版没有新功能，全部是上一轮审查里明确留到后面的遗留项：

1. `is_admin` 三份实现 → 一份
2. `human_size` 五份实现 → 一份（另两种是刻意的专用格式）
3. PID → 路径三份实现 → 一份
4. `services.resolve_mui` 每次新建 WinDLL → 缓存
5. `locks` 每路径 3 次 stat → 1 次
6. `minidump` mmap 失败时整读 → 按需 seek
7. 两份 `find_large_files` → 删掉没人调用的那份
8. `sysinfo` 依赖 `tasklist` 的千分位逗号 → 不再起子进程

测试的重点不是「函数返回什么」，而是**这些重复不会再长回来**：
所以大量用源码扫描（`_sources()`）而不是只断言行为 —— 行为测试只能证明
这一份现在是对的，证明不了明天会不会有人又贴一份一样的进来。
"""
from __future__ import annotations

import mmap
import os
import struct
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core import (cleaner, component, dumpcfg, fmt, locks, minidump,      # noqa: E402
                      netdiag, services, smart, space, sysinfo, winutil)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _sources(*subdirs: str) -> dict[str, str]:
    """收集 app/ 下全部源码，用于「不许再出现第二份」类断言。"""
    out: dict[str, str] = {}
    for sub in subdirs or ("app",):
        base = os.path.join(ROOT, sub)
        for dirpath, _dirs, files in os.walk(base):
            if "__pycache__" in dirpath:
                continue
            for fn in files:
                if not fn.endswith(".py"):
                    continue
                p = os.path.join(dirpath, fn)
                with open(p, encoding="utf-8") as f:
                    out[os.path.relpath(p, ROOT).replace("\\", "/")] = f.read()
    return out


class HumanSizeTest(unittest.TestCase):
    """字节数格式化只允许一条规则。"""

    def test_small_values_do_not_become_zero(self):
        # 这是最有价值的一条：512 字节原来会被 component._fmt_size 显示成 "0 KB"
        self.assertEqual(fmt.human_size(0), "0 B")
        self.assertEqual(fmt.human_size(512), "512 B")
        self.assertEqual(fmt.human_size(999), "999 B")

    def test_unit_boundaries(self):
        self.assertEqual(fmt.human_size(1024), "1.0 KB")
        self.assertEqual(fmt.human_size(1536), "1.5 KB")
        self.assertEqual(fmt.human_size(30 * 1024 ** 3), "30.0 GB")
        self.assertEqual(fmt.human_size(1024 ** 4), "1.0 TB")

    def test_three_digit_drops_decimals(self):
        self.assertEqual(fmt.human_size(456 * 1024 ** 2), "456 MB")

    def test_cleaner_reexports_the_same_function(self):
        """`cleaner.human_size` 必须是同一个对象，不能是复制粘贴的副本。"""
        self.assertIs(cleaner.human_size, fmt.human_size)
        self.assertEqual(cleaner.human_size(1536), fmt.human_size(1536))

    def test_only_one_general_formatter_in_source(self):
        srcs = _sources()
        definers = [p for p, s in srcs.items()
                    if "\ndef human_size(" in s or s.startswith("def human_size(")]
        self.assertEqual(definers, ["app/core/fmt.py"],
                         "human_size 又出现了第二份实现：%s" % definers)

    def test_dumpcfg_keeps_two_decimals_on_purpose(self):
        """页面文件核算是**算数**不是展示：差值必须对得上，不能四舍五入。"""
        self.assertEqual(dumpcfg.human(2048), "2 KB")
        self.assertIn("30.25 GB", dumpcfg.human(int(30.25 * 1024 ** 3)))

    def test_gb_text_used_by_smart_disk(self):
        self.assertEqual(fmt.gb_text(512), "512 GB")
        self.assertEqual(fmt.gb_text(2048), "2.00 TB")
        self.assertEqual(smart._gb_text(2048), fmt.gb_text(2048))


class IsAdminSingleSourceTest(unittest.TestCase):
    def test_all_modules_share_one_implementation(self):
        from app.core import boot
        self.assertIs(cleaner.is_admin, winutil.is_admin)
        self.assertIs(boot.is_admin, winutil.is_admin)
        self.assertIs(component.is_admin, winutil.is_admin)

    def test_no_module_defines_its_own(self):
        srcs = _sources()
        definers = [p for p, s in srcs.items()
                    if "\ndef is_admin(" in s or s.startswith("def is_admin(")]
        self.assertEqual(definers, ["app/core/winutil.py"],
                         "is_admin 又出现了第二份实现：%s" % definers)


class ProcPathSingleSourceTest(unittest.TestCase):
    """PID → 路径：三份实现合并成 `winutil.proc_image_path`。"""

    def test_paths_agree_for_own_process(self):
        me = winutil.proc_image_path(os.getpid())
        self.assertTrue(me.lower().endswith(("python.exe", "pythonw.exe")), me)
        self.assertEqual(locks._proc_exe(os.getpid()), me)
        self.assertEqual(netdiag._proc_path(os.getpid()), me)

    def test_proc_name_is_basename(self):
        self.assertEqual(winutil.proc_name(os.getpid()),
                         os.path.basename(winutil.proc_image_path(os.getpid())))

    def test_session_lookup(self):
        sid = winutil.session_of(os.getpid())
        self.assertTrue(sid.isdigit(), "会话号应当是数字：%r" % sid)

    def test_pid_zero_and_negative_are_empty(self):
        self.assertEqual(winutil.proc_image_path(0), "")
        self.assertEqual(winutil.proc_image_path(-4), "")
        self.assertEqual(winutil.session_of(0), "")

    def test_only_winutil_declares_the_api(self):
        srcs = _sources()
        cfg = [p for p, s in srcs.items() if "QueryFullProcessImageNameW.argtypes" in s]
        self.assertEqual(cfg, ["app/core/winutil.py"],
                         "QueryFullProcessImageNameW 又在本文件外声明了：%s" % cfg)


class MuiCacheTest(unittest.TestCase):
    """`resolve_mui` 不许每次新建 WinDLL。"""

    def test_dll_is_loaded_once(self):
        self.assertIs(services._shlwapi(), services._shlwapi())

    def test_resolves_real_indirect_string(self):
        got = services.resolve_mui(r"@%SystemRoot%\system32\shell32.dll,-8964")
        self.assertFalse(got.startswith("@"), "没解析成功：%r" % got)
        self.assertNotIn(".dll", got.lower())

    def test_plain_text_passes_through(self):
        self.assertEqual(services.resolve_mui("Windows Audio"), "Windows Audio")
        self.assertEqual(services.resolve_mui(""), "")

    def test_bad_reference_falls_back_to_readable_text(self):
        self.assertEqual(services.resolve_mui(r"@%SystemRoot%\system32\nope.dll,-1"),
                         "nope.dll")

    def test_no_windll_inside_resolve(self):
        srcs = _sources()
        hits = [p for p, s in srcs.items() if 'WinDLL("shlwapi"' in s]
        self.assertEqual(hits, ["app/core/services.py"])
        body = srcs["app/core/services.py"]
        # resolve_mui 函数体里不许再出现 WinDLL（只允许出现在 _shlwapi 里）
        start = body.index("def resolve_mui(")
        end = body.index("\ndef ", start + 1)
        self.assertNotIn("WinDLL", body[start:end])


class LocksStatCountTest(unittest.TestCase):
    """`locks` 每个路径只允许 stat 一次。"""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="lk_stat_")
        for i in range(30):
            with open(os.path.join(self.root, "f%02d.txt" % i), "w", encoding="utf-8") as f:
                f.write("x")

    def _count(self):
        calls = {"n": 0}
        real_stat = os.stat

        def fake(*a, **k):
            calls["n"] += 1
            return real_stat(*a, **k)

        return calls, real_stat, fake

    def test_expand_target_stats_target_once(self):
        calls, real, fake = self._count()
        os.stat = fake
        try:
            paths, cut, _note = locks.expand_target(self.root)
        finally:
            os.stat = real
        self.assertEqual(len(paths), 30)
        self.assertEqual(calls["n"], 1,
                         "expand_target 对目标路径 stat 了 %d 次（应当 1 次："
                         "isfile/isdir 各一次的老写法已经用一次 stat 代替）" % calls["n"])

    def test_query_files_stats_each_path_once(self):
        paths = [os.path.join(self.root, n) for n in sorted(os.listdir(self.root))[:10]]
        calls, real, fake = self._count()
        os.stat = fake
        try:
            rep = locks.query_files(paths, budget_s=2.0)
        finally:
            os.stat = real
        self.assertEqual(len(rep.files), 10)
        self.assertEqual(calls["n"], len(paths),
                         "10 个路径 stat 了 %d 次（应当 10 次，原来是 30 次）" % calls["n"])


class _CountingFile:
    """给 `_Buf` 用的文件包装：记下实际读了多少字节。"""

    def __init__(self, fh):
        self.fh = fh
        self.read_bytes = 0

    def seek(self, off, whence=0):
        return self.fh.seek(off, whence)

    def read(self, n=-1):
        data = self.fh.read(n)
        self.read_bytes += len(data)
        return data


class MinidumpBufTest(unittest.TestCase):
    """转储解析：mmap 失败时按需读，不整读。"""

    @staticmethod
    def _build(path: str, tail_pad: int = 0) -> int:
        """32 字节头 + 两条流（SystemInfo / CommentW）。"""
        nstreams, dir_rva = 2, 32
        si_rva = dir_rva + nstreams * 12
        cmt = "崩溃注释".encode("utf-16-le") + b"\x00\x00"
        cmt_rva = si_rva + 32
        si = struct.pack("<HHHBBIIIII", 9, 0, 0, 8, 1, 10, 0, 22631, 2,
                         cmt_rva + len(cmt))
        head = (b"MDMP" + struct.pack("<IIIII", 0xA793, nstreams, dir_rva, 0, 0x1234)
                + struct.pack("<Q", 0x42))
        assert len(head) == 32
        streams = (struct.pack("<III", 7, 32, si_rva)
                   + struct.pack("<III", 10, len(cmt), cmt_rva))
        blob = head + streams + si + cmt + b"\x00" * tail_pad
        with open(path, "wb") as f:
            f.write(blob)
        return len(blob)

    def test_file_mode_reads_a_fraction_of_the_file(self):
        d = tempfile.mkdtemp(prefix="dmp_buf_")
        p = os.path.join(d, "big.dmp")
        size = self._build(p, tail_pad=8 * 1024 * 1024)
        with open(p, "rb") as fh:
            cf = _CountingFile(fh)
            buf = minidump._Buf(fh=cf, size=size)
            buf.raw(0, 8)
            buf.u32(8)
            buf.u64(24)
            self.assertLess(cf.read_bytes, size // 4,
                            "按需读模式下读进了 %d / %d 字节" % (cf.read_bytes, size))
            self.assertLessEqual(cf.read_bytes, minidump._Buf.BLOCK)

    def test_handle_mode_matches_mmap_mode(self):
        d = tempfile.mkdtemp(prefix="dmp_eq_")
        p = os.path.join(d, "mini.dmp")
        self._build(p)
        as_mmap = minidump.parse(p)

        real = mmap.mmap

        def boom(*_a, **_k):
            raise OSError("模拟 mmap 失败（文件被占用 / 网络路径）")

        mmap.mmap = boom
        try:
            as_file = minidump.parse(p)
        finally:
            mmap.mmap = real

        self.assertEqual(as_mmap.kind, "minidump")
        self.assertEqual(as_mmap.kind, as_file.kind)
        self.assertEqual(as_mmap.system.build, 22631)
        self.assertEqual(as_file.system.build, as_mmap.system.build)
        self.assertEqual(as_file.system.cpus, as_mmap.system.cpus)

    def test_wstr_and_astr_in_both_modes(self):
        text = "驱动器 \\Device\\HarddiskVolume3 上的错误"
        raw_w = text.encode("utf-16-le") + b"\x00\x00"
        raw_a = b"ntoskrnl.exe\x00"
        blob = raw_w + raw_a + b"\x00" * (minidump._Buf.BLOCK + 16)
        d = tempfile.mkdtemp(prefix="dmp_str_")
        p = os.path.join(d, "s.bin")
        with open(p, "wb") as f:
            f.write(blob)
        a_off = len(raw_w)
        with open(p, "rb") as fh:
            buf = minidump._Buf(fh=fh, size=len(blob))
            self.assertEqual(buf.wstr(0), text)
            self.assertEqual(buf.astr(a_off), "ntoskrnl.exe")
            # 跨块读：第二次读落在块缓存之外
            self.assertEqual(buf.raw(len(blob) - 8, 8), b"\x00" * 8)

    def test_out_of_range_still_returns_defaults(self):
        buf = minidump._Buf(b"MDMP" + b"\x00" * 28)
        self.assertEqual(buf.u32(1000), 0)
        self.assertEqual(buf.raw(1000, 4), b"")
        self.assertEqual(buf.wstr(1000), "")


class DeadImplementationTest(unittest.TestCase):
    """删掉的那两份不许回来。"""

    def test_disk_has_no_second_find_large_files(self):
        from app.core import disk
        self.assertFalse(hasattr(disk, "find_large_files"),
                         "disk.find_large_files 又回来了（space 里已经有一份）")
        self.assertTrue(hasattr(disk, "parse_defrag_percent"),
                        "删 find_large_files 时把后面的进度解析一起删掉了")

    def test_only_one_find_large_files_definition(self):
        srcs = _sources()
        definers = [p for p, s in srcs.items()
                    if "\ndef find_large_files(" in s or s.startswith("def find_large_files(")]
        self.assertEqual(definers, ["app/core/space.py"], definers)

    def test_component_has_no_private_size_formatter(self):
        self.assertFalse(hasattr(component, "_fmt_size"),
                         "component._fmt_size 会把 512 字节显示成 0 KB")


class SysinfoProcsTest(unittest.TestCase):
    """进程列表不再依赖 tasklist。"""

    def test_no_tasklist_invocation_left(self):
        """只断言**调用**：注释和说明里提到 tasklist 是可以的（而且应当提）。"""
        import re
        pattern = re.compile(r"""["']tasklist["']""")
        srcs = _sources()
        hits = [p for p, s in srcs.items() if pattern.search(s)]
        self.assertEqual(hits, [], "还有模块在调 tasklist：%s" % hits)

    def test_name_map_covers_system_processes(self):
        """Toolhelp 拿得到 SYSTEM 会话的进程名 —— 这正是原 tasklist 的唯一理由。"""
        m = winutil.process_name_map()
        self.assertGreater(len(m), 30, "没枚举到进程")
        names = {v.lower() for v in m.values()}
        self.assertIn("svchost.exe", names)
        self.assertIn("system", names, "连 System 都没有，说明权限口径退化了")

    def test_netdiag_uses_the_shared_map(self):
        got = netdiag._proc_map()
        self.assertGreater(len(got), 30)
        self.assertEqual(got.get(4, "").lower(), "system")

    def test_top_processes_without_spawning(self):
        import subprocess
        real = subprocess.run

        def boom(*_a, **_k):
            raise AssertionError("top_processes 不该再起子进程")

        subprocess.run = boom
        try:
            rows, total = sysinfo.top_processes(10)
        finally:
            subprocess.run = real
        self.assertGreater(total, 0, "没枚举到任何进程")
        self.assertLessEqual(len(rows), 10)
        for r in rows:
            self.assertTrue(r.name)
            self.assertGreaterEqual(r.pid, 0)
        mems = [r.mem for r in rows]
        self.assertEqual(mems, sorted(mems, reverse=True), "没有按工作集降序")

    def test_session_column_is_filled(self):
        rows, _total = sysinfo.top_processes(5)
        filled = [r for r in rows if r.session]
        self.assertTrue(filled, "会话列全是空的（ProcessIdToSessionId 没生效）")

    def test_collect_fills_proc_rows(self):
        snap = sysinfo.collect()
        self.assertGreater(snap.proc_total, 0)
        self.assertTrue(snap.procs)
        self.assertEqual(len(snap.procs), min(12, snap.proc_total))


class SpaceTruncationStillWiredTest(unittest.TestCase):
    """v2.2.0 的截断标记不能因为这次重构丢了。"""

    def test_stats_keys(self):
        root = tempfile.mkdtemp(prefix="sp_")
        for i in range(3):
            with open(os.path.join(root, "f%d.bin" % i), "wb") as f:
                f.write(b"x" * 4096)
        st: dict = {}
        rows = space.find_large_files(root, min_size=1024, limit=1, stats=st)
        self.assertEqual(len(rows), 1)
        self.assertEqual(st["matched"], 3)
        self.assertEqual(st["shown"], 1)
        self.assertTrue(st["truncated"])


if __name__ == "__main__":
    unittest.main()
