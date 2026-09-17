"""和桌面系统打交道的小工具：前台全屏检测。"""
from __future__ import annotations

import os
import sys


def foreground_fullscreen() -> bool:
    """Windows：前台是不是别的程序的全屏窗口（看视频、打游戏）。其他系统返回 False。"""
    if sys.platform != "win32":
        return False
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return False
    cls = ctypes.create_unicode_buffer(64)
    user32.GetClassNameW(hwnd, cls, 64)
    if cls.value in ("Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd"):   # 桌面、任务栏不算
        return False
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    if pid.value == os.getpid():
        return False

    class MONITORINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT), ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]
    rect, info = wintypes.RECT(), MONITORINFO()
    info.cbSize = ctypes.sizeof(MONITORINFO)
    if not user32.GetWindowRect(hwnd, ctypes.byref(rect)) or \
            not user32.GetMonitorInfoW(user32.MonitorFromWindow(hwnd, 2), ctypes.byref(info)):
        return False
    m = info.rcMonitor
    return rect.left <= m.left and rect.top <= m.top and rect.right >= m.right and rect.bottom >= m.bottom
