# -*- coding: utf-8 -*-
"""系统优化模块的回归测试（stdlib unittest）。

安全约定
- 注册表测试只写 `HKCU\\Software\\SystemToolboxSelfTest` 这个临时键，测完即删；
- StartupApproved 测试用自造的值名，不碰任何真实启动项；
- 服务测试只验证「不存在/无权限」的返回，不真的改系统服务；
- 微调测试用临时 Tweak 指向临时键，不碰真实设置。
"""
from __future__ import annotations

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app.core import services, startup, sysinfo, tweaks, winver  # noqa: E402

TEST_KEY = r"Software\SystemToolboxSelfTest"


def _clean_reg() -> None:
    """删除测试用的注册表键（含子键）。"""
    if os.name != "nt":
        return
    import winreg
    for path in (TEST_KEY, TEST_KEY + r"\Sub"):
        try:
            k = winreg.OpenKey(winreg.HKEY_CURRENT_USER, path, 0,
                               winreg.KEY_SET_VALUE | winreg.KEY_READ)
        except OSError:
            continue
        try:
            while True:
                try:
                    name, _v, _t = winreg.EnumValue(k, 0)
                except OSError:
                    break
                try:
                    winreg.DeleteValue(k, name)
                except OSError:
                    break
        finally:
            k.Close()
    for path in (TEST_KEY + r"\Sub", TEST_KEY):
        try:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, path)
        except OSError:
            pass


def _clear_tweak_backup(key: str) -> None:
    """清掉某个微调项的备份记录（测试专用）。"""
    data = tweaks.load_backup()
    if key in data:
        data.pop(key, None)
        tweaks._save_backup(data)


# ---------------------------------------------------------------- 命令行解析
class TestParseTarget(unittest.TestCase):
    def test_quoted_path(self):
        self.assertEqual(
            startup.parse_target('"C:\\Program Files\\App\\a.exe" --run'),
            r"C:\Program Files\App\a.exe")

    def test_spaced_without_quotes(self):
        # 没引号但带空格：必须贪婪取到 .exe，不能截成 "C:\Program"
        self.assertEqual(
            startup.parse_target(r"C:\Program Files\My App\agent.exe -silent"),
            r"C:\Program Files\My App\agent.exe")

    def test_nonexec_keeps_first_token(self):
        self.assertTrue(startup.parse_target("rundll32.dll,Entry").lower().startswith("rundll32"))

    def test_empty(self):
        self.assertEqual(startup.parse_target(""), "")


class TestExpandTarget(unittest.TestCase):
    def test_windir(self):
        out = startup.expand_target(r"%windir%\system32\notepad.exe")
        self.assertTrue(out.lower().endswith(r"system32\notepad.exe"))
        self.assertNotIn("%", out)

    def test_plain_untouched(self):
        self.assertEqual(startup.expand_target(r"C:\a\b.exe"), r"C:\a\b.exe")


class TestTempPath(unittest.TestCase):
    def test_temp_detected(self):
        tmp = os.environ.get("TEMP", "")
        self.assertTrue(startup.is_temp_path(os.path.join(tmp, "x.exe")))

    def test_normal_not_temp(self):
        self.assertFalse(startup.is_temp_path(r"C:\Program Files\App\a.exe"))


