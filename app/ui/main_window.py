# -*- coding: utf-8 -*-
"""主窗口：左侧导航 rail + 八个工作台。

日志分析 / 蓝屏转储 / 清理优化 / 残留治理 / 系统优化 / 网络诊断 / 系统体检 / U 盘测速
"""
from __future__ import annotations

import os
import time

from PyQt5.QtCore import QSize, Qt, QThread, QTimer
from PyQt5.QtWidgets import (QApplication, QButtonGroup, QFrame, QHBoxLayout,
                             QLabel, QMainWindow, QStackedWidget, QToolButton,
                             QVBoxLayout, QWidget)

from .. import theme
from ..core import settings
from ..core.report import COMPANY, VERSION
from . import motion, notice
from .clean_page import CleanPage
from .dump_page import DumpPage
from .health_page import HealthPage
from .usb_page import UsbPage
from .log_page import LogPage
from .net_page import NetPage
from .optimize_page import OptimizePage
from .residue_page import ResiduePage
from .tables import begin_wait_window, end_wait_window, stop_all
from .widgets import app_icon, make_icon

APP_NAME = "系统万能工具箱"

# 每个工作台的图标与顶部提示
TABS = (
    ("日志分析", "log", "拖入 .evtx 文件，或直接读取本机实时日志通道"),
    ("蓝屏转储", "crash", "支持 MDMP 与内核转储（MEMORY.DMP）"),
    ("清理优化", "clean", "垃圾清理 · 大文件 · 大目录 · 磁盘优化"),
    ("残留治理", "shield", "注册表冗余 · 卸载残留，只报有证据的项"),
    ("系统优化", "tune", "系统信息 · 启动项 · 服务调优 · 响应微调"),
    ("网络诊断", "net", "网卡 · 外网 · 网关 · DNS · 端口 · 代理 / hosts"),
    ("系统体检", "health", "设备驱动 · 升级条件 · 实时占用 · 安全基线 · 系统文件 · "
                          "磁盘 SMART · 硬件错误 · 崩溃转储 · 环境变量 · 驱动体检"),
    ("U 盘测速", "speed", "顺序读写 · 4K 随机读 · 接口判定 · 同盘历史对比"),
)

# 页面按 TABS 顺序懒建：启动时只建第一个工作台，其余首次切换时才建。
# 八个页面全量构造实测 1081ms（清理 / 日志 / 体检最重），而用户开机只看第一个。
PAGE_CLASSES = (LogPage, DumpPage, CleanPage, ResiduePage, OptimizePage,
                NetPage, HealthPage, UsbPage)
PAGE_ATTRS = ("log_page", "dump_page", "clean_page", "residue_page",
              "optimize_page", "net_page", "health_page", "usb_page")


