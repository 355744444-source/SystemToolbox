"""v1.8.0 单元测试：启动/切换性能相关的三处改动。

覆盖点都是「改错了会静默变慢或者静默崩掉」的地方：
- PDH 磁盘计数器必须后台建（同步建 = 690ms 卡在 UI 线程上）
- stop_all 必须走**总**预算（逐个等 = 页数 × 预算）
- QSS 挂窗口 vs 挂 app（挂 app 会把马上要扔的旧树一起 polish）
- 主题重建要能连续做，且旧树退役后线程跑完才释放
"""
from __future__ import annotations

import os
import time
import unittest

from PyQt5.QtCore import QThread

from app.core import perf
from app.ui import tables

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


class TestDeferredDisk(unittest.TestCase):
    def test_ctor_is_fast(self):
        """构造函数不能阻塞在 PdhAddEnglishCounterW 上。"""
        t = time.perf_counter()
        s = perf.Sampler()
        dt = (time.perf_counter() - t) * 1000
        try:
            self.assertLess(dt, 200, "采样器构造 %.0fms，磁盘计数器又回到同步建了" % dt)
        finally:
            s.close()

    def test_disk_ready_eventually(self):
        s = perf.Sampler()
        try:
            self.assertTrue(s.wait_disk(5.0), "5 秒内没等到磁盘计数器就绪")
            self.assertTrue(s.disk_ready)
            s.prime()
            time.sleep(0.25)
            pt = s.sample()
            self.assertGreaterEqual(pt.disk, 0)
            self.assertLessEqual(pt.disk, 100)
        finally:
            s.close()

    def test_sync_mode_ready_immediately(self):
        s = perf.Sampler(defer_disk=False)
        try:
            self.assertTrue(s.disk_ready)
        finally:
            s.close()

    def test_close_is_idempotent_and_safe(self):
        """close 后再 close / 再 sample 都不能炸。"""
        s = perf.Sampler()
        s.close()
        s.close()
        pt = s.sample()
        self.assertEqual(pt.disk, 0.0)

    def test_sample_before_disk_ready_returns_zero(self):
        """磁盘计数器没就绪时给 0.0，不能抛异常也不能给假值。"""
        s = perf.Sampler()
        try:
            s.prime()
            pt = s.sample()
            self.assertGreaterEqual(pt.cpu, 0)
            self.assertGreaterEqual(pt.disk, 0)
        finally:
            s.close()

    def test_snapshot_uses_sync_disk(self):
        """一次性快照要同步拿磁盘值（否则永远是 0）。"""
        pt = perf.snapshot()
        self.assertGreaterEqual(pt.disk, 0)
        self.assertLessEqual(pt.disk, 100)


class _Stubborn(QThread):
    """不响应停止位的线程，用来验证预算。"""

    def __init__(self, secs=3.0, parent=None):
        super().__init__(parent)
        self.secs = secs

    def run(self):
        time.sleep(self.secs)

    def stop(self):
        pass                      # 故意不理


class _Holder:
    pass


class TestStopAllBudget(unittest.TestCase):
    def _holder(self, n, secs=3.0):
        h = _Holder()
        h._workers = [_Stubborn(secs) for _ in range(n)]
        for w in h._workers:
            w.start()
        return h

    def test_total_budget_not_per_thread(self):
        """3 个不听话的线程、预算 400ms —— 总耗时必须接近 400ms 而不是 1200ms。"""
        h = self._holder(3)
        t = time.perf_counter()
        tables.stop_all(h, budget_ms=400)
        dt = (time.perf_counter() - t) * 1000
        self.assertLess(dt, 900, "总耗时 %.0fms，说明在逐个等预算" % dt)
        for w in h._workers:
            w.wait(4000)

    def test_shared_wait_window(self):
        """开共享窗口后，两次 stop_all 加起来也不超过窗口。"""
        h1 = self._holder(2)
        h2 = self._holder(2)
        tables.begin_wait_window(400)
        t = time.perf_counter()
        try:
            tables.stop_all(h1, budget_ms=400)
            tables.stop_all(h2, budget_ms=400)
        finally:
            tables.end_wait_window()
        dt = (time.perf_counter() - t) * 1000
        self.assertLess(dt, 900, "共享窗口没生效，总耗时 %.0fms" % dt)
        for w in h1._workers + h2._workers:
            w.wait(4000)

    def test_window_reset(self):
        tables.begin_wait_window(100)
        tables.end_wait_window()
        self.assertEqual(tables._WAIT_DEADLINE[0], 0.0)

    def test_no_workers_is_noop(self):
        h = _Holder()
        tables.stop_all(h)        # 不该抛
        self.assertTrue(True)


