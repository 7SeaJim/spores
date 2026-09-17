"""入口：参数、单实例锁、启动 Qt。"""
from __future__ import annotations

import argparse
import os
import signal
import sys
from pathlib import Path

try:
    import fcntl
except ImportError:                      # Windows
    fcntl = None

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication

from .colony import Colony


def main():
    ap = argparse.ArgumentParser(description="FUNGI.EXE — 桌面真菌宠物 demo")
    ap.add_argument("--fast", action="store_true", help="调试：成长加速")
    default_dir = (Path(os.environ.get("APPDATA", Path.home())) if sys.platform == "win32"
                   else Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share"))) / "fungi"
    ap.add_argument("--data-dir", type=Path, default=default_dir)
    args = ap.parse_args()

    args.data_dir.mkdir(parents=True, exist_ok=True)
    lock = open(args.data_dir / "lock", "w")
    try:
        if fcntl:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        else:
            import msvcrt
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        print("[fungi] 已经在运行了（同一个存档目录只能开一个）", file=sys.stderr)
        sys.exit(1)

    app = QApplication(sys.argv)
    app.setApplicationName("fungi")
    app.setQuitOnLastWindowClosed(False)
    colony = Colony(args.data_dir, fast=args.fast)
    app.aboutToQuit.connect(colony.save)

    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: app.quit())
    pump = QTimer()                       # 让 Python 有机会处理 Ctrl+C / SIGTERM
    pump.timeout.connect(lambda: None)
    pump.start(300)

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
