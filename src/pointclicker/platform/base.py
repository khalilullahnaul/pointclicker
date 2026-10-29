from __future__ import annotations

import logging
import threading
import time
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..clicker import ClickResult, Mode

log = logging.getLogger(__name__)
EFFECT_SETTLE_S = 0.5  # how long a clicked window gets to visibly react before the "after" snapshot
_click_lock = threading.Lock()  # one click at a time process-wide: clicks share the one real pointer


@dataclass
class Rect:
    left: int
    top: int
    right: int
    bottom: int

    @property
    def width(self) -> int:
        return self.right - self.left

    @property
    def height(self) -> int:
        return self.bottom - self.top


class InputBackend(ABC):
    """Abstract interface for platform-specific input and window operations."""

    @abstractmethod
    def click(self, target: Any, abort: threading.Event | None = None,
              check_effect: bool = False, **kwargs: Any) -> ClickResult:
        """Perform a click for the given Target or (x, y) coordinate."""
        pass

    @abstractmethod
    def get_cursor_pos(self) -> tuple[int, int]:
        """Current cursor position in screen coordinates."""
        pass

    @abstractmethod
    def window_at(self, x: int, y: int, root: Any = None) -> Any:
        """Window handle or identifier at screen point (x, y)."""
        pass

    @abstractmethod
    def window_rect(self, handle: Any) -> Rect | None:
        """Bounding rectangle of the specified window."""
        pass

    @abstractmethod
    def is_mouse_button_held(self) -> bool:
        """True if the user is currently holding any mouse button."""
        pass

    @abstractmethod
    def supports_background_mode(self) -> bool:
        """True if background clicks without cursor movement are supported."""
        pass

    @abstractmethod
    def capabilities(self) -> dict[str, Any]:
        """Dictionary of platform features, availability, and explanations."""
        pass

    # Convenience and window query helpers with default fallbacks
    def cursor_pos(self) -> tuple[int, int]:
        return self.get_cursor_pos()

    def top_window_at(self, x: int, y: int) -> Any:
        return self.window_at(x, y)

    def window_text(self, handle: Any) -> str:
        return ""

    def window_class(self, handle: Any) -> str:
        return ""

    def find_window(self, class_name: str | None, title: str | None = None) -> Any:
        return None

    def is_window_minimized(self, handle: Any) -> bool:
        return False

    def is_admin(self) -> bool:
        return False

    def blocked_by_elevation(self, handle: Any) -> bool:
        return False

    def snapshot(self, handle: Any) -> bytes | None:
        return None

    def set_window_pos(self, handle: Any, *args: Any, **kwargs: Any) -> bool:
        return False

    def prepare(self) -> None:  # noqa: B027 - optional hook, most backends need nothing
        """Acquire anything that may need user interaction (e.g. a portal permission dialog). May block."""

    def monitors(self) -> list[Rect]:
        return []

    # --- shared click flow for backends that implement _perform() ---

    def _perform(self, x: int, y: int, mode: Mode, button: str, double: bool, window: Any) -> str | None:
        """Do one click. Returns an error message or None. Called with the global click lock held."""
        raise NotImplementedError

    def _run_click(self, target: Any, abort: threading.Event | None, check_effect: bool,
                   kwargs: dict[str, Any]) -> ClickResult:
        from ..clicker import CANCELLED, ClickResult, Mode

        if hasattr(target, "resolve"):
            pos, mode, button = target.resolve(), Mode(target.mode), target.button
            double, window = target.click_type == "double", target.find_window()
            if pos is None:
                return ClickResult(False, mode, f"window '{target.window_title}' ({target.window_class}) not found")
        else:
            pos, mode = (int(target[0]), int(target[1])), Mode(kwargs.get("mode", Mode.FOREGROUND))
            button, double, window = kwargs.get("button", "left"), kwargs.get("double", False), kwargs.get("hwnd")
        if button not in ("left", "right", "middle"):
            raise ValueError(f"button must be left, right or middle, got {button!r}")
        if abort and abort.is_set():
            return ClickResult(False, mode, CANCELLED)
        if mode is Mode.BACKGROUND and not self.supports_background_mode():
            return ClickResult(False, mode, self.capabilities().get("background_note", "background mode unavailable"))
        if window is not None and self.is_window_minimized(window):
            return ClickResult(False, mode, "target window is minimized")

        deadline = time.monotonic() + kwargs.get("busy_timeout", 1.0)
        while self.is_mouse_button_held():  # don't fight the user's own drag
            if abort and abort.is_set():
                return ClickResult(False, mode, CANCELLED)
            if time.monotonic() >= deadline:
                log.warning("click at %s skipped: user is holding a mouse button", pos)
                return ClickResult(False, mode, "user is holding a mouse button")
            time.sleep(0.02)

        probe = (window or self.top_window_at(*pos)) if check_effect else None
        before = self._static_snapshot(probe) if probe is not None else None
        with _click_lock:
            if abort and abort.is_set():
                return ClickResult(False, mode, CANCELLED)
            t0 = time.monotonic()
            try:
                error = self._perform(pos[0], pos[1], mode, button, double, window)
            except Exception as e:  # backend I/O errors (X connection, D-Bus) become a failed click
                log.exception("%s click at %s raised", mode.value, pos)
                error = str(e) or type(e).__name__
            elapsed = (time.monotonic() - t0) * 1000
        result = ClickResult(error is None, mode, error, window, elapsed)
        if error:
            log.error("%s click at %s failed: %s", mode.value, pos, error)
        elif before is not None:
            time.sleep(EFFECT_SETTLE_S)
            after = self.snapshot(probe)
            result.changed = None if after is None else after != before
            log.info("%s click at %s: window %s", mode.value, pos,
                     {True: "changed", False: "unchanged", None: "not capturable"}[result.changed])
        return result

    def _static_snapshot(self, handle: Any) -> bytes | None:
        """Snapshot only if the window isn't animating by itself (blinking caret, video), else None."""
        a = self.snapshot(handle)
        time.sleep(0.3)
        b = self.snapshot(handle)
        return b if a is not None and a == b else None


_MODIFIERS = {"ctrl": "ctrl", "control": "ctrl", "alt": "alt", "shift": "shift",
              "super": "super", "win": "super", "meta": "super", "cmd": "super"}
_ALIASES = {"esc": "escape", "enter": "return", "del": "delete", "ins": "insert", "pgup": "page_up",
            "pageup": "page_up", "pgdn": "page_down", "pagedown": "page_down"}

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


class HotkeyBackend(ABC):
    """Abstract interface for global hotkey management."""

    @abstractmethod
    def register(self, combo: str, callback: Callable[[], None]) -> None:
        """Register a key combination and its callback."""
        pass

    @abstractmethod
    def unregister(self, combo: str) -> None:
        """Unregister a key combination."""
        pass

    @abstractmethod
    def start(self) -> None:
        """Start listening for hotkeys."""
        pass

    @abstractmethod
    def stop(self) -> None:
        """Stop listening for hotkeys."""
        pass

    def validate(self, combo: str) -> None:
        """Raise ValueError if this backend can't bind combo (e.g. 'Ctrl+Alt+F6')."""
        from pynput import keyboard
        keyboard.HotKey.parse("+".join(k if len(k) == 1 else f"<{k}>" for k in combo.lower().replace(" ", "").split("+")))

    def status(self) -> str:
        """One line for diagnostics: which mechanism is used and whether it works."""
        return type(self).__name__
