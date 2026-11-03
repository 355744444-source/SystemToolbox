# -*- coding: utf-8 -*-
"""设计令牌与 QSS 生成。

设计方向：取证控制台 (Forensic Console)
- 高数据密度：表格行高 28px，时间/ID/地址统一等宽字体
- 语义色分级：严重(红) / 错误(橙红) / 警告(黄) / 信息(蓝) / 详细(灰)
- **单一深色主题**：v2.0.0 起移除浅色皮肤。

  浅色皮肤原本是给「打印、投屏、白天办公」准备的，但实际用下来两头不落好：
  深色才是这个工具的主场景（机房、值班室、盯盘），浅色反而要额外维护一套
  对比度、按钮层级、卡片边缘；两套皮肤各 30 个令牌，每次调色都得验两遍。
  现场反馈也都是「一直在用深色」。留一套，把维护成本还给人要看的地方。
"""
from __future__ import annotations

# ---------- 字体 ----------
# 全界面统一用「微软雅黑」正体（Microsoft YaHei）。
# 不用 "Microsoft YaHei UI"：那是系统 UI 变体，字形更窄、笔画偏细，
# 中文小字号下发虚；Office / 系统对话框用的是正体，视觉更一致。
FONT_FAMILY = "Microsoft YaHei"
FONT_FAMILIES = ("Microsoft YaHei", "Microsoft YaHei UI", "Segoe UI")
# 等宽只留给纯 ASCII 的技术数据（十六进制、地址、堆栈）；
# 中文按 setFamilies 逐字回落雅黑，避免落到 SimSun 变宋体。
MONO_FAMILY = "Cascadia Mono"
MONO_FAMILIES = ("Cascadia Mono", "Consolas", "Microsoft YaHei")

FONT_UI = '"Microsoft YaHei", "Microsoft YaHei UI", "Segoe UI", sans-serif'
FONT_MONO = '"Cascadia Mono", "Consolas", "Microsoft YaHei", monospace'

# ---------- 圆角刻度 ----------
# 只用四档，按「由外到内递减」取用，不临时拍数：
#   外容器（卡片 / 面板 / KPI）= lg
#   内嵌块（表格 / 文本域 / 下拉列表）= md
#   控件（按钮 / 输入框 / 菜单）= sm
#   微件（徽标 / 提示条 / 细条 / 复选框）= xs
# 嵌套时内层必须比外层小（一致曲率），否则两块圆角在交界处会「错牙」。
R = {"lg": 10, "md": 8, "sm": 6, "xs": 4}

DARK = {
    "name": "dark",
    "bg": "#0D1117",
    "surface": "#151B23",
    "surface2": "#1C242E",
    "surface3": "#232D3A",
    # 次要按钮（ghost）的填充色：比 surface3 再亮一档并带一点蓝调，
    # 保证在 surface 底上"一眼看出是个按钮"，又不跟蓝色实心主按钮抢焦点
    "btn_alt": "#2C3B4E",
    "btn_alt_hover": "#3A4E66",
    # 次要按钮填充：深色下比默认按钮暗一档，但仍明显高于页面底色
    "btn_ghost": "#232D3A",
    "btn_ghost_hover": "#2C3B4E",
    "border": "#2A3543",
    "border_strong": "#3B4A5C",
    # 卡片的「上亮下暗」1px 边缘：替代投影。实测 QGraphicsDropShadowEffect
    # 在 8 张卡片的页面上把一次重绘从 2.9ms 推到 15.9ms（离屏渲染），
    # 而上下边各调一档色的成本是 0。
    "edge_top": "#313D4D",
    "edge_bottom": "#222B37",
    "text": "#E6EDF3",
    "dim": "#8B98A5",
    "faint": "#5E6C7B",
    "accent": "#4C9AFF",
    "accent_hover": "#6FB1FF",
    "accent_soft": "#1B3A5C",
    "ok": "#3FB950",
    "warn": "#D6A020",
    "error": "#F0603C",
    "crit": "#FF5C5C",
    "info": "#58A6FF",
    "sel": "#16324F",
    "shadow": "rgba(0,0,0,0.45)",
}

# 浅色皮肤（v1.17.0 及以前的 LIGHT 令牌表）已移除。
# 页面里所有颜色都走 `theme.current()` 取 token，只要令牌齐，改主题不用碰界面代码；
# 反过来说，想让浅色回来就得把那张表整份补回 —— 连带 41 张截图、对比度单测
# 和「切主题重建窗口」那套逻辑，所以这次是删除而不是注释掉。

