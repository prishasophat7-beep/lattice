"""Exit when the desktop app that started us goes away, however it goes (close, crash, kill).

The Tauri shell passes its own process id in OASIS_PARENT_PID. Without this, a crashed or
force-killed app would leave the engine running in the background, holding its port and memory.
"""
from __future__ import annotations

import os
import sys
import threading
import time


def _wait_windows(pid: int) -> None:
    import ctypes

    SYNCHRONIZE, INFINITE = 0x00100000, 0xFFFFFFFF
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(SYNCHRONIZE, False, pid)
    if not handle:  # already gone (or not visible to us)
        return
    kernel32.WaitForSingleObject(handle, INFINITE)
    kernel32.CloseHandle(handle)


def _wait_posix(pid: int) -> None:
    while True:
        try:
            os.kill(pid, 0)
        except OSError:
            return
        time.sleep(2)


def watch_parent() -> None:
    raw = os.environ.get("OASIS_PARENT_PID")
    if not raw or not raw.isdigit():
        return
    pid = int(raw)

    def run() -> None:
        (_wait_windows if sys.platform == "win32" else _wait_posix)(pid)
        os._exit(0)  # parent is gone: stop immediately, nothing to clean up

    threading.Thread(target=run, name="parent-watchdog", daemon=True).start()
