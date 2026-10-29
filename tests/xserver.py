"""Headless X servers for integration tests: Xvfb, and Xephyr nested inside Xvfb for Xinerama layouts."""
from __future__ import annotations

import json
import os
import queue
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLIENT = Path(__file__).with_name("x11_clickcounter.py")


def xvfb_binary() -> str | None:
    local = ROOT / ".tools" / "xvfb" / "usr" / "bin" / "Xvfb"  # `make xvfb-local` (no root needed)
    return shutil.which("Xvfb") or (str(local) if local.exists() else None)


class XServer:
    """Starts an X server with -displayfd so we learn the display number without races."""

    def __init__(self, argv: list[str], env: dict | None = None):
        r, w = os.pipe()
        self.proc = subprocess.Popen([*argv, "-displayfd", str(w), "-nolisten", "tcp"], pass_fds=(w,),
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env)
        os.close(w)
        with os.fdopen(r) as f:
            num = f.readline().strip()
        if not num:
            self.proc.kill()
            raise RuntimeError(f"{argv[0]} failed to start")
        self.display = f":{num}"

    def stop(self):
        self.proc.terminate()
        try:
            self.proc.wait(5)
        except subprocess.TimeoutExpired:
            self.proc.kill()


def start_xvfb(*screens: str) -> XServer:
    argv = [xvfb_binary()]
    for i, geometry in enumerate(screens):
        argv += ["-screen", str(i), f"{geometry}x24"]
    return XServer(argv)


def start_xinerama(host: XServer, *heads: str) -> XServer:
    """Xephyr nested in a headless Xvfb: heads side by side, joined by Xinerama (one root, several monitors)."""
    argv = [shutil.which("Xephyr"), "-ac", "+xinerama"]
    for geometry in heads:
        argv += ["-screen", geometry]
    return XServer(argv, env={**os.environ, "DISPLAY": host.display})


class ClickCounter:
    """A test window in its own process; collects click reports."""

    def __init__(self, display: str, geometry: str, title: str, cls: str = "ClickCounter"):
        self.proc = subprocess.Popen([sys.executable, str(CLIENT), "--display", display, "--geometry", geometry,
                                      "--title", title, "--class", cls],
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, bufsize=1)
        self.title, self.cls = title, cls
        self.events: queue.Queue = queue.Queue()
        threading.Thread(target=self._read, daemon=True).start()
        ready = self.events.get(timeout=10)
        assert ready["ev"] == "ready", ready

    def _read(self):
        for line in self.proc.stdout:
            self.events.put(json.loads(line))

    def send(self, cmd: str):
        self.proc.stdin.write(cmd + "\n")
        self.proc.stdin.flush()
        while self.events.get(timeout=5)["ev"] != "ok":
            pass

    def presses(self, settle: float = 0.3) -> list[dict]:
        """Every press reported until no event arrived for `settle` seconds."""
        out = []
        while True:
            try:
                ev = self.events.get(timeout=settle)
            except queue.Empty:
                return out
            if ev["ev"] == "press":
                out.append(ev)

    def close(self):
        if self.proc.poll() is None:
            try:
                self.proc.stdin.write("quit\n")
                self.proc.stdin.flush()
                self.proc.wait(3)
            except Exception:
                self.proc.kill()


def wait_until(pred, timeout=10.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.01)
    return pred()
