from __future__ import annotations

import logging
import os
import sys

from .base import HotkeyBackend, InputBackend, Rect
from .fake import FakeBackend, FakeHotkeyBackend

log = logging.getLogger(__name__)

_CURRENT_BACKEND: InputBackend | None = None
_CURRENT_HOTKEY_BACKEND: HotkeyBackend | None = None


def detect_session_type() -> str:
    """'windows', 'wayland' or 'x11'.

    A Wayland session usually also sets DISPLAY (for XWayland), so DISPLAY alone doesn't mean X11:
    trust XDG_SESSION_TYPE first, then WAYLAND_DISPLAY, then DISPLAY.
    """
    if sys.platform == "win32":
        return "windows"
    session_type = os.environ.get("XDG_SESSION_TYPE", "").strip().lower()
    if session_type in ("wayland", "x11"):
        return session_type
    return "wayland" if os.environ.get("WAYLAND_DISPLAY") else "x11"


def xwayland_available() -> bool:
    """True in a Wayland session that also runs an X server for X11 apps."""
    return detect_session_type() == "wayland" and bool(os.environ.get("DISPLAY"))


def create_backend(session_type: str | None = None) -> InputBackend:
    """Create an InputBackend instance based on OS and session type."""
    st = session_type or detect_session_type()

    if st == "fake":
        log.info("Selected platform backend: Fake (In-Memory)")
        return FakeBackend()

    if st == "windows" and sys.platform != "win32":
        raise RuntimeError("the Windows backend only runs on Windows")
    if sys.platform == "win32":
        from .windows.input import WindowsInputBackend
        log.info("Selected platform backend: Windows")
        return WindowsInputBackend()

    if st == "wayland":
        from .linux import LinuxWaylandInputBackend
        log.info("Selected platform backend: Linux (Wayland)")
        return LinuxWaylandInputBackend()

    from .linux import LinuxX11InputBackend
    log.info("Selected platform backend: Linux (X11)")
    return LinuxX11InputBackend()


def create_hotkey_backend(session_type: str | None = None) -> HotkeyBackend:
    """Create a HotkeyBackend instance based on OS and session type."""
    st = session_type or detect_session_type()

    if st == "fake":
        return FakeHotkeyBackend()

    if st == "windows" and sys.platform != "win32":
        raise RuntimeError("the Windows backend only runs on Windows")
    if sys.platform == "win32":
        from .windows.hotkeys import WindowsHotkeyBackend
        return WindowsHotkeyBackend()

    from .linux import create_linux_hotkeys
    return create_linux_hotkeys(st)


def get_backend() -> InputBackend:
    """Get the active InputBackend singleton."""
    global _CURRENT_BACKEND
    if _CURRENT_BACKEND is None:
        override = os.environ.get("POINTCLICKER_BACKEND")
        _CURRENT_BACKEND = create_backend(override)
    return _CURRENT_BACKEND


def set_backend(backend: InputBackend | None) -> None:
    """Set the active InputBackend (useful for testing)."""
    global _CURRENT_BACKEND
    _CURRENT_BACKEND = backend


def get_hotkey_backend() -> HotkeyBackend:
    """Get the active HotkeyBackend singleton."""
    global _CURRENT_HOTKEY_BACKEND
    if _CURRENT_HOTKEY_BACKEND is None:
        override = os.environ.get("POINTCLICKER_BACKEND")
        _CURRENT_HOTKEY_BACKEND = create_hotkey_backend(override)
    return _CURRENT_HOTKEY_BACKEND


def set_hotkey_backend(backend: HotkeyBackend | None) -> None:
    """Set the active HotkeyBackend (useful for testing)."""
    global _CURRENT_HOTKEY_BACKEND
    _CURRENT_HOTKEY_BACKEND = backend


__all__ = [
    "InputBackend",
    "HotkeyBackend",
    "Rect",
    "FakeBackend",
    "FakeHotkeyBackend",
    "detect_session_type",
    "xwayland_available",
    "create_backend",
    "create_hotkey_backend",
    "get_backend",
    "set_backend",
    "get_hotkey_backend",
    "set_hotkey_backend",
]
