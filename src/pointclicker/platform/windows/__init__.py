from __future__ import annotations

import sys

if sys.platform == "win32":
    from .hotkeys import WindowsHotkeyBackend
    from .input import WindowsInputBackend
else:
    WindowsInputBackend = None  # type: ignore
    WindowsHotkeyBackend = None  # type: ignore

__all__ = ["WindowsInputBackend", "WindowsHotkeyBackend"]
