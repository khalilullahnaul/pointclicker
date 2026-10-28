"""Linux global hotkeys: XGrabKey on X11; GlobalShortcuts portal or evdev keyboards on Wayland.

Combos are written like "F6" or "Ctrl+Alt+S" in every backend.
"""
from __future__ import annotations

import functools
import logging
import os
import re
import select
import threading
from typing import Callable

from ..base import HotkeyBackend

log = logging.getLogger(__name__)

_MODIFIERS = {"ctrl": "ctrl", "control": "ctrl", "alt": "alt", "shift": "shift",
              "super": "super", "win": "super", "meta": "super", "cmd": "super"}
_ALIASES = {"esc": "escape", "enter": "return", "del": "delete", "ins": "insert", "pgup": "page_up",
            "pageup": "page_up", "pgdn": "page_down", "pagedown": "page_down"}
# our key name -> X keysym name (also the xkb names the GlobalShortcuts portal expects)
_KEYSYMS = {"escape": "Escape", "return": "Return", "delete": "Delete", "insert": "Insert", "page_up": "Prior",
            "page_down": "Next", "space": "space", "tab": "Tab", "backspace": "BackSpace", "home": "Home",
            "end": "End", "up": "Up", "down": "Down", "left": "Left", "right": "Right", "pause": "Pause",
            "print": "Print", "scroll_lock": "Scroll_Lock"}
# our key name -> evdev KEY_* suffix where it differs
_EVDEV = {"escape": "ESC", "return": "ENTER", "page_up": "PAGEUP", "page_down": "PAGEDOWN",
          "print": "SYSRQ", "scroll_lock": "SCROLLLOCK"}


def parse_combo(combo: str) -> tuple[frozenset[str], str]:
    """'Ctrl+Alt+F6' -> ({'ctrl', 'alt'}, 'f6'). Raises ValueError."""
    parts = [p for p in combo.lower().replace(" ", "").split("+") if p]
    if not parts:
        raise ValueError("empty hotkey")
    *mods, key = parts
    if bad := [m for m in mods if m not in _MODIFIERS]:
        raise ValueError(f"unknown modifier {bad[0]!r} in {combo!r}")
    if key in _MODIFIERS:
        raise ValueError(f"{combo!r} has no key besides modifiers")
    return frozenset(_MODIFIERS[m] for m in mods), _ALIASES.get(key, key)


def keysym_name(key: str) -> str:
    if re.fullmatch(r"f\d{1,2}", key):
        return key.upper()
    return _KEYSYMS.get(key, key)


def evdev_code(key: str) -> int:
    from evdev import ecodes
    code = ecodes.ecodes.get("KEY_" + _EVDEV.get(key, key).upper())
    if code is None:
        raise ValueError(f"unknown key {key!r}")
    return code


class _Base(HotkeyBackend):
    def __init__(self):
        self._handlers: dict[str, Callable[[], None]] = {}
        self._running = False

    def register(self, combo: str, callback: Callable[[], None]) -> None:
        self._handlers[combo] = callback
        self._restart_if_running()

    def unregister(self, combo: str) -> None:
        if self._handlers.pop(combo, None):
            self._restart_if_running()

    def _restart_if_running(self):
        if self._running:
            self.stop()
            self.start()

    def _fire(self, combo: str):
        try:
            self._handlers[combo]()
        except Exception:
            log.exception("hotkey %s handler failed", combo)


