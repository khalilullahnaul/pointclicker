"""X11 backend: XTest foreground clicks, XSendEvent background clicks, EWMH window lookup, XRandR monitors.

Coordinates are X root-window pixels. One Display connection, guarded by a lock: python-xlib is not
thread-safe and both the GUI thread and the scheduler thread use it.
"""
from __future__ import annotations

import logging
import os
import threading
from typing import Any

from ..base import InputBackend, Rect

log = logging.getLogger(__name__)

BUTTON_NUMBERS = {"left": 1, "middle": 2, "right": 3}
BACKGROUND_NOTE = ("Background mode on X11 sends synthetic X events. It works only with some apps: many "
                   "toolkits and games ignore them. PointClicker suggests foreground mode when a click has no effect.")


def _wid(window) -> int:
    return getattr(window, "id", window) or 0


class LinuxX11InputBackend(InputBackend):
    def __init__(self, display_name: str | None = None):
        self._lock = threading.RLock()
        self._d = self._root = None
        self._error = None
        try:
            from Xlib import display
            self._d = display.Display(display_name)
            self._root = self._d.screen().root
            self._xtest = self._d.has_extension("XTEST")
            # XWayland only tracks the pointer while it is over an X11 window: its button state and
            # warps are then stale/ineffective, so we must know which X server this is.
            self.xwayland = "XWAYLAND" in self._d.list_extensions()  # has_extension() only knows modules python-xlib ships
            self._randr = self._d.has_extension("RANDR")
            self._atom = {n: self._d.intern_atom(n) for n in (
                "_NET_CLIENT_LIST_STACKING", "_NET_CLIENT_LIST", "_NET_WM_NAME", "UTF8_STRING",
                "_NET_WM_STATE", "_NET_WM_STATE_HIDDEN", "_NET_WM_PID", "WM_STATE")}
        except Exception as e:  # no X server (e.g. headless test run): every call reports it instead of crashing
            self._error = f"cannot open X display {display_name or os.environ.get('DISPLAY', '')!r}: {e}"
            log.warning(self._error)

    # --- clicking ---

    def click(self, target: Any, abort: threading.Event | None = None, check_effect: bool = False, **kwargs) -> Any:
        return self._run_click(target, abort, check_effect, kwargs)

    def _perform(self, x, y, mode, button, double, window) -> str | None:
        from Xlib import X
        from Xlib.ext import xtest

        if self._error:
            return self._error
        mons = self.monitors()
        if mons and not any(m.left <= x < m.right and m.top <= y < m.bottom for m in mons):
            return f"point ({x}, {y}) is not on any monitor"
        n = BUTTON_NUMBERS[button]
        with self._lock:
            if mode.value == "background":
                return self._send_click(x, y, n, double, window)
            if not self._xtest:
                return "the X server has no XTEST extension"
            saved = self._root.query_pointer()
            # All requests go down one connection, so the server runs them strictly in this order:
            # the press/release land at (x, y) and the pointer is back before anything else happens.
            self._root.warp_pointer(x, y)
            for _ in range(2 if double else 1):
                xtest.fake_input(self._d, X.ButtonPress, n)
                xtest.fake_input(self._d, X.ButtonRelease, n)
            self._root.warp_pointer(saved.root_x, saved.root_y)
            self._d.sync()
        return None

    def _send_click(self, x, y, n, double, window) -> str | None:
        from Xlib import X, error
        from Xlib.protocol import event

        target = self.window_at(x, y, root=window)
        if not target:
            return f"no window at ({x}, {y})"
        local = target.translate_coords(self._root, x, y)
        held = 1 << (7 + n)  # Button1Mask == 1 << 8; a release reports the button as still down

        def ev(cls, state, detail):
            return cls(time=X.CurrentTime, root=self._root, window=target, same_screen=1, child=X.NONE,
                       root_x=x, root_y=y, event_x=local.x, event_y=local.y, state=state, detail=detail)

        seq = [(event.MotionNotify, X.PointerMotionMask, 0, 0)]
        for _ in range(2 if double else 1):
            seq += [(event.ButtonPress, X.ButtonPressMask, 0, n), (event.ButtonRelease, X.ButtonReleaseMask, held, n)]
        catch = error.CatchError()
        for cls, mask, state, detail in seq:
            target.send_event(ev(cls, state, detail), event_mask=mask, propagate=True, onerror=catch)
        self._d.sync()
        return f"X error: {catch.get_error()}" if catch.get_error() else None

    # --- pointer ---

    def get_cursor_pos(self) -> tuple[int, int]:
        if self._error:
            return 0, 0
        with self._lock:
            p = self._root.query_pointer()
        return p.root_x, p.root_y

    def is_mouse_button_held(self) -> bool:
        if self._error or self.xwayland:  # XWayland's button mask is stale over native Wayland windows
            return False
        from Xlib import X
        with self._lock:
            mask = self._root.query_pointer().mask
        return bool(mask & (X.Button1Mask | X.Button2Mask | X.Button3Mask))

    # --- windows ---

    def _clients(self) -> list:
        """Managed top-level windows, bottom to top (EWMH); plain root children without a window manager."""
        from Xlib import X
        for name in ("_NET_CLIENT_LIST_STACKING", "_NET_CLIENT_LIST"):
            prop = self._root.get_full_property(self._atom[name], X.AnyPropertyType)
            if prop and len(prop.value):
                return [self._d.create_resource_object("window", w) for w in prop.value]
        return list(self._root.query_tree().children)

    def top_window_at(self, x: int, y: int) -> Any:
        if self._error:
            return None
        from Xlib import X
        with self._lock:
            for w in reversed(self._clients()):
                try:
                    if self._is_own(w) or w.get_attributes().map_state != X.IsViewable or self.is_window_minimized(w):
                        continue
                    r = self.window_rect(w)
                    if r and r.left <= x < r.right and r.top <= y < r.bottom:
                        return w
                except Exception:  # window vanished between listing and querying
                    continue
        return None

    def _is_own(self, w) -> bool:
        """Our own windows (markers, overlay) must never become a target."""
        from Xlib import X
        pid = w.get_full_property(self._atom["_NET_WM_PID"], X.AnyPropertyType)
        return bool(pid and len(pid.value) and pid.value[0] == os.getpid())

    def window_at(self, x: int, y: int, root: Any = None) -> Any:
        """Deepest mapped window containing (x, y), searching inside root if given (so covered windows work)."""
        if self._error:
            return None
        with self._lock:
            w = root or self._root
            for _ in range(32):  # window trees are shallow; the bound only guards against a cycle
                child = w.translate_coords(self._root, x, y).child
                if not _wid(child):
                    return w
                w = child
        return w

    def window_rect(self, handle: Any) -> Rect | None:
        if self._error or not handle:
            return None
        try:
            with self._lock:
                g = handle.get_geometry()
                o = self._root.translate_coords(handle, 0, 0)  # the window's origin in root coordinates
            return Rect(o.x, o.y, o.x + g.width, o.y + g.height)
        except Exception:
            return None

    def window_text(self, handle: Any) -> str:
        if self._error or not handle:
            return ""
        try:
            with self._lock:
                prop = handle.get_full_property(self._atom["_NET_WM_NAME"], self._atom["UTF8_STRING"])
                if prop:
                    return prop.value.decode("utf-8", "replace")
                name = handle.get_wm_name()
            return name.decode("latin-1") if isinstance(name, bytes) else (name or "")
        except Exception:
            return ""

    def window_class(self, handle: Any) -> str:
        if self._error or not handle:
            return ""
        try:
            with self._lock:
                cls = handle.get_wm_class()  # (instance, class)
            return cls[1] if cls else ""
        except Exception:
            return ""

    def find_window(self, class_name: str | None, title: str | None = None) -> Any:
        if self._error or not class_name:
            return None
        with self._lock:
            for w in reversed(self._clients()):  # topmost match first
                if self.window_class(w) == class_name and (title is None or self.window_text(w) == title):
                    return w
        return None

    def is_window_minimized(self, handle: Any) -> bool:
        if self._error or not handle:
            return False
        from Xlib import X
        try:
            with self._lock:
                state = handle.get_full_property(self._atom["_NET_WM_STATE"], X.AnyPropertyType)
                if state and self._atom["_NET_WM_STATE_HIDDEN"] in state.value:
                    return True
                icccm = handle.get_full_property(self._atom["WM_STATE"], self._atom["WM_STATE"])
            return bool(icccm and len(icccm.value) and icccm.value[0] == 3)  # IconicState
        except Exception:
            return False

    def snapshot(self, handle: Any) -> bytes | None:
        if self._error or not handle:
            return None
        from Xlib import X
        try:
            with self._lock:
                g = handle.get_geometry()
                return handle.get_image(0, 0, g.width, g.height, X.ZPixmap, 0xFFFFFFFF).data
        except Exception:  # unmapped/offscreen windows can't be read (BadMatch)
            return None

    def monitors(self) -> list[Rect]:
        if self._error:
            return []
        with self._lock:
            if self._randr:
                try:
                    return [Rect(m.x, m.y, m.x + m.width_in_pixels, m.y + m.height_in_pixels)
                            for m in self._root.xrandr_get_monitors().monitors]
                except Exception:
                    pass
            s = self._d.screen()
            return [Rect(0, 0, s.width_in_pixels, s.height_in_pixels)]

    # --- capabilities ---

    def is_admin(self) -> bool:
        return os.geteuid() == 0

    def supports_background_mode(self) -> bool:
        return not self._error

    def capabilities(self) -> dict[str, Any]:
        unavailable = [self._error] if self._error else []
        if not self._error and not self._xtest:
            unavailable.append("Foreground clicks need the XTEST extension, which this X server lacks.")
        if getattr(self, "xwayland", False):
            unavailable.append("This X server is XWayland: clicks reach only X11 apps and the real pointer "
                               "can't be warped or restored. Run in an X11 session or use the Wayland backend.")
        return {
            "platform_name": "Linux (XWayland)" if getattr(self, "xwayland", False) else "Linux (X11)",
            "session_type": "x11",
            "background_mode": not self._error,
            "background_note": BACKGROUND_NOTE,
            "window_tracking": not self._error,
            "cursor_positioning": True,
            "can_read_cursor": True,
            "markers": True,
            "global_hotkeys": True,
            "elevation_detection": False,
            "pixel_inspection": not self._error,
            "input_method": "XTest (foreground) + XSendEvent (background)",
            "unavailable_features": unavailable,
        }
