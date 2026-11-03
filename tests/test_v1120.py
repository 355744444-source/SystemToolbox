# -*- coding: utf-8 -*-
"""v1.12.0 视觉刻度：圆角层级 / KPI 数值与单位拆分 / 卡片边缘当投影 / 浅色对比度。

这些断言盯的是「设计刻度不被随手改坏」，不是像素级还原 ——
所以只校验结构关系（层级递减、没有游离的圆角值、对比度区间），
不锁定具体数值。
"""
from __future__ import annotations

import re
import unittest

from app import theme
from app.ui.widgets import split_value


def _lum(hex_color: str) -> float:
    """WCAG 相对亮度。"""
    c = hex_color.lstrip("#")
    rgb = [int(c[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    lin = [v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4 for v in rgb]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def contrast(fg: str, bg: str) -> float:
    a, b = _lum(fg), _lum(bg)
    hi, lo = max(a, b), min(a, b)
    return (hi + 0.05) / (lo + 0.05)


class TestRadiusScale(unittest.TestCase):
    """圆角只有四档，嵌套时内层必须小于外层。"""

    def test_scale_is_strictly_descending(self):
        r = theme.R
        self.assertEqual(sorted(r), ["lg", "md", "sm", "xs"])
        self.assertGreater(r["lg"], r["md"])
        self.assertGreater(r["md"], r["sm"])
        self.assertGreater(r["sm"], r["xs"])

    def test_all_positive_int(self):
        for k, v in theme.R.items():
            self.assertIsInstance(v, int, k)
            self.assertGreater(v, 0, k)

    def test_qss_has_no_stray_radii(self):
        """QSS 里出现的圆角必须来自刻度表（复选框的 3px 是方角特例）。"""
        allowed = set(theme.R.values()) | {3}
        for name in ("dark",):
            sheet = theme.qss(theme.THEMES[name])
            used = {int(m) for m in re.findall(r"border-radius:\s*(\d+)px", sheet)}
            self.assertTrue(used.issubset(allowed),
                            f"{name} 出现游离圆角 {sorted(used - allowed)}")

    def test_card_radius_is_outermost(self):
        """卡片（外容器）用的必须是最外档，不能比内嵌表格还小。"""
        sheet = theme.qss(theme.DARK)
        block = sheet.split('QFrame[role="card"]')[1].split("}")[0]
        rad = int(re.search(r"border-radius:\s*(\d+)px", block).group(1))
        self.assertEqual(rad, theme.R["lg"])


class TestSplitValue(unittest.TestCase):
    """把「数值 + 空格 + 短单位」拆成两段，其余原样返回。"""

    def test_speed(self):
        self.assertEqual(split_value("2.5 Gbps"), ("2.5", "Gbps"))
        self.assertEqual(split_value("892 Mbps"), ("892", "Mbps"))
        self.assertEqual(split_value("88.0 Mbps"), ("88.0", "Mbps"))

    def test_latency_and_size(self):
        self.assertEqual(split_value("163 ms"), ("163", "ms"))
        self.assertEqual(split_value("9.1 ms"), ("9.1", "ms"))
        self.assertEqual(split_value("23.0 MB"), ("23.0", "MB"))
        self.assertEqual(split_value("1.2 GB"), ("1.2", "GB"))

    def test_plain_number_untouched(self):
        self.assertEqual(split_value("14,882"), ("14,882", ""))
        self.assertEqual(split_value("0"), ("0", ""))
        self.assertEqual(split_value("32"), ("32", ""))

    def test_non_numeric_untouched(self):
        for v in ("-", "—", "正常", "内核", "MEMORY.DMP", "x64", "尚未读取"):
            self.assertEqual(split_value(v), (v, ""), v)

    def test_no_space_not_split(self):
        # "5.0s" 拆开成 "5.0" + "s" 只会让单位变成一个孤零零的字母
        self.assertEqual(split_value("5.0s"), ("5.0s", ""))
        self.assertEqual(split_value("100%"), ("100%", ""))

    def test_long_tail_not_treated_as_unit(self):
        # 单位长度上限 6，超了就整串保留（避免把 "1 Device" 之类拆坏）
        self.assertEqual(split_value("1 Devices"), ("1 Devices", ""))

    def test_whitespace_tolerated(self):
        self.assertEqual(split_value(" 892 Mbps "), ("892", "Mbps"))
        self.assertEqual(split_value(""), ("", ""))

    def test_negative_and_decimal(self):
        self.assertEqual(split_value("-1.5 ℃"), ("-1.5", "℃"))


class TestCardEdge(unittest.TestCase):
    """卡片用上下边 1px 色差代替投影（真投影实测 2.9ms→15.9ms）。"""

    def test_edge_tokens_exist(self):
        for name in ("dark",):
            t = theme.THEMES[name]
            for key in ("edge_top", "edge_bottom"):
                self.assertRegex(t[key], r"^#[0-9A-Fa-f]{6}$", f"{name}.{key}")

    def test_bottom_is_darker_than_top(self):
        """下边永远比上边暗 —— 明暗方向反了就成了「凹陷」。"""
        for name in ("dark",):
            t = theme.THEMES[name]
            self.assertLess(_lum(t["edge_bottom"]), _lum(t["edge_top"]), name)

    def test_qss_uses_per_side_colors(self):
        sheet = theme.qss(theme.DARK)
        self.assertIn("border-top-color", sheet)
        self.assertIn("border-bottom-color", sheet)

    def test_no_expensive_shadow_effect(self):
        """不许有人偷偷给卡片挂 QGraphicsDropShadowEffect。

        只扫 `app/ui/`（theme.py 里出现这个词是在注释里解释为什么不用它），
        并且只认带 `(` 的构造函数调用，避免把注释也算成用法。
        """
        import io
        import os
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        hits = []
        for dirpath, _dirs, files in os.walk(os.path.join(root, "app", "ui")):
            for fn in files:
                if not fn.endswith(".py"):
                    continue
                src = io.open(os.path.join(dirpath, fn), encoding="utf-8").read()
                for line in src.splitlines():
                    if line.lstrip().startswith("#"):
                        continue
                    if "QGraphicsDropShadowEffect(" in line:
                        hits.append(f"{fn}: {line.strip()[:60]}")
        self.assertEqual(hits, [], f"这些地方挂了真投影：{hits}")


class TestContrast(unittest.TestCase):
    """次要文字也要能读：11px 的 faint 压在背景上不能低于 3:1。"""

    def test_faint_readable(self):
        for name in ("dark",):
            t = theme.THEMES[name]
            r_bg = contrast(t["faint"], t["bg"])
            r_card = contrast(t["faint"], t["surface"])
            self.assertGreaterEqual(min(r_bg, r_card), 3.0,
                                    f"{name} faint 对比度 {r_bg:.2f}/{r_card:.2f}")
            self.assertLess(r_bg, 6.0, "faint 太亮就不再是「次要」了")

    def test_text_is_strong(self):
        for name in ("dark",):
            t = theme.THEMES[name]
            self.assertGreaterEqual(contrast(t["text"], t["surface"]), 7.0, name)

    def test_only_one_skin(self):
        """v2.0.0 只剩深色一套令牌，别有人偷偷加回第二套。"""
        self.assertEqual(list(theme.THEMES), ["dark"])


if __name__ == "__main__":
    unittest.main()
