"""Real clicks on a real (headless) X server: exact counts and positions, both modes, multi-screen and
multi-monitor layouts, window moves/covers/closes, global hotkeys, and the --diagnose self-test."""
import os
import shutil
import subprocess
import sys
import time

import pytest
from xserver import ClickCounter, start_xinerama, start_xvfb, wait_until, xvfb_binary

from pointclicker.clicker import Mode
from pointclicker.platform import set_backend
from pointclicker.platform.linux.x11 import LinuxX11InputBackend
from pointclicker.scheduler import Scheduler
from pointclicker.targets import Target

pytestmark = [pytest.mark.integration,
              pytest.mark.skipif(not xvfb_binary(), reason="no Xvfb: install xvfb or run `make xvfb-local`")]


@pytest.fixture(scope="module")
def xvfb():
    server = start_xvfb("1600x1000", "800x600")  # screen 0 and a second X screen :N.1
    yield server
    server.stop()


@pytest.fixture
def x11(xvfb):
    b = LinuxX11InputBackend(xvfb.display)
    assert b._error is None and b._xtest and not b.xwayland
    set_backend(b)
    yield b


@pytest.fixture
def counter(xvfb):
    made = []

    def make(geometry="400x300+200+150", title="Counter", cls="ClickCounter", display=None):
        c = ClickCounter(display or xvfb.display, geometry, title, cls)
        made.append(c)
        return c
    yield make
    for c in made:
        c.close()


def park_pointer(b, x=5, y=5):
    b._root.warp_pointer(x, y)
    b._d.sync()


def run(t: Target, clicks: int, timeout=10.0):
    """Run one target through the real threaded scheduler until it has made `clicks` clicks."""
    s = Scheduler(lambda tgt, abort: tgt.click(abort))
    s.start([t])
    assert wait_until(lambda: s.stats[t.id].clicks >= clicks, timeout), s.stats[t.id]
    s.shutdown()
    return s.stats[t.id]


def window_target(c: ClickCounter, rel, mode, **kw):
    return Target(x=0, y=0, rel_x=rel[0], rel_y=rel[1], window_title=c.title, window_class=c.cls, mode=mode, **kw)


@pytest.mark.parametrize("mode,synthetic", [("foreground", False), ("background", True)])
def test_exact_count_and_position(x11, counter, mode, synthetic):
    c = counter(title=f"Counter {mode}")
    park_pointer(x11)
    st = run(window_target(c, (37, 91), mode, interval=0.1, repeat=5), 5)
    presses = c.presses()
    assert (st.clicks, st.failures) == (5, 0)
    assert len(presses) == 5, presses
    assert all((p["x"], p["y"], p["button"], p["synthetic"]) == (37, 91, 1, synthetic) for p in presses)
    assert all((p["rx"], p["ry"]) == (237, 241) for p in presses)
    assert x11.get_cursor_pos() == (5, 5), "pointer not restored / moved in background mode"


@pytest.mark.parametrize("mode", ["foreground", "background"])
@pytest.mark.parametrize("button,number", [("left", 1), ("middle", 2), ("right", 3)])
def test_buttons_and_double_click(x11, counter, mode, button, number):
    c = counter(title=f"Buttons {mode} {button}")
    for click_type, presses_per_click in (("single", 1), ("double", 2)):
        t = window_target(c, (10, 20), mode, button=button, click_type=click_type)
        assert t.click().ok
        got = c.presses()
        assert [p["button"] for p in got] == [number] * presses_per_click


def test_click_follows_a_moving_window(x11, counter):
    c = counter(geometry="300x200+100+100", title="Mover")
    t = window_target(c, (50, 60), "foreground")
    for x, y in ((100, 100), (700, 500), (1250, 30)):
        c.send(f"move {x} {y}")
        assert t.click().ok
        (p,) = c.presses()
        assert (p["x"], p["y"], p["rx"], p["ry"]) == (50, 60, x + 50, y + 60)


def test_background_reaches_a_covered_window_foreground_hits_the_cover(x11, counter):
    target = counter(geometry="400x300+300+300", title="Underneath", cls="Target")
    cover = counter(geometry="400x300+300+300", title="On top", cls="Cover")
    assert x11.top_window_at(400, 400).id != x11.find_window("Target", "Underneath").id
    assert window_target(target, (40, 40), "background").click().ok
    assert len(target.presses()) == 1 and cover.presses() == []
    assert window_target(target, (40, 40), "foreground").click().ok
    assert target.presses() == [] and len(cover.presses()) == 1  # documented: real clicks hit what's on top


@pytest.mark.chaos
@pytest.mark.parametrize("mode", ["foreground", "background"])
def test_target_window_closed_mid_run_never_clicks_what_is_underneath(x11, counter, mode):
    under = counter(geometry="400x300+300+300", title="Innocent bystander", cls="Bystander")
    victim = counter(geometry="400x300+300+300", title="Closes soon", cls="Victim")
    t = window_target(victim, (40, 40), mode, interval=0.05)
    s = Scheduler(lambda tgt, abort: tgt.click(abort))
    s.start([t])
    assert wait_until(lambda: s.stats[t.id].clicks >= 3)
    victim.close()
    clicks_at_close = s.stats[t.id].clicks
    assert wait_until(lambda: s.stats[t.id].clicks >= clicks_at_close + 5)  # keeps trying, keeps failing
    s.shutdown()
    assert under.presses() == [], "clicked the window underneath after the target closed"
    assert s.stats[t.id].last_ok is False and "not found" in s.stats[t.id].last_error
    assert s._thread is not None and not s._thread.is_alive()


