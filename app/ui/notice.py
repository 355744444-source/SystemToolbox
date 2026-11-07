# -*- coding: utf-8 -*-
"""页内提示与页内确认 —— 让运行时不再弹任何模态窗口。

## 为什么要换掉 QMessageBox

原来每个页面都在用 `QMessageBox`，三个问题：

1. **模态阻塞**：批量操作时会连点十几次「确定」，每次都要把手从鼠标主键挪到对话框；
2. **外观割裂**：系统对话框是浅色的，跟本工具的深色主题完全不搭，截图里很扎眼；
3. **信息重复**：`information()` 的正文跟状态栏那句话几乎一样，等于同一条消息说两遍。

## 现在的做法

页面顶部的一条横幅：

- `note()` —— 提示型（完成 / 失败 / 说明），几秒后自己消失，**不打断手上的事**；
- `ask()`  —— 确认型（删文件、还原微调这类），**红色或黄色常驻**，点了才继续，
  安全性不降级，只是从「弹窗」变成了「页面里的一条」。

`ask()` 是**异步**的：不再同步返回 bool，而是接受 `on_yes` 回调。
原先 `if QMessageBox.question(...) == Yes: 干活()` 要改成
`ask(..., on_yes=干活)` —— 多一层缩进，但没有阻塞，也不会出现
「确认框还开着、主人已经在别处点了按钮」的重入问题。

## 宿主

`NoticeHost` 挂在主窗口（页头与内容区之间），由 `MainWindow._ensure_page`
注入成每个页面的 `notice_host`。所以切页时提示不会被页面带走 ——
但切页会主动清空，免得在 A 页看到 B 页的提示。

页面里不直接 new 这些控件，统一走模块级函数：

    from . import notice
    notice.note(self, "ok", "已清理 123 个文件")
    notice.ask(self, "确认删除", "将移入回收站 123 个文件",
               ok_text="移入回收站", danger=True, on_yes=self._do_delete)

拿不到宿主（自检、离屏探针）时这两件事都静默跳过，不会抛异常。
"""
from __future__ import annotations

from PyQt5.QtCore import QTimer, Qt, pyqtSignal
from PyQt5.QtWidgets import (QFrame, QHBoxLayout, QLabel, QSizePolicy,
                             QVBoxLayout, QWidget)

from . import motion
from .widgets import bare_button, ghost_button, primary_button

#: 语义 → 主题令牌名。danger 走 crit（最红的那档），专给「删了就没了」用。
KIND_TOKEN = {
    "info": "info",
    "ok": "ok",
    "warn": "warn",
    "error": "error",
    "danger": "crit",
}

#: 自动消失时长（秒）。错误留久一点，方便边看边截图；0 = 不自动消失。
KIND_TIMEOUT = {"info": 6.0, "ok": 6.0, "warn": 9.0, "error": 14.0}

#: 同时最多几条。再多就顶掉最老的那条 —— 堆满一屏反而看不清最新的。
MAX_BARS = 4


class NoticeBar(QFrame):
    """一条提示横幅。左侧 3px 色条是语义色，正文可选中复制。"""

    closed = pyqtSignal(object)

    def __init__(self, kind: str = "info", text: str = "",
                 timeout: float = 6.0, parent=None):
        super().__init__(parent)
        self.setObjectName("NoticeBar")
        self.setProperty("kind", kind)
        self._kind = kind
        self._timeout = timeout or 0.0
        self._timer = None

        h = QHBoxLayout(self)
        h.setContentsMargins(14, 9, 8, 9)
        h.setSpacing(10)
        self.label = QLabel(text)
        self.label.setObjectName("NoticeText")
        self.label.setWordWrap(True)
        self.label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        h.addWidget(self.label, 1)
        self._lay = h

        self.btn_close = bare_button("×")
        self.btn_close.setToolTip("关闭这条提示")
        self.btn_close.setFixedWidth(24)
        self.btn_close.clicked.connect(self.dismiss)
        h.addWidget(self.btn_close, 0, Qt.AlignTop)

        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
        if timeout and timeout > 0:
            self.start_timer(timeout)
        motion.fade_in(self)

    # ---- 生命周期
    def start_timer(self, timeout: float) -> None:
        """重新开始倒计时。鼠标移进来时暂停，移走再续上 —— 正在读的时候别消失。"""
        self.stop_timer()
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(int(timeout * 1000))
        self._timer.timeout.connect(self.dismiss)
        self._timer.start()

    def stop_timer(self) -> None:
        if self._timer is not None:
            self._timer.stop()
            self._timer.deleteLater()
            self._timer = None

    def enterEvent(self, _):
        self.stop_timer()

    def leaveEvent(self, _):
        # 只有提示型需要续命，确认型本来就不该自己走；
        # timeout=0 的（关于面板这类）移开鼠标也不许自己消失
        if self._timeout > 0 and self._kind in KIND_TIMEOUT:
            self.start_timer(self._timeout)

    def dismiss(self) -> None:
        self.stop_timer()
        self.closed.emit(self)

    def set_text(self, text: str) -> None:
        self.label.setText(text)


