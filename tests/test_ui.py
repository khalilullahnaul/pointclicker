"""Fluent UI smoke tests (offscreen on Linux, see conftest.py): pages build, small-window fit, dialog/field sync, hotkey recorder."""
import tempfile
import unittest
from dataclasses import fields
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication([])

from pointclicker import targets, ui  # noqa: E402,F401  (ui: quiet qfluentwidgets import first)
from pointclicker.controller import AppController  # noqa: E402
from pointclicker.platform import FakeBackend, FakeHotkeyBackend, set_backend, set_hotkey_backend  # noqa: E402
from pointclicker.targets import Target  # noqa: E402
from pointclicker.ui.targets import TargetDialog  # noqa: E402
from pointclicker.ui.widgets import HotkeyRecorder, SpinBox  # noqa: E402
from pointclicker.ui.window import MainWindow  # noqa: E402


def pump(n=10):
    for _ in range(n):
        app.processEvents()


class UITest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        p = patch("pointclicker.targets.config_path", lambda: Path(self.tmp.name) / "config.json")
        p.start()
        self.addCleanup(p.stop)
        self.fake = FakeBackend()
        set_backend(self.fake)
        set_hotkey_backend(FakeHotkeyBackend())
        cfg = targets.Config(tip_shown=True, setup_guide_shown=True)
        cfg.targets.extend([Target(name=f"Target {i} with a rather long descriptive name", x=10 * i, y=20 * i)
                            for i in range(1, 4)])
        self.c = AppController(cfg)
        self.win = MainWindow(self.c)
        self.win.resize(800, 550)
        self.win.show()
        pump()

    def tearDown(self):
        self.win.quit_app()
        set_backend(None)
        set_hotkey_backend(None)
        self.tmp.cleanup()

    def test_cards_and_empty_state(self):
        page = self.win.targets_page
        self.assertEqual(len(page.cards), 3)
        self.assertFalse(page.empty.isVisible())
        self.c.new_profile("Empty")
        pump()
        self.assertEqual(len(page.cards), 0)
        self.assertTrue(page.empty.isVisible())
        self.assertTrue(page.empty_pick_btn.isVisible())

    def test_small_window_has_no_horizontal_scrolling(self):
        page = self.win.targets_page
        self.assertEqual(page.scroll.horizontalScrollBar().maximum(), 0)
        for card in page.cards:
            self.assertLessEqual(card.width(), page.scroll.viewport().width())
        self.assertLessEqual(self.win.minimumWidth(), 800)
        self.assertLessEqual(self.win.minimumHeight(), 550)

    def test_card_switch_talks_to_controller(self):
        card = self.win.targets_page.cards[0]
        card.switch.setChecked(False)
        pump()
        self.assertFalse(self.c.targets[0].enabled)

    def test_dialog_covers_every_target_field_and_round_trips(self):
        t = Target(name="x", x=5, y=6, interval=2.5, interval_unit="minutes", repeat=3, jitter_pct=10.0,
                   mode="foreground", click_type="double", button="right", window_class="C", window_title="T")
        dlg = TargetDialog(t, self.c, self.win, "Edit")
        self.assertEqual(set(dlg.values()) | {"id"}, {f.name for f in fields(Target)})
        self.assertTrue(dlg.validate())
        self.assertEqual(dlg.result_target, t)
        dlg.close()

    def test_background_segment_disabled_when_unsupported(self):
        self.fake.set_supports_background_mode(False)
        dlg = TargetDialog(Target(mode="background"), self.c, self.win, "Edit")
        self.assertFalse(dlg.mode.items["background"].isEnabled())
        self.assertEqual(dlg.mode.currentRouteKey(), "foreground")
        dlg.close()

    def test_spinbox_text_is_visible(self):  # guards the Qt 6.12 1-px edit field regression workaround
        s = SpinBox()
        s.setValue(42)
        s.resize(120, 33)
        s.show()
        pump()
        self.assertGreater(s.lineEdit().width(), 20)

    def test_hotkey_recorder(self):
        rec = HotkeyRecorder("F6")
        got, recording = [], []
        rec.recorded.connect(got.append)
        rec.recording.connect(recording.append)
        rec.show()
        rec.click()
        QTest.keyClick(rec, Qt.Key_F6, Qt.ControlModifier | Qt.AltModifier)
        rec.click()
        QTest.keyClick(rec, Qt.Key_Escape)
        rec.click()
        QTest.keyClick(rec, Qt.Key_Backspace)
        self.assertEqual(got, ["Ctrl+Alt+F6", ""])  # Esc cancelled, Backspace cleared
        self.assertEqual(recording, [True, False] * 3)

    def test_toasts_go_to_tray_when_hidden(self):
        with patch.object(self.win.tray, "showMessage") as tray_msg:
            self.win.hide()
            self.c.notify.emit("warning", "Target window lost", "x")
            tray_msg.assert_called_once()


if __name__ == "__main__":
    unittest.main()
