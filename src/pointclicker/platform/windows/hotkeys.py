from __future__ import annotations

import logging
import sys
from collections.abc import Callable

from ..base import HotkeyBackend

log = logging.getLogger(__name__)


def to_pynput(combo: str) -> str:
    """'Ctrl+Alt+F6' -> '<ctrl>+<alt>+<f6>' (pynput GlobalHotKeys format)."""
    return "+".join(k if len(k) == 1 else f"<{k}>" for k in combo.lower().replace(" ", "").split("+"))


class WindowsHotkeyBackend(HotkeyBackend):
    """Windows global hotkey backend using pynput."""

    def __init__(self):
        if sys.platform != "win32":
            raise RuntimeError("WindowsHotkeyBackend is only supported on Windows")
        self._handlers: dict[str, Callable[[], None]] = {}
        self._listener = None

    def register(self, combo: str, callback: Callable[[], None]) -> None:
        self._handlers[combo] = callback
        if self._listener is not None:
            self._restart()

    def unregister(self, combo: str) -> None:
        if combo in self._handlers:
            del self._handlers[combo]
            if self._listener is not None:
                self._restart()

    def start(self) -> None:
        if self._listener is not None:
            return
        self._start_listener()

    def stop(self) -> None:
        if self._listener is not None:
            try:
                self._listener.stop()
            except Exception as e:
                log.debug("Error stopping hotkey listener: %s", e)
            self._listener = None

    def _start_listener(self) -> None:
        from pynput import keyboard

        pynput_map = {}
        for combo, cb in self._handlers.items():
            if combo.strip():
                try:
                    pynput_map[to_pynput(combo)] = cb
                except Exception as e:
                    log.warning("Invalid hotkey '%s': %s", combo, e)

        if pynput_map:
            try:
                self._listener = keyboard.GlobalHotKeys(pynput_map)
                self._listener.start()
            except Exception as e:
                log.error("Failed to start hotkey listener: %s", e)
                self._listener = None

    def _restart(self) -> None:
        self.stop()
        self._start_listener()
