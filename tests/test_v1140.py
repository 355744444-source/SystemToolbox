# -*- coding: utf-8 -*-
"""v1.14.0 单测：签名校验 / 文件占用 / 环境变量体检。

三块都以「翻译层」为主 —— 真机只能覆盖当前这一台的配置，
所以重点是构造各种结果码与 PATH 组合，把判定和文案钉死。
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core import locks  # noqa: E402
from app.core import pathenv as pe  # noqa: E402
from app.core import sign  # noqa: E402


# ====================================================================== 签名
class TestSignDecode(unittest.TestCase):
    def test_ok(self):
        self.assertEqual(sign._decode(0), ("ok", "证书链完整，文件未被改动"))

    def test_no_signature(self):
        kind, note = sign._decode(0x800B0100)
        self.assertEqual(kind, "uncatalogued")
        self.assertIn(".cat", note)

    def test_bad_digest_is_bad_not_unsigned(self):
        # 签名在但内容对不上 = 被改过，不能混进「未签名」
        kind, _ = sign._decode(0x80096010)
        self.assertEqual(kind, "bad")

    def test_untrusted_root_is_warn(self):
        kind, _ = sign._decode(0x800B0109)
        self.assertEqual(kind, "warn")

    def test_expired_cert_is_warn(self):
        kind, _ = sign._decode(0x800B0101)
        self.assertEqual(kind, "warn")

    def test_unknown_code_falls_back_to_bad(self):
        kind, note = sign._decode(0xDEADBEEF)
        self.assertEqual(kind, "bad")
        self.assertIn("0xDEADBEEF", note)

    def test_negative_rc_masked(self):
        # WinVerifyTrust 返回的是有符号 long，0x800B0100 会变成负数
        import struct
        signed = struct.unpack("<i", struct.pack("<I", 0x800B0100))[0]
        self.assertLess(signed, 0)
        self.assertEqual(sign._decode(signed)[0], "uncatalogued")


class TestSignInfo(unittest.TestCase):
    def test_only_unsigned_and_broken_are_suspicious(self):
        f = sign.SignInfo
        self.assertTrue(f(path="a", kind="uncatalogued").suspicious)
        self.assertTrue(f(path="a", kind="bad").suspicious)
        self.assertFalse(f(path="a", kind="warn").suspicious)
        self.assertFalse(f(path="a", kind="ok").suspicious)
        self.assertFalse(f(path="a", kind="missing").suspicious)

    def test_level_text_and_rc_text(self):
        info = sign.SignInfo(path="a.exe", rc=0x800B0100, kind="uncatalogued")
        self.assertEqual(info.level_text, "未签名")
        self.assertEqual(info.rc_text, "0x800B0100")

    def test_method_text(self):
        self.assertEqual(sign.SignInfo(path="a", method="embedded").method_text,
                         "内嵌签名")
        self.assertEqual(sign.SignInfo(path="a", method="catalog").method_text,
                         "目录签名")

    def test_missing_file(self):
        info = sign.verify(r"Z:\definitely\not\here.exe")
        self.assertEqual(info.kind, "missing")
        self.assertEqual(info.level_text, "文件不存在")

    def test_describe_mentions_catalog(self):
        info = sign.SignInfo(path="a", kind="ok", method="catalog",
                             catalog="Foo.cat")
        self.assertIn("Foo.cat", sign.describe(info))

    @unittest.skipUnless(os.path.isfile(r"C:\Windows\System32\notepad.exe"),
                         "没有 notepad.exe")
    def test_notepad_is_signed_via_catalog(self):
        # 这条钉住 v1.14.0 最大的坑：只查内嵌签名会把 notepad 判成「未签名」
        info = sign.verify(r"C:\Windows\System32\notepad.exe")
        self.assertTrue(info.ok, "notepad 应当通过（目录签名）")
        self.assertIn(info.method, ("embedded", "catalog"))


# ====================================================================== 占用
class TestLockVerdict(unittest.TestCase):
    def test_not_exists(self):
        f = locks.LockFile(path=r"D:\nope", exists=False)
        self.assertEqual(f.verdict, "路径不存在")
        self.assertEqual(f.verdict_level, "faint")

    def test_locked(self):
        f = locks.LockFile(path=r"C:\a.txt",
                           processes=[locks.LockProcess(pid=1, name="x")])
        self.assertEqual(f.verdict, "被占用")
        self.assertEqual(f.verdict_level, "warn")

    def test_free(self):
        f = locks.LockFile(path=r"C:\a.txt")
        self.assertEqual(f.verdict, "未占用")
        self.assertEqual(f.verdict_level, "ok")

    def test_error_outranks_free(self):
        f = locks.LockFile(path=r"C:\pagefile.sys",
                           rc=locks.ERROR_SHARING_VIOLATION)
        self.assertEqual(f.verdict, "查不了")
        self.assertIn("系统独占", f.error)
        self.assertEqual(f.verdict_level, "dim")

    def test_kernel_held_is_not_free(self):
        # RM 对 pagefile 回 0 个占用，直接说「未占用」是错的
        f = locks.LockFile(path=r"C:\pagefile.sys", kernel_held="页面文件")
        self.assertEqual(f.verdict, "系统独占")

    def test_kernel_held_names(self):
        self.assertIn("页面文件",
                      locks._kernel_held(r"C:\pagefile.sys"))
        self.assertIn("休眠", locks._kernel_held(r"C:\hiberfil.sys"))
        self.assertIn("注册表 hive",
                      locks._kernel_held(r"C:\Windows\System32\config\SYSTEM"))
        self.assertEqual(locks._kernel_held(r"C:\Windows\notepad.exe"), "")


class TestLockProcess(unittest.TestCase):
    def test_label_priority(self):
        p = locks.LockProcess(pid=7, name="explorer.exe", service="Svc",
                              exe=r"C:\Windows\explorer.exe")
        self.assertEqual(p.label, "explorer.exe")
        p2 = locks.LockProcess(pid=7, service="Svc")
        self.assertEqual(p2.label, "Svc")
        p3 = locks.LockProcess(pid=7, exe=r"C:\x\y.exe")
        self.assertEqual(p3.label, "y.exe")
        p4 = locks.LockProcess(pid=7)
        self.assertEqual(p4.label, "PID 7")

    def test_app_type_1000_is_critical_not_error(self):
        p = locks.LockProcess(pid=1, app_type=1000)
        self.assertEqual(p.type_text, "关键进程")

    def test_status_is_bitmask(self):
        p = locks.LockProcess(pid=1, status=0x01 | 0x08)
        self.assertIn("运行中", p.status_text)
        self.assertIn("需重启", p.status_text)
        self.assertEqual(locks.LockProcess(pid=1, status=0).status_text, "占用中")

    def test_system_process(self):
        self.assertTrue(locks.LockProcess(
            pid=4, exe=r"C:\Windows\System32\ntoskrnl.exe").is_system)
        self.assertFalse(locks.LockProcess(
            pid=9, exe=r"D:\tools\a.exe").is_system)


class TestLockQuery(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="cls_lock_test_")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.dir, ignore_errors=True)

    @staticmethod
    def _touch(path: str, size: int = 1) -> None:
        with open(path, "wb") as fh:
            fh.write(b"x" * size)

    def test_expand_single_file(self):
        f = os.path.join(self.dir, "a.bin")
        self._touch(f)
        files, cut, note = locks.expand_target(f)
        self.assertEqual(files, [os.path.abspath(f)])
        self.assertEqual(cut, 0)

    def test_expand_dir_only_files_non_recursive(self):
        self._touch(os.path.join(self.dir, "a.bin"))
        self._touch(os.path.join(self.dir, "b.bin"))
        os.makedirs(os.path.join(self.dir, "sub"), exist_ok=True)
        self._touch(os.path.join(self.dir, "sub", "deep.bin"))
        files, cut, note = locks.expand_target(self.dir)
        names = sorted(os.path.basename(x) for x in files)
        self.assertEqual(names, ["a.bin", "b.bin"])
        self.assertNotIn("deep.bin", names)
        self.assertEqual(note, "")

    def test_expand_empty_dir_has_note(self):
        empty = os.path.join(self.dir, "empty")
        os.makedirs(empty, exist_ok=True)
        files, cut, note = locks.expand_target(empty)
        self.assertEqual(files, [])
        self.assertIn("没有文件", note)

    def test_expand_blank(self):
        self.assertEqual(locks.expand_target("  ")[0], [])

    def test_query_missing_path(self):
        rep = locks.query(os.path.join(self.dir, "nope.bin"))
        self.assertEqual(rep.total, 1)
        self.assertEqual(rep.files[0].verdict, "路径不存在")
        self.assertEqual(rep.locked_count, 0)

    def test_query_free_file(self):
        f = os.path.join(self.dir, "free.bin")
        self._touch(f, 64)
        rep = locks.query(f)
        self.assertEqual(rep.total, 1)
        self.assertEqual(rep.files[0].verdict, "未占用")
        self.assertIn("没有进程占用", rep.headline)

    def test_headline_prefers_locked(self):
        rep = locks.LockReport(files=[
            locks.LockFile(path=r"C:\a", processes=[
                locks.LockProcess(pid=1, name="A.exe")]),
            locks.LockFile(path=r"C:\b"),
        ])
        self.assertIn("A.exe", rep.headline)
        self.assertEqual(rep.locked_count, 1)

    def test_headline_kernel(self):
        rep = locks.LockReport(files=[
            locks.LockFile(path=r"C:\pagefile.sys", kernel_held="页面文件")])
        self.assertIn("内核独占", rep.headline)

    def test_rc_text_covers_common_codes(self):
        for code in (2, 3, 5, 32, 87, 123, 161):
            self.assertTrue(locks.RC_TEXT.get(code), "rc=%d 没有解释" % code)

    # ---- 时间预算 / 取消（v1.14.0 实测踩到：winevt\Logs 446 个文件全被服务占着，
    #      二分退化成 O(n)，一个会话 200~300ms，整个目录要 110 秒）
    def test_budget_zero_marks_timeout(self):
        files = []
        for i in range(6):
            f = os.path.join(self.dir, "t%d.bin" % i)
            self._touch(f, 8)
            files.append(f)
        rep = locks.query_files(files, budget_s=0)
        self.assertEqual(rep.total, 6)
        self.assertEqual(rep.timeout_count, 6)
        self.assertEqual(rep.files[0].verdict, "未查完")
        self.assertEqual(rep.files[0].verdict_level, "dim")
        self.assertTrue(rep.files[0].detail)
        self.assertIn("没逐个确认", rep.headline)

    def test_stop_flag_marks_notes(self):
        f = os.path.join(self.dir, "s.bin")
        self._touch(f, 8)
        rep = locks.query_files([f], stop_flag=[True])
        self.assertEqual(rep.timeout_count, 1)
        self.assertTrue(any("手动停止" in n for n in rep.notes))

    def test_timeout_does_not_swallow_locked(self):
        """有占用时结论仍然先说占用，只在尾巴上补一句「没逐个确认」。"""
        rep = locks.LockReport(files=[
            locks.LockFile(path=r"C:\a", processes=[
                locks.LockProcess(pid=7, name="A.exe")]),
            locks.LockFile(path=r"C:\b", timeout=True),
        ])
        self.assertEqual(rep.timeout_count, 1)
        self.assertIn("A.exe", rep.headline)
        self.assertIn("没逐个确认", rep.headline)

    def test_no_timeout_no_suffix(self):
        rep = locks.LockReport(files=[locks.LockFile(path=r"C:\a")])
        self.assertNotIn("没逐个确认", rep.headline)
        self.assertEqual(rep.headline, "目前没有进程占用")

    def test_timeout_with_hint_says_group_locked(self):
        """超预算但上级批次查到了占用者：给「组内占用」而不是含糊的「未查完」。"""
        lf = locks.LockFile(path=r"C:\dir\a.evtx", timeout=True,
                            hint="Windows Event Log")
        self.assertEqual(lf.verdict, "组内占用")
        self.assertIn("Windows Event Log", lf.detail)
        bare = locks.LockFile(path=r"C:\dir\b.evtx", timeout=True)
        self.assertEqual(bare.verdict, "未查完")
        self.assertIn("指定具体文件", bare.detail)

    def test_kernel_count_ignores_unqueried(self):
        """没查完的文件不算「内核独占」，否则几项相加会超过文件总数。"""
        cfg = r"C:\Windows\System32\config"
        rep = locks.LockReport(files=[
            locks.LockFile(path=cfg + r"\SAM", kernel_held="注册表 hive"),
            locks.LockFile(path=cfg + r"\SOFTWARE", kernel_held="注册表 hive",
                           timeout=True),
            locks.LockFile(path=cfg + r"\SYSTEM", kernel_held="注册表 hive",
                           timeout=True,
                           processes=[locks.LockProcess(pid=4, name="System")]),
        ])
        self.assertEqual(rep.kernel_count, 1)
        self.assertEqual(rep.timeout_count, 2)
        self.assertEqual(rep.locked_count, 1)

    def test_detail_prefers_processes_over_kernel_note(self):
        lf = locks.LockFile(path=r"C:\Windows\System32\config\SYSTEM",
                            kernel_held="注册表 hive 由内核加载",
                            processes=[locks.LockProcess(pid=4, name="System")])
        self.assertEqual(lf.verdict, "被占用")
        self.assertEqual(lf.detail, "System")


# ====================================================================== PATH
GOOD_ENV = {
    "SystemRoot": r"C://Windows",
    "windir": r"C://Windows",
    "ComSpec": r"C://Windows//system32//cmd.exe",
    "PATHEXT": ".COM;.EXE;.BAT;.CMD",
    "TEMP": os.environ.get("TEMP", r"C://Windows//Temp"),
    "TMP": os.environ.get("TMP", r"C://Windows//Temp"),
    "USERPROFILE": r"C://Users//x",
    "SystemDrive": "C:",
}


def entry(raw, scope="machine"):
    return pe.PathEntry(raw=raw, scope=scope)


class TestPathEntry(unittest.TestCase):
    def test_kind_priority(self):
        e = entry(r"%NOPE%\x")
        e.unresolved = ["NOPE"]
        self.assertEqual(e.kind, "变量未定义")
        e2 = entry('"C:\\Program Files\\x"')
        e2.quoted = True
        self.assertEqual(e2.kind, "带引号")
        e3 = entry("tools\\bin")
        e3.relative = True
        self.assertEqual(e3.kind, "相对路径")
        e4 = entry(r"C:\gone")
        self.assertEqual(e4.kind, "目录不存在")
        e5 = entry(r"C:\Windows")
        e5.exists = True
        self.assertEqual(e5.kind, "正常")
        e6 = entry("   ")
        self.assertEqual(e6.kind, "空条目")

    def test_dup_hides_behind_missing(self):
        # 既重复又不存在时，表格显示「目录不存在」（更可操作），但 duplicate 仍为真
        e = entry(r"C:\gone")
        e.duplicate = True
        self.assertEqual(e.kind, "目录不存在")
        self.assertTrue(e.duplicate)

    def test_note_for_cross_scope(self):
        e = entry(r"C:\x", "user")
        e.duplicate = True
        e.dup_scope = "machine"
        self.assertIn("系统级重复", e.note)
        e2 = entry(r"C:\x", "machine")
        e2.duplicate = True
        e2.dup_scope = "machine"
        self.assertIn("不生效", e2.note)

    def test_show_prefers_expanded(self):
        e = entry(r"%SystemRoot%\System32")
        e.expanded = r"C:\Windows\System32"
        e.exists = True
        self.assertEqual(e.show, r"C:\Windows\System32")
        e2 = entry(r"%NOPE%\x")
        e2.unresolved = ["NOPE"]
        self.assertEqual(e2.show, r"%NOPE%\x")


class TestParse(unittest.TestCase):
    def test_split_and_scope(self):
        rows = pe.parse(r"C:\Windows;C:\Windows\System32",
                        r"C:\Tools", env={}, check_exists=False)
        self.assertEqual(len(rows), 3)
        self.assertEqual([r.scope for r in rows], ["machine", "machine", "user"])

    def test_dup_same_scope(self):
        rows = pe.parse(r"C:\Windows;C:\Windows\\", "", env={}, check_exists=False)
        self.assertFalse(rows[0].duplicate)
        self.assertTrue(rows[1].duplicate)
        self.assertEqual(rows[1].dup_scope, "machine")

    def test_dup_cross_scope(self):
        rows = pe.parse(r"C:\Windows", r"C:\Windows", env={}, check_exists=False)
        self.assertTrue(rows[1].duplicate)
        self.assertEqual(rows[1].dup_scope, "machine")

    def test_unresolved_variable(self):
        rows = pe.parse(r"%NOPE%\x", "", env={}, check_exists=False)
        self.assertEqual(rows[0].unresolved, ["NOPE"])
        self.assertEqual(rows[0].kind, "变量未定义")

    def test_expand_known_variable(self):
        rows = pe.parse(r"%SystemRoot%\System32", "",
                        env={"SystemRoot": r"C:\Windows"}, check_exists=False)
        self.assertEqual(rows[0].expanded, r"C:\Windows\System32")
        self.assertEqual(rows[0].unresolved, [])

    def test_quoted_entry(self):
        rows = pe.parse('"C:\\Program Files\\App"', "", env={}, check_exists=False)
        self.assertTrue(rows[0].quoted)
        self.assertEqual(rows[0].expanded, r"C:\Program Files\App")

    def test_relative_entry(self):
        rows = pe.parse(r"tools\bin", "", env={}, check_exists=False)
        self.assertTrue(rows[0].relative)
        self.assertEqual(rows[0].kind, "相对路径")

    def test_unc_path_is_absolute(self):
        rows = pe.parse(r"\\server\share\bin", "", env={}, check_exists=False)
        self.assertFalse(rows[0].relative)

    def test_trailing_slash_dup(self):
        rows = pe.parse(r"C:\A\;C:\A", "", env={}, check_exists=False)
        self.assertTrue(rows[1].duplicate)

    def test_empty_pieces_kept_as_empty_rows(self):
        # 系统级 3 段（含 2 个空段）+ 用户级空串 1 段
        rows = pe.parse(r"C:\Windows;;", "", env={}, check_exists=False)
        self.assertEqual(len(rows), 4)
        self.assertEqual(rows[1].kind, "空条目")
        self.assertEqual(rows[2].kind, "空条目")


class TestReport(unittest.TestCase):
    def _rep(self, machine, user, env=None, check_exists: bool = True):
        rep = pe.Report(env=env or {})
        rep.machine_raw, rep.user_raw = machine, user
        rep.entries = pe.parse(machine, user, env=env or {},
                               check_exists=check_exists)
        rep.findings = pe._findings(rep)
        return rep

    def test_counts(self):
        rep = self._rep(r"C:\A;C:\A;C:\B", r"C:\C")
        self.assertEqual(rep.total, 4)
        self.assertEqual(rep.dups, 1)
        self.assertEqual(rep.dups_same_scope, 1)
        self.assertEqual(rep.machine_count, 3)
        self.assertEqual(rep.user_count, 1)

    def test_headline_no_problem(self):
        # 两级都空时，各切出一个空条目 —— 这正是「PATH 被清空」的样子
        rep = self._rep("", "", env=GOOD_ENV)
        self.assertEqual(rep.total, 2)
        self.assertTrue(all(e.kind == "空条目" for e in rep.entries))

    def test_missing_produces_warn(self):
        # 带上 System32，否则会连带触发 path-sys32（那是另一条 crit 规则）
        rep = self._rep(r"C:\definitely_not_here_1140;C:\Windows\System32",
                        "", env=GOOD_ENV)
        topics = {f.topic: f for f in rep.findings}
        self.assertIn("path-missing", topics)
        self.assertEqual(topics["path-missing"].severity, "warn")
        self.assertEqual(rep.level, "warn")

    def test_separator_style_is_normalized(self):
        """`C:/A` 与 `C:\\A` 是同一个目录，重复检测和 System32 判定都得认。

        回归：_norm 早先只做 rstrip + lower，PATH 里写正斜杠、
        或变量展开后出现连续分隔符时，同一个目录会被算成两条（重复漏报）。
        """
        rep = self._rep(r"C:/Windows/System32;C:\Windows\System32", "",
                        env=GOOD_ENV)
        self.assertEqual(rep.dups, 1, "两种写法必须算同一条")
        topics = {f.topic for f in rep.findings}
        self.assertNotIn("path-sys32", topics, "System32 明明在，不能报缺失")

    def test_core_var_missing_is_crit(self):
        rep = pe.Report(env={"TEMP": r"C:\Temp", "TMP": r"C:\Temp"})
        rep.entries = []
        rep.findings = pe._findings(rep)
        topics = {f.topic: f for f in rep.findings}
        self.assertEqual(topics["env-core"].severity, "crit")
        self.assertEqual(rep.level, "crit")

    def test_temp_missing_is_crit(self):
        rep = pe.Report(env={"SystemRoot": r"C:\Windows", "ComSpec": "cmd.exe",
                             "TEMP": r"Z:\no\such", "TMP": r"Z:\no\such"})
        rep.entries = []
        rep.findings = pe._findings(rep)
        topics = {f.topic: f for f in rep.findings}
        self.assertIn("env-temp", topics)
        self.assertEqual(topics["env-temp"].severity, "crit")

    def test_pathext_without_exe(self):
        rep = pe.Report(env={"SystemRoot": r"C:\Windows", "ComSpec": "cmd.exe",
                             "TEMP": tempfile.gettempdir(),
                             "TMP": tempfile.gettempdir(),
                             "PATHEXT": ".COM;.BAT"})
        rep.entries = []
        rep.findings = pe._findings(rep)
        topics = {f.topic: f for f in rep.findings}
        self.assertIn("env-pathext", topics)

    def test_verdict_order(self):
        self.assertEqual(pe.verdict([])[0], "ok")
        f_crit = type("F", (), {"severity": "crit", "topic": "x"})()
        f_info = type("F", (), {"severity": "info", "topic": "y"})()
        self.assertEqual(pe.verdict([f_info])[0], "info")
        self.assertEqual(pe.verdict([f_info, f_crit])[0], "crit")

    def test_headline_text(self):
        rep = self._rep(r"C:\definitely_not_here_1140", r"C:\Users")
        self.assertIn("无效", rep.headline)
        self.assertIn("共", rep.headline)


class TestAuditReal(unittest.TestCase):
    def test_real_machine_runs(self):
        rep = pe.audit()
        # 真机上至少能读到系统 PATH，条目数不会是 0
        self.assertGreater(rep.total, 0)
        self.assertTrue(rep.findings)
        self.assertGreaterEqual(rep.elapsed, 0.0)

    def test_report_has_severity_known(self):
        rep = pe.audit()
        for f in rep.findings:
            self.assertIn(f.severity, ("crit", "warn", "info", "ok"))


if __name__ == "__main__":
    unittest.main()
