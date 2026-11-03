"""v1.7.0 单元测试：磁盘 SMART 解析（smart.py）与硬件错误聚合（hwevent.py）。

重点覆盖那些「看起来能跑但会输出假数据」的地方：
- NVMe 数据单位换算（1 单位 = 1000 × 512 字节，不是 512）
- Critical Warning 的位解析（不是简单非零）
- CPER 头部偏移的交叉校验（长度对不上必须判未识别）
- Section GUID 的「先后顺序」决定主部件（每条记录都带处理器上下文）
"""
from __future__ import annotations

import struct
import unittest
from datetime import datetime, timedelta

from app.core import hwevent, smart


# ------------------------------------------------------------------ 构造工具

def make_nvme(cw: int = 0, temp_c: int = 35, spare: int = 100, thr: int = 5,
              used: int = 0, read_units: int = 1000, write_units: int = 2000,
              hours: int = 100, cycles: int = 10, unsafe: int = 0,
              media: int = 0, errlog: int = 0, size: int = 512) -> str:
    """造一页 NVMe SMART/Health Log 的十六进制串。"""
    d = bytearray(size)
    d[0] = cw
    struct.pack_into("<H", d, 1, 273 + temp_c if temp_c else 0)
    d[3] = spare
    d[4] = thr
    d[5] = used
    d[32:48] = read_units.to_bytes(16, "little")
    d[48:64] = write_units.to_bytes(16, "little")
    d[128:144] = hours.to_bytes(16, "little")
    d[144:160] = cycles.to_bytes(16, "little")
    d[160:176] = unsafe.to_bytes(16, "little")
    d[176:192] = media.to_bytes(16, "little")
    d[192:200] = errlog.to_bytes(8, "little")
    if size >= 202:
        struct.pack_into("<H", d, 200, 273 + temp_c)
    return d.hex()


def make_cper(sev: int = 2, rlen: int = 952, parts: tuple[str, ...] = ()) -> str:
    """造一条 CPER 记录；parts 是 Section GUID 的 raw hex，按顺序贴在 0x60 之后。"""
    d = bytearray(rlen)
    d[0:4] = b"CPER"
    d[0x0C:0x0E] = len(parts).to_bytes(2, "little")
    d[0x10:0x14] = sev.to_bytes(4, "little")
    d[0x14:0x18] = rlen.to_bytes(4, "little")
    pos = 0x60
    for g in parts:
        d[pos:pos + 16] = bytes.fromhex(g)
        pos += 32
    return d.hex()


PCIE = "54e995d9c1bb0f43ad91b44dcb3c6f35"
CPU_GEN = "adcc7698b447db4bb65e16f193c4f3db"
MEM = "1411bca5646fde4eb8633e83ed7c83b1"


# ------------------------------------------------------------------ NVMe 解析

class TestNvmeParse(unittest.TestCase):
    def test_basic_fields(self):
        lg = smart.parse_nvme_log(make_nvme(temp_c=36, used=2, spare=100,
                                            thr=5, hours=4095, cycles=47))
        self.assertIsNotNone(lg)
        self.assertEqual(lg.temp_c, 36)
        self.assertEqual(lg.percent_used, 2)
        self.assertEqual(lg.avail_spare, 100)
        self.assertEqual(lg.hours, 4095)
        self.assertEqual(lg.cycles, 47)

    def test_life_left(self):
        self.assertEqual(smart.parse_nvme_log(make_nvme(used=2)).life_left, 98)
        self.assertEqual(smart.parse_nvme_log(make_nvme(used=100)).life_left, 0)
        # 规范允许 reported 超过 100，剩余要夹到 0 不能变负数
        self.assertEqual(smart.parse_nvme_log(make_nvme(used=120)).life_left, 0)

    def test_data_units_conversion(self):
        """NVMe 的 Data Units 是 1000 × 512 字节，不是 512 字节。

        写成 read_units * 512 会让结果小 1000 倍，而数字看起来仍然「像样」，
        这种错最难发现，所以要固定住口径。
        """
        units = 1_000_000
        lg = smart.parse_nvme_log(make_nvme(read_units=units, write_units=units))
        want = units * 512 * 1000 / 1e9      # = 512 GB
        self.assertAlmostEqual(lg.read_gb, want, places=1)
        self.assertAlmostEqual(lg.read_gb, 512.0, places=1)

    def test_critical_warning_bits(self):
        lg = smart.parse_nvme_log(make_nvme(cw=0x0C))
        self.assertEqual(len(lg.crit_names), 2)
        self.assertIn("可靠性已降级", lg.crit_names)
        self.assertIn("介质已置为只读", lg.crit_names)

    def test_critical_warning_zero(self):
        self.assertEqual(smart.parse_nvme_log(make_nvme(cw=0)).crit_names, [])

    def test_temperature_zero_not_minus_273(self):
        """温度字段为 0 表示不支持，不能显示成 -273℃。"""
        lg = smart.parse_nvme_log(make_nvme(temp_c=0))
        self.assertEqual(lg.temp_c, 0)

    def test_bad_input(self):
        self.assertIsNone(smart.parse_nvme_log(""))
        self.assertIsNone(smart.parse_nvme_log("zz"))
        self.assertIsNone(smart.parse_nvme_log("00" * 50))
        self.assertIsNone(smart.parse_nvme_log("00" * 199))

    def test_short_but_parseable(self):
        """216 字节是本机实际长度；200 字节也要能解析（只是没有温度传感器）。"""
        lg = smart.parse_nvme_log(make_nvme(size=200))
        self.assertIsNotNone(lg)
        self.assertEqual(lg.sensors_c, [])


