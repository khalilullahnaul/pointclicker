"""Windows backend, tested on Linux: a fake user32/shcore/... records every Win32 call against a tiny
simulated desktop, so we can assert the exact call sequences (PostMessage lParam packing, ScreenToClient,
DPI scaling, SendInput structs, cursor save/restore, error mapping, GDI cleanup)."""
import ctypes
import importlib.util
import os
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path

import pytest

SRC = Path(os.environ.get("PC_WIN_INPUT_SRC")  # lets a mutation check point the suite at a broken copy
           or Path(__file__).resolve().parents[1] / "src" / "pointclicker" / "platform" / "windows" / "input.py")
WM_MOUSEMOVE, WM_LBUTTONDOWN, WM_LBUTTONUP, WM_LBUTTONDBLCLK = 0x200, 0x201, 0x202, 0x203
WM_RBUTTONDOWN, WM_RBUTTONUP, WM_MBUTTONDOWN, WM_MBUTTONUP = 0x204, 0x205, 0x207, 0x208
MK_LBUTTON, MK_RBUTTON, MK_MBUTTON = 0x1, 0x2, 0x10
LEFTDOWN, LEFTUP, RIGHTDOWN, RIGHTUP = 0x2, 0x4, 0x8, 0x10


@dataclass
class Win:
    hwnd: int
    rect: tuple[int, int, int, int]  # screen left, top, right, bottom (client area == window here)
    cls: str = "App"
    title: str = "App"
    parent: int | None = None
    dpi: int = 96
    iconic: bool = False
    pid: int = 100


@dataclass
class World:
    """A tiny Windows desktop. Windows later in the list are on top."""

    windows: list[Win] = field(default_factory=list)
    cursor: list[int] = field(default_factory=lambda: [500, 500])
    monitor_dpi: int = 96
    held: set[int] = field(default_factory=set)  # virtual keys of held mouse buttons
    last_error: int = 0
    fail: dict[str, int] = field(default_factory=dict)  # function name -> GetLastError code to fail with
    calls: list[tuple] = field(default_factory=list)

    def win(self, hwnd):
        return next((w for w in self.windows if w.hwnd == hwnd), None)

    def at(self, x, y, parent=None):
        for w in reversed(self.windows):
            left, top, right, bottom = w.rect
            if w.parent == parent and left <= x < right and top <= y < bottom:
                return w
        return None


class FakeFunc:
    def __init__(self, world, name, impl):
        self.world, self.name, self.impl = world, name, impl
        self.restype = self.argtypes = None

    def __call__(self, *args):
        self.world.calls.append((self.name, *args))
        if self.name in self.world.fail:
            self.world.last_error = self.world.fail[self.name]
            return 0
        return self.impl(*args)


def _obj(ref):
    return getattr(ref, "_obj", ref)  # byref(x) -> x


