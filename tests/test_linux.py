"""Linux backend logic that runs without a desktop: parsing, hotkey matching, portal/uinput mapping."""
import os
import sys
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

if not sys.platform.startswith("linux"):
    raise unittest.SkipTest("Linux backends")

from evdev import ecodes  # noqa: E402

from pointclicker.clicker import Mode  # noqa: E402
from pointclicker.platform import detect_session_type  # noqa: E402
from pointclicker.platform.base import Rect  # noqa: E402
from pointclicker.platform.linux import hotkeys as hk  # noqa: E402
from pointclicker.platform.linux.wayland import LinuxWaylandInputBackend  # noqa: E402
from pointclicker.platform.linux.x11 import LinuxX11InputBackend  # noqa: E402


class ComboTest(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(hk.parse_combo("Ctrl+Alt+F6"), (frozenset({"ctrl", "alt"}), "f6"))
        self.assertEqual(hk.parse_combo(" win + Esc "), (frozenset({"super"}), "escape"))
        for bad in ("", "Ctrl+", "Hyper+X", "Shift"):
            with self.assertRaises(ValueError):
                hk.parse_combo(bad)

    def test_names_per_backend(self):
        self.assertEqual(hk.keysym_name("f10"), "F10")
        self.assertEqual(hk.keysym_name("page_up"), "Prior")
        self.assertEqual(hk.PortalHotkeys.trigger("shift+ctrl+s"), "CTRL+SHIFT+s")
        self.assertEqual(hk.PortalHotkeys.trigger("Super+F6"), "LOGO+F6")
        self.assertEqual(hk.evdev_code("f6"), ecodes.KEY_F6)
        self.assertEqual(hk.evdev_code("escape"), ecodes.KEY_ESC)
        with self.assertRaises(ValueError):
            hk.evdev_code("nosuchkey")


class FakeKeyboard:
    """Quacks like evdev.InputDevice; a real pipe makes select() wake up for each batch."""

    def __init__(self):
        self.fd, self._w = os.pipe()
        self._batches = []

    def send(self, *events):
        self._batches.append([SimpleNamespace(type=ecodes.EV_KEY, code=c, value=v) for c, v in events])
        os.write(self._w, b"x")

    def read(self):
        os.read(self.fd, 1)
        return self._batches.pop(0)

    def close(self):
        os.close(self.fd)
        os.close(self._w)


class EvdevHotkeysTest(unittest.TestCase):
    def test_matches_modifiers_ignores_repeat_and_plain_keys(self):
        kbd = FakeKeyboard()
        fired = []
        h = hk.EvdevHotkeys()
        h.register("F10", lambda: fired.append("F10"))
        h.register("Ctrl+Alt+S", lambda: fired.append("Ctrl+Alt+S"))
        with patch.object(hk.EvdevHotkeys, "keyboards", staticmethod(lambda: [kbd])):
            h.start()
        kbd.send((ecodes.KEY_F10, 1), (ecodes.KEY_F10, 2), (ecodes.KEY_F10, 2), (ecodes.KEY_F10, 0))  # + auto-repeat
        kbd.send((ecodes.KEY_S, 1), (ecodes.KEY_S, 0))  # plain s: no match
        kbd.send((ecodes.KEY_LEFTCTRL, 1), (ecodes.KEY_RIGHTALT, 1), (ecodes.KEY_S, 1), (ecodes.KEY_S, 0),
                 (ecodes.KEY_RIGHTALT, 0), (ecodes.KEY_LEFTCTRL, 0))
        kbd.send((ecodes.KEY_LEFTCTRL, 1), (ecodes.KEY_F10, 1))  # Ctrl+F10 is not F10
        time.sleep(0.2)
        h.stop()
        self.assertEqual(fired, ["F10", "Ctrl+Alt+S"])


class WaylandMappingTest(unittest.TestCase):
    def setUp(self):
        with patch.dict(os.environ, {"DISPLAY": ""}):
            self.b = LinuxWaylandInputBackend()
        self.calls = []
        self.b._portal = SimpleNamespace(call=lambda *a: self.calls.append(a[1:]))
        self.b._session, self.b.method = "/session/1", "portal"
        # two monitors side by side, the right one is the second stream
        self.b._streams = [(41, Rect(0, 0, 1920, 1080)), (42, Rect(1920, 0, 4480, 1440))]

    def test_point_maps_to_the_stream_it_is_on(self):
        res = self.b.click((2000, 100), mode=Mode.FOREGROUND, button="right", double=True)
        self.assertTrue(res.ok, res.error)
        motion, *buttons = self.calls
        self.assertEqual(motion, ("NotifyPointerMotionAbsolute", "oa{sv}udd", "/session/1", {}, 42, 80.0, 100.0))
        self.assertEqual([c[-2:] for c in buttons], [(0x111, 1), (0x111, 0)] * 2)

    def test_off_monitor_and_background_are_errors(self):
        self.assertIn("not on a shared monitor", self.b.click((5000, 10), mode=Mode.FOREGROUND).error)
        res = self.b.click((10, 10), mode=Mode.BACKGROUND)
        self.assertFalse(res.ok)
        self.assertIn("isn't available on Wayland", res.error)
        self.assertEqual(self.calls, [])

    def test_uinput_scales_to_desktop_box(self):
        written = []
        self.b.method, self.b._box = "uinput", Rect(0, 0, 4480, 1440)
        self.b._uinput = SimpleNamespace(write=lambda *a: written.append(a), syn=lambda: written.append("syn"))
        self.assertTrue(self.b.click((2000, 100), mode=Mode.FOREGROUND).ok)
        self.assertEqual(written[:3], [(ecodes.EV_ABS, ecodes.ABS_X, 2000), (ecodes.EV_ABS, ecodes.ABS_Y, 100), "syn"])
        self.assertEqual([w[2] for w in written if w != "syn" and w[0] == ecodes.EV_KEY], [1, 0])

    def test_failed_access_is_cached_not_reprompted(self):
        b = self.b
        b.method = None
        attempts = []

        def fail():
            attempts.append(1)
            raise RuntimeError("denied by the user")
        with patch.object(b, "_start_portal", fail), \
                patch("pointclicker.platform.linux.setup.permissions", lambda: {"uinput_writable": False}):
            first = b.click((10, 10), mode=Mode.FOREGROUND)
            second = b.click((10, 10), mode=Mode.FOREGROUND)
        self.assertIn("denied by the user", first.error)
        self.assertEqual(second.error, first.error)
        self.assertEqual(len(attempts), 1)  # the permission dialog isn't shown again for every click

    def test_capabilities_hide_background_and_cursor(self):
        with patch.object(hk, "wayland_hotkey_method", lambda: None):
            caps = self.b.capabilities()
        self.assertFalse(caps["background_mode"])
        self.assertFalse(caps["can_read_cursor"])
        self.assertTrue(caps["setup_needed"])


class SharedFlowTest(unittest.TestCase):
    def test_abort_while_user_holds_button(self):
        with patch.dict(os.environ, {"DISPLAY": ""}):
            b = LinuxWaylandInputBackend()
        b.method = "portal"
        b.is_mouse_button_held = lambda: True
        abort = threading.Event()
        threading.Timer(0.05, abort.set).start()
        res = b.click((1, 1), mode=Mode.FOREGROUND, abort=abort, busy_timeout=5)
        self.assertEqual(res.error, "cancelled")


class SessionDetectionTest(unittest.TestCase):
    def test_xdg_session_type_wins_then_wayland_display(self):
        cases = [({"XDG_SESSION_TYPE": "x11", "WAYLAND_DISPLAY": "wayland-0", "DISPLAY": ":0"}, "x11"),
                 ({"XDG_SESSION_TYPE": "wayland", "WAYLAND_DISPLAY": "", "DISPLAY": ":0"}, "wayland"),
                 ({"XDG_SESSION_TYPE": "", "WAYLAND_DISPLAY": "wayland-0", "DISPLAY": ":0"}, "wayland"),
                 ({"XDG_SESSION_TYPE": "tty", "WAYLAND_DISPLAY": "", "DISPLAY": ":0"}, "x11")]
        for env, expected in cases:
            with patch.dict(os.environ, env):
                self.assertEqual(detect_session_type(), expected, env)


class X11WithoutDisplayTest(unittest.TestCase):
    def test_reports_instead_of_crashing(self):
        b = LinuxX11InputBackend(display_name=":987")
        res = b.click((1, 1), mode=Mode.FOREGROUND)
        self.assertFalse(res.ok)
        self.assertIn("cannot open X display", res.error)
        self.assertFalse(b.supports_background_mode())
        self.assertEqual(b.find_window("x"), None)


if __name__ == "__main__":
    unittest.main()
