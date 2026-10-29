"""Every page, dialog and button of the Fluent UI, driven with pytest-qt (offscreen)."""
import os
import subprocess
import sys
from pathlib import Path

import pytest
from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtGui import QColor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QAbstractButton, QAbstractScrollArea, QWidget
from qfluentwidgets import InfoBar, MessageBox, MessageBoxBase, isDarkTheme, themeColor
from qfluentwidgets.components.dialog_box.mask_dialog_base import MaskDialogBase

from pointclicker import ui  # noqa: F401  (quiet qfluentwidgets import first)
from pointclicker.controller import AppController
from pointclicker.platform import FakeBackend, FakeHotkeyBackend, set_backend, set_hotkey_backend
from pointclicker.scheduler import State
from pointclicker.targets import Config, Target

pytestmark = pytest.mark.gui
HERE = Path(__file__).parent


@pytest.fixture
def answers(monkeypatch):
    """Modal dialogs answer themselves: MessageBoxBase subclasses accept if validate() passes, MessageBox says yes."""
    class Answers(list):
        yes = True  # what MessageBox questions answer; tests flip it to test "Cancel"

    shown = Answers()

    def exec_(self):
        shown.append(self)
        if isinstance(self, MessageBoxBase):
            return 1 if self.validate() else 0
        return 1 if shown.yes else 0
    monkeypatch.setattr(MaskDialogBase, "exec", exec_)
    return shown


@pytest.fixture
def app_window(qtbot, answers):
    fake, hk = FakeBackend(), FakeHotkeyBackend()
    set_backend(fake)
    set_hotkey_backend(hk)
    cfg = Config(tip_shown=True, setup_guide_shown=True)
    cfg.targets.extend([Target(name=f"Target {i}", x=10 * i, y=10 * i, interval=0.2 + i, mode="foreground")
                        for i in range(1, 4)])
    from pointclicker.ui.window import MainWindow
    c = AppController(cfg)
    win = MainWindow(c)
    qtbot.addWidget(win)
    win.resize(1100, 700)
    win.show()
    qtbot.waitExposed(win)
    c.start_services()
    yield win, c, fake, hk
    win.quit_app()


def toasts(win):
    return [b for b in win.findChildren(InfoBar) if b.isVisible()]


def visible_buttons(widget):
    return [b for b in widget.findChildren(QAbstractButton) if b.isVisible() and b.width() > 0]


def in_scroll_area(w):
    while (w := w.parentWidget()) is not None:
        if isinstance(w, QAbstractScrollArea):
            return True
    return False


def assert_fits(win, page):
    """Nothing visible sticks out of the window sideways (scrolled-down content is fine, it's reachable),
    nothing outside a scroll area sticks out at all, and no scroll area needs horizontal scrolling."""
    for b in visible_buttons(page):
        r = QRect(b.mapTo(win, QPoint(0, 0)), b.size())
        assert r.left() >= -1 and r.right() <= win.width(), f"{type(b).__name__} {b.text()!r} at {r} sticks out"
        if not in_scroll_area(b):
            assert r.top() >= -1 and r.bottom() <= win.height(), f"{type(b).__name__} {b.text()!r} at {r} cut off"
    for area in [page, *page.findChildren(QAbstractScrollArea)]:
        if isinstance(area, QAbstractScrollArea) and area.isVisible():
            cols = ([area.columnWidth(i) for i in range(area.columnCount())] if hasattr(area, "columnWidth") else "")
            assert area.horizontalScrollBar().maximum() == 0, \
                f"horizontal scrolling in {type(area).__name__} (viewport {area.viewport().width()}) {cols}"


# --- navigation and layout ---

def test_every_page_reachable_from_navigation(app_window, qtbot):
    win, *_ = app_window
    for page in (win.targets_page, win.run_page, win.profiles_page, win.settings_page, win.about_page):
        item = win.navigationInterface.widget(page.objectName())
        qtbot.mouseClick(item.itemWidget, Qt.LeftButton)  # what a real click hits: the item, not its container
        qtbot.waitUntil(lambda p=page: win.stackedWidget.currentWidget() is p)