# ---------------------------------------------------------------- StartupApproved
class TestApprovalBlob(unittest.TestCase):
    def test_disabled_blob_is_odd(self):
        self.assertTrue(startup.is_disabled_blob(startup.disabled_data()))

    def test_enabled_blob_is_even(self):
        self.assertFalse(startup.is_disabled_blob(bytes([0x02, 0, 0, 0])))

    def test_blob_length(self):
        self.assertEqual(len(startup.disabled_data()), 12)

    def test_empty_blob_means_enabled(self):
        self.assertFalse(startup.is_disabled_blob(b""))

    def test_roundtrip_registry(self):
        """写入 StartupApproved 再删除，验证注册表路径可用。"""
        if os.name != "nt":
            self.skipTest("仅 Windows")
        import winreg
        path = r"Software\SystemToolboxSelfTest"
        name = "SelfTestEntry"
        try:
            k = winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, path, 0,
                                   winreg.KEY_SET_VALUE | winreg.KEY_READ)
            winreg.SetValueEx(k, name, 0, winreg.REG_BINARY, startup.disabled_data())
            k.Close()

            k = winreg.OpenKey(winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_READ)
            blob = winreg.QueryValueEx(k, name)[0]
            k.Close()
            self.assertTrue(startup.is_disabled_blob(blob))

            ok, msg = startup._delete_approval("HKCU", path, name)
            self.assertTrue(ok, msg)
            self.assertIsNone(startup._read_approval("HKCU", path, name))
        finally:
            _clean_reg()

    def test_disable_enable_via_item(self):
        """用临时 StartupApproved 键走一遍 set_enabled 的两条分支。"""
        if os.name != "nt":
            self.skipTest("仅 Windows")
        item = startup.StartupItem(
            uid="test", name="SelfTestEntry", command="x.exe",
            kind="注册表 · 当前用户", location="test",
            hive="HKCU", reg_path=TEST_KEY, value_name="SelfTestEntry",
            approval_path=TEST_KEY)
        try:
            ok, msg = startup.set_enabled(item, False)
            self.assertTrue(ok, msg)
            self.assertFalse(item.enabled)
            blob = startup._read_approval("HKCU", TEST_KEY, "SelfTestEntry")
            self.assertTrue(startup.is_disabled_blob(blob))

            ok, msg = startup.set_enabled(item, True)
            self.assertTrue(ok, msg)
            self.assertTrue(item.enabled)
            self.assertIsNone(startup._read_approval("HKCU", TEST_KEY, "SelfTestEntry"))
        finally:
            _clean_reg()


class TestSchtasksParse(unittest.TestCase):
    TEXT = (
        "文件夹: \\Microsoft\\Windows\\Foo\n"
        "任务名:                             \\Microsoft\\Windows\\Foo\\Bar\n"
        "计划任务状态:                       已启用\n"
        "计划类型:                            登录时\n"
        "要运行的任务:                        C:\\Windows\\System32\\notepad.exe\n"
        "\n"
        "文件夹: \\\n"
        "任务名:                             Boss\n"
        "计划任务状态:                       已禁用\n"
        "计划类型:                            每天\n"
        "要运行的任务:                        C:\\app\\boss.exe\n"
    )

    def test_records(self):
        recs = startup.parse_schtasks_list(self.TEXT)
        self.assertEqual(len(recs), 2)
        self.assertEqual(recs[0]["name"], r"\Microsoft\Windows\Foo\Bar")
        self.assertEqual(recs[0]["type"], "登录时")

    def test_logon_filter(self):
        """只有登录/启动触发的任务才算启动项。"""
        recs = startup.parse_schtasks_list(self.TEXT)
        logon = [r for r in recs
                 if any(t in r.get("type", "").lower() for t in startup._LOGON_TYPES)]
        self.assertEqual(len(logon), 1)
        self.assertIn("Bar", logon[0]["name"])