THEMES = {"dark": DARK}

_CURRENT = {"t": DARK}


def current() -> dict:
    """当前主题令牌，供自绘控件取色。"""
    return _CURRENT["t"]

# 级别 -> 语义色 key
LEVEL_COLORS = {
    1: "crit",
    2: "error",
    3: "warn",
    4: "info",
    5: "faint",
}


def level_color(level: int, theme: dict) -> str:
    return theme.get(LEVEL_COLORS.get(level, "dim"), theme["dim"])


def qss(t: dict) -> str:
    """按令牌生成整窗样式。"""
    from PyQt5.QtGui import QColor

    def rgba(hex_color: str, a: float) -> str:
        c = QColor(hex_color)
        return f"rgba({c.red()}, {c.green()}, {c.blue()}, {a})"

    danger_hover = rgba(t["crit"], 0.18)
    danger_idle = rgba(t["crit"], 0.10)
    return f"""
* {{
    font-family: {FONT_UI};
    font-size: 12px;
    color: {t['text']};
    outline: none;
}}
QWidget#Root, QMainWindow {{ background: {t['bg']}; }}

/* ---------- 左侧导航栏 ---------- */
QWidget#Rail {{
    background: {t['surface']};
    border-right: 1px solid {t['border']};
}}
QToolButton#RailBtn {{
    background: transparent;
    border: none;
    border-radius: {R['md']}px;
    padding: 8px 2px 4px 2px;
    color: {t['dim']};
    font-size: 11px;
}}
QToolButton#RailBtn:hover {{ background: {t['surface2']}; color: {t['text']}; }}
/* 选中态的「填充」交给 RailView 的 pill 层画 —— 只有它能在按钮之间滑。
   这里必须透明，否则会盖住 pill，看见的还是各自闪背景。 */
QToolButton#RailBtn:checked {{
    background: transparent;
    color: {t['accent']};
    font-weight: 600;
}}

/* ---------- 顶部标题 ---------- */
QLabel#H1 {{ font-size: 15px; font-weight: 600; letter-spacing: 0.2px; }}
QLabel#H2 {{ font-size: 13px; font-weight: 600; }}
QLabel#H3 {{ font-size: 12px; font-weight: 600; padding-top: 2px; }}
QLabel[role="dim"] {{ color: {t['dim']}; }}
QLabel[role="faint"] {{ color: {t['faint']}; font-size: 11px; }}
QLabel[role="mono"] {{ font-family: {FONT_UI}; }}
QLabel[role="kpi"] {{
    font-family: {FONT_UI};
    font-size: 22px;
    font-weight: 700;
}}
QLabel[role="kpiLabel"] {{ color: {t['dim']}; font-size: 11px; }}

/* ---------- 卡片 ---------- */
/* 卡片用「上边亮一档、下边暗一档」的 1px 边缘代替投影：
   肉眼读作浮起，成本是 0（真投影会让 8 卡片的页面重绘 2.9ms→15.9ms）。 */
QFrame[role="card"], QFrame[role="kpi"], QFrame[role="findingCard"] {{
    background: {t['surface']};
    border: 1px solid {t['border']};
    border-top-color: {t['edge_top']};
    border-bottom-color: {t['edge_bottom']};
    border-radius: {R['lg']}px;
}}
QFrame[role="cardFlat"] {{
    background: {t['surface2']};
    border: 1px solid {t['border']};
    border-radius: {R['md']}px;
}}
QFrame[role="accentBar"] {{ border: none; border-radius: {R['xs']}px; }}
QFrame[role="hr"] {{ background: {t['border']}; border: none; max-height: 1px; }}
QFrame[role="findingCard"]:hover {{
    border-color: {t['accent']};
    background: {t['surface2']};
}}

/* ---------- 按钮 ---------- */
/* 层级：primary（蓝实心）> 默认/plain（亮填充）> ghost（次亮填充）> bare（无底） */
QPushButton {{
    background: {t['btn_alt']};
    border: 1px solid {t['border_strong']};
    border-radius: {R['sm']}px;
    padding: 6px 14px;
    color: {t['text']};
}}
QPushButton:hover {{ background: {t['btn_alt_hover']}; border-color: {t['accent']}; }}
QPushButton:pressed {{ background: {t['accent_soft']}; }}
QPushButton:disabled {{ color: {t['faint']}; border-color: {t['border']}; background: {t['surface2']}; }}
QPushButton[role="primary"] {{
    background: {t['accent']};
    border: 1px solid {t['accent']};
    color: #FFFFFF;
    font-weight: 600;
}}
QPushButton[role="primary"]:hover {{ background: {t['accent_hover']}; border-color: {t['accent_hover']}; }}
/* 次要按钮：有填充 + 描边，与页面底色拉开层次；比默认按钮低一档 */
QPushButton[role="ghost"] {{
    background: {t['btn_ghost']};
    border: 1px solid {t['border_strong']};
    color: {t['text']};
    font-weight: 500;
    padding: 5px 12px;
}}
QPushButton[role="ghost"]:hover {{
    background: {t['btn_ghost_hover']};
    border-color: {t['accent']};
    color: {t['text']};
}}
QPushButton[role="ghost"]:pressed {{
    background: {t['accent_soft']};
    border-color: {t['accent']};
}}
QPushButton[role="ghost"]:disabled {{
    background: {t['surface2']};
    border-color: {t['border']};
    color: {t['faint']};
}}
/* 纯文字按钮：只给贴在输入框/行内的小图标用（如清除来源的 ×），保持无底 */
QPushButton[role="bare"] {{
    background: transparent;
    border: 1px solid transparent;
    color: {t['dim']};
    padding: 5px 8px;
}}
QPushButton[role="bare"]:hover {{ color: {t['text']}; background: {t['surface2']}; }}
QPushButton[role="chip"] {{
    background: transparent;
    border: 1px solid {t['border']};
    border-radius: {R['lg']}px;   /* 高约 21px，10px 即胶囊形 */
    padding: 3px 12px;
    color: {t['dim']};
    font-size: 11px;
}}
QPushButton[role="chip"]:checked {{
    background: {t['accent_soft']};
    border-color: {t['accent']};
    color: {t['accent']};
    font-weight: 600;
}}
/* 危险动作（清理 / 删除 / 修复）：淡红填充 + 红描边，够醒目又不至于误点 */
QPushButton#DangerBtn {{
    background: {danger_idle};
    border: 1px solid {t['crit']};
    color: {t['crit']};
    font-weight: 600;
    padding: 6px 14px;
}}
QPushButton#DangerBtn:hover {{ background: {danger_hover}; border-color: {t['crit']}; }}
QPushButton#DangerBtn:disabled {{
    color: {t['faint']};
    border-color: {t['border']};
    background: {t['surface2']};
}}

/* ---------- 页内提示 / 页内确认（替代 QMessageBox） ---------- */
/* 左侧 3px 色条是语义色，右侧圆角统一走 R['md']。
   只设 border-left-color 而不重设整条 border，是为了让上下右边框保持 1px 的淡色。 */
QFrame#NoticeBar, QFrame#AskBar {{
    background: {t['surface2']};
    border: 1px solid {t['border']};
    border-left: 3px solid {t['accent']};
    border-radius: {R['md']}px;
}}
QFrame#NoticeBar[kind="info"] {{ border-left-color: {t['info']}; }}
QFrame#NoticeBar[kind="ok"]   {{ border-left-color: {t['ok']}; }}
QFrame#NoticeBar[kind="warn"] {{ border-left-color: {t['warn']}; }}
QFrame#NoticeBar[kind="error"] {{ border-left-color: {t['error']}; }}
QFrame#AskBar[kind="warn"] {{ border-left-color: {t['warn']}; }}
/* 危险确认：淡红底 + 红边，跟弹窗时代的「红色感叹号」一样醒目，但不抢焦点 */
QFrame#AskBar[kind="danger"] {{
    background: {danger_idle};
    border-color: {rgba(t['crit'], 0.45)};
    border-left-color: {t['crit']};
}}
QLabel#NoticeText {{ color: {t['text']}; font-size: 12px; }}
QLabel#AskTitle {{ color: {t['text']}; font-size: 12px; font-weight: 600; }}
QFrame#AskBar[kind="danger"] QLabel#AskTitle {{ color: {t['crit']}; }}
QWidget#NoticeHost {{ background: transparent; }}

/* ---------- 输入 ---------- */
QLineEdit, QComboBox, QAbstractSpinBox, QDateTimeEdit {{
    background: {t['surface2']};
    border: 1px solid {t['border']};
    border-radius: {R['sm']}px;
    padding: 5px 9px;
    selection-background-color: {t['accent']};
    selection-color: #FFFFFF;
}}
QLineEdit:focus, QComboBox:focus, QDateTimeEdit:focus {{ border-color: {t['accent']}; }}
QLineEdit::placeholder {{ color: {t['faint']}; }}
/* 下拉框：不覆盖 drop-down / down-arrow，交给 Fusion 按调色板绘制箭头 */
/* 可编辑下拉（如磁盘选择）：内嵌输入框只做文字容器，边框交给外层 */
QComboBox QLineEdit {{
    background: transparent;
    border: none;
    padding: 0px;
    margin: 0px;
}}
QComboBox QAbstractItemView {{
    background: {t['surface']};
    border: 1px solid {t['border_strong']};
    border-radius: {R['sm']}px;
    selection-background-color: {t['accent_soft']};
    selection-color: {t['text']};
    outline: none;
    padding: 4px;
}}

/* ---------- 表格 ---------- */
QTableView {{
    background: {t['surface']};
    alternate-background-color: {t['surface2']};
    border: 1px solid {t['border']};
    border-radius: {R['md']}px;
    gridline-color: transparent;
    selection-background-color: {t['sel']};
    selection-color: {t['text']};
}}
QTableView::item {{ padding: 0px 8px; border: none; }}
/* 显式指定选中态颜色：否则窗口失焦时 Qt 会退回系统浅色高亮，深色主题里冒白条 */
QTableView::item:selected, QTableView::item:selected:!active {{
    background: {t['sel']};
    color: {t['text']};
}}
QHeaderView::section {{
    background: {t['surface2']};
    color: {t['dim']};
    border: none;
    border-bottom: 1px solid {t['border']};
    padding: 6px 8px;
    font-size: 11px;
    font-weight: 600;
}}
QHeaderView::section:hover {{ color: {t['text']}; }}
QTableCornerButton::section {{ background: {t['surface2']}; border: none; }}

/* ---------- 页签 ---------- */
QTabWidget::pane {{ border: none; background: transparent; }}
QTabBar::tab {{
    background: transparent;
    color: {t['dim']};
    padding: 7px 14px;
    margin-right: 4px;
    border: none;
    border-bottom: 2px solid transparent;
    font-size: 12px;
}}
QTabBar::tab:hover {{ color: {t['text']}; }}
QTabBar::tab:selected {{
    color: {t['accent']};
    border-bottom: 2px solid {t['accent']};
    font-weight: 600;
}}

/* ---------- 分隔条 ---------- */
QSplitter::handle {{ background: transparent; }}
QSplitter::handle:horizontal {{ width: 8px; }}
QSplitter::handle:vertical {{ height: 8px; }}
QSplitter::handle:hover {{ background: {t['surface3']}; }}

/* ---------- 滚动条 ---------- */
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{
    background: {t['border_strong']};
    border-radius: 4px;
    min-height: 28px;
}}
QScrollBar::handle:vertical:hover {{ background: {t['accent']}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{
    background: {t['border_strong']};
    border-radius: 4px;
    min-width: 28px;
}}
QScrollBar::handle:horizontal:hover {{ background: {t['accent']}; }}

/* ---------- 滚动容器：必须透明，否则深色主题会漏出系统浅色底 ---------- */
QScrollArea {{ background: transparent; border: none; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
QAbstractScrollArea::viewport {{ background: transparent; }}
QTabWidget::pane {{ background: transparent; }}
QStackedWidget {{ background: transparent; }}
QStatusBar::item {{ border: none; }}

/* ---------- 文本域 ---------- */
QPlainTextEdit[role="tech"], QTextEdit[role="tech"] {{
    font-family: {FONT_MONO};
    font-size: 12px;
}}
QPlainTextEdit, QTextEdit {{
    background: {t['surface2']};
    border: 1px solid {t['border']};
    border-radius: {R['md']}px;
    padding: 8px;
    font-family: {FONT_UI};
    font-size: 12px;
    selection-background-color: {t['accent']};
    selection-color: #FFFFFF;
}}

/* ---------- 其他 ---------- */
QProgressBar {{
    background: {t['surface2']};
    border: none;
    border-radius: {R['xs']}px;
    height: 6px;
    text-align: center;
    color: transparent;
}}
QProgressBar::chunk {{ background: {t['accent']}; border-radius: {R['xs']}px; }}
QCheckBox {{ color: {t['dim']}; spacing: 7px; }}
QCheckBox::indicator {{
    width: 14px; height: 14px;
    border: 1px solid {t['border_strong']};
    border-radius: 3px;          /* 14px 方块上 3px 是最接近圆的方角 */
    background: {t['surface2']};
}}
QCheckBox::indicator:checked {{
    background: {t['accent']};
    border-color: {t['accent']};
    image: none;
}}
QToolTip {{
    background: {t['surface3']};
    color: {t['text']};
    border: 1px solid {t['border_strong']};
    padding: 5px 8px;
    border-radius: {R['sm']}px;
}}
QStatusBar {{ background: {t['surface']}; border-top: 1px solid {t['border']}; color: {t['dim']}; }}
QMenu {{
    background: {t['surface']};
    border: 1px solid {t['border_strong']};
    padding: 5px;
    border-radius: {R['sm']}px;
}}
QMenu::item {{ padding: 6px 22px 6px 14px; border-radius: {R['xs']}px; }}
QMenu::item:selected {{ background: {t['accent_soft']}; color: {t['text']}; }}
"""


