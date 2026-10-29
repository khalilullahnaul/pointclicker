from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .base import HotkeyBackend, InputBackend, Rect, parse_combo


@dataclass
class RecordedClick:
    target: Any
    x: int
    y: int
    mode: str
    button: str
    click_type: str
    timestamp: float
    abort_set: bool = False


class FakeBackend(InputBackend):
    """In-memory backend for testing without triggering physical OS events."""

    def __init__(self) -> None:
        self.clicks: list[RecordedClick] = []
        self._cursor_pos: tuple[int, int] = (100, 200)
        self._mouse_held: bool = False
        self._background_supported: bool = True
        self.windows: dict[Any, dict[str, Any]] = {}
        self.fail_targets: dict[str, str] = {}  # target id -> error message

    def click(self, target: Any, abort: threading.Event | None = None,
              check_effect: bool = False, **kwargs: Any) -> Any:
        # Import lazily to avoid circular imports
        from ..clicker import ClickResult, Mode

        abort_set = bool(abort and abort.is_set())
        mode_val = getattr(target, "mode", kwargs.get("mode", "background"))
        mode = Mode(mode_val) if isinstance(mode_val, str) else mode_val

        if abort_set:
            return ClickResult(ok=False, mode=mode, error="cancelled")

        # Determine target attributes
        target_id = getattr(target, "id", "")
        if target_id in self.fail_targets:
            return ClickResult(ok=False, mode=mode, error=self.fail_targets[target_id])

        if hasattr(target, "resolve"):
            pos = target.resolve()
            if pos is None:
                return ClickResult(ok=False, mode=mode, error=f"window '{target.window_title}' not found")
            x, y = pos
        elif isinstance(target, (tuple, list)) and len(target) >= 2:
            x, y = int(target[0]), int(target[1])
        else:
            x = getattr(target, "x", kwargs.get("x", 0))
            y = getattr(target, "y", kwargs.get("y", 0))

        btn = getattr(target, "button", kwargs.get("button", "left"))
        ct = getattr(target, "click_type", kwargs.get("click_type", "single"))

        record = RecordedClick(
            target=target,
            x=x,
            y=y,
            mode=mode.value if hasattr(mode, "value") else str(mode),
            button=btn,
            click_type=ct,
            timestamp=time.monotonic(),
            abort_set=abort_set,
        )
        self.clicks.append(record)

        return ClickResult(
            ok=True,
            mode=mode,
            elapsed_ms=0.5,
            changed=True if check_effect else None,
        )

    def get_cursor_pos(self) -> tuple[int, int]:
        return self._cursor_pos

    def set_cursor_pos(self, x: int, y: int) -> None:
        self._cursor_pos = (x, y)

    def window_at(self, x: int, y: int, root: Any = None) -> Any:
        for hwnd, info in self.windows.items():
            r = info.get("rect")
            if r and r.left <= x <= r.right and r.top <= y <= r.bottom:
                return hwnd
        return 12345 if self.windows else None

    def window_rect(self, handle: Any) -> Rect | None:
        if handle in self.windows:
            rect: Rect = self.windows[handle].get("rect", Rect(100, 100, 800, 600))
            return rect
        return Rect(100, 100, 800, 600) if handle else None

    def is_mouse_button_held(self) -> bool:
        return self._mouse_held

    def set_mouse_button_held(self, held: bool) -> None:
        self._mouse_held = held

    def supports_background_mode(self) -> bool:
        return self._background_supported

    def set_supports_background_mode(self, supported: bool) -> None:
        self._background_supported = supported

    def capabilities(self) -> dict[str, Any]:
        return {
            "platform_name": "Fake (Test)",
            "session_type": "fake",
            "background_mode": self._background_supported,
            "window_tracking": True,
            "cursor_positioning": True,
            "global_hotkeys": True,
            "elevation_detection": False,
            "pixel_inspection": True,
            "unavailable_features": [] if self._background_supported else ["Background mode is disabled."],
        }

    def clear(self) -> None:
        self.clicks.clear()

    def window_text(self, handle: Any) -> str:
        return str(self.windows.get(handle, {}).get("title", "Test Window"))

    def window_class(self, handle: Any) -> str:
        return str(self.windows.get(handle, {}).get("class", "TestClass"))

    def find_window(self, class_name: str | None, title: str | None = None) -> Any:
        for hwnd, info in self.windows.items():
            if class_name and info.get("class") == class_name:
                if title is None or info.get("title") == title:
                    return hwnd
        return None

    def is_window_minimized(self, handle: Any) -> bool:
        return bool(self.windows.get(handle, {}).get("minimized", False))

    def is_admin(self) -> bool:
        return False

    def blocked_by_elevation(self, handle: Any) -> bool:
        return bool(self.windows.get(handle, {}).get("elevated", False))

    def snapshot(self, handle: Any) -> bytes | None:
        return b"fake_pixel_data"


class FakeHotkeyBackend(HotkeyBackend):
    """In-memory hotkey backend for testing."""

    def __init__(self) -> None:
        self.registered: dict[str, Callable[[], None]] = {}
        self.started: bool = False

    def register(self, combo: str, callback: Callable[[], None]) -> None:
        self.registered[combo] = callback

    def unregister(self, combo: str) -> None:
        self.registered.pop(combo, None)

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.started = False

    def validate(self, combo: str) -> None:
        parse_combo(combo)  # syntax only: the base check imports pynput, which needs an X display on Linux

    def trigger(self, combo: str) -> bool:
        """Helper to invoke a registered hotkey."""
        cb = self.registered.get(combo)
        if cb:
            cb()
            return True
        return False
