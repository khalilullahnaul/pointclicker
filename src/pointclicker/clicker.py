"""Core click engine and platform dispatcher.

Coordinates are dispatched to the active platform InputBackend.
BACKGROUND: PostMessage mouse messages (Windows only).
FOREGROUND: jumps cursor, sends physical/synthetic click, restores cursor position.
"""
from __future__ import annotations

import logging
import sys
import threading
from dataclasses import dataclass
from enum import Enum
from typing import Any

from .platform import get_backend

log = logging.getLogger(__name__)

# Standard button definitions
_BUTTONS = {
    "left": (0x0201, 0x0202, 0x0203, 0x0001, 0x0002, 0x0004),
    "right": (0x0204, 0x0205, 0x0206, 0x0002, 0x0008, 0x0010),
    "middle": (0x0207, 0x0208, 0x0209, 0x0010, 0x0020, 0x0040),
}

CANCELLED = "cancelled"


class Mode(Enum):
    BACKGROUND = "background"
    FOREGROUND = "foreground"


@dataclass
class ClickResult:
    ok: bool
    mode: Mode
    error: str | None = None
    hwnd: Any | None = None
    elapsed_ms: float = 0.0
    changed: bool | None = None  # with check_effect: did the window's pixels change? None = couldn't tell

    def __bool__(self) -> bool:
        return self.ok


def click(x: int, y: int, mode: Mode = Mode.BACKGROUND, button: str = "left",
          double: bool = False, busy_timeout: float = 1.0, hwnd: Any = None,
          abort: threading.Event | None = None, check_effect: bool = False) -> ClickResult:
    """Click at screen coordinates (x, y). Dispatches to current platform backend."""
    return get_backend().click((x, y), mode=mode, button=button, double=double,
                               busy_timeout=busy_timeout, hwnd=hwnd, abort=abort,
                               check_effect=check_effect)


def cursor_pos() -> tuple[int, int]:
    return get_backend().get_cursor_pos()


def window_at(x: int, y: int, root: Any = None) -> Any:
    return get_backend().window_at(x, y, root=root)


def top_window_at(x: int, y: int) -> Any:
    return get_backend().top_window_at(x, y)


def window_rect(hwnd: Any) -> Any:
    return get_backend().window_rect(hwnd)


def window_text(hwnd: Any) -> str:
    return get_backend().window_text(hwnd)


def window_class(hwnd: Any) -> str:
    return get_backend().window_class(hwnd)


def FindWindowW(class_name: str | None, window_name: str | None = None) -> Any:
    return get_backend().find_window(class_name, window_name)


def IsIconic(hwnd: Any) -> bool:
    return get_backend().is_window_minimized(hwnd)


def is_admin() -> bool:
    return get_backend().is_admin()


def blocked_by_elevation(hwnd: Any) -> bool:
    return get_backend().blocked_by_elevation(hwnd)


def snapshot(hwnd: Any) -> bytes | None:
    return get_backend().snapshot(hwnd)


def SetWindowPos(hwnd: Any, *args: Any, **kwargs: Any) -> bool:
    return get_backend().set_window_pos(hwnd, *args, **kwargs)


# Re-export Win32 DLL handles ONLY on Windows to prevent breaking scripts like test_apps.py
if sys.platform == "win32":
    from .platform.windows.input import (
        advapi32 as advapi32,
    )
    from .platform.windows.input import (
        gdi32 as gdi32,
    )
    from .platform.windows.input import (
        kernel32 as kernel32,
    )
    from .platform.windows.input import (
        shcore as shcore,
    )
    from .platform.windows.input import (
        user32 as user32,
    )