class AskBar(QFrame):
    """一条页内确认。跟 NoticeBar 长得像，但按钮是「取消 / 确认」并且不会自动消失。"""

    answered = pyqtSignal(object, bool)     # (self, 用户是否点了确认)

    def __init__(self, title: str = "", text: str = "", ok_text: str = "确认",
                 cancel_text: str = "取消", danger: bool = False, parent=None):
        super().__init__(parent)
        self.setObjectName("AskBar")
        self.setProperty("kind", "danger" if danger else "warn")
        self._done = False

        h = QHBoxLayout(self)
        h.setContentsMargins(14, 10, 12, 10)
        h.setSpacing(10)
        box = QVBoxLayout()
        box.setSpacing(2)
        self.title = QLabel(title)
        self.title.setObjectName("AskTitle")
        self.title.setWordWrap(True)
        box.addWidget(self.title)
        self.label = QLabel(text)
        self.label.setObjectName("NoticeText")
        self.label.setWordWrap(True)
        self.label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        box.addWidget(self.label)
        h.addLayout(box, 1)

        # 取消用文字链接，不用按钮：v2.2.0 起所有动作按钮都是同一种蓝实心，
        # 两条一模一样的蓝按钮并排表示「取消」和「确认」是纯坑 ——
        # 一条确认里只能有一个实心动作，取消退成文字。
        self.btn_cancel = bare_button(cancel_text)
        self.btn_cancel.clicked.connect(lambda: self._answer(False))
        h.addWidget(self.btn_cancel, 0, Qt.AlignVCenter)

        self.btn_ok = primary_button(ok_text)
        if danger:
            self.btn_ok.setObjectName("DangerBtn")
            # objectName 变了必须重新 polish，否则还是主按钮的蓝色
            self.btn_ok.style().unpolish(self.btn_ok)
            self.btn_ok.style().polish(self.btn_ok)
        self.btn_ok.setDefault(True)
        self.btn_ok.clicked.connect(lambda: self._answer(True))
        h.addWidget(self.btn_ok, 0, Qt.AlignVCenter)

        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
        motion.fade_in(self)

    def _answer(self, yes: bool) -> None:
        if self._done:
            return
        self._done = True
        self.btn_ok.setEnabled(False)
        self.btn_cancel.setEnabled(False)
        self.answered.emit(self, yes)

    def dismiss(self) -> None:
        """外部清场（切页/关窗）当成取消处理，免得回调永远挂在那里。"""
        if not self._done:
            self._done = True
            self.answered.emit(self, False)


