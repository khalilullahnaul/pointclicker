"""Application logic between the UI and the engine.

The UI calls the slots below and listens to the signals; it never touches the scheduler or the platform
backends itself. Worker threads (scheduler, hotkeys, test clicks) only ever emit signals, which Qt delivers
on the GUI thread.
"""
from __future__ import annotations

import logging
import os
import shlex
import subprocess
import sys
import threading
from dataclasses import replace
from functools import partial
from logging.handlers import RotatingFileHandler
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Signal, Slot

from . import clicker, targets
from .platform import get_backend, get_hotkey_backend
from .scheduler import State, Scheduler, Stats
from .targets import Target

log = logging.getLogger(__name__)

ACTIONS = {"start_pause": "Start / Pause", "stop": "Stop", "emergency_stop": "Emergency stop", "pick": "Pick a point"}
LOG_DIR = targets.config_path().parent / "logs"


class AppController(QObject):
    targets_changed = Signal()  # list or any target field changed: re-render
    target_updated = Signal(str)  # one target clicked (stats/status changed)
    state_changed = Signal(str)  # State value
    test_finished = Signal(str, object)  # target id, ClickResult
    suggest_foreground = Signal(str)  # target id whose background click had no visible effect
    notify = Signal(str, str, str)  # level (info|success|warning|error), title, message
    profiles_changed = Signal()
    settings_changed = Signal()
    pick_requested = Signal()  # the pick hotkey needs the overlay (cursor position unreadable, e.g. Wayland)

    _clicked = Signal(str)  # internal hops from worker threads to the GUI thread
    _no_effect = Signal(str)
    _hotkey = Signal(str)
    _tested = Signal(str, object)

    def __init__(self, cfg: targets.Config, parent: QObject | None = None):
        super().__init__(parent)
        self.cfg = cfg
        self.backend = get_backend()
        self.hotkey_backend = get_hotkey_backend()
        self.caps = self.backend.capabilities()
        self.blocked: set[str] = set()  # window elevated while we are not (Windows)
        self.lost: set[str] = set()  # window gone
        self.no_effect: set[str] = set()  # background click changed nothing
        self._effect_checked: set[str] = set()
        self._combos: list[str] = []
        self.scheduler = Scheduler(self._fire, on_click=lambda t: self._clicked.emit(t.id))
        self._clicked.connect(self._after_click)
        self._no_effect.connect(self._on_no_effect)
        self._hotkey.connect(self._on_hotkey)
        self._tested.connect(self._after_test)
        self._watch = QTimer(self, interval=2000, timeout=self._check_windows)

    # --- lifecycle ---

    @Slot()
    def start_services(self):
        """Once the UI is up: hotkeys, input access (Wayland may show a permission dialog), autostart."""
        self._apply_hotkeys()
        self._watch.start()
        threading.Thread(target=self._prepare, name="prepare-backend", daemon=True).start()
        if self.cfg.start_clicking_on_launch:
            self.start()

    def _prepare(self):
        try:
            self.backend.prepare()
        except Exception as e:
            log.warning("input access failed: %s", e)
            self.notify.emit("error", "Permission missing", str(e))  # signals are queued across threads

    def shutdown(self, window_geometry: str = ""):
        if window_geometry:
            self.cfg.window_geometry = window_geometry
        self.save()
        self.scheduler.shutdown()
        self.hotkey_backend.stop()

    def save(self):
        targets.save(self.cfg)

    # --- queries for rendering ---

    @property
    def targets(self) -> list[Target]:
        return self.cfg.targets

    @property
    def state(self) -> State:
        return self.scheduler.state

    def find(self, target_id: str) -> Target | None:
        return next((t for t in self.targets if t.id == target_id), None)

    def stats(self, target_id: str) -> Stats | None:
        return self.scheduler.stats.get(target_id)

    def remaining(self, target_id: str) -> float | None:
        return self.scheduler.remaining(target_id)

    def progress(self, t: Target) -> float:
        """0..1 of the current countdown elapsed (for the progress ring)."""
        left = self.remaining(t.id)
        if left is None:
            return 0.0
        period = max(t.interval_seconds, left, 1e-6)
        return min(1.0, max(0.0, 1 - left / period))

    def next_click(self) -> tuple[str, float] | None:
        due = [(left, t.name) for t in self.targets if (left := self.remaining(t.id)) is not None]
        return (min(due)[1], min(due)[0]) if due else None

    def status(self, t: Target) -> tuple[str, str]:
        """(text, level) where level is ok|warning|error|muted."""
        st = self.stats(t.id)
        if t.id in self.blocked:
            return "Window runs as administrator", "error"
        if not t.enabled:
            return "Disabled", "muted"
        if t.mode == "background" and not self.backend.supports_background_mode():
            return "Background mode unavailable here", "warning"
        if t.id in self.no_effect and t.mode == "background":
            return "No visible effect: try foreground", "warning"
        if t.id in self.lost:
            return "Window not found", "warning"
        if st and t.repeat and st.clicks >= t.repeat:
            return "Done", "muted"
        if st and st.last_ok is not None:
            return ("OK", "ok") if st.last_ok else (f"Failed: {st.last_error}", "error")
        return "Ready", "muted"

    def profiles(self) -> list[str]:
        return list(self.cfg.profiles)

    def hotkey_status(self) -> str:
        return self.hotkey_backend.status()

    def supports_background(self) -> bool:
        return self.backend.supports_background_mode()

    def linux_permissions(self) -> dict:
        from .platform.linux import setup
        return setup.permissions()

    @staticmethod
    def linux_setup_guide() -> str:
        from .platform.linux.setup import GUIDE
        return GUIDE

    # --- scheduler ---

    @Slot()
    def start(self):
        if not any(t.enabled for t in self.targets):
            self.notify.emit("warning", "Nothing to run", "Enable at least one target first.")
            return
        resuming = self.state is State.PAUSED
        if self.state is State.STOPPED:
            self._effect_checked.clear()
        self.scheduler.start(self.targets)
        self.state_changed.emit(self.state.value)
        self.notify.emit("success", "Resumed" if resuming else "Started",
                         f"{sum(t.enabled for t in self.targets)} target(s) running.")

    @Slot()
    def pause_resume(self):
        if self.state is State.PAUSED:
            self.start()
            return
        if self.state is State.RUNNING:
            self.scheduler.pause()
            self.state_changed.emit(self.state.value)
            self.notify.emit("info", "Paused", "Countdowns are frozen until you resume.")

    @Slot()
    def toggle_start_pause(self):
        self.pause_resume() if self.state is State.RUNNING else self.start()

    @Slot()
    def stop(self):
        if self.state is not State.STOPPED:
            self.scheduler.stop()
            self.state_changed.emit(self.state.value)
            self.notify.emit("info", "Stopped", "All countdowns were reset.")

    @Slot()
    def emergency_stop(self):
        self.scheduler.stop()
        for t in self.targets:
            t.enabled = False
        self._targets_changed()
        self.state_changed.emit(self.state.value)
        self.notify.emit("warning", "Emergency stop", "Stopped and disabled every target.")

    # --- targets ---

    def new_target(self, t: Target) -> Target:
        """Defaults for a freshly picked point."""
        mode = self.cfg.default_mode if self.backend.supports_background_mode() else "foreground"
        return replace(t, name=t.name or f"Target {len(self.targets) + 1}", mode=mode)

    @Slot(object)
    def add_target(self, t: Target):
        self.targets.append(t)
        self._targets_changed()
        self.notify.emit("success", "Target added", f"{t.name} at {t.x}, {t.y}"
                         + (f" in {t.window_title}" if t.window_title else ""))

    @Slot(object)
    def update_target(self, t: Target):
        for i, old in enumerate(self.targets):
            if old.id == t.id:
                self.targets[i] = t
                if t.mode != "background":
                    self.no_effect.discard(t.id)
                self._targets_changed()
                return

    @Slot(str)
    def duplicate_target(self, target_id: str):
        if t := self.find(target_id):
            self.add_target(replace(t, id=targets.new_id(), name=f"{t.name} copy"))

    @Slot(str)
    def delete_target(self, target_id: str):
        self.cfg.profiles[self.cfg.active_profile] = [t for t in self.targets if t.id != target_id]
        self._targets_changed()

    @Slot(str, bool)
    def set_enabled(self, target_id: str, on: bool):
        if (t := self.find(target_id)) and t.enabled != on:
            t.enabled = on
            self._targets_changed()

    @Slot(str)
    def use_foreground(self, target_id: str):
        if t := self.find(target_id):
            self.update_target(replace(t, mode="foreground"))

    def _targets_changed(self):
        self.save()
        self.scheduler.sync(self.targets)
        self.targets_changed.emit()

    @Slot()
    def pick_at_cursor(self):
        """Pick hotkey: the point under the cursor, or the overlay where the cursor can't be read."""
        if not self.caps.get("can_read_cursor", True):
            self.pick_requested.emit()
            return
        self.add_target(self.new_target(Target.from_point(*clicker.cursor_pos())))

    @Slot(str)
    def test_click(self, target_id: str):
        if t := self.find(target_id):
            self._effect_checked.discard(t.id)
            self.no_effect.discard(t.id)
            threading.Thread(target=lambda: self._tested.emit(t.id, self._fire(t, None)),
                             name="test-click", daemon=True).start()

    def _after_test(self, target_id: str, res):
        self.test_finished.emit(target_id, res)
        t = self.find(target_id)
        name = t.name if t else "Target"
        if res.ok:
            effect = {True: " The window changed.", False: " The window didn't change.", None: ""}[res.changed]
            self.notify.emit("success", "Test click", f"{name}: clicked in {res.elapsed_ms:.0f} ms.{effect}")
        else:
            self.notify.emit("error", "Test click failed", f"{name}: {res.error}")

    # --- clicks (worker threads) ---

    def _fire(self, t: Target, abort):
        """Scheduler / test thread. The first background click of each target per run is effect-checked."""
        check = t.mode == "background" and t.id not in self._effect_checked
        res = t.click(abort, check_effect=check)
        if res.changed is not None:
            self._effect_checked.add(t.id)
            if res.changed is False:
                self._no_effect.emit(t.id)
        return res

    def _after_click(self, target_id: str):
        st, t = self.stats(target_id), self.find(target_id)
        if t and st and st.last_ok is False and "not found" in (st.last_error or "") and t.id not in self.lost:
            self.lost.add(t.id)
            self.notify.emit("warning", "Target window lost", f"{t.name}: {st.last_error}")
        self.target_updated.emit(target_id)

    def _on_no_effect(self, target_id: str):
        if target_id not in self.no_effect:  # ask once per target
            self.no_effect.add(target_id)
            self.suggest_foreground.emit(target_id)

    def _check_windows(self):
        for t in self.targets:
            if not t.window_class:
                continue
            hwnd = t.find_window()
            if hwnd is None and t.id not in self.lost:
                self.lost.add(t.id)
                if t.enabled:
                    self.notify.emit("warning", "Target window lost", f"{t.name}: '{t.window_title}' is closed.")
            elif hwnd is not None:
                self.lost.discard(t.id)
                if self.caps.get("elevation_detection") and clicker.blocked_by_elevation(hwnd):
                    if t.id not in self.blocked:
                        self.blocked.add(t.id)
                        self.notify.emit("error", "Administrator window",
                                         f"{t.name}: the window runs as administrator. Restart PointClicker as "
                                         "administrator or Windows will block its clicks.")
                else:
                    self.blocked.discard(t.id)

    # --- profiles ---

    @Slot(str)
    def switch_profile(self, name: str):
        if name in self.cfg.profiles and name != self.cfg.active_profile:
            self.scheduler.stop()
            self.state_changed.emit(self.state.value)
            self.cfg.active_profile = name
            self._targets_changed()
            self.profiles_changed.emit()

    def new_profile(self, name: str) -> str | None:
        """Create and switch to a profile. Returns an error message, or None."""
        name = name.strip()
        if not name:
            return "Enter a name."
        if name in self.cfg.profiles:
            return f"A profile called '{name}' already exists."
        self.cfg.profiles[name] = []
        self.switch_profile(name)
        return None

    def rename_profile(self, old: str, new: str) -> str | None:
        new = new.strip()
        if not new or new in self.cfg.profiles:
            return "Enter a new, unused name."
        self.cfg.profiles = {new if k == old else k: v for k, v in self.cfg.profiles.items()}
        if self.cfg.active_profile == old:
            self.cfg.active_profile = new
        self.save()
        self.profiles_changed.emit()
        return None

    def delete_profile(self, name: str) -> str | None:
        if len(self.cfg.profiles) < 2:
            return "You can't delete the only profile."
        if name == self.cfg.active_profile:
            self.switch_profile(next(p for p in self.cfg.profiles if p != name))
        del self.cfg.profiles[name]
        self.save()
        self.profiles_changed.emit()
        return None

    # --- settings ---

    def set_setting(self, name: str, value):
        setattr(self.cfg, name, value)
        if name == "launch_at_login":
            try:
                set_launch_at_login(bool(value))
            except OSError as e:
                self.notify.emit("error", "Launch at login", str(e))
        self.save()
        self.settings_changed.emit()

    def validate_hotkey(self, combo: str) -> str | None:
        try:
            self.hotkey_backend.validate(combo)
            return None
        except Exception as e:
            return str(e) or "unsupported key"

    def set_hotkey(self, action: str, combo: str) -> str | None:
        if combo and (error := self.validate_hotkey(combo)):
            return error
        if combo and any(a != action and c.lower() == combo.lower() for a, c in self.cfg.hotkeys.items()):
            return f"{combo} is already used by another action."
        self.cfg.hotkeys[action] = combo
        self.save()
        self._apply_hotkeys()
        self.settings_changed.emit()
        return None

    @Slot(bool)
    def suspend_hotkeys(self, suspended: bool):
        """While the user records a new hotkey, the old ones must not fire."""
        self.hotkey_backend.stop() if suspended else self._apply_hotkeys()

    def _apply_hotkeys(self):
        hk = self.hotkey_backend
        hk.stop()
        for combo in self._combos:
            hk.unregister(combo)
        self._combos = [c for c in self.cfg.hotkeys.values() if c.strip()]
        for action, combo in self.cfg.hotkeys.items():
            if combo.strip():
                hk.register(combo, partial(self._hotkey.emit, action))
        try:
            hk.start()
        except Exception as e:
            log.warning("hotkeys failed to start: %s", e)
            self.notify.emit("warning", "Hotkeys unavailable", str(e))

    def _on_hotkey(self, action: str):
        {"start_pause": self.toggle_start_pause, "stop": self.stop,
         "emergency_stop": self.emergency_stop, "pick": self.pick_at_cursor}[action]()