class X11Hotkeys(_Base):
    """XGrabKey on the root window, once per lock-key state so NumLock/CapsLock don't break the hotkey."""

    def __init__(self):
        super().__init__()
        self._thread = self._wake = None
        self.taken: list[str] = []  # combos another client had already grabbed

    def validate(self, combo: str) -> None:
        from Xlib import XK
        if not XK.string_to_keysym(keysym_name(parse_combo(combo)[1])):
            raise ValueError(f"unknown key in {combo!r}")

    def start(self) -> None:
        from Xlib import X, XK, display, error
        if self._running:
            return
        d = display.Display()
        root = d.screen().root
        masks = {"ctrl": X.ControlMask, "alt": X.Mod1Mask, "shift": X.ShiftMask, "super": X.Mod4Mask}
        numlock = self._modifier_mask(d, XK.XK_Num_Lock)
        locks = sorted({0, X.LockMask, numlock, numlock | X.LockMask})
        table, self.taken = {}, []
        for combo in self._handlers:
            try:
                mods, key = parse_combo(combo)
                keycode = d.keysym_to_keycode(XK.string_to_keysym(keysym_name(key)))
            except ValueError as e:
                log.warning("hotkey %r ignored: %s", combo, e)
                continue
            if not keycode:
                log.warning("hotkey %r ignored: no key on this keyboard layout", combo)
                continue
            mask = sum(masks[m] for m in mods)
            catch = error.CatchError(error.BadAccess)
            for lock in locks:
                root.grab_key(keycode, mask | lock, True, X.GrabModeAsync, X.GrabModeAsync, onerror=catch)
            d.sync()
            if catch.get_error():
                self.taken.append(combo)
                log.warning("hotkey %s is already grabbed by another application", combo)
            else:
                table[(keycode, mask)] = combo
        self._wake = os.pipe()
        self._thread = threading.Thread(target=self._loop, args=(d, root, table), name="x11-hotkeys", daemon=True)
        self._thread.start()
        self._running = True

    @staticmethod
    def _modifier_mask(d, keysym) -> int:
        keycode = d.keysym_to_keycode(keysym)
        for index, keycodes in enumerate(d.get_modifier_mapping()):
            if keycode and keycode in keycodes:
                return 1 << index
        return 0

    def _loop(self, d, root, table):
        from Xlib import X
        relevant = X.ShiftMask | X.ControlMask | X.Mod1Mask | X.Mod4Mask
        last_release: dict[int, int] = {}
        try:
            while True:
                if self._wake[0] in select.select([d.fileno(), self._wake[0]], [], [])[0]:
                    return
                while d.pending_events():
                    ev = d.next_event()
                    if ev.type == X.KeyRelease:
                        last_release[ev.detail] = ev.time
                    # auto-repeat shows up as release+press with the same timestamp; act on real presses only
                    elif ev.type == X.KeyPress and last_release.get(ev.detail) != ev.time:
                        if combo := table.get((ev.detail, ev.state & relevant)):
                            self._fire(combo)
        finally:
            root.ungrab_key(X.AnyKey, X.AnyModifier)
            d.close()

    def stop(self) -> None:
        if not self._running:
            return
        os.write(self._wake[1], b"x")
        self._thread.join(timeout=2)
        for fd in self._wake:
            os.close(fd)
        self._running = False

    def status(self) -> str:
        taken = f"; already taken by another app: {', '.join(self.taken)}" if self.taken else ""
        return f"XGrabKey (X11), {len(self._handlers)} hotkeys{taken}"


class PortalHotkeys(_Base):
    """xdg-desktop-portal GlobalShortcuts (KDE Plasma, GNOME 48+, Hyprland). The desktop may ask the user once."""

    def __init__(self):
        super().__init__()
        self._thread = self._portal = None
        self._stop = threading.Event()

    def validate(self, combo: str) -> None:
        parse_combo(combo)

    @staticmethod
    def trigger(combo: str) -> str:
        """'Ctrl+Alt+F6' -> 'CTRL+ALT+F6' (XDG shortcuts spec)."""
        mods, key = parse_combo(combo)
        names = {"ctrl": "CTRL", "alt": "ALT", "shift": "SHIFT", "super": "LOGO"}
        return "+".join([names[m] for m in ("ctrl", "alt", "shift", "super") if m in mods] + [keysym_name(key)])

    def start(self) -> None:
        from .portal import GLOBAL_SHORTCUTS, Portal
        if self._running or not self._handlers:
            return
        p = self._portal = Portal()
        session = p.request(GLOBAL_SHORTCUTS, "CreateSession", "a{sv}",
                            {"session_handle_token": ("s", p.token())})["session_handle"][1]
        ids = {f"pointclicker-{i}": combo for i, combo in enumerate(self._handlers)}
        shortcuts = [(sid, {"description": ("s", f"PointClicker: {combo}"),
                            "preferred_trigger": ("s", self.trigger(combo))}) for sid, combo in ids.items()]
        p.request(GLOBAL_SHORTCUTS, "BindShortcuts", "oa(sa{sv})sa{sv}", session, shortcuts, "", {})
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, args=(p, session, ids), name="portal-hotkeys", daemon=True)
        self._thread.start()
        self._running = True

    def _loop(self, p, session, ids):
        from .portal import GLOBAL_SHORTCUTS
        with p.signals(GLOBAL_SHORTCUTS, "Activated") as queue:
            while not self._stop.is_set():
                try:
                    msg = p.conn.recv_until_filtered(queue, timeout=0.5)
                except TimeoutError:
                    continue
                if msg.body[0] == session and (combo := ids.get(msg.body[1])):
                    self._fire(combo)

    def stop(self) -> None:
        if not self._running:
            return
        self._stop.set()
        self._thread.join(timeout=2)
        self._portal.close()
        self._running = False

    def status(self) -> str:
        return f"GlobalShortcuts portal, {len(self._handlers)} hotkeys"


