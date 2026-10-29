"""Real clicks on real Windows: a plain Win32 test window in another process counts what arrives. Runs only on Windows (CI's
windows-latest runner has an interactive desktop); the mocked call-sequence tests are in test_windows_backend.py."""
import json
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from pointclicker.scheduler import Scheduler
from pointclicker.targets import Target

pytestmark = [pytest.mark.integration,
              pytest.mark.skipif(sys.platform != "win32", reason="real Win32 clicks need Windows")]
COUNTER = Path(__file__).with_name("win32_clickcounter.py")
WINDOW_CLASS = "PointClickerTestWindow"  # win32_clickcounter.CLASS_NAME
PARK = (5, 5)  # where the pointer waits; nothing of ours is there


class Counter:
    def __init__(self, title: str, geometry: str = "400x300+300+200", topmost: bool = True):
        self.title = title
        argv = [sys.executable, str(COUNTER), "--title", title, "--geometry", geometry] + (["--topmost"] * topmost)
        self.proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, bufsize=1)
        self.events: queue.Queue = queue.Queue()
        threading.Thread(target=lambda: [self.events.put(json.loads(line)) for line in self.proc.stdout],
                         daemon=True).start()
        ready = self.events.get(timeout=20)
        assert ready["ev"] == "ready", ready
        self.hwnd = ready["hwnd"]
        time.sleep(0.3)  # let the window manager finish showing it

    def presses(self, settle: float = 0.5) -> list[dict]:
        out = []
        while True:
            try:
                ev = self.events.get(timeout=settle)
            except queue.Empty:
                return out
            if ev["ev"] == "press":
                out.append(ev)

    def close(self):
        if self.proc.poll() is None:
            try:
                self.proc.stdin.write("quit\n")
                self.proc.stdin.flush()
                self.proc.wait(5)
            except Exception:
                self.proc.kill()


@pytest.fixture
def win():
    from pointclicker.platform import set_backend
    from pointclicker.platform.windows.input import WindowsInputBackend
    b = WindowsInputBackend()
    set_backend(b)
    return b


@pytest.fixture
def counter():
    made = []

    def make(*a, **kw):
        c = Counter(*a, **kw)
        made.append(c)
        return c
    yield make
    for c in made:
        c.close()


def park(b):
    import ctypes
    ctypes.windll.user32.SetCursorPos(*PARK)
    assert b.get_cursor_pos() == PARK


def target_for(c: Counter, rel, **kw) -> Target:
    return Target(rel_x=rel[0], rel_y=rel[1], window_class=WINDOW_CLASS, window_title=c.title, **kw)


def run(t: Target, clicks: int, timeout=15.0):
    s = Scheduler(lambda tgt, abort: tgt.click(abort))
    s.start([t])
    end = time.monotonic() + timeout
    while s.stats[t.id].clicks < clicks and time.monotonic() < end:
        time.sleep(0.02)
    s.shutdown()
    return s.stats[t.id]


def screen_point(b, c: Counter, rel):
    r = b.window_rect(b.find_window(WINDOW_CLASS, c.title))
    return r.left + rel[0], r.top + rel[1]


@pytest.mark.parametrize("mode", ["background", "foreground"])
def test_exact_count_and_position(win, counter, mode):
    c = counter(f"PointClicker test {mode}")
    park(win)
    rel = (120, 140)
    st = run(target_for(c, rel, mode=mode, interval=0.15, repeat=5), 5)
    presses = c.presses()
    assert (st.clicks, st.failures) == (5, 0), st
    assert len(presses) == 5, presses
    assert all((p["rx"], p["ry"], p["button"]) == (*screen_point(win, c, rel), "left") for p in presses), presses
    assert win.get_cursor_pos() == PARK, "pointer moved by a background click / not restored after a foreground one"


@pytest.mark.parametrize("mode", ["background", "foreground"])
@pytest.mark.parametrize("button", ["left", "middle", "right"])
def test_buttons(win, counter, mode, button):
    c = counter(f"PointClicker test {mode} {button}")
    park(win)
    st = run(target_for(c, (100, 120), mode=mode, button=button, interval=0.2, repeat=2), 2)
    presses = c.presses()
    assert st.failures == 0 and [p["button"] for p in presses] == [button, button], presses


@pytest.mark.parametrize("mode", ["background", "foreground"])
def test_double_click(win, counter, mode):
    c = counter(f"PointClicker test double {mode}")
    park(win)
    st = run(target_for(c, (100, 120), mode=mode, click_type="double", repeat=1), 1)
    presses = c.presses()
    assert st.failures == 0 and len(presses) == 2 and presses[-1]["double"], presses


def test_background_reaches_a_covered_window_foreground_hits_the_cover(win, counter):
    under = counter("PointClicker test covered", "400x300+300+200", topmost=False)
    cover = counter("PointClicker test cover", "500x400+250+150", topmost=True)
    park(win)
    rel = (150, 150)
    assert run(target_for(under, rel, mode="background", repeat=3, interval=0.15), 3).failures == 0
    assert len(under.presses()) == 3 and not cover.presses()
    assert run(target_for(under, rel, mode="foreground", repeat=2, interval=0.15), 2).failures == 0
    assert not under.presses() and len(cover.presses()) == 2  # foreground clicks what you'd click by hand
    assert win.get_cursor_pos() == PARK


@pytest.mark.parametrize("mode", ["background", "foreground"])
def test_target_window_closed_mid_run_never_clicks_what_is_underneath(win, counter, mode):
    behind = counter(f"PointClicker test behind {mode}", "600x500+200+100", topmost=False)
    gone = counter(f"PointClicker test gone {mode}", "400x300+300+200", topmost=True)
    t = target_for(gone, (100, 100), mode=mode, interval=0.1)
    s = Scheduler(lambda tgt, abort: tgt.click(abort))
    s.start([t])
    time.sleep(0.6)
    gone.close()
    time.sleep(0.8)
    s.shutdown()
    st = s.stats[t.id]
    assert st.clicks > 3 and st.failures > 0 and "not found" in st.last_error, st
    assert not behind.presses(), "clicked the window underneath after the target closed"