def make_dll(world):
    def ScreenToClient(hwnd, ref):
        w = world.win(hwnd)
        if not w:
            world.last_error = 1400
            return 0
        pt = _obj(ref)
        pt.x, pt.y = pt.x - w.rect[0], pt.y - w.rect[1]
        return 1

    def ChildWindowFromPointEx(hwnd, pt, flags):
        w = world.win(hwnd)
        child = world.at(pt.x + w.rect[0], pt.y + w.rect[1], parent=hwnd)
        return child.hwnd if child else hwnd

    def GetDpiForMonitor(hmon, kind, xref, yref):
        _obj(xref).value = _obj(yref).value = world.monitor_dpi
        return 0

    def GetCursorPos(ref):
        _obj(ref).x, _obj(ref).y = world.cursor
        return 1

    def SetCursorPos(x, y):
        world.cursor[:] = [x, y]
        return 1

    def GetWindowRect(hwnd, ref):
        w = world.win(hwnd)
        if not w:
            return 0
        r = _obj(ref)
        r.left, r.top, r.right, r.bottom = w.rect
        return 1

    def text(attr):
        def get(hwnd, buf, n):
            w = world.win(hwnd)
            buf.value = getattr(w, attr)[: n - 1] if w else ""
            return len(buf.value)
        return get

    def FindWindowW(cls, title):
        w = next((w for w in reversed(world.windows) if w.parent is None and w.cls == cls
                  and (title is None or w.title == title)), None)
        return w.hwnd if w else None

    impls = {
        "SetProcessDpiAwarenessContext": lambda ctx: 1,
        "WindowFromPoint": lambda pt: (lambda w: w.hwnd if w else None)(world.at(pt.x, pt.y)),
        "ChildWindowFromPointEx": ChildWindowFromPointEx,
        "ScreenToClient": ScreenToClient,
        "GetDpiForWindow": lambda hwnd: world.win(hwnd).dpi,
        "MonitorFromWindow": lambda hwnd, flags: 0x10001,
        "GetDpiForMonitor": GetDpiForMonitor,
        "PostMessageW": lambda hwnd, msg, wp, lp: 1 if world.win(hwnd) else 0,
        "GetAsyncKeyState": lambda vk: -32768 if vk in world.held else 0,  # c_short with the high bit set
        "GetCursorPos": GetCursorPos,
        "SetCursorPos": SetCursorPos,
        "SendInput": lambda n, inputs, size: n,
        "GetAncestor": lambda hwnd, flag: hwnd,
        "GetWindowRect": GetWindowRect,
        "GetWindowTextW": text("title"),
        "GetClassNameW": text("cls"),
        "FindWindowW": FindWindowW,
        "IsIconic": lambda hwnd: int(world.win(hwnd).iconic),
        "GetWindowThreadProcessId": lambda hwnd, ref: setattr(_obj(ref), "value", world.win(hwnd).pid) or 1,
        "OpenProcess": lambda access, inherit, pid: 0x500 + pid,
        "OpenProcessToken": lambda proc, access, ref: setattr(_obj(ref), "value", 0x900) or 1,
        "GetTokenInformation": lambda tok, cls, ref, size, outsize: setattr(_obj(ref), "value", 0) or 1,
        "CloseHandle": lambda h: 1,
        "GetCurrentProcessId": lambda: 1,
        "GetDC": lambda hwnd: 0xDC,
        "ReleaseDC": lambda hwnd, dc: 1,
        "PrintWindow": lambda hwnd, dc, flags: 1,
        "CreateCompatibleDC": lambda dc: 0xD2,
        "CreateCompatibleBitmap": lambda dc, w, h: 0xB1,
        "SelectObject": lambda dc, obj: 0x01D,
        "DeleteObject": lambda obj: 1,
        "DeleteDC": lambda dc: 1,
        "GetDIBits": lambda *a: a[3],
        "SetWindowPos": lambda *a: 1,
    }

    class FakeDLL:
        def __init__(self, name, use_last_error=False):
            self._funcs = {}

        def __getattr__(self, name):
            if name.startswith("_"):
                raise AttributeError(name)
            if name not in self._funcs:
                self._funcs[name] = FakeFunc(world, name, impls[name])
            return self._funcs[name]

    return FakeDLL


@pytest.fixture
def win32(monkeypatch):
    """Load a fresh copy of windows/input.py as if on Windows, wired to a simulated desktop."""
    world = World()
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(ctypes, "WinDLL", make_dll(world), raising=False)
    monkeypatch.setattr(ctypes, "HRESULT", ctypes.c_long, raising=False)
    monkeypatch.setattr(ctypes, "get_last_error", lambda: world.last_error, raising=False)
    monkeypatch.setattr(ctypes, "FormatError", lambda code: f"system error {code}", raising=False)
    spec = importlib.util.spec_from_file_location("pointclicker.platform.windows._input_under_test", SRC)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "FOREGROUND_SETTLE_S", 0)
    yield mod, world
    sys.modules.pop(spec.name, None)


def posts(world):
    return [c[1:] for c in world.calls if c[0] == "PostMessageW"]


def named(world, *names):
    return [c for c in world.calls if c[0] in names]


def test_process_becomes_per_monitor_v2_dpi_aware_on_import(win32):
    _, world = win32
    (call,) = named(world, "SetProcessDpiAwarenessContext")
    assert call[1].value == ctypes.c_void_p(-4).value  # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2


def test_background_click_message_sequence_and_lparam_packing(win32):
    mod, world = win32
    world.windows.append(Win(7, (100, 50, 900, 650)))
    err, hwnd = mod._background(400, 250, "left", False, None)
    assert (err, hwnd) == (None, 7)
    lparam = (200 << 16) | 300  # ScreenToClient: (400-100, 250-50)
    assert posts(world) == [(7, WM_MOUSEMOVE, 0, lparam), (7, WM_LBUTTONDOWN, MK_LBUTTON, lparam),
                            (7, WM_LBUTTONUP, 0, lparam)]
    assert mod.MOUSEINPUT and not named(world, "SetCursorPos", "SendInput")  # the real cursor never moves


@pytest.mark.parametrize("button,down,up,mk", [("right", WM_RBUTTONDOWN, WM_RBUTTONUP, MK_RBUTTON),
                                               ("middle", WM_MBUTTONDOWN, WM_MBUTTONUP, MK_MBUTTON)])