# ------------------------------------------------------------------ 判定分级

class TestJudge(unittest.TestCase):
    def _judge(self, **kw) -> smart.Disk:
        d = smart.Disk(product="TEST", serial="SN")
        d.log = smart.parse_nvme_log(make_nvme(**kw))
        smart._judge(d)
        return d

    def test_healthy(self):
        d = self._judge(used=2, temp_c=36)
        self.assertEqual(d.level, "ok")

    def test_critical_warning_wins(self):
        d = self._judge(cw=0x08, used=99, temp_c=90)
        self.assertEqual(d.level, "crit")
        self.assertIn("只读", d.status)

    def test_media_errors(self):
        d = self._judge(media=3)
        self.assertEqual(d.level, "crit")
        self.assertIn("介质错误", d.status)

    def test_spare_below_threshold(self):
        d = self._judge(spare=5, thr=10)
        self.assertEqual(d.level, "crit")

    def test_spare_equal_threshold_is_crit(self):
        d = self._judge(spare=5, thr=5)
        self.assertEqual(d.level, "crit")

    def test_percent_used_tiers(self):
        self.assertEqual(self._judge(used=96).level, "crit")
        self.assertEqual(self._judge(used=95).level, "crit")
        self.assertEqual(self._judge(used=85).level, "warn")
        self.assertEqual(self._judge(used=80).level, "warn")
        self.assertEqual(self._judge(used=60).level, "info")
        self.assertEqual(self._judge(used=59).level, "ok")

    def test_temperature_tiers(self):
        self.assertEqual(self._judge(temp_c=85).level, "crit")
        self.assertEqual(self._judge(temp_c=80).level, "crit")
        self.assertEqual(self._judge(temp_c=75).level, "warn")
        self.assertEqual(self._judge(temp_c=70).level, "warn")
        self.assertEqual(self._judge(temp_c=69).level, "ok")

    def test_unsafe_shutdown_ratio(self):
        """异常掉电要按比例判：通电 100 次里掉电 60 次才算问题。"""
        self.assertEqual(self._judge(unsafe=60, cycles=100).level, "warn")
        # 只通电过 3 次时，掉电 2 次不该报
        self.assertEqual(self._judge(unsafe=2, cycles=3).level, "ok")

    def test_no_log_is_info(self):
        d = smart.Disk(product="SATA", serial="X")
        smart._judge(d)
        self.assertEqual(d.level, "info")
        self.assertIn("无 SMART", d.status)


