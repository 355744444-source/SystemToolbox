# -*- coding: utf-8 -*-
"""v1.15.0 驱动体检单测：投影函数、签名缓存、结论分级。

只测 core，不碰 Qt。需要真实文件的用例放临时目录，
**缓存路径一律显式注入临时目录**（不能写到 %LOCALAPPDATA%）。
"""
import os
import struct
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core import drivers as drv          # noqa: E402
from app.core import sign as sg              # noqa: E402


class ImagePathTest(unittest.TestCase):
    """ImagePath → 真实路径。本机 461 个服务里这四种写法都有。"""

    def setUp(self):
        self.root = os.environ.get("SystemRoot", r"C:\Windows")

    def test_systemroot(self):
        got = drv.resolve_image(r"\SystemRoot\System32\drivers\tcpip.sys")
        self.assertEqual(got.lower(),
                         os.path.join(self.root, "System32", "drivers", "tcpip.sys").lower())

    def test_relative_system32(self):
        got = drv.resolve_image(r"System32\drivers\beep.sys")
        self.assertTrue(got.lower().endswith(r"system32\drivers\beep.sys"))

    def test_nt_prefix(self):
        got = drv.resolve_image(r"\??\C:\Windows\System32\drivers\x.sys")
        self.assertEqual(got.lower(), r"c:\windows\system32\drivers\x.sys")

    def test_quoted(self):
        got = drv.resolve_image(r'"C:\Windows\System32\drivers\x.sys" -k foo')
        self.assertEqual(got.lower(), r"c:\windows\system32\drivers\x.sys")

    def test_trailing_args(self):
        got = drv.resolve_image(r"C:\Windows\System32\drivers\x.sys /debug")
        self.assertTrue(got.lower().endswith("x.sys"))

    def test_empty_is_empty(self):
        """ImagePath 为空是内置驱动的正常写法（Ntfs/Beep），不是路径错误。"""
        self.assertEqual(drv.resolve_image(""), "")
        self.assertEqual(drv.resolve_image("   "), "")


class FileTimeTest(unittest.TestCase):
    def _raw(self, dt):
        import datetime
        base = datetime.datetime(1601, 1, 1)
        ticks = int((dt - base).total_seconds() * 10_000_000)
        return struct.pack("<Q", ticks)

    def test_roundtrip(self):
        import datetime
        raw = self._raw(datetime.datetime(2026, 10, 22, 15, 30))
        self.assertEqual(drv._filetime_text(raw), "2026-10-22")

    def test_zero_is_blank(self):
        """0 = 从未导入，不是 1601 年。"""
        self.assertEqual(drv._filetime_text(struct.pack("<Q", 0)), "")

    def test_garbage(self):
        self.assertEqual(drv._filetime_text(None), "")
        self.assertEqual(drv._filetime_text(b"\x01\x02"), "")
        self.assertEqual(drv._filetime_text(struct.pack("<Q", 2)), "")   # 1601 年，不合理


class PackageTest(unittest.TestCase):
    def test_oem_is_third_party(self):
        self.assertTrue(drv.DriverPackage(key="a", inf="oem12.inf").third_party)
        self.assertFalse(drv.DriverPackage(key="a", inf="acpi.inf").third_party)
        # 只有 oemNN.inf 这个形态才算，`oemfoo.inf` 不算
        self.assertFalse(drv.DriverPackage(key="a", inf="oemfoo.inf").third_party)

    def test_signer_short_and_full(self):
        p = drv.DriverPackage(
            key="a", inf="oem1.inf",
            signer="Microsoft Windows Hardware Compatibility Publisher")
        self.assertEqual(p.signer_text, "Microsoft WHQL")
        self.assertEqual(p.signer_full, p.signer)

    def test_signer_fallback(self):
        builtin = drv.DriverPackage(key="a", inf="acpi.inf")
        self.assertEqual(builtin.signer_text, "Microsoft")
        self.assertIn("系统内置", builtin.signer_full)
        third = drv.DriverPackage(key="a", inf="oem2.inf")
        self.assertEqual(third.signer_text, "（未记录）")

    def test_size_text(self):
        self.assertEqual(drv.DriverPackage(key="a", size=0).size_text, "—")
        self.assertEqual(drv.DriverPackage(key="a", size=37 * 1024).size_text, "37 KB")
        self.assertEqual(drv.DriverPackage(key="a", size=5 * 1048576).size_text, "5.0 MB")
        # 小于 1KB 也要说 1KB，不能显示 0KB
        self.assertEqual(drv.DriverPackage(key="a", size=100).size_text, "1 KB")

    def test_source_shortened(self):
        long_src = r"C:\Users\MSI\AppData\Local\Temp\game-power\sound-headset\driver\m1"
        p = drv.DriverPackage(key="a", source=long_src)
        self.assertLess(len(p.source_text), len(long_src))
        self.assertIn("…", p.source_text)
        self.assertEqual(drv.DriverPackage(key="a").source_text, "—")