@pytest.mark.parametrize("size", [(800, 550), (640, 480)])
def test_small_windows_fit(app_window, qtbot, size):
    win, *_ = app_window
    win.resize(*size)
    for page in (win.targets_page, win.run_page, win.profiles_page, win.settings_page, win.about_page):
        win.switchTo(page)
        qtbot.wait(200)
        assert_fits(win, page)


@pytest.mark.parametrize("scale", ["1.0", "1.5", "2.0"])
def test_scaling_at_small_size(scale):
    env = {**os.environ, "QT_SCALE_FACTOR": scale, "QT_QPA_PLATFORM": "offscreen", "PYTHONPATH": str(HERE)}
    out = subprocess.run([sys.executable, str(HERE / "gui_scale_probe.py")], env=env, capture_output=True,
                         text=True, timeout=120)
    assert out.returncode == 0 and "FITS" in out.stdout, out.stdout[-3000:] + out.stderr[-3000:]


# --- targets page ---

def test_cards_buttons_switch_and_click_to_edit(app_window, qtbot, answers):
    win, c, fake, _ = app_window
    page = win.targets_page
    assert len(page.cards) == 3
    card = page.cards[0]
    tid = card.t.id
    card.switch.setChecked(False)
    assert c.find(tid).enabled is False
    qtbot.mouseClick(card, Qt.LeftButton)  # click a card = edit
    assert isinstance(answers[-1], MessageBoxBase) and answers[-1].t.id == tid
    assert card.actions is None  # hover buttons are built lazily
    card.enterEvent(None)
    assert card.actions.isVisible()
    card.leaveEvent(None)
    assert not card.actions.isVisible()
    card.enterEvent(None)
    edit, test, dup, delete = [b for b in card.actions.findChildren(QAbstractButton)]
    qtbot.mouseClick(edit, Qt.LeftButton)
    assert answers[-1].t.id == tid
    qtbot.mouseClick(test, Qt.LeftButton)
    qtbot.waitUntil(lambda: any(t.windowTitle() or True for t in toasts(win)))
    qtbot.mouseClick(dup, Qt.LeftButton)
    assert len(c.targets) == 4 and c.targets[-1].name.endswith("copy")
    qtbot.waitUntil(lambda: len(page.cards) == 4)
    assert page.cards[0] is card  # updated in place, not rebuilt
    qtbot.mouseClick(delete, Qt.LeftButton)
    assert c.find(tid) is None and len(page.cards) == 3


def test_card_context_menu_offers_the_same_actions(app_window, monkeypatch):
    win, c, *_ = app_window
    from qfluentwidgets import RoundMenu
    menus = []
    monkeypatch.setattr(RoundMenu, "exec", lambda self, *a, **k: menus.append(self))
    card = win.targets_page.cards[1]
    from PySide6.QtGui import QContextMenuEvent
    card.contextMenuEvent(QContextMenuEvent(QContextMenuEvent.Mouse, QPoint(5, 5), card.mapToGlobal(QPoint(5, 5))))
    texts = [a.text() for a in menus[0].actions()]
    assert texts == ["Edit", "Test click now", "Duplicate", "Delete"]
    menus[0].actions()[2].trigger()
    assert len(c.targets) == 4


def test_edit_dialog_saves_changes_and_shows_errors(app_window, qtbot):
    win, c, *_ = app_window
    from pointclicker.ui.targets import TargetDialog
    t = c.targets[0]
    dlg = TargetDialog(t, c, win, "Edit")
    dlg.name.setText("Renamed")
    dlg.interval.setValue(2)
    dlg.unit.setCurrentIndex(2)  # hours
    dlg.double.setChecked(True)
    dlg.mode.setCurrentItem("background")
    assert dlg.validate()
    c.update_target(dlg.result_target)
    saved = c.find(t.id)
    assert (saved.name, saved.interval_seconds, saved.click_type, saved.mode) == ("Renamed", 7200, "double", "background")
    dlg = TargetDialog(saved, c, win, "Edit")
    dlg.interval.setValue(999_999)
    dlg.unit.setCurrentIndex(2)  # 999,999 hours > 30 days: rejected in the dialog, not by a crash later
    assert not dlg.validate() and dlg.error.isVisibleTo(dlg) and "30 days" in dlg.error.text()


