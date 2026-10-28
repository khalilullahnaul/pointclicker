"""`pointclicker --diagnose`: what PointClicker detected, what it can use, and a self-test click.

The self-test opens a full-screen test window and clicks its center through the real backend (this moves
the pointer). It passes only if our own window receives the press at the expected spot.
"""
from __future__ import annotations

import logging
import os
import platform as pyplatform
import sys
import threading
import time

from . import clicker
from .platform import detect_session_type, get_backend, get_hotkey_backend

TOLERANCE_PX = 3


def _row(label: str, value) -> None:
    print(f"  {label:<22} {value}")


def _section(title: str) -> None:
    print(f"\n{title}")


def _xserver_kind() -> str:
    try:
        from Xlib import display
        d = display.Display()
        kind = "XWayland" if "XWAYLAND" in d.list_extensions() else f"X.Org-style server ({d.display.info.vendor})"
        d.close()
        return kind
    except Exception as e:
        return f"not reachable ({e})"


def _linux_report(backend) -> None:
    from .platform.linux import setup
    from .platform.linux.portal import GLOBAL_SHORTCUTS, REMOTE_DESKTOP, SCREEN_CAST, Portal

    _section("Permissions")
    for k, v in setup.permissions().items():
        _row(k.replace("_", " "), v)
    _section("Desktop portals")
    try:
        p = Portal()
        for name in (REMOTE_DESKTOP, SCREEN_CAST, GLOBAL_SHORTCUTS):
            v = p.version(name)
            _row(name.rsplit(".", 1)[1], f"version {v}" if v else "not available")
        p.close()
    except Exception as e:
        _row("session bus", f"unreachable: {e}")
    if detect_session_type() == "wayland":
        from .platform.linux.wayland import token_path
        _row("restore token", f"saved ({token_path()})" if token_path().exists() else "none yet (dialog on first use)")


def _self_test(app, backend) -> bool:
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QColor, QGuiApplication, QPainter, QPen
    from PySide6.QtWidgets import QWidget

    class TestWindow(QWidget):
        def __init__(self):
            super().__init__()
            self.setWindowTitle("PointClicker self-test")
            self.presses = []

        def paintEvent(self, _):
            p = QPainter(self)
            p.fillRect(self.rect(), QColor(30, 41, 59))
            c = self.rect().center()
            p.setPen(QPen(QColor(255, 80, 80), 2))
            p.drawLine(c.x() - 30, c.y(), c.x() + 30, c.y())
            p.drawLine(c.x(), c.y() - 30, c.x(), c.y() + 30)
            p.setPen(Qt.white)
            p.drawText(self.rect().adjusted(0, 40, 0, 0), Qt.AlignHCenter | Qt.AlignTop,
                       "PointClicker self-test: a click will land on the cross. Don't move the mouse.")

        def mousePressEvent(self, e):
            self.presses.append((e.position().x(), e.position().y(), e.button().name))

    def pump(seconds: float):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            app.processEvents()
            time.sleep(0.01)

    def run_click(**kw):
        out = {}
        t = threading.Thread(target=lambda: out.update(res=backend.click((x, y), **kw)), daemon=True)
        t.start()
        while t.is_alive():  # keep our window responsive while the click (or a portal dialog) is pending
            pump(0.02)
        pump(0.6)
        return out["res"]

    caps = backend.capabilities()
    _section("Self-test click")
    print("  Getting input access (on Wayland the desktop may ask you to allow remote control once)...")
    try:
        backend.prepare()
    except Exception as e:
        _row("input access", f"FAIL: {e}")
        return False
    _row("input method", backend.capabilities().get("input_method", "?"))

    w = TestWindow()
    screen = QGuiApplication.primaryScreen()
    w.setScreen(screen)
    w.showFullScreen()
    pump(1.2)  # map + fullscreen animation
    expected = (w.width() / 2, w.height() / 2)
    if caps.get("can_read_cursor", True) and detect_session_type() == "x11":
        handle = backend._d.create_resource_object("window", int(w.winId()))  # X11: measure in X pixels
        r = backend.window_rect(handle)
        x, y = (r.left + r.right) // 2, (r.top + r.bottom) // 2
        expected = (w.width() * (x - r.left) / r.width, w.height() * (y - r.top) / r.height)
    else:  # Wayland (and Windows): logical coordinates of the screen we're fullscreen on
        g = screen.geometry()
        x, y = g.x() + g.width() // 2, g.y() + g.height() // 2  # QRect.center() rounds down by one
    _row("target point", f"({x}, {y}) on {screen.name()} {screen.geometry().getRect()}")

    before = backend.get_cursor_pos() if caps.get("can_read_cursor", True) else None
    res = run_click(mode=clicker.Mode.FOREGROUND)
    hit = [p for p in w.presses if abs(p[0] - expected[0]) <= TOLERANCE_PX and abs(p[1] - expected[1]) <= TOLERANCE_PX]
    ok = res.ok and bool(hit)
    _row("foreground click", f"{'PASS' if ok else 'FAIL'}: backend {'ok' if res.ok else res.error}, "
                             f"{res.elapsed_ms:.1f} ms, window got {w.presses or 'nothing'}, expected ≈{expected}")
    if getattr(backend, "xwayland", False):
        _row("pointer restored", "can't be checked on XWayland (it can't move the real Wayland pointer)")
    elif before is not None:
        after = backend.get_cursor_pos()
        _row("pointer restored", "yes" if before == after else f"NO: {before} -> {after}")
    else:
        _row("pointer restored", "not possible on Wayland (pointer stays on the cross)")

    if backend.supports_background_mode() and detect_session_type() == "x11":
        w.presses.clear()
        res = run_click(mode=clicker.Mode.BACKGROUND, hwnd=handle)
        _row("background click", ("received by the test window" if w.presses else
                                  "sent, but ignored by the test window") + f" ({res.error or 'ok'})")
    w.close()
    pump(0.3)
    return ok


