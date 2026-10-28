"""Tests for platform abstraction layer, backends, factory and fake backend."""
import logging
import sys
import unittest
from unittest.mock import patch

from pointclicker.platform import (
    FakeBackend,
    FakeHotkeyBackend,
    InputBackend,
    HotkeyBackend,
    Rect,
    create_backend,
    create_hotkey_backend,
    detect_session_type,
    get_backend,
    set_backend,
)
from pointclicker.targets import Target
from pointclicker.clicker import Mode, ClickResult


class PlatformBackendTest(unittest.TestCase):
    def setUp(self):
        self.original_backend = get_backend()

    def tearDown(self):
        set_backend(self.original_backend)

    def test_fake_backend_records_clicks_in_memory(self):
        fake = FakeBackend()
        set_backend(fake)

        t1 = Target(name="Target 1", x=100, y=200, mode="foreground", button="left")
        res1 = t1.click()
        self.assertTrue(res1.ok)
        self.assertEqual(res1.mode, Mode.FOREGROUND)
        self.assertEqual(len(fake.clicks), 1)
        self.assertEqual(fake.clicks[0].x, 100)
        self.assertEqual(fake.clicks[0].y, 200)
        self.assertEqual(fake.clicks[0].mode, "foreground")
        self.assertEqual(fake.clicks[0].button, "left")

        t2 = Target(name="Target 2", x=350, y=450, mode="background", button="right", click_type="double")
        res2 = t2.click()
        self.assertTrue(res2.ok)
        self.assertEqual(len(fake.clicks), 2)
        self.assertEqual(fake.clicks[1].x, 350)
        self.assertEqual(fake.clicks[1].y, 450)
        self.assertEqual(fake.clicks[1].mode, "background")
        self.assertEqual(fake.clicks[1].button, "right")
        self.assertEqual(fake.clicks[1].click_type, "double")

    def test_fake_backend_handles_abort(self):
        import threading
        fake = FakeBackend()
        abort = threading.Event()
        abort.set()

        t = Target(name="Aborted", x=50, y=50)
        res = fake.click(t, abort=abort)
        self.assertFalse(res.ok)
        self.assertEqual(res.error, "cancelled")
        self.assertEqual(len(fake.clicks), 0)

    def test_fake_backend_cursor_and_window(self):
        fake = FakeBackend()
        self.assertEqual(fake.get_cursor_pos(), (100, 200))
        fake.set_cursor_pos(500, 600)
        self.assertEqual(fake.get_cursor_pos(), (500, 600))

        fake.windows[999] = {
            "rect": Rect(10, 20, 200, 300),
            "title": "My Test App",
            "class": "MyWindowClass",
        }
        self.assertEqual(fake.window_at(50, 50), 999)
        self.assertEqual(fake.window_rect(999), Rect(10, 20, 200, 300))
        self.assertEqual(fake.window_text(999), "My Test App")
        self.assertEqual(fake.window_class(999), "MyWindowClass")
        self.assertEqual(fake.find_window("MyWindowClass", "My Test App"), 999)

    def test_fake_hotkey_backend(self):
        hk = FakeHotkeyBackend()
        called = []
        hk.register("F6", lambda: called.append("f6"))
        hk.start()
        self.assertTrue(hk.started)

        hk.trigger("F6")
        self.assertEqual(called, ["f6"])

        hk.unregister("F6")
        self.assertFalse(hk.trigger("F6"))
        hk.stop()
        self.assertFalse(hk.started)

    def test_factory_logging_and_selection(self):
        # Test fake selection
        with self.assertLogs("pointclicker.platform", level="INFO") as cm:
            backend = create_backend("fake")
            self.assertIsInstance(backend, FakeBackend)
            self.assertTrue(any("Fake" in msg for msg in cm.output))

        # Test Linux Wayland selection
        with self.assertLogs("pointclicker.platform", level="INFO") as cm:
            backend = create_backend("wayland")
            self.assertTrue(any("Wayland" in msg for msg in cm.output))
            caps = backend.capabilities()
            self.assertFalse(backend.supports_background_mode())
            self.assertIn("unavailable_features", caps)

        # Test Linux X11 selection
        with self.assertLogs("pointclicker.platform", level="INFO") as cm:
            backend = create_backend("x11")
            self.assertTrue(any("X11" in msg for msg in cm.output))
            caps = backend.capabilities()
            # X11 background mode (XSendEvent) exists whenever an X display is reachable, flagged as partial
            self.assertEqual(caps["background_mode"], backend.supports_background_mode())
            self.assertIn("only with some apps", caps["background_note"])

    def test_session_type_detection(self):
        with patch.dict("os.environ", {"XDG_SESSION_TYPE": "wayland", "WAYLAND_DISPLAY": ""}):
            if sys.platform != "win32":
                self.assertEqual(detect_session_type(), "wayland")

        with patch.dict("os.environ", {"XDG_SESSION_TYPE": "x11", "WAYLAND_DISPLAY": ""}):
            if sys.platform != "win32":
                self.assertEqual(detect_session_type(), "x11")

    def test_no_win32_import_at_module_level_on_linux(self):
        if sys.platform != "win32":
            # Ensure importing pointclicker.clicker does not attempt WinDLL
            import pointclicker.clicker as clk
            self.assertFalse(hasattr(clk, "user32"))
            import pointclicker.platform.windows as win_mod
            self.assertIsNone(win_mod.WindowsInputBackend)


if __name__ == "__main__":
    unittest.main()