def test_pick_flow_overlay_to_dialog_to_new_target(app_window, qtbot, answers):
    win, c, fake, _ = app_window
    fake.set_cursor_pos(321, 123)
    qtbot.mouseClick(win.targets_page.add_btn, Qt.LeftButton)
    qtbot.waitUntil(lambda: win.overlay.active, timeout=3000)
    assert not win.isVisible()  # our window steps aside while picking
    layer = win.overlay._layers[0]
    QTest.mouseClick(layer, Qt.LeftButton, pos=QPoint(321, 123))
    qtbot.waitUntil(lambda: len(c.targets) == 4)
    assert (c.targets[-1].x, c.targets[-1].y) == (321, 123) and win.isVisible()


def test_overlay_cancel_with_escape_and_right_click(app_window, qtbot):
    win, c, *_ = app_window
    for cancel in ("esc", "right"):
        win.start_pick()
        qtbot.waitUntil(lambda: win.overlay.active, timeout=3000)
        layer = win.overlay._layers[0]
        if cancel == "esc":
            QTest.keyClick(layer, Qt.Key_Escape)
        else:
            QTest.mouseClick(layer, Qt.RightButton, pos=QPoint(10, 10))
        qtbot.waitUntil(lambda: not win.overlay.active and win.isVisible())
    assert len(c.targets) == 3


def test_empty_state(app_window, qtbot):
    win, c, *_ = app_window
    assert c.new_profile("Empty") is None
    page = win.targets_page
    qtbot.waitUntil(page.empty.isVisible)
    assert page.empty_pick_btn.isVisible() and not page.scroll.isVisible()


# --- command bar and run page ---

def test_command_bar_buttons_drive_the_scheduler(app_window, qtbot):
    win, c, *_ = app_window
    bar = win.targets_page.command_bar
    qtbot.mouseClick(bar.start_btn, Qt.LeftButton)
    assert c.state is State.RUNNING and not bar.start_btn.isEnabled() and bar.stop_btn.isEnabled()
    qtbot.mouseClick(bar.pause_btn, Qt.LeftButton)
    assert c.state is State.PAUSED and "Resume" in bar.pause_btn.toolTip()
    qtbot.mouseClick(bar.pause_btn, Qt.LeftButton)
    assert c.state is State.RUNNING
    qtbot.mouseClick(bar.stop_btn, Qt.LeftButton)
    assert c.state is State.STOPPED
    win.resize(640, 480)
    qtbot.wait(100)
    assert bar.start_btn.text() == "" and "F6" in bar.start_btn.toolTip()  # compact: icons, hints in tooltips


def test_run_page_table_and_emergency_stop(app_window, qtbot):
    win, c, *_ = app_window
    win.switchTo(win.run_page)
    assert win.run_page.table.rowCount() == 3
    c.start()
    qtbot.wait(700)
    win.run_page.tick()
    assert win.run_page.table.item(0, 4).text() != "0"
    qtbot.mouseClick(win.run_page.emergency, Qt.LeftButton)
    assert c.state is State.STOPPED and not any(t.enabled for t in c.targets)


# --- profiles ---

