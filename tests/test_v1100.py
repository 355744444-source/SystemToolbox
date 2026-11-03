"""v1.10.0 单元测试：宽带测速（netspeed.py）。

测的是「分级与结论」而不是真实带宽 —— 家庭宽带在 CI 上不可重复，
但阈值、文案、多线程计数这些是纯逻辑，必须钉死。

覆盖的坑：
- 2.5G 网卡 + 千兆宽带的达成率只有三成多，不能按「跑不满」报警
- 上传是 4 MB 一个请求，瞬时速度必须按 1 秒窗口算，否则曲线全是尖刺
- `_measure` 的稳态口径要剔掉前 1 秒慢启动
- 计数器在多线程下必须真的准（CPython 的 `+=` 不是原子的）
- 节点清单只允许 https，key 不能重复
"""
from __future__ import annotations

import threading
import time
import unittest

from app.core import netspeed as ns


def make_report(down: float = 800.0, up: float = 100.0, lat: float = 20.0,
                jit: float = 4.0, link: float = 1000.0, **kw) -> ns.Report:
    return ns.Report(node="腾讯云镜像", nodes=["腾讯云镜像"], down_mbps=down,
                     up_mbps=up, up_ok=up > 0, latency_ms=lat, jitter_ms=jit,
                     link_mbps=link, **kw)


class TestNodes(unittest.TestCase):
    def test_nodes_are_https_and_unique(self):
        keys = [n.key for n in ns.NODES]
        self.assertEqual(len(keys), len(set(keys)), "节点 key 重复")
        for n in ns.NODES:
            self.assertTrue(n.url.startswith("https://"), n.key)
            self.assertIn(n.host, n.url)
            self.assertTrue(n.name and n.where)

    def test_node_of(self):
        self.assertIs(ns.node_of("tencent"), ns.NODES[0])
        self.assertIsNone(ns.node_of("nope"))
        self.assertIsNone(ns.node_of(""))

    def test_sizes(self):
        self.assertTrue(all(s >= 5 for s in ns.SIZES))
        self.assertEqual(list(ns.SIZES), sorted(ns.SIZES))

    def test_concurrency_budget(self):
        # 单个节点 8 线反而更慢（实测 589 vs 599），只用 4 线；最多压 2 个节点
        self.assertEqual(ns.DL_THREADS, 4)
        self.assertEqual(ns.MAX_NODES, 2)
        self.assertLessEqual(ns.UP_THREADS, 3)
        self.assertEqual(ns.WARMUP, 1.0)


class TestFormat(unittest.TestCase):
    def test_mbps_text(self):
        self.assertEqual(ns.mbps_text(0), "-")
        self.assertEqual(ns.mbps_text(-1), "-")
        self.assertEqual(ns.mbps_text(5.24), "5.2 Mbps")
        self.assertEqual(ns.mbps_text(98.44), "98.4 Mbps")
        self.assertEqual(ns.mbps_text(512.4), "512 Mbps")
        self.assertEqual(ns.mbps_text(1250), "1.25 Gbps")

    def test_mb_text(self):
        self.assertEqual(ns.mb_text(0), "0 MB")
        self.assertEqual(ns.mb_text(500.4), "500 MB")
        self.assertEqual(ns.mb_text(2048), "2.00 GB")


class TestGrades(unittest.TestCase):
    def test_down_grade_edges(self):
        self.assertEqual(ns.down_grade(0), ("失败", "bad"))
        self.assertEqual(ns.down_grade(19.9), ("偏慢", "warn"))
        self.assertEqual(ns.down_grade(20), ("偏慢", "warn"))
        self.assertEqual(ns.down_grade(50), ("一般", "warn"))
        self.assertEqual(ns.down_grade(100), ("良好", "ok"))
        self.assertEqual(ns.down_grade(200), ("很快", "ok"))
        self.assertEqual(ns.down_grade(500), ("极快", "ok"))

    def test_up_grade_edges(self):
        self.assertEqual(ns.up_grade(0), ("失败", "bad"))
        self.assertEqual(ns.up_grade(9.9), ("偏慢", "warn"))
        self.assertEqual(ns.up_grade(10), ("一般", "warn"))
        self.assertEqual(ns.up_grade(20), ("正常", "ok"))
        self.assertEqual(ns.up_grade(50), ("很好", "ok"))

    def test_latency_grade_edges(self):
        self.assertEqual(ns.latency_grade(0), ("未测", "faint"))
        self.assertEqual(ns.latency_grade(20), ("优秀", "ok"))
        self.assertEqual(ns.latency_grade(50), ("良好", "ok"))
        self.assertEqual(ns.latency_grade(100), ("一般", "warn"))
        self.assertEqual(ns.latency_grade(101), ("偏高", "warn"))

    def test_jitter_grade_edges(self):
        self.assertEqual(ns.jitter_grade(0), ("未测", "faint"))
        self.assertEqual(ns.jitter_grade(5), ("稳定", "ok"))
        self.assertEqual(ns.jitter_grade(15), ("轻微", "ok"))
        self.assertEqual(ns.jitter_grade(30), ("明显", "warn"))
        self.assertEqual(ns.jitter_grade(31), ("严重", "bad"))

    def test_tier_text(self):
        self.assertIn("千兆档", ns.tier_text(940))
        self.assertIn("500M", ns.tier_text(500))
        self.assertIn("200~300M", ns.tier_text(200))
        self.assertIn("100M", ns.tier_text(100))
        self.assertIn("50M", ns.tier_text(50))
        self.assertIn("都没跑到", ns.tier_text(10))