# ---------------------------------------------------------------- 服务判定
class TestServiceVerdict(unittest.TestCase):
    def test_known_service_tunable(self):
        v, _r, _a, _t = services.verdict_of("SysMain", {"ImagePath": "x"})
        self.assertEqual(v, "可调优")

    def test_core_service_blocked(self):
        v, _r, advice, _t = services.verdict_of(
            "RpcSs", {"ImagePath": r"C:\Windows\system32\svchost.exe"})
        self.assertEqual(v, "核心服务")
        self.assertEqual(advice, "禁止操作")

    def test_known_beats_core(self):
        # Spooler 同时在 KNOWN 与旧 CORE 里，必须以 KNOWN 为准（可提出建议）
        v, _r, _a, _t = services.verdict_of("Spooler", {"ImagePath": "x"})
        self.assertEqual(v, "可调优")

    def test_system_path_with_variable(self):
        # %SystemRoot% 写法是最容易误判成第三方的
        self.assertTrue(services.is_system_path(
            r"%SystemRoot%\system32\svchost.exe -k netsvcs"))
        self.assertTrue(services.is_system_path(r"C:\Windows\System32\a.exe"))
        self.assertFalse(services.is_system_path(r"C:\Program Files\Foo\a.exe"))

    def test_third_party_verdict(self):
        v, _r, _a, third = services.verdict_of(
            "SomeVendorSvc", {"ImagePath": r"C:\Program Files\Vendor\svc.exe"})
        self.assertEqual(v, "第三方")
        self.assertTrue(third)

    def test_start_types(self):
        self.assertEqual(services.START_TYPES[2], "自动")
        self.assertEqual(services.START_TYPES[4], "禁用")

    def test_scan_has_tunable(self):
        svcs = services.scan()
        self.assertGreater(len(svcs), 20)
        self.assertTrue(any(s.verdict == "可调优" for s in svcs))
        # 核心服务不能出现在「可操作」集合里
        for s in svcs:
            if s.verdict == "核心服务":
                self.assertFalse(s.safe_disable, s.name)

    def test_set_start_type_rejects_bad_value(self):
        ok, msg = services.set_start_type("SomeService", 9)
        self.assertFalse(ok)
        self.assertIn("不支持", msg)

    def test_missing_service_no_crash(self):
        ok, _msg = services.set_start_type(
            "SystemToolboxNoSuchService__", 4)
        self.assertFalse(ok)


# ---------------------------------------------------------------- 版本分支
class TestWinVer(unittest.TestCase):
    """版本判定只认 build 号 —— 注册表 ProductName 在 Win11 上还写着 Windows 10。"""

    def test_win10_builds(self):
        for build, name in ((19041, "2004"), (19044, "21H2"), (19045, "22H2")):
            v = winver.detect(build=build, ubr=0, display="", edition="Professional")
            self.assertEqual(v.major, 10, build)
            self.assertEqual(v.family, "win10", build)
            self.assertEqual(v.codename, name, build)
            self.assertFalse(v.is_11, build)
            self.assertEqual(v.full_name, "Windows 10 专业版", build)

    def test_win11_builds(self):
        v = winver.detect(build=22631, ubr=0, display="", edition="Professional")
        self.assertEqual(v.major, 11)
        self.assertEqual(v.family, "win11")
        self.assertTrue(v.is_11)
        self.assertEqual(v.codename, "23H2")
        self.assertEqual(v.full_name, "Windows 11 专业版")

    def test_boundary_22000(self):
        self.assertEqual(winver.detect(build=21999, display="").major, 10)
        self.assertEqual(winver.detect(build=22000, display="").major, 11)

    def test_display_version_wins(self):
        v = winver.detect(build=26300, ubr=9711, display="26H2", edition="Professional")
        self.assertEqual(v.codename, "26H2")
        self.assertEqual(v.version_text, "26H2 · 26300.9711")
        self.assertIn("Windows 11 专业版", v.label)

    def test_unknown_build_falls_back_to_nearest(self):
        v = winver.detect(build=26500, display="")
        self.assertEqual(v.major, 11)
        self.assertEqual(v.codename, "26H2")     # 超出已知表时取最高一档，不返回空

    def test_supports(self):
        v10 = winver.detect(build=19045, display="")
        v11 = winver.detect(build=22631, display="")
        self.assertTrue(v10.supports("any"))
        self.assertTrue(v10.supports("win10"))
        self.assertFalse(v10.supports("win11"))
        self.assertTrue(v11.supports("win11"))
        self.assertFalse(v11.supports("win10"))
        self.assertFalse(v10.supports("any", min_build=22000))
        self.assertTrue(v11.supports("any", min_build=22000))

    def test_why_not_text(self):
        v10 = winver.detect(build=19045, display="")
        self.assertIn("仅 Windows 11", v10.why_not("win11"))
        self.assertEqual(v10.why_not("any"), "")
        self.assertIn("build", v10.why_not("any", min_build=22000))

    def test_unknown_edition_keeps_base_name(self):
        v = winver.detect(build=19045, display="", edition="SomeWeirdEdition")
        self.assertEqual(v.edition, "")
        self.assertEqual(v.full_name, "Windows 10")

    def test_current_is_windows(self):
        v = winver.current(force=True)
        self.assertIn(v.major, (10, 11))
        self.assertGreater(v.build, 0)
        self.assertTrue(v.label.startswith("Windows"))