def palette(t: dict):
    """与 QSS 配套的调色板：让未被 QSS 覆盖的部件（滚动区 viewport、
    未命名容器、原生对话框）也保持一致，避免深色主题下漏出浅色底。"""
    from PyQt5.QtGui import QColor, QPalette
    p = QPalette()
    p.setColor(QPalette.Window, QColor(t["bg"]))
    p.setColor(QPalette.WindowText, QColor(t["text"]))
    p.setColor(QPalette.Base, QColor(t["surface"]))
    p.setColor(QPalette.AlternateBase, QColor(t["surface2"]))
    p.setColor(QPalette.Text, QColor(t["text"]))
    p.setColor(QPalette.Button, QColor(t["surface2"]))
    p.setColor(QPalette.ButtonText, QColor(t["text"]))
    p.setColor(QPalette.BrightText, QColor(t["crit"]))
    p.setColor(QPalette.Highlight, QColor(t["accent"]))
    p.setColor(QPalette.HighlightedText, QColor("#FFFFFF"))
    p.setColor(QPalette.ToolTipBase, QColor(t["surface3"]))
    p.setColor(QPalette.ToolTipText, QColor(t["text"]))
    p.setColor(QPalette.PlaceholderText, QColor(t["faint"]))
    p.setColor(QPalette.Link, QColor(t["accent"]))
    p.setColor(QPalette.Light, QColor(t["border"]))
    p.setColor(QPalette.Midlight, QColor(t["border"]))
    p.setColor(QPalette.Mid, QColor(t["border_strong"]))
    p.setColor(QPalette.Dark, QColor(t["bg"]))
    # 失焦窗口（切到别的程序再回来、或被弹窗盖住）不能退回系统浅色高亮，
    # 否则深色主题里会出现一条白底行
    for group in (QPalette.Inactive,):
        p.setColor(group, QPalette.Highlight, QColor(t["sel"]))
        p.setColor(group, QPalette.HighlightedText, QColor(t["text"]))
        p.setColor(group, QPalette.Base, QColor(t["surface"]))
        p.setColor(group, QPalette.Text, QColor(t["text"]))
    for group in (QPalette.Disabled,):
        p.setColor(group, QPalette.Text, QColor(t["faint"]))
        p.setColor(group, QPalette.WindowText, QColor(t["faint"]))
        p.setColor(group, QPalette.ButtonText, QColor(t["faint"]))
        p.setColor(group, QPalette.Highlight, QColor(t["sel"]))
        p.setColor(group, QPalette.HighlightedText, QColor(t["faint"]))
    return p


def apply_theme(app, name: str = "dark", target=None) -> dict:
    """挂上深色样式。

    v2.0.0 起只剩一套皮肤，`name` 参数保留只为兼容历史调用点
    （`main.py` 读偏好、老探针脚本、`--selftest`）—— 传什么都得到深色。
    真要再加一套皮肤，改这里和 `THEMES` 就够了。

    `target=None`（默认）把 QSS 挂到 QApplication 上 —— 启动时用这个。
    传 `target=<控件>` 则只挂到那个控件：给离屏探针用，避免把 QSS 糊到整个
    进程（多个探针串跑时会互相踩）。
    """
    t = THEMES.get(name, DARK)
    _CURRENT["t"] = t
    try:
        app.setPalette(palette(t))
    except Exception:
        pass
    sheet = qss(t)
    if target is not None:
        target.setStyleSheet(sheet)
    else:
        app.setStyleSheet(sheet)
    return t