def test_background_other_buttons(win32, button, down, up, mk):
    mod, world = win32
    world.windows.append(Win(7, (0, 0, 500, 500)))
    mod._background(10, 20, button, False, None)
    assert [p[1:3] for p in posts(world)] == [(WM_MOUSEMOVE, 0), (down, mk), (up, 0)]


def test_background_double_click_is_down_up_dblclk_up(win32):
    mod, world = win32
    world.windows.append(Win(7, (0, 0, 500, 500)))
    mod._background(10, 20, "left", True, None)
    assert [p[1:3] for p in posts(world)] == [(WM_MOUSEMOVE, 0), (WM_LBUTTONDOWN, MK_LBUTTON), (WM_LBUTTONUP, 0),
                                              (WM_LBUTTONDBLCLK, MK_LBUTTON), (WM_LBUTTONUP, 0)]


def test_background_goes_to_deepest_child_in_child_coordinates(win32):
    mod, world = win32
    world.windows += [Win(7, (100, 100, 900, 700)), Win(8, (200, 150, 600, 450), parent=7),
                      Win(9, (250, 200, 350, 300), parent=8)]
    err, hwnd = mod._background(260, 230, "left", False, None)
    assert (err, hwnd) == (None, 9)
    assert posts(world)[0][3] == (30 << 16) | 10


def test_background_with_root_targets_a_covered_window(win32):
    mod, world = win32
    world.windows += [Win(7, (0, 0, 800, 600), cls="Target"), Win(99, (0, 0, 800, 600), cls="CoversIt")]
    _, hwnd = mod._background(50, 60, "left", False, 7)
    assert hwnd == 7 and not named(world, "WindowFromPoint")


def test_lparam_packs_negative_client_coordinates_as_16_bit(win32):
    mod, world = win32
    world.windows.append(Win(7, (100, 100, 900, 700)))
    mod._background(95, 90, "left", False, 7)  # left of and above the client area
    assert posts(world)[0][3] == ((-10 & 0xFFFF) << 16) | (-5 & 0xFFFF)


@pytest.mark.parametrize("win_dpi,mon_dpi,expected", [(96, 144, (200, 133)), (144, 144, (300, 200)),
                                                       (96, 192, (150, 100)), (120, 96, (375, 250))])
def test_dpi_unaware_window_gets_logical_coordinates(win32, win_dpi, mon_dpi, expected):
    mod, world = win32
    world.monitor_dpi = mon_dpi
    world.windows.append(Win(7, (100, 50, 1900, 1050), dpi=win_dpi))
    mod._background(400, 250, "left", False, None)
    x, y = expected
    assert posts(world)[0][3] == (y << 16) | x


@pytest.mark.parametrize("code,message", [(5, "access denied"), (1400, "target window is gone"),
                                          (87, "system error 87")])
def test_post_message_failures_are_explained(win32, code, message):
    mod, world = win32
    world.windows.append(Win(7, (0, 0, 500, 500)))
    world.fail["PostMessageW"] = code
    err, _ = mod._background(10, 10, "left", False, None)
    assert message in err


def test_foreground_saves_moves_clicks_restores_in_order(win32):
    mod, world = win32
    world.cursor[:] = [11, 22]
    err, _ = mod._foreground(640, 480, "left", False, None)
    assert err is None
    seq = [c[0] for c in named(world, "GetCursorPos", "SetCursorPos", "SendInput")]
    assert seq == ["GetCursorPos", "SetCursorPos", "SendInput", "SetCursorPos"]
    moves = [c[1:] for c in named(world, "SetCursorPos")]
    assert moves == [(640, 480), (11, 22)] and world.cursor == [11, 22]
    _, n, inputs, size = named(world, "SendInput")[0]
    assert n == 2 and size == ctypes.sizeof(mod.INPUT)
    assert [(i.type, i.mi.dwFlags, i.mi.dx, i.mi.dy) for i in inputs] == [(0, LEFTDOWN, 0, 0), (0, LEFTUP, 0, 0)]


def test_foreground_double_right_click_sends_four_inputs(win32):
    mod, world = win32
    mod._foreground(1, 2, "right", True, None)
    _, n, inputs, _ = named(world, "SendInput")[0]
    assert n == 4 and [i.mi.dwFlags for i in inputs] == [RIGHTDOWN, RIGHTUP, RIGHTDOWN, RIGHTUP]