def test_profiles_new_rename_switch_delete(app_window, qtbot, answers, monkeypatch):
    win, c, *_ = app_window
    from pointclicker.ui.pages import NameDialog
    names = iter(["Game", "Work"])
    monkeypatch.setattr(NameDialog, "validate", lambda self: self.edit.setText(next(names)) or True)
    page = win.profiles_page
    win.switchTo(page)
    qtbot.mouseClick(page.new_btn, Qt.LeftButton)
    assert c.cfg.active_profile == "Game"
    page.rename("Game")
    assert c.profiles() == ["Default", "Work"] and c.cfg.active_profile == "Work"
    qtbot.waitUntil(lambda: any(b.text() == "Switch to" for b in visible_buttons(page)))  # new cards show next turn
    switch_btn = next(b for b in visible_buttons(page) if b.text() == "Switch to")
    qtbot.mouseClick(switch_btn, Qt.LeftButton)
    assert c.cfg.active_profile == "Default"
    answers.yes = False
    page.delete("Work")
    assert "Work" in c.profiles()  # cancelled
    answers.yes = True
    page.delete("Work")
    assert c.profiles() == ["Default"]
    page.delete("Default")
    assert c.profiles() == ["Default"] and any("only profile" in t.contentLabel.text() for t in toasts(win))


# --- settings ---

def test_theme_and_accent(app_window, qtbot):
    win, c, *_ = app_window
    s = win.settings_page
    s.theme.setCurrentIndex(2)
    assert isDarkTheme() and c.cfg.theme == "dark"
    s.theme.setCurrentIndex(1)
    assert not isDarkTheme() and c.cfg.theme == "light"
    s.accent.colorChanged.emit(QColor("#e81123"))
    assert themeColor().name() == "#e81123" and c.cfg.accent == "#e81123"
    s._reset_accent()
    from pointclicker.ui.style import system_accent
    assert c.cfg.accent == "" and themeColor().name() == system_accent().name()  # Windows: the user's accent


def test_hotkey_recording_rebinds_and_suspends_hotkeys(app_window, qtbot):
    win, c, _, hk = app_window
    rec = win.settings_page.recorders["pick"]
    win.switchTo(win.settings_page)
    qtbot.mouseClick(rec, Qt.LeftButton)
    assert not hk.started  # recording: the old hotkeys must not fire
    QTest.keyClick(rec, Qt.Key_F2, Qt.ControlModifier | Qt.ShiftModifier)
    assert hk.started and c.cfg.hotkeys["pick"] == "Ctrl+Shift+F2"
    assert hk.trigger("Ctrl+Shift+F2") and not hk.trigger("F10")
    qtbot.mouseClick(rec, Qt.LeftButton)
    QTest.keyClick(rec, Qt.Key_F6)  # already Start/Pause: refused with a toast
    assert c.cfg.hotkeys["pick"] == "Ctrl+Shift+F2"
    assert any("already used" in t.contentLabel.text() for t in toasts(win))
    qtbot.mouseClick(rec, Qt.LeftButton)
    QTest.keyClick(rec, Qt.Key_Backspace)
    assert c.cfg.hotkeys["pick"] == "" and rec.text() == "Not set"


def test_behavior_switches_persist(app_window, qtbot, monkeypatch):
    win, c, *_ = app_window
    calls = []
    monkeypatch.setattr("pointclicker.controller.set_launch_at_login", calls.append)
    from qfluentwidgets import SwitchButton
    switches = [w for w in win.settings_page.findChildren(SwitchButton)]
    keys = ["minimize_to_tray", "start_minimized", "start_clicking_on_launch", "launch_at_login", "show_markers"]
    assert len(switches) == len(keys)
    for sw, key in zip(switches, keys, strict=True):
        before = getattr(c.cfg, key)
        sw.setChecked(not before)
        assert getattr(c.cfg, key) is (not before), key
    assert calls == [True]
    from pointclicker import targets
    assert targets.load().start_minimized is True
    win.settings_page.default_mode.setCurrentIndex(1)
    assert c.cfg.default_mode == "foreground"


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux section")
def test_linux_permissions_card(app_window, qtbot, answers):
    win, *_ = app_window
    s = win.settings_page
    buttons = {b.text(): b for b in s.perm_card.findChildren(QAbstractButton)}
    qtbot.mouseClick(buttons["Re-check"], Qt.LeftButton)
    assert "uinput" in s.perm_card.contentLabel.text()
    qtbot.mouseClick(buttons["Setup guide"], Qt.LeftButton)
    assert isinstance(answers[-1], MessageBox) and "usermod" in answers[-1].contentLabel.text()


