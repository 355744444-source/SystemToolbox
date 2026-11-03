# -*- coding: utf-8 -*-
"""生成应用图标 assets/app.ico（深色底 + 强调色崩溃符号）。

运行：QT_QPA_PLATFORM=offscreen python tools/make_icon.py
"""
from __future__ import annotations

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from PIL import Image  # noqa: E402
from PyQt5.QtCore import QPointF, QRectF, Qt  # noqa: E402
from PyQt5.QtGui import QBrush, QColor, QLinearGradient, QPainter, QPainterPath, QPen  # noqa: E402
from PyQt5.QtWidgets import QApplication  # noqa: E402


def render(size: int = 512) -> Image.Image:
    from PyQt5.QtGui import QImage
    img = QImage(size, size, QImage.Format_ARGB32)
    img.fill(Qt.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing, True)

    # 底板：深色圆角 + 顶部强调色微光
    r = size * 0.22
    grad = QLinearGradient(0, 0, 0, size)
    grad.setColorAt(0.0, QColor("#1B2430"))
    grad.setColorAt(1.0, QColor("#0D1117"))
    p.setPen(Qt.NoPen)
    p.setBrush(QBrush(grad))
    p.drawRoundedRect(QRectF(0, 0, size, size), r, r)

    # 顶部高光条
    p.setBrush(QColor(76, 154, 255, 46))
    p.drawRoundedRect(QRectF(size * 0.16, size * 0.12, size * 0.68, size * 0.10),
                      size * 0.05, size * 0.05)

    # 崩溃三角
    path = QPainterPath()
    path.moveTo(size * 0.5, size * 0.24)
    path.lineTo(size * 0.845, size * 0.775)
    path.lineTo(size * 0.155, size * 0.775)
    path.closeSubpath()
    p.setBrush(QColor("#FF5C5C"))
    p.setPen(Qt.NoPen)
    p.drawPath(path)

    # 感叹号（用底色挖空）
    p.setBrush(QColor("#0D1117"))
    p.drawRoundedRect(QRectF(size * 0.468, size * 0.40, size * 0.064, size * 0.20),
                      size * 0.032, size * 0.032)
    p.drawEllipse(QPointF(size * 0.50, size * 0.685), size * 0.036, size * 0.036)

    # 底部两条日志线，暗示"事件日志"
    p.setPen(QPen(QColor(76, 154, 255, 150), size * 0.016, Qt.SolidLine, Qt.RoundCap))
    p.drawLine(QPointF(size * 0.30, size * 0.865), QPointF(size * 0.70, size * 0.865))
    p.end()

    img = img.convertToFormat(QImage.Format_RGBA8888)
    ptr = img.bits()
    ptr.setsize(img.byteCount())
    data = bytes(ptr)
    return Image.frombytes("RGBA", (size, size), data)


def main() -> int:
    QApplication(sys.argv)
    out_dir = os.path.join(ROOT, "assets")
    os.makedirs(out_dir, exist_ok=True)
    base = render(512)
    ico = os.path.join(out_dir, "app.ico")
    base.save(ico, format="ICO",
              sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    base.resize((512, 512)).save(os.path.join(out_dir, "app.png"))
    print("icon ->", ico, os.path.getsize(ico))
    return 0


if __name__ == "__main__":
    sys.exit(main())