class TestDiskText(unittest.TestCase):
    def test_gb_text(self):
        self.assertEqual(smart._gb_text(500), "500 GB")
        self.assertEqual(smart._gb_text(2048), "2.00 TB")

    def test_remain_text_without_samples(self):
        d = smart.Disk()
        d.log = smart.parse_nvme_log(make_nvme(used=50))
        d.per_day_gb = 0
        self.assertEqual(d.remain_text, "—")

    def test_remain_text_with_zero_used(self):
        d = smart.Disk()
        d.log = smart.parse_nvme_log(make_nvme(used=0))
        d.per_day_gb = 10
        self.assertEqual(d.remain_text, "样本不足")

    def test_remain_text_normal(self):
        # 已用 2%、写入 20000 GB → 推算总量 1,000,000 GB；剩 980,000 GB
        # 日均 100 GB → 9800 天 ≈ 26.8 年
        d = smart.Disk()
        d.log = smart.parse_nvme_log(make_nvme(used=2, write_units=39_062_500))
        self.assertAlmostEqual(d.log.write_gb, 20000.0, delta=1)
        d.per_day_gb = 100
        self.assertIn("年", d.remain_text)

    def test_bus_from_miniport(self):
        self.assertEqual(smart._bus_from_miniport("stornvme"), "NVMe")
        self.assertEqual(smart._bus_from_miniport("storahci"), "SATA")
        self.assertEqual(smart._bus_from_miniport("USBSTOR"), "USB")
        self.assertEqual(smart._bus_from_miniport(""), "-")

    def test_io_event_ids_are_known(self):
        for key in (("disk", 7), ("disk", 51), ("disk", 153), ("disk", 157)):
            self.assertIn(key, smart.IO_EVENT_IDS)
            name, level, hint = smart.IO_EVENT_IDS[key]
            self.assertTrue(name and hint)
            self.assertIn(level, ("crit", "warn", "info"))

    def test_event_id_alone_is_not_enough(self):
        """本机实测：System 里的 ID 7 来自 Hyper-V-VmSwitch，是网卡初始化。

        只按 ID 判定会把 17 条「网卡初始化成功」统计成「磁盘有坏块」，
        这是这段代码最危险的一个坑，必须钉住。
        """
        self.assertIsNone(smart.io_kind("Microsoft-Windows-Hyper-V-VmSwitch", 7))
        self.assertIsNotNone(smart.io_kind("disk", 7))
        self.assertEqual(smart.io_kind("disk", 7)[1], "crit")

    def test_reset_provider_set(self):
        self.assertIsNotNone(smart.io_kind("UASPStor", 129))
        self.assertIsNotNone(smart.io_kind("storahci", 129))
        self.assertIsNotNone(smart.io_kind("stornvme", 129))
        # 别的 provider 报 129 与磁盘无关
        self.assertIsNone(smart.io_kind("Hyper-V-VmSwitch", 129))

    def test_unknown_event_ignored(self):
        self.assertIsNone(smart.io_kind("disk", 999))
        self.assertIsNone(smart.io_kind("", 153))

    def test_dev_label(self):
        self.assertEqual(smart._dev_label({"#0": "\\Device\\Harddisk1\\DR2"}),
                         "磁盘 1")
        self.assertEqual(smart._dev_label({"#0": "\\Device\\RaidPort1"}),
                         "RAID 端口 1")
        self.assertEqual(smart._dev_label({}), "")


class TestSmartReport(unittest.TestCase):
    def test_headline_all_ok(self):
        r = smart.Report(disks=[smart.Disk(level="ok")])
        self.assertIn("全部健康", r.headline)

    def test_headline_risks(self):
        r = smart.Report(disks=[smart.Disk(level="crit"), smart.Disk(level="ok")])
        self.assertIn("立即处理", r.headline)
        self.assertEqual(r.worst, "crit")

    def test_headline_empty(self):
        self.assertIn("没有读到", smart.Report().headline)

    def test_io_total(self):
        r = smart.Report(io_counts={153: 5, 7: 2})
        self.assertEqual(r.io_total, 7)


# ------------------------------------------------------------------ CPER 解析

class TestCperParse(unittest.TestCase):
    def test_basic(self):
        cp = hwevent.parse_cper(make_cper(sev=2, rlen=952, parts=(PCIE,)), 952)
        self.assertIsNotNone(cp)
        self.assertTrue(cp.ok)
        self.assertEqual(cp.severity, 2)
        self.assertEqual(cp.severity_text, "已纠正")
        self.assertEqual(cp.record_length, 952)
        self.assertEqual(cp.section_count, 1)
        self.assertEqual(cp.part, "PCIe 设备/链路")

    def test_length_mismatch_marks_unverified(self):
        """偏移读错时必须判未识别，否则会输出错误部件。"""
        cp = hwevent.parse_cper(make_cper(rlen=952), 999)
        self.assertFalse(cp.ok)

    def test_no_declared_length(self):
        cp = hwevent.parse_cper(make_cper(rlen=952), 0)
        self.assertTrue(cp.ok)

    def test_not_cper(self):
        self.assertIsNone(hwevent.parse_cper("0011223344"))
        self.assertIsNone(hwevent.parse_cper(""))
        self.assertIsNone(hwevent.parse_cper("zz"))
        self.assertIsNone(hwevent.parse_cper("00" * 10))

    def test_severity_tiers(self):
        expect = {0: "可恢复", 1: "致命", 2: "已纠正", 3: "通知"}
        for sev, name in expect.items():
            cp = hwevent.parse_cper(make_cper(sev=sev, parts=(PCIE,)), 952)
            self.assertEqual(cp.severity_text, name)

    def test_severity_levels(self):
        self.assertEqual(
            hwevent.parse_cper(make_cper(sev=1), 952).severity_level, "crit")
        self.assertEqual(
            hwevent.parse_cper(make_cper(sev=2), 952).severity_level, "info")

    def test_first_guid_is_primary(self):
        """PCIe 在前时主部件是 PCIe，处理器只作上下文（本机 194 条全是这样）。"""
        cp = hwevent.parse_cper(make_cper(parts=(PCIE, CPU_GEN)), 952)
        self.assertEqual(cp.part, "PCIe 设备/链路")
        self.assertIn("处理器", cp.context)

    def test_order_reversed(self):
        cp = hwevent.parse_cper(make_cper(parts=(CPU_GEN, PCIE)), 952)
        self.assertEqual(cp.part, "处理器（通用）")
        self.assertIn("PCIe", cp.context)

    def test_memory_section(self):
        cp = hwevent.parse_cper(make_cper(parts=(MEM,)), 952)
        self.assertEqual(cp.part, "内存")

    def test_unknown_sections(self):
        cp = hwevent.parse_cper(make_cper(parts=()), 952)
        self.assertEqual(cp.part, "未识别部件")