# --- about, toasts, tray, markers, window ---

def test_about_links(app_window, qtbot, monkeypatch):
    win, *_ = app_window
    opened = []
    monkeypatch.setattr("pointclicker.ui.pages.QDesktopServices.openUrl", lambda url: opened.append(url.toLocalFile()))
    for b in visible_buttons(win.about_page) or win.about_page.findChildren(QAbstractButton):
        if b.text().startswith("Open"):
            b.click()
    assert len(opened) == 2 and any(p.endswith("logs") for p in opened)


def test_toasts_and_foreground_suggestion(app_window, qtbot, answers):
    win, c, *_ = app_window
    c.notify.emit("error", "Permission missing", "test")
    assert any(t.titleLabel.text() == "Permission missing" for t in toasts(win))
    t = c.targets[0]
    c.update_target(c.new_target(t).__class__(**{**t.__dict__, "mode": "background"}))
    c._on_no_effect(t.id)
    qtbot.waitUntil(lambda: c.find(t.id).mode == "foreground")
    assert isinstance(answers[-1], MessageBox)


def test_many_suggestions_become_one_dialog(app_window, qtbot, answers):
    win, c, *_ = app_window
    for t in list(c.targets):
        c.update_target(c.new_target(t).__class__(**{**t.__dict__, "mode": "background"}))
    n = len(answers)
    for t in c.targets:
        c._on_no_effect(t.id)
    qtbot.waitUntil(lambda: all(t.mode == "foreground" for t in c.targets))
    boxes = [a for a in answers[n:] if isinstance(a, MessageBox)]
    assert len(boxes) == 1 and "them" in boxes[0].contentLabel.text()


def test_tray_menu_and_minimize_to_tray(app_window, qtbot, monkeypatch):
    win, c, *_ = app_window
    win.showMinimized()  # no system tray (offscreen): hiding would strand the window, so it must stay
    qtbot.wait(200)
    assert win.isVisible()
    win.showNormal()
    monkeypatch.setattr("pointclicker.ui.window.QSystemTrayIcon.isSystemTrayAvailable", staticmethod(lambda: True))
    actions = {a.text(): a for a in win.tray_menu.actions() if a.text()}
    actions["Start"].trigger()
    assert c.state is State.RUNNING
    win._update_tray()
    assert win.tray_toggle.text() == "Pause"
    actions["Stop"].trigger()
    assert c.state is State.STOPPED
    win.showMinimized()
    qtbot.waitUntil(lambda: not win.isVisible())
    actions["Show PointClicker"].trigger()
    assert win.isVisible()


def test_markers_follow_setting(app_window, qtbot):
    win, c, *_ = app_window
    assert len(win.markers._markers) == 3
    c.set_setting("show_markers", False)
    assert win.markers._markers == []
    c.set_setting("show_markers", True)
    assert len(win.markers._markers) == 3


def test_first_launch_tip_and_guide(qtbot, answers):
    from qfluentwidgets import TeachingTip
    set_backend(FakeBackend())
    set_hotkey_backend(FakeHotkeyBackend())
    from pointclicker.ui.window import MainWindow
    c = AppController(Config())
    win = MainWindow(c)
    qtbot.addWidget(win)
    win.show()
    qtbot.waitUntil(lambda: c.cfg.tip_shown, timeout=3000)
    assert win.findChildren(TeachingTip)
    win.quit_app()


