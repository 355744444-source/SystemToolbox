# -*- coding: utf-8 -*-
"""v1.16.0：偏好存储（主题 / 窗口位置）。"""
from __future__ import annotations

import io
import json
import os
import shutil
import tempfile
import unittest

from app.core import settings


class SettingsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = tempfile.mkdtemp(prefix="cls_st_")
        self.path = os.path.join(self.dir, "ui.json")

    def tearDown(self) -> None:
        shutil.rmtree(self.dir, ignore_errors=True)

    def _write(self, text: str) -> None:
        with io.open(self.path, "w", encoding="utf-8") as f:
            f.write(text)

    # ---------- 读
    def test_missing_file_is_empty(self):
        self.assertEqual(settings.load(self.path), {})

    def test_broken_json_falls_back(self):
        self._write("{ this is not json")
        self.assertEqual(settings.load(self.path), {},
                         "坏文件必须当成「没设置过」，不能抛")

    def test_non_dict_falls_back(self):
        self._write('["a", "b"]')
        self.assertEqual(settings.load(self.path), {})

    def test_unknown_keys_dropped(self):
        # 老版本写的 "theme" 现在也不认识了：只剩深色，没有主题可存
        self._write('{"win": "0,0,1200,800", "theme": "light", "nonsense": 1}')
        self.assertEqual(settings.load(self.path), {"win": "0,0,1200,800"},
                         "不认识的键要滤掉，免得旧版本写的东西漏进来")

    # ---------- 写
    def test_save_roundtrip(self):
        self.assertTrue(settings.save({"win": "0,0,1200,800"}, self.path))
        self.assertEqual(settings.load(self.path)["win"], "0,0,1200,800")

    def test_save_creates_dir(self):
        deep = os.path.join(self.dir, "a", "b", "ui.json")
        self.assertTrue(settings.save({"win": "0,0,1200,800"}, deep))
        self.assertTrue(os.path.isfile(deep))

    def test_save_is_atomic(self):
        settings.save({"win": "0,0,1200,800"}, self.path)
        settings.save({"win": "0,0,1440,900"}, self.path)
        self.assertFalse(os.path.isfile(self.path + ".tmp"),
                         "临时文件应当被原子替换掉，不留残骸")
        self.assertEqual(settings.load(self.path)["win"], "0,0,1440,900")

    def test_save_drops_unknown(self):
        settings.save({"win": "0,0,1200,800", "extra": 9}, self.path)
        with io.open(self.path, encoding="utf-8") as f:
            data = json.load(f)
        self.assertNotIn("extra", data)

    # ---------- get
    def test_get_default_when_absent(self):
        self.assertEqual(settings.get("win", file_path=self.path), "")

    # ---------- 窗口几何
    def test_geometry_empty_by_default(self):
        self.assertEqual(settings.geometry(self.path), ())

    def test_geometry_roundtrip(self):
        settings.save({"win": "100,200,1440,900"}, self.path)
        self.assertEqual(settings.geometry(self.path), (100, 200, 1440, 900))

    def test_geometry_rejects_junk(self):
        for bad in ("", "1,2,3", "a,b,c,d", "1,2,3,4,5"):
            self.assertEqual(settings._parse_win(bad), (),
                             "坏的几何串应当被拒：%r" % bad)

    def test_geometry_rejects_tiny(self):
        # 旧版本写坏、或高分屏换分辨率后剩个 100x100 —— 用回去窗口就没法看
        self.assertEqual(settings._parse_win("10,10,320,200"), (),
                         "小于 800x600 的尺寸要当没设置")

    def test_only_win_survives_roundtrip(self):
        """落盘的键只剩 win：主题已不是认识的键，不该被继续带进文件。"""
        settings.save({"win": "0,0,1200,800", "theme": "light"}, self.path)
        self.assertEqual(set(settings.load(self.path)), {"win"})
        self.assertEqual(settings.geometry(self.path), (0, 0, 1200, 800))


if __name__ == "__main__":
    unittest.main()