class TestReport(unittest.TestCase):
    def test_ratio_without_link(self):
        self.assertEqual(make_report(link=0).ratio, 0.0)
        self.assertEqual(make_report(link=1000, down=800).ratio, 0.8)

    def test_level(self):
        self.assertEqual(make_report().level, "ok")
        # 下载 60 Mbps 落在「一般」，整体应降成 warn 而不是 bad
        self.assertEqual(make_report(down=60).level, "warn")
        self.assertEqual(make_report(down=0).level, "bad")
        self.assertEqual(make_report(errors=["下载：超时"]).level, "bad")
        # 抖动 40 是 bad
        self.assertEqual(make_report(jit=40).level, "bad")

    def test_headline(self):
        h = make_report(800, 100, 20).headline
        self.assertIn("下载 800 Mbps", h)
        self.assertIn("上传 100 Mbps", h)
        self.assertIn("延迟 20 ms", h)
        self.assertIn("没跑起来", make_report(down=0).headline)

    def test_up_grade_absent_when_not_measured(self):
        r = ns.Report(down_mbps=800, up_mbps=0, up_ok=False)
        self.assertEqual(r.up_grade, ("未测", "faint"))

    def test_hint_nic_faster_than_broadband(self):
        """2.5G 网口跑 894（34%）：不能说「只跑到三成，查网线」。"""
        r = make_report(down=894, link=2500)
        hints = r.hints(wifi=False)
        joined = " ".join(hints)
        self.assertIn("2.5G", joined)
        self.assertNotIn("网线是不是超五类", joined)
        self.assertIn("千兆档", joined)

    def test_hint_wifi_low_ratio(self):
        r = make_report(down=120, link=1200)
        self.assertTrue(any("无线" in h for h in r.hints(wifi=True)))

    def test_hint_wired_low_ratio(self):
        r = make_report(down=120, link=1000)
        self.assertTrue(any("网线" in h for h in r.hints(wifi=False)))

    def test_hint_full_speed(self):
        r = make_report(down=900, link=1000)
        self.assertTrue(any("接近" in h and "跑满" in h for h in r.hints()))

    def test_hint_asymmetric_upload(self):
        r = make_report(down=900, up=30, link=2500)
        self.assertTrue(any("非对等" in h for h in r.hints()))

    def test_hint_proxy_only_when_slow(self):
        """跑得动的时候提代理只是噪音；跑不动时它可能是元凶。"""
        fast = make_report(down=800, link=2500, proxy="127.0.0.1:7890")
        self.assertFalse(any("代理" in h for h in fast.hints()))
        slow = make_report(down=80, link=2500, proxy="127.0.0.1:7890")
        self.assertTrue(any("代理" in h for h in slow.hints()))

    def test_hint_stopped(self):
        r = make_report(down=0, stopped=True)
        self.assertTrue(any("手动停止" in h for h in r.hints()))

    def test_hint_failure(self):
        r = make_report(down=0)
        self.assertTrue(any("防火墙" in h for h in r.hints()))


class TestCounter(unittest.TestCase):
    def test_counter_thread_safe(self):
        c = ns._Counter()

        def w():
            for _ in range(2000):
                c.add(1)

        ts = [threading.Thread(target=w) for _ in range(8)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        self.assertEqual(c.get(), 16000)


class TestMeasure(unittest.TestCase):
    def test_steady_excludes_warmup(self):
        """单线程每秒恒定灌 20 MB → 稳态应接近 20 MB/s ≈ 160 Mbps。"""
        per_sec = 20 * 1024 * 1024

        def target(job, i, deadline, counter, stop, errs):
            chunk = per_sec // 20
            while time.perf_counter() < deadline:
                counter.add(chunk)
                time.sleep(0.05)

        steady, peak, dt, errs, stopped, series, total = ns._measure(
            [None], target, 1.8, None, None, 0.0, 1.0, "测试")
        self.assertFalse(errs)
        self.assertFalse(stopped)
        self.assertGreater(total, 0)
        self.assertGreater(dt, 1.5)
        self.assertAlmostEqual(steady, 160.0, delta=40.0)

    def test_series_has_points_on_longer_run(self):
        def target(job, i, deadline, counter, stop, errs):
            while time.perf_counter() < deadline:
                counter.add(1024 * 1024)
                time.sleep(0.05)

        _s, _p, _d, _e, _st, series, _t = ns._measure(
            [None], target, 2.4, None, None, 0.0, 1.0, "测试")
        self.assertGreaterEqual(len(series), 3)
        for rel, v in series:
            self.assertGreater(rel, 0)
            self.assertGreaterEqual(v, 0)

    def test_stop_flag(self):
        def target(job, i, deadline, counter, stop, errs):
            while time.perf_counter() < deadline:
                if stop and stop():
                    return
                counter.add(1024)
                time.sleep(0.05)

        flag = [True]
        _s, _p, _d, _e, stopped, _se, _t = ns._measure(
            [None], target, 3.0, None, lambda: flag[0], 0.0, 1.0, "测试")
        self.assertTrue(stopped)


class TestQuickCheck(unittest.TestCase):
    def test_quick_check_never_raises(self):
        ok, msg = ns.quick_check()
        self.assertIsInstance(ok, bool)
        self.assertTrue(msg)
        self.assertIn("节点", msg)
        # 断网环境也要返回 ok=True + 说明，不能把自检搞挂
        self.assertTrue(ok, msg)

    def test_peek_reports_error_instead_of_raising(self):
        bad = ns.Node("bad", "坏节点", "https://no-such-host.invalid/x.gz",
                      "no-such-host.invalid")
        got, ttfb, err = ns.peek(bad, 1024)
        self.assertEqual(got, 0)
        self.assertEqual(ttfb, 0.0)
        self.assertTrue(err)


if __name__ == "__main__":
    unittest.main()