@pytest.mark.chaos
def test_500_targets_stay_responsive(qtbot, answers):
    """Budgets for a 500-target profile (this used to take 33 s per toggle and minutes for emergency stop)."""
    import time
    from dataclasses import replace
    set_backend(FakeBackend())
    set_hotkey_backend(FakeHotkeyBackend())
    cfg = Config(tip_shown=True, setup_guide_shown=True)
    cfg.targets.extend(Target(name=f"t{i}", x=i, y=i) for i in range(500))
    from pointclicker.ui.window import MainWindow

    def timed(fn):
        t0 = time.perf_counter()
        fn()
        qtbot.wait(1)
        return time.perf_counter() - t0
    c = AppController(cfg)
    win = None

    def build():
        nonlocal win
        win = MainWindow(c)
        qtbot.addWidget(win)
        win.resize(1100, 700)
        win.show()
    assert timed(build) < 5
    assert timed(lambda: c.set_enabled(cfg.targets[3].id, False)) < 0.2
    assert timed(lambda: c.update_target(replace(cfg.targets[4], name="edited"))) < 0.2
    assert timed(win.targets_page.tick) < 0.1
    assert timed(lambda: [c._after_click(cfg.targets[i % 500].id) for i in range(5000)]) < 1.0
    win.switchTo(win.run_page)
    assert timed(win.run_page.tick) < 0.1
    assert timed(c.emergency_stop) < 1.0
    win.switchTo(win.targets_page)
    bar = win.targets_page.scroll.verticalScrollBar()
    assert timed(lambda: bar.setValue(bar.maximum())) < 2.0 and len(win.targets_page.cards) == 60
    assert len(win.run_page.table.model().index(499, 0).data() or "") > 0  # the schedule lists every target
    win.quit_app()


def test_live_refresh_never_paints_synchronously(app_window, qtbot):
    """tick() runs 4x a second on the GUI thread while the scheduler thread needs the GIL to click on time. A
    synchronous repaint (QProgressBar.setValue does one) holds the GIL for milliseconds per card: soak clicks
    landed up to 70 ms late. Every paint must be deferred to the event loop."""
    win, c, *_ = app_window
    c.start()
    qtbot.wait(300)
    painted = []
    for card in win.targets_page.cards:
        for w in [card, *card.findChildren(QWidget)]:
            w.paintEvent = lambda e, w=w, orig=w.paintEvent: (painted.append(type(w).__name__), orig(e))[1]
    for card in win.targets_page.cards:
        card._live = (None,) * 5  # force every live field to change
    win.targets_page.tick()
    assert not painted, f"synchronous repaint during tick: {painted}"
    qtbot.waitUntil(lambda: "ProgressRing" in painted, timeout=2000)  # ...but the rings do repaint afterwards


# --- pick overlay: frozen screenshot + magnifier ---

def _desktop_image(w, h, dots):
    from PySide6.QtGui import QColor, QImage
    img = QImage(w, h, QImage.Format_RGB32)
    img.fill(QColor("white"))
    for (x, y), color in dots.items():
        img.setPixelColor(x, y, QColor(color))
    return img


def _rgb(img, x, y):
    c = img.pixelColor(x, y)
    return c.red(), c.green(), c.blue()


def _pick_layer(qtbot, monkeypatch, wayland):
    from PySide6.QtGui import QGuiApplication

    from pointclicker import overlay
    set_backend(FakeBackend())
    geo = QGuiApplication.primaryScreen().geometry()
    shot = _desktop_image(geo.width(), geo.height(), {(200, 150): "red", (5, 5): "blue"})
    if wayland:
        monkeypatch.setattr(overlay.QGuiApplication, "platformName", staticmethod(lambda: "wayland"))
        monkeypatch.setattr(overlay, "portal_screenshot", lambda: shot)
    else:
        monkeypatch.setattr(overlay, "grab_screen", lambda s: shot)
    o = overlay.PickOverlay()
    o.start()
    assert o.active
    qtbot.waitUntil(lambda: bool(o._layers), timeout=3000)  # Wayland: after the screenshot thread reports back
    layer = o._layers[0]
    qtbot.waitExposed(layer)
    QTest.mouseMove(layer, QPoint(100, 100))  # a move to where the mouse already is sends no event
    QTest.mouseMove(layer, QPoint(200, 150))
    qtbot.waitUntil(lambda: layer._pos == QPoint(200, 150))
    return o, layer


