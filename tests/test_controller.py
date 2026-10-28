"""AppController: the logic between UI and engine, tested without any UI."""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from pointclicker import targets  # noqa: E402
from pointclicker.controller import AppController, set_launch_at_login  # noqa: E402
from pointclicker.platform import FakeBackend, FakeHotkeyBackend, set_backend, set_hotkey_backend  # noqa: E402
from pointclicker.scheduler import State  # noqa: E402
from pointclicker.targets import Target  # noqa: E402

app = QApplication.instance() or QApplication([])  # a full app: other test modules create widgets


def pump():
    for _ in range(5):
        app.processEvents()


class ControllerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        cfg_file = Path(self.tmp.name) / "config.json"
        p = patch("pointclicker.targets.config_path", lambda: cfg_file)
        p.start()
        self.addCleanup(p.stop)
        self.cfg_file = cfg_file
        self.fake, self.hotkeys = FakeBackend(), FakeHotkeyBackend()
        set_backend(self.fake)
        set_hotkey_backend(self.hotkeys)
        self.c = AppController(targets.Config())
        self.events = []
        for name in ("targets_changed", "state_changed", "notify", "profiles_changed", "suggest_foreground",
                     "pick_requested"):
            getattr(self.c, name).connect(lambda *a, n=name: self.events.append((n, *a)))

    def tearDown(self):
        self.c.shutdown()
        set_backend(None)
        set_hotkey_backend(None)
        self.tmp.cleanup()

    def names(self):
        return [e[0] for e in self.events]

    def test_target_crud_persists_and_signals(self):
        t = Target(name="a", x=1, y=2)
        self.c.add_target(t)
        self.c.duplicate_target(t.id)
        self.assertEqual([x.name for x in self.c.targets], ["a", "a copy"])
        self.c.set_enabled(t.id, False)
        self.c.update_target(Target(id=t.id, name="renamed", x=5, y=6))
        self.c.delete_target(self.c.targets[1].id)
        saved = targets.load(self.cfg_file)
        self.assertEqual([(x.name, x.x) for x in saved.targets], [("renamed", 5)])
        self.assertGreaterEqual(self.names().count("targets_changed"), 5)

    def test_run_controls_emit_state_and_toasts(self):
        self.c.start()  # nothing enabled yet
        self.assertEqual(self.c.state, State.STOPPED)
        self.assertEqual(self.events[-1][1], "warning")
        self.c.add_target(Target(name="a", interval=60))
        self.events.clear()
        self.c.start()
        self.c.pause_resume()
        self.c.toggle_start_pause()
        self.c.stop()
        states = [e[1] for e in self.events if e[0] == "state_changed"]
        self.assertEqual(states, ["running", "paused", "running", "stopped"])
        toasts = [e[2] for e in self.events if e[0] == "notify"]
        self.assertEqual(toasts, ["Started", "Paused", "Resumed", "Stopped"])

    def test_emergency_stop_disables_everything(self):
        for n in "ab":
            self.c.add_target(Target(name=n, interval=60))
        self.c.start()
        self.c.emergency_stop()
        self.assertEqual(self.c.state, State.STOPPED)
        self.assertFalse(any(t.enabled for t in self.c.targets))

    def test_profiles(self):
        self.assertIsNone(self.c.new_profile("Game"))
        self.assertEqual(self.c.cfg.active_profile, "Game")
        self.assertIn("already exists", self.c.new_profile("Game"))
        self.assertIsNone(self.c.rename_profile("Game", "Work"))
        self.assertEqual(self.c.profiles(), ["Default", "Work"])
        self.assertEqual(self.c.cfg.active_profile, "Work")
        self.assertIsNone(self.c.delete_profile("Work"))  # deleting the active one switches away first
        self.assertEqual(self.c.cfg.active_profile, "Default")
        self.assertIn("only profile", self.c.delete_profile("Default"))

    def test_hotkeys_validate_reject_duplicates_and_fire(self):
        self.assertIn("already used", self.c.set_hotkey("pick", "F6"))
        self.assertIsNotNone(self.c.set_hotkey("pick", "Ctrl+"))
        self.c.start_services()
        self.assertIsNone(self.c.set_hotkey("stop", "Ctrl+Alt+S"))
        self.assertFalse(self.hotkeys.trigger("F7"), "old combo must be unregistered")
        self.c.add_target(Target(name="a", interval=60))
        self.hotkeys.trigger("F6")  # start/pause, delivered through a queued signal
        pump()
        self.assertEqual(self.c.state, State.RUNNING)
        self.hotkeys.trigger("Ctrl+Alt+S")
        pump()
        self.assertEqual(self.c.state, State.STOPPED)

    def test_pick_uses_cursor_or_asks_for_overlay(self):
        self.fake.set_cursor_pos(300, 400)
        self.c.pick_at_cursor()
        self.assertEqual((self.c.targets[0].x, self.c.targets[0].y), (300, 400))
        self.c.caps = {**self.c.caps, "can_read_cursor": False}  # e.g. Wayland
        self.c.pick_at_cursor()
        self.assertEqual(len(self.c.targets), 1)
        self.assertIn("pick_requested", self.names())

    def test_new_target_mode_follows_setting_and_platform(self):
        self.c.set_setting("default_mode", "foreground")
        self.assertEqual(self.c.new_target(Target()).mode, "foreground")
        self.c.set_setting("default_mode", "background")
        self.assertEqual(self.c.new_target(Target()).mode, "background")
        self.fake.set_supports_background_mode(False)
        self.assertEqual(self.c.new_target(Target()).mode, "foreground")
        self.assertEqual(self.c.new_target(Target()).name, "Target 1")

    def test_no_effect_suggests_once_and_use_foreground(self):
        t = Target(name="a", mode="background")
        self.c.add_target(t)
        self.c._on_no_effect(t.id)
        self.c._on_no_effect(t.id)
        self.assertEqual(self.names().count("suggest_foreground"), 1)
        self.assertEqual(self.c.status(t)[0], "No visible effect: try foreground")
        self.c.use_foreground(t.id)
        self.assertEqual(self.c.find(t.id).mode, "foreground")
        self.assertEqual(self.c.status(self.c.find(t.id))[0], "Ready")

    @unittest.skipUnless(sys.platform.startswith("linux"), "XDG autostart")
    def test_launch_at_login_writes_and_removes_autostart_entry(self):
        with patch.dict(os.environ, {"XDG_CONFIG_HOME": self.tmp.name}):
            entry = Path(self.tmp.name) / "autostart" / "pointclicker.desktop"
            set_launch_at_login(True)
            self.assertIn("Exec=", entry.read_text())
            self.assertIn("-m pointclicker", entry.read_text())
            set_launch_at_login(False)
            self.assertFalse(entry.exists())


if __name__ == "__main__":
    unittest.main()