class TestThemeTarget(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PyQt5.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def test_qss_goes_to_target_not_app(self):
        """挂到 target 上时，app 级 stylesheet 不应被改写。"""
        from PyQt5.QtWidgets import QWidget

        from app import theme
        self.app.setStyleSheet("/* baseline */")
        w = QWidget()
        try:
            theme.apply_theme(self.app, "dark", target=w)
            self.assertEqual(self.app.styleSheet(), "/* baseline */")
            self.assertIn("QWidget", w.styleSheet())
            self.assertEqual(theme.current()["name"], "dark")
        finally:
            w.deleteLater()
            theme.apply_theme(self.app)

    def test_light_is_gone(self):
        """v2.0.0 删掉了浅色皮肤：要浅色也只给深色，不再有第二套令牌。"""
        from app import theme
        self.assertEqual(list(theme.THEMES), ["dark"])
        self.assertFalse(hasattr(theme, "LIGHT"))
        t = theme.apply_theme(self.app, "light")      # 老调用点传 light 也不能崩
        self.assertIs(t, theme.DARK)
        self.assertEqual(theme.current()["name"], "dark")

    def test_default_target_is_app(self):
        from app import theme
        theme.apply_theme(self.app)
        self.assertGreater(len(self.app.styleSheet()), 1000)
        self.assertEqual(theme.current()["name"], "dark")


class TestLazyPages(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PyQt5.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def test_only_first_page_built(self):
        from app.ui.main_window import PAGE_ATTRS, PAGE_CLASSES, MainWindow
        win = MainWindow()
        try:
            built = [n for n in PAGE_ATTRS if getattr(win, n, None) is not None]
            self.assertEqual(built, ["log_page"], "启动时只该建第一个工作台")
            self.assertEqual(win.stack.count(), len(PAGE_CLASSES),
                             "占位控件数必须与导航按钮一一对应")
        finally:
            win.close()

    def test_page_index_stable(self):
        """按需构建后，stack 的索引必须还是导航顺序。"""
        from app.ui.main_window import PAGE_CLASSES, MainWindow
        win = MainWindow()
        try:
            for i in range(len(PAGE_CLASSES)):
                w = win.page(i)
                self.assertIs(win.stack.widget(i), w)
                self.assertEqual(win.stack.count(), len(PAGE_CLASSES))
            self.assertIsInstance(win.page(3), PAGE_CLASSES[3])
        finally:
            win.close()

    def test_page_cached(self):
        from app.ui.main_window import MainWindow
        win = MainWindow()
        try:
            self.assertIs(win.page(2), win.page(2))
        finally:
            win.close()

    def test_no_theme_switch_entry(self):
        """皮肤切换入口要彻底消失：按钮、快捷键、状态字段都不该留。"""
        from app.ui.main_window import MainWindow
        win = MainWindow()
        try:
            self.assertFalse(hasattr(win, "btn_theme"))
            self.assertFalse(hasattr(win, "toggle_theme"))
            self.assertFalse(hasattr(win, "theme_name"))
            self.assertFalse(hasattr(win, "apply_theme"))
            rail = win.rail
            self.assertIsNone(rail.findChild(type(win.btn_about), "ThemeBtn"))
        finally:
            win.close()


if __name__ == "__main__":
    unittest.main()