@pytest.mark.parametrize("wayland", [False, True])
def test_picker_shows_the_real_screen_and_magnifies_the_exact_pixel(qtbot, monkeypatch, wayland):
    """Picking used to dim the whole screen (black on some compositors): the user couldn't see what to pick."""
    from pointclicker.overlay import LOUPE_PX, LOUPE_ZOOM
    o, layer = _pick_layer(qtbot, monkeypatch, wayland)
    img = layer.grab().toImage()
    assert _rgb(img, 5, 5) == (0, 0, 255)  # the screenshot, undimmed
    assert _rgb(img, 600, 500) == (255, 255, 255)
    half = LOUPE_PX // 2
    cx, cy = 220 + half * LOUPE_ZOOM + LOUPE_ZOOM // 2, 170 + half * LOUPE_ZOOM + LOUPE_ZOOM // 2
    assert _rgb(img, cx, cy) == (255, 0, 0), "the boxed loupe pixel is the one under the pointer"
    assert _rgb(img, cx + LOUPE_ZOOM, cy) == (255, 255, 255)  # its neighbour, magnified 8x
    QTest.mouseClick(layer, Qt.RightButton, pos=QPoint(200, 150))
    assert not o.active


def test_picker_falls_back_to_a_dimmed_live_screen_without_a_screenshot(qtbot, monkeypatch):
    from pointclicker import overlay
    set_backend(FakeBackend())
    monkeypatch.setattr(overlay, "grab_screen", lambda s: None)
    o = overlay.PickOverlay()
    o.start()
    assert o._layers and o._layers[0].shot is None
    QTest.keyClick(o._layers[0], Qt.Key_Escape)
    assert not o.active


def test_split_desktop_handles_hidpi_and_side_by_side_screens():
    from types import SimpleNamespace

    from PySide6.QtCore import QRect

    from pointclicker.overlay import split_desktop
    screens = [SimpleNamespace(name=lambda: "A", geometry=lambda: QRect(0, 0, 100, 50)),
               SimpleNamespace(name=lambda: "B", geometry=lambda: QRect(100, 0, 50, 50))]
    shots = split_desktop(_desktop_image(300, 100, {(0, 0): "red", (200, 0): "blue"}), screens)  # 2x pixels
    assert (shots["A"].width(), shots["A"].height(), shots["B"].width(), shots["B"].height()) == (200, 100, 100, 100)
    assert _rgb(shots["A"], 0, 0) == (255, 0, 0) and _rgb(shots["B"], 0, 0) == (0, 0, 255)
    assert split_desktop(None, screens) == {}


def test_portal_screenshot_deletes_the_file_the_portal_saved(tmp_path, monkeypatch):
    pytest.importorskip("jeepney")  # D-Bus: Linux only
    from pointclicker import overlay
    from pointclicker.platform.linux import portal
    saved = tmp_path / "Screenshot from now.png"
    assert _desktop_image(4, 3, {}).save(str(saved))
    asked = []

    class FakePortal:
        def request(self, interface, method, sig, *args, timeout):
            asked.append((interface, method, args[-1]["interactive"]))
            return {"uri": ("s", saved.as_uri())}

        def close(self):
            pass
    monkeypatch.setattr(portal, "Portal", FakePortal)
    img = overlay.portal_screenshot()
    assert (img.width(), img.height()) == (4, 3) and not saved.exists()
    assert asked == [("org.freedesktop.portal.Screenshot", "Screenshot", ("b", False))]

    class Denied(FakePortal):
        def request(self, *a, **k):
            raise portal.PortalError("Screenshot: denied by the user (code 1)")
    monkeypatch.setattr(portal, "Portal", Denied)
    assert overlay.portal_screenshot() is None  # falls back to the live screen