class TestTweakVersioning(unittest.TestCase):
    """微调项的 os 标注：给另一代系统准备的项必须点不动。"""

    def test_os_tags_valid(self):
        for t in tweaks.TWEAKS:
            self.assertIn(t.os, tweaks.OS_TAGS, t.key)

    def test_both_branches_covered(self):
        w10 = [t for t in tweaks.TWEAKS if t.os == "win10"]
        w11 = [t for t in tweaks.TWEAKS if t.os == "win11"]
        self.assertGreaterEqual(len(w10), 2, "必须有为 Win10 准备的项")
        self.assertGreaterEqual(len(w11), 2, "必须有为 Win11 准备的项")
        v10 = winver.detect(build=19045, display="")
        v11 = winver.detect(build=22631, display="")
        self.assertGreaterEqual(len(tweaks.usable(v10)), 5)
        self.assertGreaterEqual(len(tweaks.usable(v11)), 5)
        self.assertEqual(len(tweaks.usable(v10)) + len(tweaks.others(v10)),
                         len(tweaks.TWEAKS))

    def test_exclusive_items(self):
        v10 = winver.detect(build=19045, display="")
        v11 = winver.detect(build=22631, display="")
        for t in tweaks.TWEAKS:
            if t.os == "win11":
                self.assertFalse(t.applies_to(v10), t.key)
                self.assertTrue(t.applies_to(v11), t.key)
                self.assertTrue(t.why_not(v10), t.key)
            elif t.os == "win10":
                self.assertTrue(t.applies_to(v10), t.key)
                self.assertFalse(t.applies_to(v11), t.key)
                self.assertTrue(t.why_not(v11), t.key)
            else:
                self.assertTrue(t.applies_to(v10), t.key)
                self.assertTrue(t.applies_to(v11), t.key)

    def test_min_build(self):
        long_paths = tweaks.by_key("long_paths")
        self.assertFalse(long_paths.applies_to(winver.detect(build=10586, display="")))
        self.assertTrue(long_paths.applies_to(winver.detect(build=14393, display="")))

    def test_stats_keys(self):
        st = tweaks.stats(winver.detect(build=22631, display=""))
        for k in ("total", "usable", "other", "applied", "w10", "w11"):
            self.assertIn(k, st)
        self.assertEqual(st["usable"] + st["other"], st["total"])

    def test_apply_refuses_other_branch(self):
        """不适用项必须连注册表都不碰（不能被写成 0/1 后留个假开关）。"""
        if os.name != "nt":
            self.skipTest("仅 Windows")
        cur = winver.current()
        other = "win10" if cur.is_11 else "win11"
        t = tweaks.Tweak("__os_probe__", "自检项", "单元测试用", hive="HKCU",
                         path=TEST_KEY, value="ProbeOs", vtype=tweaks.REG_DWORD,
                         on=0, off=1, os=other)
        try:
            ok, msg = tweaks.apply(t)
            self.assertFalse(ok, "不适用项不该返回成功")
            self.assertTrue(msg)
            exists, _v, _t = tweaks.read_value("HKCU", TEST_KEY, "ProbeOs")
            self.assertFalse(exists, "不适用项不能写注册表")
            self.assertNotIn("__os_probe__", tweaks.load_backup(),
                             "不适用项不该留下备份记录")
        finally:
            _clean_reg()
            _clear_tweak_backup("__os_probe__")


