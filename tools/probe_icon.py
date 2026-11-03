# -*- coding: utf-8 -*-
"""检查 exe 的图标资源：把 shell 实际取到的 HICON 渲染成 png，另存 ico 各尺寸预览。

结果写 probe_icon.log + shots/icon_*.png
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wintypes
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "windows")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

LOG = open("probe_icon.log", "w", encoding="utf-8", buffering=1)


def w(s):
    LOG.write(str(s) + "\n")


from PyQt5.QtCore import Qt  # noqa: E402
from PyQt5.QtGui import QImage, QPainter  # noqa: E402
from PyQt5.QtWidgets import QApplication  # noqa: E402


class SHFILEINFO(ctypes.Structure):
    _fields_ = [("hIcon", wintypes.HICON),
                ("iIcon", ctypes.c_int),
                ("dwAttributes", wintypes.DWORD),
                ("szDisplayName", ctypes.c_wchar * 260),
                ("szTypeName", ctypes.c_wchar * 80)]


shell32 = ctypes.windll.shell32
shell32.SHGetFileInfoW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD,
                                   ctypes.POINTER(SHFILEINFO), wintypes.UINT,
                                   wintypes.UINT]
shell32.SHGetFileInfoW.restype = ctypes.c_void_p
shell32.ExtractIconExW.argtypes = [wintypes.LPCWSTR, ctypes.c_int,
                                   ctypes.POINTER(wintypes.HICON),
                                   ctypes.POINTER(wintypes.HICON), wintypes.UINT]
shell32.ExtractIconExW.restype = wintypes.UINT
shell32.DestroyIcon = ctypes.windll.user32.DestroyIcon
shell32.DestroyIcon.argtypes = [wintypes.HICON]
shell32.DestroyIcon.restype = wintypes.BOOL


def hicon_to_png(hicon, path, zoom=4):
    from PyQt5.QtWinExtras import QtWin
    pm = QtWin.fromHICON(int(hicon))
    if pm is None or pm.isNull():
        w(f"  !! fromHICON null for {path}")
        return None
    img = pm.toImage()
    w(f"  hic size = {img.width()}x{img.height()}")
    big = img.scaled(img.width() * zoom, img.height() * zoom,
                     Qt.KeepAspectRatio, Qt.FastTransformation)
    # 贴到棋盘格上，便于看透明区域/是否全空
    canvas = QImage(big.width(), big.height(), QImage.Format_ARGB32)
    canvas.fill(Qt.white)
    p = QPainter(canvas)
    for y in range(0, canvas.height(), 12):
        for x in range(0, canvas.width(), 12):
            if (x // 12 + y // 12) % 2:
                p.fillRect(x, y, 12, 12, Qt.lightGray)
    p.drawImage(0, 0, big)
    p.end()
    canvas.save(path)
    return img


def main():
    app = QApplication(sys.argv)
    target = os.path.join(ROOT, "dist", "SystemToolbox.exe")
    w(f"target exists = {os.path.isfile(target)}")
    w(f"groups = {shell32.ExtractIconExW(target, -1, None, None, 0)}")

    # 1) shell 取大图标 / 小图标
    for tag, flag in (("large", 0x0), ("small", 0x1), ("shell", 0x4)):
        sfi = SHFILEINFO()
        r = shell32.SHGetFileInfoW(target, 0, ctypes.byref(sfi), ctypes.sizeof(sfi),
                                   0x100 | flag)  # SHGFI_ICON
        w(f"SHGetFileInfo[{tag}] ret={r} hIcon={sfi.hIcon} type={sfi.szTypeName!r}")
        if sfi.hIcon:
            hicon_to_png(sfi.hIcon, f"shots/icon_shell_{tag}.png")
            shell32.DestroyIcon(sfi.hIcon)

    # 2) 直接从 exe 抽 index 0
    lg = wintypes.HICON()
    sm = wintypes.HICON()
    n = shell32.ExtractIconExW(target, 0, ctypes.byref(lg), ctypes.byref(sm), 1)
    w(f"ExtractIconEx[0] n={n}")
    if lg.value:
        hicon_to_png(lg.value, "shots/icon_extract_large.png")
        shell32.DestroyIcon(lg)
    if sm.value:
        hicon_to_png(sm.value, "shots/icon_extract_small.png")
        shell32.DestroyIcon(sm)

    # 3) ico 文件本身各尺寸
    from PyQt5.QtGui import QIcon
    ic = QIcon(os.path.join(ROOT, "assets", "app.ico"))
    w(f"ico availableSizes = {[f'{s.width()}x{s.height()}' for s in ic.availableSizes()]}")
    for s in ic.availableSizes():
        pm = ic.pixmap(s)
        path = f"shots/ico_{s.width()}.png"
        pm.save(path)
        w(f"  ico {s.width()}x{s.height()} -> {path} isNull={pm.isNull()}")

    w("DONE")
    LOG.close()


if __name__ == "__main__":
    main()
