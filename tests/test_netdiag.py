# -*- coding: utf-8 -*-
"""网络诊断（netdiag.py）单测。

只测纯逻辑与本机即可拿到的数据：不发外网请求、不依赖网络可用性，
否则在 CI 或拔了网线的机器上必然红。
"""
from __future__ import annotations

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app.core import netdiag                                  # noqa: E402


class TestAdapterText(unittest.TestCase):
    def test_speed_text(self):
        cases = ((0, "—"), (2_500_000_000, "2.5 Gbps"), (802_000_000, "802 Mbps"),
                 (100_000_000, "100 Mbps"), (56_000, "56 Kbps"))
        for bps, want in cases:
            self.assertEqual(netdiag.Adapter(speed=bps).speed_text, want, bps)

    def test_title_prefers_desc(self):
        self.assertEqual(netdiag.Adapter(name="以太网", desc="Realtek").title,
                         "Realtek")
        self.assertEqual(netdiag.Adapter(name="以太网").title, "以太网")

    def test_link_and_detail(self):
        a = netdiag.Adapter(desc="R", medium="以太网", speed=1_000_000_000,
                            up=True, dhcp=True)
        self.assertEqual(a.link_text, "已连接")
        self.assertEqual(a.detail_text, "以太网 · 1.0 Gbps · 已连接 · DHCP")
        b = netdiag.Adapter(up=False, dhcp=False)
        self.assertEqual(b.link_text, "已断开")
        self.assertTrue(b.detail_text.startswith("未知介质 · — · 已断开 · 静态"))


class TestVirtual(unittest.TestCase):
    def test_hints(self):
        for text in ("vEthernet (WSL)", "Hyper-V Virtual Ethernet Adapter",
                     "TAP-Windows Adapter V9", "Tailscale Tunnel",
                     "Bluetooth Network Connection", "Loopback Pseudo"):
            self.assertTrue(netdiag._looks_virtual(text), text)

    def test_real_cards(self):
        for text in ("Realtek Gaming 2.5GbE Family Controller",
                     "Intel(R) Wi-Fi 6 AX200 160MHz", "以太网 2"):
            self.assertFalse(netdiag._looks_virtual(text), text)

    def test_empty(self):
        self.assertFalse(netdiag._looks_virtual(""))
        self.assertFalse(netdiag._looks_virtual(None))


class TestPrimary(unittest.TestCase):
    def _a(self, ip, gw, up=True, virt=False):
        return netdiag.Adapter(name=ip, ip=ip, gateway=gw, up=up,
                               is_virtual=virt)

    def test_prefers_physical_up(self):
        virt = self._a("172.24.96.1", "172.24.96.1", True, True)
        wifi = self._a("192.168.1.20", "192.168.1.1", False, False)
        eth = self._a("192.168.50.14", "192.168.50.1", True, False)
        self.assertIs(netdiag.primary([virt, wifi, eth]), eth)

    def test_skips_loopback_and_apipa(self):
        lo = self._a("127.0.0.1", "127.0.0.1")
        apipa = self._a("169.254.3.4", "169.254.3.4", False)
        self.assertIsNone(netdiag.primary([lo, apipa]))

    def test_no_gateway_means_none(self):
        self.assertIsNone(netdiag.primary([self._a("192.168.1.5", "")]))

    def test_falls_back_to_virtual(self):
        """只有虚拟网卡带网关时也得给个结果，不能返回 None。"""
        v = self._a("10.0.0.2", "10.0.0.1", True, True)
        self.assertIs(netdiag.primary([v]), v)


class TestHostsKind(unittest.TestCase):
    def test_block(self):
        for ip in ("0.0.0.0", "127.0.0.1", "::1", "255.255.255.255"):
            self.assertEqual(netdiag.hosts_kind(ip, "ads.example.com"), "block", ip)

    def test_hijack_needs_both_public_ip_and_watched_domain(self):
        self.assertEqual(
            netdiag.hosts_kind("45.1.2.3", "update.microsoft.com"), "hijack")
        self.assertEqual(netdiag.hosts_kind("45.1.2.3", "baidu.com"), "hijack")

    def test_plain_map(self):
        self.assertEqual(netdiag.hosts_kind("10.0.0.9", "nas.lan"), "map")
        self.assertEqual(netdiag.hosts_kind("45.1.2.3", "cdn.example.org"), "map")


class TestReport(unittest.TestCase):
    def test_problems_only_counts_false(self):
        rep = netdiag.Report(checks=[
            netdiag.Check("a", "ok", True),
            netdiag.Check("b", "info", None),
            netdiag.Check("c", "bad", False),
        ])
        self.assertEqual([c.name for c in rep.problems], ["c"])

    def test_empty(self):
        self.assertEqual(netdiag.Report().problems, [])

    def test_steps(self):
        self.assertEqual(netdiag.STEPS, 7)


class TestLocalReadOnly(unittest.TestCase):
    """只碰本机的东西：注册表、TCP 表、回环 socket。"""

    def test_listening_shape(self):
        items = netdiag.listening(limit=50)
        self.assertIsInstance(items, list)
        ports = [x.port for x in items]
        self.assertEqual(ports, sorted(ports), "应按端口升序")
        for x in items[:5]:
            self.assertEqual(x.proto, "TCP")
            self.assertTrue(0 < x.port < 65536)
            self.assertGreaterEqual(x.pid, 0)

    def test_adapters_readable(self):
        items = netdiag.adapters()
        self.assertIsInstance(items, list)
        for a in items:
            self.assertTrue(a.ip)
            self.assertFalse(a.ip.startswith("0."), "0.x 是无效地址，应被过滤")
            self.assertIn(a.link_text, ("已连接", "已断开"))

    def test_proxy_keys(self):
        p = netdiag.proxy()
        self.assertEqual(set(p), {"enabled", "server", "pac"})
        self.assertIsInstance(p["enabled"], bool)

    def test_tcp_check_closed_port(self):
        # 127.0.0.1:1 一定没有服务，连接被拒是即时返回的
        ok, ms, msg = netdiag.tcp_check("127.0.0.1", 1, timeout=0.3)
        self.assertFalse(ok)
        self.assertGreaterEqual(ms, 0.0)
        self.assertTrue(msg)

    def test_ping_returns_tuple(self):
        ok, ms, ttl, err = netdiag.ping("127.0.0.1", timeout_ms=1500)
        self.assertIsInstance(ok, bool)
        self.assertIsInstance(ms, float)
        self.assertIsInstance(ttl, int)
        self.assertIsInstance(err, str)
        if ok:
            self.assertEqual(err, "")
            self.assertGreaterEqual(ms, 0.0)

    def test_hosts_entries_shape(self):
        n, entries, level, note = netdiag.hosts_entries()
        self.assertIsInstance(n, int)
        self.assertIn(level, ("", "info", "warn"))
        self.assertIsInstance(note, str)
        for ip, name in entries[:5]:
            self.assertTrue(ip)
            self.assertTrue(name)
            self.assertIn(netdiag.hosts_kind(ip, name), ("block", "hijack", "map"))


if __name__ == "__main__":
    unittest.main()