# ---------------------------------------------------------------- 系统信息
class TestSysinfo(unittest.TestCase):
    def test_os_name(self):
        name, ver = sysinfo.os_name()
        self.assertIn("Windows", name)
        self.assertTrue(ver)

    def test_memory(self):
        total, avail, pct = sysinfo.memory()
        self.assertGreater(total, 0)
        self.assertLessEqual(avail, total)
        self.assertGreaterEqual(pct, 0)

    def test_uptime(self):
        self.assertGreater(sysinfo.uptime(), 0)

    def test_physical_cores_reasonable(self):
        cores = sysinfo._physical_cores()
        logical = os.cpu_count() or 1
        self.assertTrue(0 < cores <= logical, f"cores={cores} logical={logical}")

    def test_duration_text(self):
        self.assertEqual(sysinfo.human_duration(0), "0 分")
        self.assertTrue(sysinfo.human_duration(3700).endswith("分"))
        self.assertIn("天", sysinfo.human_duration(90000))

    def test_collect(self):
        snap = sysinfo.collect()
        self.assertTrue(snap.host)
        self.assertGreater(len(snap.os_rows), 2)
        self.assertGreater(len(snap.hw_rows), 2)
        self.assertGreater(snap.proc_total, 0)
        # 系统表里必须有一行告诉用户「按哪一代系统呈现」
        self.assertIn("适配分支", [r.label for r in snap.os_rows])
        self.assertIsNotNone(snap.ver)

    def test_branch_info(self):
        info = sysinfo.branch_info()
        self.assertEqual(info.label, "适配分支")
        self.assertIn("Win", info.value)


# ---------------------------------------------------------------- 微调
class TestTweaks(unittest.TestCase):
    def test_definitions_sane(self):
        keys = [t.key for t in tweaks.TWEAKS]
        self.assertEqual(len(keys), len(set(keys)), "key 必须唯一")
        for t in tweaks.TWEAKS:
            self.assertTrue(t.name and t.desc, t.key)
            self.assertIn(t.risk, ("低", "中", "高"), t.key)
            if t.kind == "reg":
                self.assertTrue(t.path and t.value, t.key)
                self.assertIn(t.vtype, (tweaks.REG_SZ, tweaks.REG_DWORD), t.key)
            else:
                self.assertEqual(t.kind, "power", t.key)

    def test_admin_items_marked(self):
        long_paths = tweaks.by_key("long_paths")
        self.assertIsNotNone(long_paths)
        self.assertTrue(long_paths.admin)

    def test_unknown_key(self):
        self.assertIsNone(tweaks.by_key("no_such_key"))

    def test_apply_revert_roundtrip(self):
        """在临时键上验证「改 → 还原」后值与动手前完全一致。"""
        if os.name != "nt":
            self.skipTest("仅 Windows")
        t = tweaks.Tweak("__selftest__", "自检项", "单元测试用", hive="HKCU",
                         path=TEST_KEY, value="Probe", vtype=tweaks.REG_SZ,
                         on="1", off="0")
        try:
            # 情形一：原来没有这个值 → 还原后必须重新消失
            ok, msg = tweaks.apply(t)
            self.assertTrue(ok, msg)
            exists, val, _typ = tweaks.read_value("HKCU", TEST_KEY, "Probe")
            self.assertTrue(exists)
            self.assertEqual(val, "1")
            self.assertEqual(t.state(), "已优化")

            ok, msg = tweaks.revert(t)
            self.assertTrue(ok, msg)
            exists, _val, _typ = tweaks.read_value("HKCU", TEST_KEY, "Probe")
            self.assertFalse(exists, "原值本不存在，还原后应删除")

            # 情形二：原来有值 → 还原后必须写回原值
            # （备份是「首次改动前记录、之后不覆盖」，所以要先把上一轮的记录清掉）
            _clear_tweak_backup("__selftest__")
            tweaks._write_value("HKCU", TEST_KEY, "Probe", "400", tweaks.REG_SZ)
            ok, _msg = tweaks.apply(t)
            self.assertTrue(ok)
            ok, _msg = tweaks.revert(t)
            self.assertTrue(ok)
            _e, val, _typ = tweaks.read_value("HKCU", TEST_KEY, "Probe")
            self.assertEqual(val, "400")
        finally:
            _clean_reg()
            _clear_tweak_backup("__selftest__")

    def test_state_without_value(self):
        t = tweaks.by_key("startup_delay")
        # 该值默认不存在，状态必须是「未优化」而不是报错
        self.assertIn(t.state(), ("已优化", "未优化", "未知"))

    def test_current_text_no_crash(self):
        for t in tweaks.TWEAKS:
            self.assertIsInstance(t.current_text(), str)


if __name__ == "__main__":
    unittest.main()