def launch_command() -> list[str]:
    return [sys.executable] if getattr(sys, "frozen", False) else [sys.executable, "-m", "pointclicker"]


def set_launch_at_login(on: bool) -> None:
    """Windows: HKCU Run key. Linux: an XDG autostart entry."""
    if sys.platform == "win32":
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run", 0,
                            winreg.KEY_SET_VALUE) as key:
            if on:
                winreg.SetValueEx(key, "PointClicker", 0, winreg.REG_SZ, subprocess.list2cmdline(launch_command()))
            else:
                try:
                    winreg.DeleteValue(key, "PointClicker")
                except FileNotFoundError:
                    pass
        return
    path = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "autostart" / "pointclicker.desktop"
    if on:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("[Desktop Entry]\nType=Application\nName=PointClicker\n"
                        f"Exec={shlex.join(launch_command())}\nX-GNOME-Autostart-enabled=true\n", encoding="utf-8")
    else:
        path.unlink(missing_ok=True)


def setup_logging():
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    handlers = [RotatingFileHandler(LOG_DIR / "pointclicker.log", maxBytes=1_000_000, backupCount=5, encoding="utf-8")]
    if sys.stderr:  # None in the windowed exe
        handlers.append(logging.StreamHandler())
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(threadName)s %(name)s: %(message)s",
                        handlers=handlers)
    sys.excepthook = lambda *exc: log.critical("unhandled exception", exc_info=exc)
    threading.excepthook = lambda a: log.critical("unhandled exception in thread %s", a.thread and a.thread.name,
                                                  exc_info=(a.exc_type, a.exc_value, a.exc_traceback))
