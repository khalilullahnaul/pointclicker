"""Wayland backend: RemoteDesktop portal (primary) or a /dev/uinput absolute pointer (fallback).

Wayland gives apps no global pointer position and no way to send input to another app's window, so:
  - foreground clicks move the real pointer and can't put it back (its position is unknowable),
  - background mode doesn't exist,
  - window tracking only sees X11 apps running under XWayland.
Coordinates are compositor logical pixels, the same space Qt reports for screens on Wayland.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from pathlib import Path
from typing import Any

from ..base import InputBackend, Rect
from . import setup

log = logging.getLogger(__name__)

BTN_CODES = {"left": 0x110, "right": 0x111, "middle": 0x112}  # evdev BTN_LEFT / BTN_RIGHT / BTN_MIDDLE
BACKGROUND_NOTE = ("Background mode isn't available on Wayland: the compositor doesn't let one app send "
                   "input to another app's window. Use foreground mode.")
POINTER_DEVICE = 2  # RemoteDesktop device type bit


def token_path() -> Path:
    from ...targets import config_path
    return config_path().parent / "portal_restore_token"


class LinuxWaylandInputBackend(InputBackend):
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._x = None  # XWayland connection: window lookup for X11 apps only
        if os.environ.get("DISPLAY"):
            from .x11 import LinuxX11InputBackend
            x = LinuxX11InputBackend()
            self._x = None if x._error else x
        self.method: str | None = None  # "portal" or "uinput" once connected
        self.error: str | None = None  # why neither worked; cached so a denied dialog isn't re-shown per click
        self._portal = self._session = self._uinput = None
        self._streams: list[tuple[int, Rect]] = []
        self._box: Rect | None = None

    # --- connecting ---

    def prepare(self) -> None:
        if error := self._connect():
            raise RuntimeError(error)

    def _connect(self) -> str | None:
        with self._lock:
            if self.method or self.error:
                return self.error
            try:
                self._start_portal()
                self.method = "portal"
                return None
            except Exception as e:
                portal_error = str(e)
                log.warning("RemoteDesktop portal unavailable: %s", e)
            perms = setup.permissions()
            if perms["uinput_writable"]:
                try:
                    self._start_uinput()
                    self.method = "uinput"
                    return None
                except Exception as e:
                    uinput_error = str(e)
            else:
                uinput_error = "/dev/uinput is not writable (see the Linux setup guide)"
            self.error = f"no pointer access: portal: {portal_error}; uinput: {uinput_error}"
            log.error(self.error)
            return self.error

    def _start_portal(self):
        from .portal import REMOTE_DESKTOP, SCREEN_CAST, Portal, PortalError

        p = Portal()
        try:
            if not p.version(REMOTE_DESKTOP):
                raise PortalError("this desktop has no RemoteDesktop portal")
            session = p.request(REMOTE_DESKTOP, "CreateSession", "a{sv}",
                                {"session_handle_token": ("s", p.token())})["session_handle"][1]
            devices = {"types": ("u", POINTER_DEVICE), "persist_mode": ("u", 2)}  # 2 = until revoked
            if token_path().exists():
                devices["restore_token"] = ("s", token_path().read_text().strip())
            p.request(REMOTE_DESKTOP, "SelectDevices", "oa{sv}", session, devices)
            # Absolute pointer motion is relative to a screen-cast stream, so share the monitors too.
            p.request(SCREEN_CAST, "SelectSources", "oa{sv}", session, {"types": ("u", 1), "multiple": ("b", True)})
            started = p.request(REMOTE_DESKTOP, "Start", "osa{sv}", session, "", {}, timeout=180)
        except Exception:
            p.close()
            raise
        if not started.get("devices", ("u", 0))[1] & POINTER_DEVICE:
            p.close()
            raise PortalError("pointer access was not granted")
        if token := started.get("restore_token"):
            token_path().parent.mkdir(parents=True, exist_ok=True)
            token_path().write_text(token[1])  # tokens are single-use: always keep the newest
        self._streams = []
        for node, props in started.get("streams", ("a(ua{sv})", []))[1]:
            (x, y), (w, h) = props.get("position", ("(ii)", (0, 0)))[1], props["size"][1]
            self._streams.append((node, Rect(x, y, x + w, y + h)))
        if not self._streams:
            p.close()
            raise PortalError("no monitor was shared, so absolute pointer positions can't be sent")
        self._portal, self._session = p, session
        log.info("RemoteDesktop portal session started, monitors %s", [r for _, r in self._streams])

    def _start_uinput(self):
        from evdev import AbsInfo, UInput
        from evdev import ecodes as e

        mons = self.monitors()
        if not mons:
            raise RuntimeError("monitor layout unknown")
        self._box = Rect(min(m.left for m in mons), min(m.top for m in mons),
                         max(m.right for m in mons), max(m.bottom for m in mons))
        # ABS_X/ABS_Y + BTN_LEFT and no BTN_TOUCH = an "absolute mouse" (like a VM tablet); the compositor
        # maps its range onto the whole desktop, so 1 unit = 1 logical pixel.
        self._uinput = UInput({e.EV_KEY: list(BTN_CODES.values()),
                               e.EV_ABS: [(e.ABS_X, AbsInfo(0, 0, self._box.width - 1, 0, 0, 0)),
                                          (e.ABS_Y, AbsInfo(0, 0, self._box.height - 1, 0, 0, 0))]},
                              name="PointClicker virtual pointer")
        time.sleep(0.5)  # give the compositor time to pick up the new device

    def _disconnect(self):
        if self._portal:
            self._portal.close()
        if self._uinput:
            self._uinput.close()
        self._portal = self._session = self._uinput = None
        self.method = None

    # --- clicking ---

    def click(self, target: Any, abort: threading.Event | None = None, check_effect: bool = False, **kwargs) -> Any:
        return self._run_click(target, abort, check_effect, kwargs)

    def _perform(self, x, y, mode, button, double, window) -> str | None:
        if error := self._connect():
            return error
        with self._lock:
            try:
                return self._portal_click(x, y, button, double) if self.method == "portal" else \
                    self._uinput_click(x, y, button, double)
            except Exception as e:
                if self.method != "portal":
                    raise
                # The user may have ended sharing from the top bar: reconnect once (the token avoids a dialog).
                log.warning("portal click failed (%s); reconnecting", e)
                self._disconnect()
                if error := self._connect():
                    return error
                return self._portal_click(x, y, button, double)

    def _portal_click(self, x, y, button, double) -> str | None:
        from .portal import REMOTE_DESKTOP

        hit = next(((node, r) for node, r in self._streams if r.left <= x < r.right and r.top <= y < r.bottom), None)
        if hit is None:
            return f"point ({x}, {y}) is not on a shared monitor"
        node, r = hit
        call = self._portal.call
        call(REMOTE_DESKTOP, "NotifyPointerMotionAbsolute", "oa{sv}udd", self._session, {}, node,
             float(x - r.left), float(y - r.top))
        for _ in range(2 if double else 1):
            for state in (1, 0):
                call(REMOTE_DESKTOP, "NotifyPointerButton", "oa{sv}iu", self._session, {}, BTN_CODES[button], state)
        return None

    def _uinput_click(self, x, y, button, double) -> str | None:
        from evdev import ecodes as e

        b = self._box
        if not (b.left <= x < b.right and b.top <= y < b.bottom):
            return f"point ({x}, {y}) is outside the desktop"
        ui = self._uinput
        ui.write(e.EV_ABS, e.ABS_X, x - b.left)
        ui.write(e.EV_ABS, e.ABS_Y, y - b.top)
        ui.syn()
        for _ in range(2 if double else 1):
            for state in (1, 0):
                ui.write(e.EV_KEY, BTN_CODES[button], state)
                ui.syn()
        return None

    # --- pointer: unknowable on Wayland ---

    def get_cursor_pos(self) -> tuple[int, int]:
        return 0, 0  # callers check capabilities()["can_read_cursor"]; picking uses the overlay's own click

    def is_mouse_button_held(self) -> bool:
        return False  # XWayland's button state is stale whenever the pointer is over a native Wayland window

    # --- windows: X11 apps via XWayland ---

    def window_at(self, x, y, root=None):
        return self._x.window_at(x, y, root) if self._x else None

    def top_window_at(self, x, y):
        return self._x.top_window_at(x, y) if self._x else None

    def window_rect(self, handle):
        return self._x.window_rect(handle) if self._x else None

    def window_text(self, handle):
        return self._x.window_text(handle) if self._x else ""

    def window_class(self, handle):
        return self._x.window_class(handle) if self._x else ""

    def find_window(self, class_name, title=None):
        return self._x.find_window(class_name, title) if self._x else None

    def is_window_minimized(self, handle):
        return self._x.is_window_minimized(handle) if self._x else False

    def snapshot(self, handle):
        return self._x.snapshot(handle) if self._x else None

    def monitors(self) -> list[Rect]:
        try:  # Qt knows the compositor's logical layout (xdg-output); prefer it when the GUI is running
            from PySide6.QtGui import QGuiApplication
            if QGuiApplication.instance():
                return [Rect(g.x(), g.y(), g.x() + g.width(), g.y() + g.height())
                        for g in (s.geometry() for s in QGuiApplication.screens())]
        except ImportError:
            pass
        if self._streams:
            return [r for _, r in self._streams]
        return self._x.monitors() if self._x else []

    # --- capabilities ---

    def is_admin(self) -> bool:
        return os.geteuid() == 0

    def supports_background_mode(self) -> bool:
        return False

    def capabilities(self) -> dict[str, Any]:
        from .hotkeys import wayland_hotkey_method

        perms = setup.permissions()
        hotkeys = wayland_hotkey_method()
        unavailable = [
            BACKGROUND_NOTE,
            "Foreground clicks leave the pointer at the target (Wayland apps can't read the pointer position to restore it).",
            "Window tracking works only for X11 apps (XWayland)." if self._x else "Window tracking is unavailable.",
            "Add points with the pick overlay; the pick hotkey opens it too.",
        ]
        if not hotkeys:
            unavailable.append("Global hotkeys need the input group here (no GlobalShortcuts portal): see Settings → Linux setup guide.")
        if self.error:
            unavailable.append(self.error)
        return {
            "platform_name": "Linux (Wayland" + (" + XWayland)" if self._x else ")"),
            "session_type": "wayland",
            "background_mode": False,
            "background_note": BACKGROUND_NOTE,
            "window_tracking": bool(self._x),
            "cursor_positioning": True,
            "can_read_cursor": False,
            "markers": False,
            "global_hotkeys": bool(hotkeys),
            "elevation_detection": False,
            "pixel_inspection": bool(self._x),
            "input_method": {"portal": "RemoteDesktop portal", "uinput": "/dev/uinput virtual pointer"}.get(
                self.method, "not connected yet (RemoteDesktop portal, uinput fallback)"),
            "setup_needed": not hotkeys or (self.error is not None and not perms["uinput_writable"]),
            "unavailable_features": unavailable,
        }