# ------------------------------------------------------------------ 聚合

class TestAggregate(unittest.TestCase):
    def _rec(self, sev: int = 2, part: str = "PCIe 设备/链路",
             days_ago: float = 1) -> hwevent.ErrRec:
        return hwevent.ErrRec(
            time=datetime.now() - timedelta(days=days_ago), event_id=20,
            severity=sev, part=part, length=952, ok=True)

    def test_grouping(self):
        recs = [self._rec(), self._rec(), self._rec(part="内存")]
        rows = hwevent._aggregate(recs)
        self.assertEqual(len(rows), 2)
        pcie = [r for r in rows if "PCIe" in r.part][0]
        self.assertEqual(pcie.count, 2)

    def test_corrected_recent_promotes_to_warn(self):
        """最近 7 天还在刷的已纠正错误要提级，否则会被人忽略。"""
        recs = [self._rec(days_ago=1) for _ in range(3)]
        rows = hwevent._aggregate(recs)
        self.assertEqual(rows[0].level, "warn")
        self.assertEqual(rows[0].recent7, 3)

    def test_corrected_old_stays_info(self):
        recs = [self._rec(days_ago=30) for _ in range(3)]
        rows = hwevent._aggregate(recs)
        self.assertEqual(rows[0].level, "info")
        self.assertEqual(rows[0].recent7, 0)

    def test_corrected_many_promotes(self):
        recs = [self._rec(days_ago=40) for _ in range(25)]
        rows = hwevent._aggregate(recs)
        self.assertEqual(rows[0].level, "warn")
        self.assertIn("25", rows[0].hint)

    def test_pcie_hint(self):
        recs = [self._rec(days_ago=1) for _ in range(3)]
        rows = hwevent._aggregate(recs)
        self.assertIn("ASPM", rows[0].hint)

    def test_memory_hint(self):
        recs = [self._rec(part="内存", days_ago=1) for _ in range(3)]
        rows = hwevent._aggregate(recs)
        self.assertIn("XMP", rows[0].hint)

    def test_fatal_sorts_first(self):
        recs = [self._rec(sev=2), self._rec(sev=1, part="内存")]
        rows = hwevent._aggregate(recs)
        self.assertEqual(rows[0].severity, 1)

    def test_times_recorded(self):
        rows = hwevent._aggregate([self._rec(days_ago=5), self._rec(days_ago=1)])
        self.assertIsNotNone(rows[0].first)
        self.assertLess(rows[0].first, rows[0].last)


class TestHwReport(unittest.TestCase):
    def test_headline_no_errors(self):
        self.assertIn("未发现", hwevent.Report().headline)

    def test_headline_io_only(self):
        r = hwevent.Report(io_counts={153: 3})
        self.assertIn("磁盘 IO", r.headline)

    def test_headline_fatal(self):
        r = hwevent.Report(rows=[hwevent.ErrRow(sev="致命", severity=1,
                                                part="内存", count=2)])
        self.assertIn("致命", r.headline)
        self.assertEqual(r.worst, "crit")

    def test_worst_with_corrected(self):
        r = hwevent.Report(rows=[hwevent.ErrRow(sev="已纠正", severity=2,
                                                part="PCIe", count=1,
                                                level="info")])
        self.assertEqual(r.worst, "info")

    def test_parts_aggregation(self):
        r = hwevent.Report(rows=[
            hwevent.ErrRow(sev="已纠正", severity=2, part="PCIe", count=3),
            hwevent.ErrRow(sev="已纠正", severity=2, part="内存", count=1),
        ])
        self.assertEqual(r.parts[0], ("PCIe", 3))

    def test_info_ids_are_documented(self):
        for eid, text in hwevent.INFO_EVENT_IDS.items():
            self.assertTrue(text)
        self.assertIn(42, hwevent.INFO_EVENT_IDS)

    def test_section_guids_are_32_hex(self):
        for g in hwevent.SECTION_GUIDS:
            self.assertEqual(len(g), 32, g)
            int(g, 16)


if __name__ == "__main__":
    unittest.main()