class RailButton(QToolButton):
    def __init__(self, text: str, icon: str, parent=None):
        super().__init__(parent)
        self.setText(text)
        self.setIcon(make_icon(icon, theme.current()["dim"], 22))
        self.setIconSize(QSize(22, 22))
        self.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
        self.setObjectName("RailBtn")
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(64, 58)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"{APP_NAME} · 日志 / 转储 / 清理 / 残留 / 优化 / 网络 / 体检 / 测速")
        # 主题切换会整窗重建，所以每个新窗口都要重新挂一次图标
        ic = app_icon()
        if not ic.isNull():
            self.setWindowIcon(ic)
        self.resize(1440, 900)
        self.setMinimumSize(1180, 740)
        self.setAcceptDrops(True)
        self._restore_geometry()
        self._crash_times: list = []
        self._build()

    # ---------------- 构建
    def _build(self) -> None:
        root = QWidget()
        root.setObjectName("Root")
        self.setCentralWidget(root)
        lay = QHBoxLayout(root)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.rail = self._build_rail()
        lay.addWidget(self.rail)

        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(0)
        rl.addWidget(self._build_header())
        # 页内提示的宿主：横跨内容区、紧贴页头下沿。
        # 放在 stack 外面而不是每个页面里，是为了切页时提示不跟着页面一起消失
        # （确认条还在等回答时尤其重要）。
        if getattr(self, "notice_host", None) is None:
            self.notice_host = notice.NoticeHost()
        rl.addWidget(self.notice_host)
        self.stack = QStackedWidget()
        self._build_pages()
        rl.addWidget(self.stack, 1)
        lay.addWidget(right, 1)

        self._sync_rail_icons()
        # 让 pill 先就位（此时布局还没跑，resizeEvent 会再校正一次）
        if getattr(self, "rail", None) is not None and getattr(self, "stack", None):
            self.rail.set_active(self.stack.currentIndex(), animate=False)
        if not getattr(self, "_sb_done", False):
            self._build_statusbar()
            self._sb_done = True

    def _build_rail(self) -> QWidget:
        rail = motion.RailView()
        rail.setFixedWidth(84)
        v = QVBoxLayout(rail)
        v.setContentsMargins(10, 16, 10, 14)
        v.setSpacing(8)

        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        self.rail_buttons = []
        for i, (text, icon, _hint) in enumerate(TABS):
            b = RailButton(text, icon)
            self.group.addButton(b, i)
            v.addWidget(b)
            rail.add(b)
            self.rail_buttons.append(b)
        self.btn_log = self.rail_buttons[0]
        self.btn_dump = self.rail_buttons[1]
        self.btn_clean = self.rail_buttons[2]
        self.btn_residue = self.rail_buttons[3]
        self.btn_opt = self.rail_buttons[4]
        self.btn_net = self.rail_buttons[5]
        self.btn_health = self.rail_buttons[6]
        self.btn_usb = self.rail_buttons[7]
        self.btn_log.setChecked(True)
        self.group.idClicked.connect(self._switch)
        v.addStretch(1)

        self.btn_about = QToolButton()
        self.btn_about.setObjectName("RailBtn")
        self.btn_about.setText("关于")
        self.btn_about.setIcon(make_icon("info", theme.current()["dim"], 20))
        self.btn_about.setIconSize(QSize(20, 20))
        self.btn_about.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
        self.btn_about.setCursor(Qt.PointingHandCursor)
        self.btn_about.setFixedSize(64, 54)
        self.btn_about.clicked.connect(self._about)
        v.addWidget(self.btn_about)
        return rail

    def _build_header(self) -> QWidget:
        bar = QFrame()
        bar.setFixedHeight(66)
        t = theme.current()
        bar.setStyleSheet(f"background:{t['surface']}; border-bottom:1px solid {t['border']};")
        h = QHBoxLayout(bar)
        h.setContentsMargins(20, 10, 20, 10)
        h.setSpacing(10)
        box = QVBoxLayout()
        box.setSpacing(1)
        self.title = QLabel(APP_NAME)
        self.title.setObjectName("H1")
        box.addWidget(self.title)
        self.subtitle = QLabel("事件日志 · 崩溃转储 · 垃圾清理 · 残留治理 · 系统优化 · "
                               "网络诊断 · 系统体检 · U 盘测速")
        self.subtitle.setProperty("role", "dim")
        box.addWidget(self.subtitle)
        h.addLayout(box)
        h.addStretch(1)
        self.hint = QLabel(TABS[0][2])
        self.hint.setProperty("role", "faint")
        h.addWidget(self.hint)
        return bar

    def _build_pages(self) -> None:
        # 先铺 7 个空占位，保证 stack 索引与导航按钮一一对应；
        # 真正的页面在 _ensure_page 里 replaceWidget 顶上去。
        self._pages: list[QWidget | None] = [None] * len(PAGE_CLASSES)
        for name in PAGE_ATTRS:
            setattr(self, name, None)
        for _ in PAGE_CLASSES:
            self.stack.addWidget(QWidget())
        self._ensure_page(0)

    def _ensure_page(self, idx: int) -> QWidget:
        """按需构建第 idx 个工作台（已建过就直接返回）。"""
        w = self._pages[idx]
        if w is not None:
            return w
        w = PAGE_CLASSES[idx]()
        w.status.connect(self._say)
        w.notice_host = self.notice_host       # 页面统一从这里发提示 / 要确认
        if idx == 0:
            w.dataReady.connect(self._log_ready)
        elif idx == 1:
            w.requestLogAt.connect(self._jump_to_log_at)
            w.set_log_context(self._crash_times)
        old = self.stack.widget(idx)
        # PyQt5 的 QStackedWidget 没暴露 replaceWidget，用「插入再摘占位」
        # 保持索引一一对应（插入把占位推到 idx+1，摘掉后正好复原）。
        # 注意：被摘掉的占位如果正好是 current，Qt 会把 current 挪到别的格子，
        # 所以这里先记下、事后还原 —— 否则启动后第一页会显示成空白占位。
        cur = self.stack.currentIndex()
        self.stack.insertWidget(idx, w)
        if old is not None:
            self.stack.removeWidget(old)
            old.deleteLater()
        self.stack.setCurrentIndex(cur)
        self._pages[idx] = w
        setattr(self, PAGE_ATTRS[idx], w)
        return w

    def page(self, idx: int) -> QWidget:
        """取第 idx 个工作台（必要时构建）。自检 / 外部调用统一走这里。"""
        return self._ensure_page(idx)

    def _build_statusbar(self) -> None:
        sb = self.statusBar()
        sb.setSizeGripEnabled(True)
        self.status_label = QLabel("就绪")
        sb.addWidget(self.status_label, 1)
        right = QLabel(f"{COMPANY} · v{VERSION}")
        right.setProperty("role", "faint")
        sb.addPermanentWidget(right)
        sb.setFixedHeight(28)

    # ---------------- 交互
    def _switch(self, idx: int) -> None:
        # 先建好再切：占位控件若是当前页，切过去会先闪一下空白
        self._ensure_page(idx)
        same = self.stack.currentIndex() == idx
        self.stack.setCurrentIndex(idx)
        # 顺手修掉一个老问题：主题重建后 rail 的 checked 一直停在第 1 个，
        # 内容和选中态对不上（pill 上来以后会更显眼）
        if idx < len(getattr(self, "rail_buttons", [])):
            self.rail_buttons[idx].setChecked(True)
        if self.rail is not None:
            self.rail.set_active(idx, animate=not same)
        self.hint.setText(TABS[idx][2])
        self._sync_rail_icons()
        # 换了工作台就把上一条提示收掉，免得在 A 页看到 B 页的「已清理」
        if not same and getattr(self, "notice_host", None) is not None:
            self.notice_host.clear()
        # 真换了页才淡入：原地重复点同一个按钮不该闪一下
        if not same:
            motion.fade_in(self._pages[idx])

    def _say(self, text: str) -> None:
        if getattr(self, "status_label", None) is not None:
            self.status_label.setText(text)

    def _log_ready(self, events, analysis) -> None:
        self._crash_times = list(getattr(analysis, "crash_times", []) or [])
        if self.dump_page is not None:
            self.dump_page.set_log_context(self._crash_times)

    def _jump_to_log_at(self, when) -> None:
        self.btn_log.setChecked(True)
        self._switch(0)

        def _focus():
            # 60ms 内可能刚好碰上主题切换换掉了日志页，这里必须咽下
            # 「对象已析构」——否则 PyQt5 会把整个进程 abort 掉
            try:
                self.log_page.focus_time(when)
            except RuntimeError:
                pass

        QTimer.singleShot(60, _focus)

    def _about(self) -> None:
        """关于：原来是 QMessageBox.about（模态、浅色、跟主题割裂），
        改成一条常驻的页内提示，点 × 收掉。"""
        rows = (
            ("日志分析", "原生 wevtapi 读取 .evtx 与实时通道，关联出崩溃、掉电、磁盘、服务类结论"),
            ("蓝屏转储", "纯本地解析 MDMP 与内核转储：蓝屏码、故障模块、模块清单"),
            ("清理优化", "白名单式垃圾扫描（默认进回收站）、大文件、目录下钻、磁盘优化与 TRIM"),
            ("残留治理", "注册表孤立引用（自动 .reg 备份）、卸载残留、文件占用排查"),
            ("系统优化", "系统概况、启动项启停、服务白名单调优、响应微调（逐项可还原）"),
            ("网络诊断", "网卡 · 外网 · 网关 · DNS · 端口 · 代理 / hosts，按层定位「连不上」"),
            ("系统体检", "设备驱动 · 升级条件 · 实时占用 · 安全基线 · 系统文件 · 磁盘 · 硬件错误 · 崩溃转储 · 环境变量 · 驱动签名"),
            ("U 盘测速", "顺序读写、4K 随机读、接口判定、同一块盘的历史对比"),
        )
        body = "<br>".join("· <b>%s</b> —— %s" % (a, b) for a, b in rows)
        dim = theme.current()["dim"]      # 不能用固定色：换配色时固定色会糊成一片
        notice.note(
            self, "info",
            "<b>%s SystemToolbox v%s</b>　·　%d 个工作台　·　发行：%s<br>%s<br>"
            "<span style='color:%s'>快捷键：Ctrl+1~8 切换工作台"
            " · F5 重新读取日志　·　全程只读，改动类操作逐项可还原</span>"
            % (APP_NAME, VERSION, len(TABS), COMPANY, body, dim),
            timeout=0)

    def _restore_geometry(self) -> None:
        """恢复上次的窗口位置。

        必须校验这份几何还在屏幕上：笔记本外接屏拔掉以后，
        上次存的 (x, y) 可能落在已经不存在的显示器上，
        直接用会得到「窗口在屏幕外」—— 用户以为程序没启动。
        """
        g = settings.geometry()
        if not g:
            return
        from PyQt5.QtCore import QRect

        rect = QRect(*g)
        for scr in QApplication.screens():
            if scr.availableGeometry().intersects(rect):
                self.setGeometry(rect)
                return

    def _save_prefs(self) -> None:
        try:
            g = self.normalGeometry() if self.isMaximized() else self.geometry()
            settings.save({
                "win": "%d,%d,%d,%d" % (g.x(), g.y(), g.width(), g.height()),
            })
        except Exception:       # noqa: BLE001
            pass

    def closeEvent(self, ev) -> None:
        """关窗前必须收掉后台线程。

        否则 QThread 在窗口析构时仍在运行，Qt 会直接 abort
        （进程退出码 0xC0000409），用户看到的是「关了还报崩溃」。
        """
        self._save_prefs()
        try:
            if getattr(self.log_page, "_thread", None) is not None:
                self.log_page._cancel()
                self.log_page._teardown_thread()
        except Exception:      # noqa: BLE001
            pass
        # 关窗时所有页面共用一个收敛窗口，避免逐页各等一份预算
        begin_wait_window(1500)
        for page in PAGE_ATTRS[1:]:
            obj = getattr(self, page, None)
            if obj is not None and hasattr(obj, "shutdown"):
                try:
                    obj.shutdown()
                except Exception:      # noqa: BLE001
                    pass
        end_wait_window()
        stop_all(self)
        # 兜底：bookkeeping 漏登记的线程也要收（启动瞬间关窗最容易漏）
        threads = [t for t in self.findChildren(QThread) if t.isRunning()]
        seen = set()
        threads = [t for t in threads if not (id(t) in seen or seen.add(id(t)))]
        for t in threads:
            try:
                if hasattr(t, "stop"):
                    t.stop()
            except Exception:      # noqa: BLE001
                pass
        # 共用总预算，别逐个 wait —— 六个扫描线程各等 1.2 秒就是 7 秒关不掉窗
        deadline = time.monotonic() + 1.5
        for t in threads:
            left = deadline - time.monotonic()
            if left <= 0:
                break
            try:
                t.wait(int(left * 1000) + 1)
            except Exception:      # noqa: BLE001
                pass
        still = [t for t in threads if t.isRunning()]
        super().closeEvent(ev)
        if still:
            # 有线程卡在不可中断的系统调用里（CBS / SMART 事件查询要几十秒），
            # 让 Qt 继续析构就会 abort（用户看到的是「一关就报崩溃」，0xC0000409）。
            # 这时主动退出，退出码干净，代价只是不做 Qt 的收尾。
            os._exit(0)

    # ---------------- 皮肤
    # v2.0.0 起只有一套深色皮肤，「切主题」这条路径（整窗重建 + 旧树退役 +
    # 状态快照回填，原 200 行）随之整段删除。QSS 在启动时由 main.py 挂一次，
    # 之后没有任何运行期改样式的入口。将来若要再加皮肤，别恢复「销毁整棵树
    # 重建」这套：页面里带着扫描线程，退役树一旦析构在线程还活着的时候，
    # 进程会静默消失（0xC0000409 / 退出码 127 都踩过）。

    def _sync_rail_icons(self) -> None:
        t = theme.current()
        self.btn_about.setIcon(make_icon("info", t["dim"], 20))
        active = self.stack.currentIndex() if hasattr(self, "stack") else 0
        for i, b in enumerate(getattr(self, "rail_buttons", [])):
            b.setIcon(make_icon(TABS[i][1], t["accent"] if i == active else t["dim"], 22))

    # ---------------- 全窗口拖放
    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            for u in e.mimeData().urls():
                p = u.toLocalFile().lower()
                if p.endswith((".evtx", ".dmp", ".mdmp", ".hdmp")):
                    e.acceptProposedAction()
                    self.hint.setText("松手即开始分析")
                    return
        e.ignore()

    def dragLeaveEvent(self, e):
        self.hint.setText(TABS[self.stack.currentIndex()][2])
        super().dragLeaveEvent(e)

    def dropEvent(self, e):
        self.hint.setText(TABS[self.stack.currentIndex()][2])
        for u in e.mimeData().urls():
            path = u.toLocalFile()
            low = path.lower()
            if low.endswith(".evtx"):
                self.btn_log.setChecked(True)
                self._switch(0)
                self.page(0).set_file(path)
                return
            if low.endswith((".dmp", ".mdmp", ".hdmp")):
                self.btn_dump.setChecked(True)
                self._switch(1)
                self.page(1).load_file(path)
                return

    def keyPressEvent(self, e):
        if e.modifiers() & Qt.ControlModifier:
            key_map = {Qt.Key_1: 0, Qt.Key_2: 1, Qt.Key_3: 2,
                       Qt.Key_4: 3, Qt.Key_5: 4, Qt.Key_6: 5,
                       Qt.Key_7: 6, Qt.Key_8: 7}
            if e.key() in key_map:
                i = key_map[e.key()]
                self.rail_buttons[i].setChecked(True)
                self._switch(i)
                return
        if e.key() == Qt.Key_F5 and self.stack.currentIndex() == 0:
            self.log_page._start()
            return
        super().keyPressEvent(e)