def run() -> int:
    logging.basicConfig(level=logging.CRITICAL)  # every failure is reported in the rows below
    print("PointClicker diagnostics")
    _section("System")
    _row("OS", f"{pyplatform.system()} {pyplatform.release()} ({sys.platform})")
    _row("Python", sys.version.split()[0])
    session = detect_session_type()
    _row("session type", session)
    if sys.platform.startswith("linux"):
        for var in ("XDG_SESSION_TYPE", "WAYLAND_DISPLAY", "DISPLAY", "XDG_CURRENT_DESKTOP"):
            _row(var, os.environ.get(var, "(unset)"))
        if os.environ.get("DISPLAY"):
            _row("X server", _xserver_kind())
        if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
            print("\nNo display (DISPLAY / WAYLAND_DISPLAY unset): nothing to click on.")
            return 1

    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv[:1])
    _row("Qt platform", f"{app.platformName()}" + (" (running through XWayland)"
                                                    if session == "wayland" and app.platformName() == "xcb" else ""))

    backend, hotkeys = get_backend(), get_hotkey_backend()
    caps = backend.capabilities()
    _section("Backends")
    _row("input backend", f"{type(backend).__name__} — {caps.get('platform_name')}")
    _row("input method", caps.get("input_method", "?"))
    _row("background mode", ("yes" if caps.get("background_mode") else "no") +
         (f" — {caps['background_note']}" if caps.get("background_note") else ""))
    _row("window tracking", ("X11 apps only (via XWayland)" if session == "wayland" else "yes")
         if caps.get("window_tracking") else "no")
    _row("hotkeys", hotkeys.status())
    for note in caps.get("unavailable_features", []):
        _row("note", note)
    _section("Monitors")
    for m in backend.monitors() or []:
        _row("backend", f"{m.left},{m.top} {m.width}x{m.height}")
    for s in app.screens():
        _row(f"Qt {s.name()}", f"{s.geometry().getRect()} scale {s.devicePixelRatio()}")
    if sys.platform.startswith("linux"):
        _linux_report(backend)

    ok = _self_test(app, backend)
    print(f"\nResult: {'PASS' if ok else 'FAIL'}")
    if not ok and caps.get("setup_needed"):
        print("Some features need extra permissions; see: Settings → Linux setup guide in the app.")
    return 0 if ok else 1