class ServiceTest(unittest.TestCase):
    def test_texts(self):
        s = drv.DriverService(name="tcpip", start=0, type=1)
        self.assertEqual(s.start_text, "引导")
        self.assertEqual(s.type_text, "内核驱动")
        self.assertEqual(drv.DriverService(name="x", start=9).start_text, "未知(9)")

    def test_has_file(self):
        """空 ImagePath 不参与「文件缺失」判定 —— 本机 12 个内置驱动就是这样。"""
        self.assertFalse(drv.DriverService(name="Ntfs", image="", file="").has_file)
        self.assertTrue(drv.DriverService(name="tcpip", file=r"C:\x.sys").has_file)


class LevelTest(unittest.TestCase):
    @staticmethod
    def _f(kind, loaded=False):
        return drv.DriverFile(path=r"C:\x.sys", loaded=loaded,
                              info=sg.SignInfo(path=r"C:\x.sys", kind=kind))

    def test_unsigned_loaded_is_crit(self):
        self.assertEqual(self._f("uncatalogued", True).level, "crit")
        self.assertEqual(self._f("bad", True).level, "crit")

    def test_unsigned_idle_is_warn(self):
        self.assertEqual(self._f("uncatalogued").level, "warn")
        self.assertTrue(self._f("uncatalogued").problem)

    def test_warn_kind_follows_loaded(self):
        """证书链有问题的驱动：在跑才抬到警示档（本机 OrayUSBVHCI.sys 就是）。"""
        self.assertEqual(self._f("warn").level, "info")
        self.assertEqual(self._f("warn", True).level, "warn")

    def test_ok_and_missing(self):
        self.assertEqual(self._f("ok").level, "ok")
        self.assertFalse(self._f("ok").problem)
        none = drv.DriverFile(path=r"C:\x.sys")
        self.assertEqual(none.level, "faint")
        self.assertEqual(none.level_text, "未扫描")
        self.assertFalse(none.problem)

    def test_start_text(self):
        self.assertEqual(drv.DriverFile(path=r"C:\x.sys", start=3).start_text, "手动")
        self.assertEqual(drv.DriverFile(path=r"C:\x.sys").start_text, "—")


class StoppedTest(unittest.TestCase):
    def test_accepts_list_and_callable(self):
        self.assertFalse(drv._stopped(None))
        self.assertFalse(drv._stopped([False]))
        self.assertTrue(drv._stopped([True]))
        self.assertTrue(drv._stopped(lambda: True))
        self.assertFalse(drv._stopped(lambda: False))


class ScanCacheTest(unittest.TestCase):
    """缓存键是 (大小, mtime)：文件没变就不重扫。"""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="drvtest_")
        self.cache = os.path.join(self.dir, "c.json")
        self.files = []
        for i in range(3):
            p = os.path.join(self.dir, "f%d.sys" % i)
            with open(p, "wb") as fh:
                fh.write(b"MZ" + bytes([i]) * 64)
            self.files.append(p)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_first_pass_then_cache_hit(self):
        out1, hits1, _cut1 = drv.scan_signatures(self.files, cache_path=self.cache)
        self.assertEqual(len(out1), 3)
        self.assertEqual(hits1, 0)
        self.assertTrue(os.path.isfile(self.cache))

        out2, hits2, _cut2 = drv.scan_signatures(self.files, cache_path=self.cache)
        self.assertEqual(len(out2), 3)
        self.assertEqual(hits2, 3, "第二次应当全部命中缓存")

    def test_touch_invalidates_one(self):
        drv.scan_signatures(self.files, cache_path=self.cache)
        p = self.files[0]
        st = os.stat(p)
        with open(p, "ab") as fh:        # 改了大小 → 必须重扫
            fh.write(b"XX")
        out, hits, _cut = drv.scan_signatures(self.files, cache_path=self.cache)
        self.assertEqual(hits, 2)
        self.assertEqual(len(out), 3)
        self.assertGreater(os.stat(p).st_size, st.st_size)

    def test_missing_file_reported(self):
        ghost = os.path.join(self.dir, "nope.sys")
        out, hits, _cut = drv.scan_signatures([ghost], cache_path=self.cache)
        self.assertEqual(hits, 0)
        self.assertEqual(out[0].kind, "missing")

    def test_stop_flag_no_result(self):
        out, _hits, cut = drv.scan_signatures(self.files, stop_flag=[True],
                                              cache_path=self.cache)
        self.assertEqual(out, [])
        self.assertTrue(cut)

    def test_budget_zero(self):
        out, _hits, cut = drv.scan_signatures(self.files, budget_s=0,
                                              cache_path=self.cache)
        self.assertEqual(out, [])
        self.assertTrue(cut)