class NoticeHost(QWidget):
    """提示条与确认条的容器。空的时候高度为 0，不占版面。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("NoticeHost")
        self._lay = QVBoxLayout(self)
        self._lay.setContentsMargins(20, 8, 20, 0)
        self._lay.setSpacing(6)
        self._bars: list[QWidget] = []
        self._asks: dict[object, tuple] = {}      # bar -> (on_yes, on_no)
        self.hide()

    # ---- 提示
    def note(self, kind: str, text: str, timeout: float | None = None,
             action: tuple[str, object] | None = None) -> NoticeBar | None:
        """加一条提示。`action=("查看详情", 回调)` 会在右侧加一个按钮。"""
        if not text:
            return None
        for b in self._bars:
            if isinstance(b, NoticeBar) and b._kind == kind and b.label.text() == text:
                return b          # 同一条正在显示，别叠两遍
        kind = kind if kind in KIND_TOKEN else "info"
        bar = NoticeBar(kind, text, timeout if timeout is not None
                        else KIND_TIMEOUT[kind], self)
        if action is not None:
            cap, cb = action
            btn = ghost_button(cap)
            btn.clicked.connect(lambda: (bar.dismiss(), cb()))
            bar._lay.insertWidget(bar._lay.count() - 1, btn, 0, Qt.AlignTop)
        bar.closed.connect(self._remove)
        self._add(bar)
        return bar

    # ---- 确认
    def ask(self, title: str, text: str, ok_text: str = "确认", danger: bool = False,
            on_yes=None, on_no=None, cancel_text: str = "取消") -> AskBar:
        """加一条页内确认。同一时刻只留一条 —— 排队确认比弹窗还烦。"""
        for b in list(self._bars):
            if isinstance(b, AskBar):
                b.dismiss()
        bar = AskBar(title, text, ok_text, cancel_text, danger, self)
        self._asks[bar] = (on_yes, on_no)
        bar.answered.connect(self._answered)
        self._add(bar)
        return bar

    def _answered(self, bar: AskBar, yes: bool) -> None:
        cb = self._asks.pop(bar, (None, None))
        # 先收条再回调：回调里可能立刻又弹一条新的确认，
        # 顺序反了会把新的那条一起摘掉
        QTimer.singleShot(0, lambda: self._remove(bar))
        fn = cb[0] if yes else cb[1]
        if fn is not None:
            fn()

    # ---- 栈管理
    def _add(self, bar: QWidget) -> None:
        self._bars.append(bar)
        self._lay.addWidget(bar)
        while len(self._bars) > MAX_BARS:
            self._remove(self._bars[0])
        self.show()

    def _remove(self, bar: QWidget | None = None) -> None:
        if bar is None:
            return
        self._asks.pop(bar, None)
        if bar in self._bars:
            self._bars.remove(bar)
        self._lay.removeWidget(bar)
        bar.setParent(None)
        bar.deleteLater()
        if not self._bars:
            self.hide()

    def clear(self) -> None:
        """清场。待答的确认按「取消」处理，回调里的收尾逻辑照样会跑。"""
        for b in list(self._bars):
            if isinstance(b, AskBar):
                b.dismiss()
        for b in list(self._bars):
            self._remove(b)

    @property
    def count(self) -> int:
        return len(self._bars)


# ---------------------------------------------------------------- 页面侧接口
def _host(widget) -> NoticeHost | None:
    """找到页面挂的宿主。

    不能只看 `widget` 自己：一个工作台里往往还有若干子页签
    （系统优化页里的「启动项 / 服务 / 微调」就是独立 QWidget），
    宿主只注在顶层页面上，所以这里顺着父控件链往上找几层。
    页面被单独构造（自检 / 探针）时找不到，返回 None。
    """
    node = widget
    for _ in range(8):
        if node is None:
            break
        host = getattr(node, "notice_host", None)
        if isinstance(host, NoticeHost):
            return host
        node = node.parent() if hasattr(node, "parent") else None
    return None


def note(widget, kind: str, text: str, timeout: float | None = None,
         action: tuple[str, object] | None = None) -> NoticeBar | None:
    """在所属页面的顶部显示一条提示（不弹窗）。"""
    host = _host(widget)
    if host is None:
        return None
    return host.note(kind, text, timeout, action)


def ask(widget, title: str, text: str, ok_text: str = "确认", danger: bool = False,
        on_yes=None, on_no=None, cancel_text: str = "取消") -> AskBar | None:
    """在所属页面的顶部显示一条确认（不弹窗），确认后执行 `on_yes`。"""
    host = _host(widget)
    if host is None:
        return None
    return host.ask(title, text, ok_text, danger, on_yes, on_no, cancel_text)


def clear(widget) -> None:
    host = _host(widget)
    if host is not None:
        host.clear()
