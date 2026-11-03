# -*- coding: utf-8 -*-
"""v1.11.0 单元测试：流体交互层的物理参数与降级行为。

这里只测「不需要事件循环」的部分 —— 弹簧曲线是纯数学，
`Spring.to()` 的快速返回路径是同步回调，都能直接断言。
动画本身靠 tools/probe_apple_ui.py + probe_motion_stress.py 实测覆盖。
"""
from __future__ import annotations

import unittest

from app.ui import motion as mo


class TestSpringCurve(unittest.TestCase):
    def test_progress_shape(self):
        """0 时刻是 0，长时间后收敛到 1。"""
        self.assertEqual(mo.spring_progress(0.0), 0.0)
        self.assertEqual(mo.spring_progress(-1.0), 0.0)
        self.assertAlmostEqual(mo.spring_progress(2.0), 1.0, places=6)

    def test_overshoot_is_invisible(self):
        """超调必须 <1% —— 330px 的行程上不到 3px，看不出来。"""
        peak = max(mo.spring_progress(i / 2000.0) for i in range(1, 2000))
        self.assertLess((peak - 1.0) * 100, 1.0, f"超调 {peak * 100 - 100:.2f}% 偏大")

    def test_starts_early_enough(self):
        """「即时响应」的量化口径：前 25ms 必须已经开始动，不能纹丝不动。"""
        self.assertGreater(mo.spring_progress(0.025), 0.015)
        # 一半行程要在 150ms 内走完，否则用户会觉得「点了没反应」
        self.assertGreater(mo.spring_progress(0.150), 0.80)

    def test_settles_within_budget(self):
        """SPRING_MS 之内必须收敛（误差 1%）。"""
        t = mo.SPRING_MS / 1000.0
        self.assertAlmostEqual(mo.spring_progress(t), 1.0, delta=0.01)

    def test_no_second_bounce(self):
        """超调过后不能再冒出第二个峰（欠阻尼的大忌：来回摆）。

        不能要求逐点严格单调 —— 阻尼振动的尾部本身有 1e-8 级的数值起伏；
        真正要守住的是「首个峰必须是全局最高」。
        """
        peak_i = max(range(1, 1500), key=lambda i: mo.spring_progress(i / 1000.0))
        peak = mo.spring_progress(peak_i / 1000.0)
        after = max(mo.spring_progress(i / 1000.0) for i in range(peak_i, 1500))
        self.assertLessEqual(after, peak + 1e-12)
        # 计时器落点之后偏差必须小到看不见（1% 行程以内，330px 上不到 3px）
        for ms in range(mo.SPRING_MS, 1500, 17):
            self.assertAlmostEqual(mo.spring_progress(ms / 1000.0), 1.0, delta=0.005)

    def test_critical_damping_variant(self):
        """ζ>=1 的过阻尼分支不能除零（表达式里没有 sqrt(1-ζ²) 了）。"""
        v = mo.spring_progress(0.5, zeta=1.0, omega=20.0)
        self.assertTrue(0.0 < v < 1.0)


class TestGate(unittest.TestCase):
    def test_animations_flag_is_bool(self):
        self.assertIsInstance(mo.animations_enabled(), bool)

    def test_constants_sane(self):
        self.assertTrue(0.5 <= mo.ZETA < 1.0, "阻尼比超出「有弹性但不晃」的区间")
        self.assertTrue(10.0 <= mo.OMEGA <= 40.0)
        self.assertTrue(80 <= mo.FADE_MS <= 300)
        self.assertTrue(0.0 <= mo.FADE_FROM < 0.6, "起步太透明会像卡了半拍")
        self.assertGreaterEqual(mo.SPRING_MS, 250)


class TestSpringDispatch(unittest.TestCase):
    def test_zero_distance_applies_immediately(self):
        sp = mo.Spring()
        got = []
        sp.to(100.0, 100.3, got.append)      # 差 0.3px：不值得起动画
        self.assertEqual(got, [100.0])
        self.assertFalse(sp._timer.isActive())

    def test_disabled_when_system_says_so(self):
        sp = mo.Spring()
        got = []
        saved = dict(mo._cache)
        try:
            mo._cache["on"] = False
            sp.to(500.0, 0.0, got.append)
            self.assertEqual(got, [500.0], "系统关了动画就该一步到位")
            self.assertFalse(sp._timer.isActive())
        finally:
            mo._cache.clear()
            mo._cache.update(saved)

    def test_reto_overrides_without_queueing(self):
        """可打断：改目标时从当前值重新起跳，不起两个计时器。"""
        sp = mo.Spring()
        got = []
        saved = dict(mo._cache)
        try:
            mo._cache["on"] = True
            sp.to(400.0, 0.0, got.append)
            self.assertTrue(sp._timer.isActive())
            first_from = sp._from
            sp.to(120.0, 88.0, got.append)
            self.assertEqual(first_from, 0.0)
            self.assertEqual(sp._from, 88.0, "必须从当前实际位置起跳")
            self.assertEqual(sp._to, 120.0)
            # 只有一个计时器：改道不会留下第二个在跑
            self.assertEqual(len(sp.findChildren(type(sp._timer))), 1)
        finally:
            sp.stop()
            mo._cache.clear()
            mo._cache.update(saved)


class TestPillGeometry(unittest.TestCase):
    """不建真控件：只校验「没有按钮时不炸」这条降级路径。"""

    def test_bounds_without_host(self):
        class _FakeRail:
            active = 0

            def button_at(self, _i):
                return None

        layer = mo.PillLayer.__new__(mo.PillLayer)
        layer._rail = _FakeRail()
        layer._y = 0.0
        layer._h = 0.0
        self.assertEqual(layer.bounds(), (0.0, 0.0, 0.0, 0.0))


if __name__ == "__main__":
    unittest.main()
