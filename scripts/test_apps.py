"""Real-app check: which click mode works in Notepad, Chrome (local click counter) and Calculator.

Run on Windows, not as administrator, and don't touch the mouse while it runs (~40 s):
    python scripts/test_apps.py
Each app is opened and clicked once in BACKGROUND mode, then once in FOREGROUND mode. "Worked" is judged by:
    Notepad     caret position (EM_GETSEL) moved
    Chrome      the page title "Clicks: N" counted up (scripts/click_counter.html)
    Calculator  the window's pixels changed (the click lands on a digit key; use Standard mode)
It also reports what PointClicker's own auto-detect (check_effect) concluded for the background click,
which is what drives the "switch to foreground?" suggestion in the app.
"""
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from pointclicker import clicker
from pointclicker.clicker import Mode

if sys.platform != "win32":
    if __name__ == "__main__":
        print("scripts/test_apps.py requires Windows (Win32 apps).")
        sys.exit(0)
    user32 = None
else:
    import ctypes
    from ctypes import wintypes

    from pointclicker.clicker import user32

    HERE = Path(__file__).resolve().parent
    WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.EnumWindows.argtypes = [WNDENUMPROC, wintypes.LPARAM]
    user32.EnumChildWindows.argtypes = [wintypes.HWND, WNDENUMPROC, wintypes.LPARAM]
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.SendMessageW.restype = ctypes.c_ssize_t
    user32.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
EM_GETSEL, EM_SETSEL = 0x00B0, 0x00B1
HWND_TOPMOST, HWND_NOTOPMOST, SWP_NOMOVE_NOSIZE = -1, -2, 0x3


def windows(parent=None) -> list[int]:
    found = []
    cb = WNDENUMPROC(lambda h, _: found.append(h) or True)
    user32.EnumChildWindows(parent, cb, 0) if parent else user32.EnumWindows(cb, 0)
    return [h for h in found if h and user32.IsWindowVisible(h)]


def wait_window(match, timeout=20) -> int | None:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        for h in windows():
            if match(clicker.window_class(h), clicker.window_text(h)):
                return h
        time.sleep(0.3)
    return None


def point(hwnd, fx, fy) -> tuple[int, int]:
    r = clicker.window_rect(hwnd)
    return r.left + int((r.right - r.left) * fx), r.top + int((r.bottom - r.top) * fy)


def trial(hwnd, xy, mode, state) -> tuple[bool | None, clicker.ClickResult]:
    user32.ShowWindow(hwnd, 9)  # SW_RESTORE: minimized windows can't be clicked in either mode
    if mode is Mode.FOREGROUND:  # a real click hits whatever is on top, so put the app there
        clicker.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0, SWP_NOMOVE_NOSIZE)
    try:
        time.sleep(0.5)
        before, cursor = state(), clicker.cursor_pos()
        res = clicker.click(*xy, mode, hwnd=hwnd, check_effect=mode is Mode.BACKGROUND)
        if clicker.cursor_pos() != cursor:
            moved.append(f"{mode.value}: cursor {cursor} -> {clicker.cursor_pos()}")
        time.sleep(0.8)
        after = state()
    finally:
        clicker.SetWindowPos(hwnd, HWND_NOTOPMOST, 0, 0, 0, 0, SWP_NOMOVE_NOSIZE)
    worked = None if before is None or after is None or not res.ok else before != after
    return worked, res


def notepad():
    path = Path(tempfile.gettempdir()) / "pointclicker-test.txt"
    path.write_text("".join(f"line {i:02} - PointClicker click test\n" for i in range(60)), encoding="utf-8")
    subprocess.Popen(["notepad.exe", str(path)])
    hwnd = wait_window(lambda c, t: c == "Notepad" and "pointclicker-test" in t)
    if not hwnd:
        return None
    time.sleep(1)
    edit = next((h for h in windows(hwnd) if clicker.window_class(h) in ("RichEditD2DPT", "Edit")), None)
    if not edit:
        return None
    user32.SendMessageW(edit, EM_SETSEL, 100_000, 100_000)  # caret to the end, away from the click point
    sel = lambda: user32.SendMessageW(edit, EM_GETSEL, 0, 0) & 0xFFFF  # noqa: E731
    return hwnd, point(hwnd, 0.3, 0.4), sel, lambda: user32.SendMessageW(edit, EM_SETSEL, 100_000, 100_000)


def chrome():
    url = (HERE / "click_counter.html").as_uri()
    subprocess.Popen(["cmd", "/c", "start", "", "chrome", "--new-window", url])
    hwnd = wait_window(lambda c, t: c.startswith("Chrome_WidgetWin") and t.startswith("Clicks:"))
    if not hwnd:
        return None
    count = lambda: int(clicker.window_text(hwnd).split()[1])  # noqa: E731  "Clicks: 3 - Google Chrome"
    return hwnd, point(hwnd, 0.5, 0.6), count, None


def calculator():
    subprocess.Popen(["calc.exe"])
    hwnd = wait_window(lambda c, t: c == "ApplicationFrameWindow" and "Calc" in t)
    if not hwnd:
        return None
    time.sleep(1.5)
    return hwnd, point(hwnd, 0.4, 0.78), lambda: clicker.snapshot(hwnd), None


def describe(worked):
    return {True: "worked", False: "NO EFFECT", None: "unknown"}[worked]


moved: list[str] = []  # the real cursor must end where it started in both modes


if __name__ == "__main__":
    print(f"admin: {clicker.is_admin()}. Don't touch the mouse.\n")
    rows = []
    for name, setup in (("Notepad", notepad), ("Chrome (click counter)", chrome), ("Calculator", calculator)):
        found = setup()
        if not found:
            rows.append((name, "app/window not found", "", "", ""))
            continue
        hwnd, xy, state, reset = found
        bg, bg_res = trial(hwnd, xy, Mode.BACKGROUND, state)
        if reset:
            reset()
        fg, fg_res = trial(hwnd, xy, Mode.FOREGROUND, state)
        need = ("background works" if bg else "foreground needed" if fg else
                "neither worked: check the logs" if bg is False else "inconclusive")
        rows.append((name, describe(bg) + ("" if bg_res.ok else f" ({bg_res.error})"),
                     {True: "changed", False: "unchanged -> suggests foreground", None: "can't tell"}[bg_res.changed],
                     describe(fg) + (f" in {fg_res.elapsed_ms:.0f} ms" if fg_res.ok else f" ({fg_res.error})"), need))

    head = ("App", "Background", "Auto-detect said", "Foreground", "Verdict")
    widths = [max(len(str(r[i])) for r in [head, *rows]) for i in range(len(head))]
    for r in [head, *rows]:
        print("  ".join(str(c).ljust(w) for c, w in zip(r, widths, strict=True)))
    print("\ncursor restored after every click:", "yes" if not moved else "NO\n  " + "\n  ".join(moved))
    print("Paste this output back to update the README's tested-apps section.")