def test_second_x_screen(xvfb, counter, x11):
    park_pointer(x11, 33, 44)  # the user's pointer is on screen 0
    b = LinuxX11InputBackend(f"{xvfb.display}.1")
    set_backend(b)
    assert b.monitors()[0].width == 800
    c = counter(geometry="300x200+50+60", title="Screen one", display=f"{xvfb.display}.1")
    assert b.click((150, 160), mode=Mode.FOREGROUND).ok
    (p,) = c.presses()
    assert (p["x"], p["y"], p["synthetic"]) == (100, 100, False)
    ptr = x11._root.query_pointer()
    assert ptr.same_screen and (ptr.root_x, ptr.root_y) == (33, 44), "pointer left stranded on the other screen"


def test_hotkeys_work_while_pointer_is_on_another_screen(xvfb, monkeypatch):
    from Xlib import XK, X, display
    from Xlib.ext import xtest

    from pointclicker.platform.linux.hotkeys import X11Hotkeys
    monkeypatch.setenv("DISPLAY", xvfb.display)
    fired = []
    hk = X11Hotkeys()
    hk.register("F9", lambda: fired.append("F9"))
    hk.start()
    d = display.Display(xvfb.display)
    try:
        d.screen(1).root.warp_pointer(100, 100)
        d.screen(0).root.warp_pointer(10, 10)
        for screen in (1, 0):
            d.screen(screen).root.warp_pointer(100, 100)
            kc = d.keysym_to_keycode(XK.string_to_keysym("F9"))
            xtest.fake_input(d, X.KeyPress, kc)
            xtest.fake_input(d, X.KeyRelease, kc)
            d.sync()
            # two presses of one key inside the same millisecond are, by X's definition, auto-repeat
            time.sleep(0.05)
            want = 1 if screen == 1 else 2
            assert wait_until(lambda want=want: len(fired) == want, 3), (screen, fired)
    finally:
        hk.stop()
        d.close()


@pytest.mark.skipif(not shutil.which("Xephyr"), reason="no Xephyr")
def test_xinerama_side_by_side_monitors_and_dead_zone(xvfb):
    host = start_xvfb("2200x1100")
    nested = start_xinerama(host, "1280x1024", "800x600")
    try:
        b = LinuxX11InputBackend(nested.display)
        set_backend(b)
        mons = sorted((m.left, m.top, m.width, m.height) for m in b.monitors())
        assert mons == [(0, 0, 1280, 1024), (1280, 0, 800, 600)]
        c = ClickCounter(nested.display, "300x200+1400+100", "Right monitor")
        try:
            assert b.click((1500, 250), mode=Mode.FOREGROUND).ok
            (p,) = c.presses()
            assert (p["x"], p["y"]) == (100, 150)
            dead = b.click((1500, 800), mode=Mode.FOREGROUND)  # right of monitor 1, below monitor 2
            assert not dead.ok and "not on any monitor" in dead.error
            assert c.presses() == []
        finally:
            c.close()
    finally:
        nested.stop()
        host.stop()


def test_xgrabkey_hotkeys_with_lock_keys(xvfb, monkeypatch):
    from Xlib import XK, X, display
    from Xlib.ext import xtest

    from pointclicker.platform.linux.hotkeys import X11Hotkeys
    monkeypatch.setenv("DISPLAY", xvfb.display)
    fired = []
    hk = X11Hotkeys()
    hk.register("F6", lambda: fired.append("F6"))
    hk.register("Ctrl+Alt+S", lambda: fired.append("CAS"))
    hk.start()
    d = display.Display(xvfb.display)
    kc = lambda n: d.keysym_to_keycode(XK.string_to_keysym(n))  # noqa: E731

    def tap(*names):
        for n in names:
            xtest.fake_input(d, X.KeyPress, kc(n))
        for n in reversed(names):
            xtest.fake_input(d, X.KeyRelease, kc(n))
        d.sync()

    try:
        tap("F6")
        tap("Control_L", "Alt_L", "s")
        tap("s")
        tap("Num_Lock")
        tap("F6")
        tap("Num_Lock")
        tap("Caps_Lock")
        tap("Control_L", "Alt_L", "s")
        tap("Caps_Lock")
        assert wait_until(lambda: len(fired) >= 4, 3), fired
        time.sleep(0.2)
        assert fired == ["F6", "CAS", "F6", "CAS"]
    finally:
        hk.stop()
        d.close()


def test_diagnose_self_test_passes_headless(xvfb):
    env = {k: v for k, v in os.environ.items() if k not in ("WAYLAND_DISPLAY", "QT_QPA_PLATFORM")}
    env.update(DISPLAY=xvfb.display, XDG_SESSION_TYPE="x11", QT_QPA_PLATFORM="xcb")
    out = subprocess.run([sys.executable, "-m", "pointclicker", "--diagnose"], env=env, capture_output=True,
                         text=True, timeout=120)
    assert "Result: PASS" in out.stdout, out.stdout + out.stderr
    assert "received by the test window" in out.stdout
    assert "pointer restored       yes" in out.stdout