class ReportTest(unittest.TestCase):
    def _rep(self):
        bad_loaded = drv.DriverFile(path=r"C:\a\u1.sys", loaded=True,
                                    info=sg.SignInfo(path=r"C:\a\u1.sys",
                                                     kind="uncatalogued"))
        bad_idle = drv.DriverFile(path=r"C:\a\u2.sys", loaded=False,
                                  info=sg.SignInfo(path=r"C:\a\u2.sys", kind="bad"))
        warn_live = drv.DriverFile(path=r"C:\a\w.sys", loaded=True,
                                   info=sg.SignInfo(path=r"C:\a\w.sys", kind="warn",
                                                    note="证书链不完整"))
        ok = drv.DriverFile(path=r"C:\a\ok.sys", loaded=True,
                            info=sg.SignInfo(path=r"C:\a\ok.sys", kind="ok"))
        miss = drv.DriverService(name="Ghost", start=3, file=r"C:\a\ghost.sys",
                                 exists=False)
        pkg = drv.DriverPackage(key="k", inf="oem3.inf", original_inf="amd.inf",
                                signer="Microsoft Windows Hardware Compatibility "
                                       "Publisher", installed="2026-10-22")
        return drv.Report(packages=[pkg], services=[miss],
                          files=[bad_loaded, bad_idle, warn_live, ok], modules=200)

    def test_counts(self):
        rep = self._rep()
        self.assertEqual(len(rep.unsigned), 2)
        self.assertEqual(len(rep.unsigned_loaded), 1)
        self.assertEqual(len(rep.suspicious_sign), 1)
        self.assertEqual(len(rep.missing_files), 1)
        self.assertEqual(rep.loaded_count, 3)      # u1 / w / ok

    def test_findings_severity_order(self):
        out = drv.build_findings(self._rep())
        sevs = [f.severity for f in out]
        self.assertEqual(sevs[0], "crit", "已加载的无签名驱动必须排第一")
        self.assertIn("warn", sevs)          # 未签名未加载 / 签名可疑在跑 / 文件缺失
        self.assertIn("info", sevs)          # 第三方包统计
        self.assertEqual(sevs, sorted(sevs, key=lambda s: {"crit": 0, "warn": 1,
                                                           "info": 2, "ok": 3}[s]))

    def test_advice_is_list(self):
        """FindingCard 逐条渲染 advice；传字符串会被逐字拆成竖排（踩过）。"""
        for f in drv.build_findings(self._rep()):
            self.assertIsInstance(f.advice, list, f.topic)
            for a in f.advice:
                self.assertGreater(len(a), 4, "%s 的建议太短，可能被当成字符拆开了" % f.topic)

    def test_verdict_worst_wins(self):
        level, text = drv.verdict(self._rep())
        self.assertEqual(level, "crit")
        self.assertIn("无签名驱动正在内核里运行", text)

    def test_all_clean(self):
        ok = drv.DriverFile(path=r"C:\a\ok.sys",
                            info=sg.SignInfo(path=r"C:\a\ok.sys", kind="ok"))
        rep = drv.Report(files=[ok], packages=[drv.DriverPackage(key="k",
                                                                 inf="acpi.inf")])
        out = drv.build_findings(rep)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].severity, "ok")
        self.assertEqual(drv.verdict(rep)[0], "ok")

    def test_third_party_summary(self):
        out = drv.build_findings(self._rep())
        third = [f for f in out if f.topic == "drv-third"]
        self.assertEqual(len(third), 1)
        self.assertIn("2026-10-22", third[0].detail)
        self.assertIn("Microsoft WHQL", third[0].detail)

    def test_builtin_count(self):
        rep = self._rep()
        rep.packages.append(drv.DriverPackage(key="k2", inf="acpi.inf"))
        self.assertEqual(rep.builtin_count, 1)
        self.assertEqual(len(rep.third_packages), 1)


class AuditTest(unittest.TestCase):
    def test_audit_without_sign(self):
        """体检页「全部体检」串行跑十项，但 core 必须支持不扫签名（快）。"""
        rep = drv.audit(want_sign=False)
        self.assertGreater(len(rep.packages), 0, "读不到驱动包数据库")
        self.assertGreater(len(rep.services), 0, "读不到驱动服务")
        self.assertGreater(rep.modules, 0, "EnumDeviceDrivers 一个模块都没拿到")
        # 不扫签名也要给出文件清单（服务引用关系），只是 info 全为空
        self.assertGreater(len(rep.files), 0, "服务引用的文件应当照样列出来")
        self.assertTrue(all(f.info is None for f in rep.files),
                        "want_sign=False 时不该有任何签名结论")
        self.assertEqual(rep.unsigned, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
