# -*- coding: utf-8 -*-
"""变更记录（changes.py）单测：解析、去重、统计与排序。

不碰真实备份目录 —— 用临时 jsonl 注入，避免污染用户机器上的操作痕迹。
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app.core import changes as chg                          # noqa: E402


def _rec(**kw) -> dict:
    base = {"type": "registry", "hive": "HKCU",
            "path": r"Software\Microsoft\Windows\CurrentVersion\Run",
            "name": "Demo", "action": "disable",
            "time": "2026-01-02 03:04:05"}
    base.update(kw)
    return base


class TestTimeParse(unittest.TestCase):
    def test_known_formats(self):
        for text in ("2026-01-02 03:04:05", "2026-01-02T03:04:05",
                     "2026-01-02 03:04"):
            self.assertGreater(chg._ts(text), 0, text)

    def test_bad_text_is_zero(self):
        for text in ("", "not-a-time", "2026/01/02", None):
            self.assertEqual(chg._ts(text), 0.0, repr(text))


class TestRecordParsing(unittest.TestCase):
    def test_key_registry(self):
        k = chg._rec_key(_rec())
        self.assertTrue(k.startswith("reg:HKCU\\"))
        self.assertTrue(k.endswith("\\Demo"))

    def test_key_folder(self):
        k = chg._rec_key({"type": "folder", "original": r"C:\Users\a\Startup\x.lnk"})
        self.assertTrue(k.startswith("folder:"))

    def test_key_task(self):
        self.assertEqual(chg._rec_key({"type": "task", "task": r"\StartCN"}),
                         "task:\\StartCN")

    def test_key_unknown(self):
        self.assertEqual(chg._rec_key({"type": "nope"}), "")

    def test_target(self):
        self.assertEqual(chg._rec_target(_rec()), "Demo")
        self.assertEqual(chg._rec_target({"type": "task", "task": "\\StartDVR"}),
                         "StartDVR")
        self.assertEqual(
            chg._rec_target({"type": "folder",
                             "original": r"C:\a\b\WeChat.lnk"}),
            "WeChat.lnk")
        self.assertEqual(chg._rec_target({"type": "nope"}), "(未知)")

    def test_detail(self):
        self.assertIn(r"Run", chg._rec_detail(_rec()))
        self.assertEqual(chg._rec_detail({"type": "task", "task": "\\x"}),
                         "计划任务")


class TestStartupTimeline(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cls_chg_")
        self.path = os.path.join(self.tmp, "startup_actions.jsonl")
        self._orig = chg._actions_path
        chg._actions_path = lambda: self.path

    def tearDown(self):
        chg._actions_path = self._orig
        try:
            os.remove(self.path)
        except OSError:
            pass
        os.rmdir(self.tmp)

    def _write(self, recs):
        with open(self.path, "w", encoding="utf-8") as f:
            for r in recs:
                f.write((r if isinstance(r, str)
                         else json.dumps(r, ensure_ascii=False)) + "\n")

    def test_same_target_keeps_last_action(self):
        self._write([
            _rec(action="disable", time="2026-01-01 10:00:00"),
            _rec(action="enable", time="2026-01-01 11:00:00"),
        ])
        rows = chg._startup_changes()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].action, "已启用")
        self.assertFalse(rows[0].active, "最后一次是启用 → 无需还原")

    def test_disable_last_is_active(self):
        self._write([
            _rec(action="enable", time="2026-01-01 10:00:00"),
            _rec(action="disable", time="2026-01-01 11:00:00"),
        ])
        rows = chg._startup_changes()
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0].active)
        self.assertEqual(rows[0].state_text, "生效中")

    def test_two_targets_kept_apart(self):
        self._write([_rec(name="A"), _rec(name="B", time="2026-01-03 00:00:00")])
        rows = chg._startup_changes()
        self.assertEqual(len(rows), 2)
        self.assertEqual({r.target for r in rows}, {"A", "B"})

    def test_selftest_and_broken_lines_filtered(self):
        self._write([
            _rec(name=chg.SELFTEST_MARK + "_x"),
            "{ this is not json",
            "",
            _rec(name="Real"),
        ])
        rows = chg._startup_changes()
        self.assertEqual([r.target for r in rows], ["Real"])


class TestCollect(unittest.TestCase):
    def test_real_system_consistency(self):
        """真机跑一遍：统计必须自洽（只读，不改任何东西）。"""
        rows, sm = chg.collect()
        self.assertEqual(sm.total, len(rows))
        self.assertEqual(sm.active_total + sm.restored, sm.total)
        for c in rows:
            self.assertIn(c.kind, ("startup", "service", "tweak"))
            self.assertIn(c.state_text, ("生效中", "已还原"))

    def test_sorted_desc_and_filter(self):
        orig = (chg._startup_changes, chg._service_changes, chg._tweak_changes)
        chg._startup_changes = lambda: [
            chg.Change(ts=100, when="a", module="启动项", action="已禁用",
                       target="Old", active=True, kind="startup"),
            chg.Change(ts=300, when="b", module="启动项", action="已启用",
                       target="New", active=False, kind="startup"),
        ]
        chg._service_changes = lambda: [
            chg.Change(ts=200, when="c", module="服务", action="已改启动类型",
                       target="Svc", active=True, kind="service"),
        ]
        chg._tweak_changes = lambda: []
        try:
            rows, sm = chg.collect()
            self.assertEqual([r.target for r in rows], ["New", "Svc", "Old"])
            self.assertEqual(sm.total, 3)
            self.assertEqual(sm.active_startup, 1)
            self.assertEqual(sm.active_service, 1)
            self.assertEqual(sm.active_tweak, 0)
            self.assertEqual(sm.restored, 1)
            self.assertTrue(sm.pending)

            only, sm2 = chg.collect(include_restored=False)
            self.assertEqual([r.target for r in only], ["Svc", "Old"])
            self.assertEqual(sm2.total, 3, "过滤展示不应影响统计口径")
        finally:
            (chg._startup_changes, chg._service_changes,
             chg._tweak_changes) = orig


class TestSummary(unittest.TestCase):
    def test_props(self):
        s = chg.Summary(active_startup=1, active_service=2, active_tweak=0,
                        restored=3, total=6)
        self.assertEqual(s.active_total, 3)
        self.assertTrue(s.pending)
        self.assertFalse(chg.Summary().pending)


if __name__ == "__main__":
    unittest.main()
