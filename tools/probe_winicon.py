# -*- coding: utf-8 -*-
"""探测打包 exe 运行时窗口实际持有的图标（任务栏/Alt-Tab 用的就是它）。

结论写 probe_winicon.log
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
LOG = open("probe_winicon.log", "w", encoding="utf-8", buffering=1)


def w(s):
    LOG.write(str(s) + "\n")


user32 = ctypes.WinDLL("user32", use_last_error=True)
user32.GetClassLongPtrW = getattr(user32, "GetClassLongPtrW", user32.GetClassLongW)
user32.GetClassLongPtrW.argtypes = [wt.HWND, ctypes.c_int]
user32.GetClassLongPtrW.restype = ctypes.c_void_p
user32.SendMessageW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
user32.SendMessageW.restype = ctypes.c_void_p
user32.GetWindowTextW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
user32.GetWindowTextW.restype = ctypes.c_int
user32.IsWindowVisible.argtypes = [wt.HWND]
user32.GetPropW.argtypes = [wt.HWND, wt.LPCWSTR]
user32.GetPropW.restype = wt.HANDLE
user32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]

WM_GETICON = 0x007F
ICON_SMALL, ICON_BIG, ICON_SMALL2 = 0, 1, 2
GCLP_HICON, GCLP_HICONSM = -14, -34
WM_CLOSE = 0x0010

EnumProc = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)


def find_windows(title_part, pid):
    found = []

    def cb(hwnd, _):
        n = user32.GetWindowTextLengthW(hwnd)
        if n:
            buf = ctypes.create_unicode_buffer(n + 2)
            user32.GetWindowTextW(hwnd, buf, n + 2)
            if title_part in buf.value:
                d = wt.DWORD()
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(d))
                found.append((hwnd, buf.value, d.value))
        return True

    user32.EnumWindows(EnumProc(cb), 0)
    return found


def main():
    exe = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else "dist/SystemToolbox.exe")
    w(f"exe = {exe}")
    p = subprocess.Popen([exe], cwd=os.path.dirname(exe))
    w(f"pid = {p.pid}")

    wins = []
    for _ in range(120):
        time.sleep(0.25)
        wins = find_windows("系统万能工具箱", p.pid)
        if wins:
            break
    w(f"windows found = {[(h, t, pid) for h, t, pid in wins]}")

    # 可选：等 N 秒再关，区分「扫描线程还在跑时关窗」与「空闲时关窗」
    delay = float(os.environ.get("CLS_CLOSE_DELAY", "0"))
    if delay and wins:
        w(f"sleep {delay}s before close")
        time.sleep(delay)

    for hwnd, title, pid in wins:
        w(f"--- hwnd={hwnd} title={title!r} pid={pid}")
        for tag, idx in (("ICON_SMALL", ICON_SMALL), ("ICON_BIG", ICON_BIG),
                         ("ICON_SMALL2", ICON_SMALL2)):
            v = user32.SendMessageW(hwnd, WM_GETICON, idx, 0)
            w(f"  WM_GETICON[{tag}] = {v}")
        w(f"  GCLP_HICON   = {user32.GetClassLongPtrW(hwnd, GCLP_HICON)}")
        w(f"  GCLP_HICONSM = {user32.GetClassLongPtrW(hwnd, GCLP_HICONSM)}")
        w(f"  prop AppUserModelID = {user32.GetPropW(hwnd, 'AppUserModelID')}")

    # 进程级 AppUserModelID
    try:
        ctypes.windll.shell32.GetCurrentProcessExplicitAppUserModelID.argtypes = [
            ctypes.POINTER(ctypes.c_wchar_p)]
        val = ctypes.c_wchar_p()
        hr = ctypes.windll.shell32.GetCurrentProcessExplicitAppUserModelID(ctypes.byref(val))
        w(f"process AppUserModelID hr={hr} val={val.value!r}")
    except Exception as e:
        w(f"AppUserModelID query fail: {e}")

    for hwnd, _, _ in wins:
        user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
    try:
        p.wait(timeout=8)
    except Exception:
        p.kill()
    w(f"closed, returncode={p.returncode}")
    w("DONE")
    LOG.close()


if __name__ == "__main__":
    main()