class EvdevHotkeys(_Base):
    """Reads every keyboard under /dev/input (needs the input group). Keys still reach the focused app."""

    MODS = {"KEY_LEFTCTRL": "ctrl", "KEY_RIGHTCTRL": "ctrl", "KEY_LEFTALT": "alt", "KEY_RIGHTALT": "alt",
            "KEY_LEFTSHIFT": "shift", "KEY_RIGHTSHIFT": "shift", "KEY_LEFTMETA": "super", "KEY_RIGHTMETA": "super"}

    def __init__(self):
        super().__init__()
        self._thread = self._wake = None
        self._devices = []

    def validate(self, combo: str) -> None:
        evdev_code(parse_combo(combo)[1])

    @staticmethod
    def keyboards():
        import evdev
        found = []
        for path in evdev.list_devices():  # lists only devices we may open
            dev = evdev.InputDevice(path)
            if evdev.ecodes.KEY_A in dev.capabilities().get(evdev.ecodes.EV_KEY, []):
                found.append(dev)
            else:
                dev.close()
        return found

    def start(self) -> None:
        from evdev import ecodes
        if self._running:
            return
        table = {}
        for combo in self._handlers:
            try:
                mods, key = parse_combo(combo)
                table[(mods, evdev_code(key))] = combo
            except ValueError as e:
                log.warning("hotkey %r ignored: %s", combo, e)
        self._devices = self.keyboards()
        if not self._devices:
            log.warning("no readable keyboards under /dev/input; hotkeys are off (see the Linux setup guide)")
            return
        mod_codes = {ecodes.ecodes[name]: mod for name, mod in self.MODS.items()}
        self._wake = os.pipe()
        self._thread = threading.Thread(target=self._loop, args=(table, mod_codes), name="evdev-hotkeys", daemon=True)
        self._thread.start()
        self._running = True

    def _loop(self, table, mod_codes):
        from evdev import ecodes
        held: set[int] = set()
        fds = {dev.fd: dev for dev in self._devices}
        while fds:
            ready = select.select([*fds, self._wake[0]], [], [])[0]
            if self._wake[0] in ready:
                return
            for fd in ready:
                try:
                    events = list(fds[fd].read())
                except OSError:  # keyboard unplugged
                    fds.pop(fd).close()
                    continue
                for ev in events:
                    if ev.type != ecodes.EV_KEY:
                        continue
                    if ev.code in mod_codes:
                        (held.add if ev.value else held.discard)(ev.code)
                    elif ev.value == 1:  # 1 = press, 2 = auto-repeat, 0 = release
                        mods = frozenset(mod_codes[c] for c in held)
                        if combo := table.get((mods, ev.code)):
                            self._fire(combo)

    def stop(self) -> None:
        if not self._running:
            return
        os.write(self._wake[1], b"x")
        self._thread.join(timeout=2)
        for fd in self._wake:
            os.close(fd)
        for dev in self._devices:
            dev.close()
        self._devices = []
        self._running = False

    def status(self) -> str:
        return f"evdev keyboards ({len(self._devices)} open), {len(self._handlers)} hotkeys"


class UnavailableHotkeys(_Base):
    def __init__(self, reason: str):
        super().__init__()
        self.reason = reason

    def validate(self, combo: str) -> None:
        parse_combo(combo)

    def start(self) -> None:
        log.warning("global hotkeys unavailable: %s", self.reason)

    def stop(self) -> None:
        pass

    def status(self) -> str:
        return f"unavailable: {self.reason}"


@functools.cache  # permissions only change after a re-login
def wayland_hotkey_method() -> str | None:
    """'portal', 'evdev' or None. X11 grabs only see keys while an X11 window is focused, so they aren't global here."""
    try:
        from .portal import GLOBAL_SHORTCUTS, Portal
        p = Portal()
        try:
            if p.version(GLOBAL_SHORTCUTS):
                return "portal"
        finally:
            p.close()
    except Exception:
        pass
    try:
        kbds = EvdevHotkeys.keyboards()
        for k in kbds:
            k.close()
        return "evdev" if kbds else None
    except Exception:
        return None


def create_linux_hotkeys(session_type: str) -> HotkeyBackend:
    if session_type == "x11":
        return X11Hotkeys()
    method = wayland_hotkey_method()
    if method == "portal":
        return PortalHotkeys()
    if method == "evdev":
        return EvdevHotkeys()
    return UnavailableHotkeys("this desktop has no GlobalShortcuts portal and PointClicker can't read "
                              "/dev/input keyboards (join the input group; see the Linux setup guide)")