def test_input_struct_layout_matches_win32():
    """INPUT is a DWORD type + union; on 64-bit Windows sizeof(INPUT) == 40 (MOUSEINPUT is the largest member)."""
    class MI(ctypes.Structure):
        _fields_ = [("dx", ctypes.c_int32), ("dy", ctypes.c_int32), ("mouseData", ctypes.c_uint32),
                    ("dwFlags", ctypes.c_uint32), ("time", ctypes.c_uint32), ("dwExtraInfo", ctypes.c_size_t)]

    class INPUT(ctypes.Structure):
        _fields_ = [("type", ctypes.c_uint32), ("mi", MI)]
    assert ctypes.sizeof(INPUT) == (40 if ctypes.sizeof(ctypes.c_void_p) == 8 else 28)


def test_foreground_restores_cursor_even_if_sendinput_raises(win32, monkeypatch):
    mod, world = win32
    world.cursor[:] = [5, 6]

    def boom(*a):
        raise OSError("driver went away")
    monkeypatch.setattr(mod.SendInput, "impl", boom)
    with pytest.raises(OSError):
        mod._foreground(100, 100, "left", False, None)
    assert world.cursor == [5, 6]


def test_foreground_reports_blocked_input(win32):
    mod, world = win32
    world.fail["SendInput"] = 5
    err, _ = mod._foreground(100, 100, "left", False, None)
    assert "access denied" in err and world.cursor == [500, 500]


def test_foreground_fails_cleanly_on_secure_desktop(win32):
    mod, world = win32
    world.fail["GetCursorPos"] = 5  # UAC prompt / lock screen
    err, _ = mod._foreground(100, 100, "left", False, None)
    assert "access denied" in err and not named(world, "SetCursorPos", "SendInput")


def backend(mod):
    b = object.__new__(mod.WindowsInputBackend)  # __init__ refuses non-Windows; the module is already "Windows"
    return b


def test_backend_waits_for_users_mouse_button_then_skips(win32):
    mod, world = win32
    world.held = {0x01}
    res = backend(mod).click((10, 10), mode="foreground", busy_timeout=0.05)
    assert not res.ok and "holding a mouse button" in res.error and not named(world, "SendInput")


def test_backend_abort_while_waiting_cancels(win32):
    mod, world = win32
    world.held = {0x02}
    abort = threading.Event()
    threading.Timer(0.05, abort.set).start()
    res = backend(mod).click((10, 10), mode="foreground", busy_timeout=5, abort=abort)
    assert res.error == "cancelled" and not named(world, "SendInput")


def test_backend_target_follows_window_and_refuses_minimized(win32, monkeypatch):
    mod, world = win32
    from pointclicker import clicker
    from pointclicker.targets import Target
    world.windows.append(Win(7, (300, 200, 1100, 800), cls="Notepad", title="a.txt - Notepad"))
    b = backend(mod)
    monkeypatch.setattr(clicker, "get_backend", lambda: b)
    t = Target(x=0, y=0, rel_x=40, rel_y=30, window_class="Notepad", window_title="a.txt - Notepad", mode="background")
    assert b.click(t).ok
    assert posts(world)[0][0] == 7 and posts(world)[0][3] == (30 << 16) | 40
    world.windows[0].rect = (0, 0, 800, 600)  # window moved: the click follows
    world.calls.clear()
    b.click(t)
    assert posts(world)[0][3] == (30 << 16) | 40
    world.windows[0].iconic = True
    assert b.click(t).error == "target window is minimized"


def test_snapshot_releases_gdi_objects_even_when_printwindow_fails(win32):
    mod, world = win32
    world.windows.append(Win(7, (0, 0, 40, 30)))
    world.fail["PrintWindow"] = 0
    assert mod._snapshot(7) is None
    assert [c[0] for c in named(world, "DeleteObject", "DeleteDC", "ReleaseDC")] == ["DeleteObject", "DeleteDC",
                                                                                     "ReleaseDC"]
    world.fail.clear()
    assert len(mod._snapshot(7)) == 40 * 30 * 4


def test_elevation_check_closes_every_handle(win32):
    mod, world = win32
    world.windows.append(Win(7, (0, 0, 10, 10), pid=4242))
    assert mod._process_elevated(4242) is False
    opened = {c[3] + 0x500 for c in named(world, "OpenProcess")} | {0x900}
    closed = {c[1].value if hasattr(c[1], "value") else c[1] for c in named(world, "CloseHandle")}
    assert opened <= closed


def test_elevated_process_that_denies_access_counts_as_elevated(win32):
    mod, world = win32
    world.fail["OpenProcess"] = 5
    assert mod._process_elevated(4242) is True
