"""Test window for the real Win32 click tests: a plain Win32 window in its own process that reports every mouse
button message as a JSON line on stdout. It handles the messages itself, like most Win32 apps, so it sees both real
input (foreground) and posted messages (background). Tk can't be used: it rebuilds the button state from
GetKeyState, so it ignores posted clicks by design. Window class CLASS_NAME, title from --title; "quit" on stdin."""
import argparse
import ctypes
import json
import re
import sys
import threading
from ctypes import byref, wintypes

CLASS_NAME = "PointClickerTestWindow"
LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
WM_DESTROY, WM_CLOSE = 0x0002, 0x0010
BUTTON_MSGS = {0x201: ("left", False), 0x203: ("left", True), 0x204: ("right", False), 0x206: ("right", True),
               0x207: ("middle", False), 0x209: ("middle", True)}  # WM_?BUTTONDOWN / WM_?BUTTONDBLCLK


class WNDCLASSEXW(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.UINT), ("style", wintypes.UINT), ("lpfnWndProc", WNDPROC),
                ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int), ("hInstance", wintypes.HINSTANCE),
                ("hIcon", wintypes.HICON), ("hCursor", wintypes.HANDLE), ("hbrBackground", wintypes.HBRUSH),
                ("lpszMenuName", wintypes.LPCWSTR), ("lpszClassName", wintypes.LPCWSTR), ("hIconSm", wintypes.HICON)]


user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.DefWindowProcW.restype = LRESULT
user32.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_int,
                                   ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.HWND, wintypes.HMENU,
                                   wintypes.HINSTANCE, wintypes.LPVOID]
user32.CreateWindowExW.restype = wintypes.HWND
user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.LoadCursorW.argtypes = [wintypes.HINSTANCE, wintypes.LPVOID]
user32.LoadCursorW.restype = wintypes.HANDLE
kernel32.GetModuleHandleW.restype = wintypes.HMODULE


def say(**kw):
    sys.stdout.write(json.dumps(kw) + "\n")
    sys.stdout.flush()


@WNDPROC
def wndproc(hwnd, msg, wparam, lparam):
    if msg in BUTTON_MSGS:
        button, double = BUTTON_MSGS[msg]
        x, y = ctypes.c_short(lparam & 0xFFFF).value, ctypes.c_short((lparam >> 16) & 0xFFFF).value
        pt = wintypes.POINT(x, y)
        user32.ClientToScreen(hwnd, byref(pt))
        say(ev="press", button=button, double=double, x=x, y=y, rx=pt.x, ry=pt.y)
        return 0
    if msg == WM_DESTROY:
        user32.PostQuitMessage(0)
        return 0
    return user32.DefWindowProcW(hwnd, msg, wparam, lparam)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--title", required=True)
    ap.add_argument("--geometry", default="400x300+300+200")
    ap.add_argument("--topmost", action="store_true")
    a = ap.parse_args()
    w, h, x, y = map(int, re.fullmatch(r"(\d+)x(\d+)\+(\d+)\+(\d+)", a.geometry).groups())

    hinst = kernel32.GetModuleHandleW(None)
    wc = WNDCLASSEXW(cbSize=ctypes.sizeof(WNDCLASSEXW), style=0x0008,  # CS_DBLCLKS: real double clicks too
                     lpfnWndProc=wndproc, hInstance=hinst, hCursor=user32.LoadCursorW(None, 32512),  # IDC_ARROW
                     hbrBackground=wintypes.HBRUSH(6), lpszClassName=CLASS_NAME)  # COLOR_WINDOW + 1
    user32.RegisterClassExW(byref(wc))
    hwnd = user32.CreateWindowExW(0x8 if a.topmost else 0, CLASS_NAME, a.title,  # WS_EX_TOPMOST
                                  0x10CF0000, x, y, w, h, None, None, hinst, None)  # WS_OVERLAPPEDWINDOW|WS_VISIBLE
    if not hwnd:
        raise ctypes.WinError(ctypes.get_last_error())
    user32.UpdateWindow(hwnd)

    def read_stdin():
        for line in sys.stdin:
            if line.strip() == "quit":
                break
        user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)  # quit, or the test process went away
    threading.Thread(target=read_stdin, daemon=True).start()
    say(ev="ready", hwnd=hwnd)

    msg = wintypes.MSG()
    while user32.GetMessageW(byref(msg), None, 0, 0) > 0:
        user32.TranslateMessage(byref(msg))
        user32.DispatchMessageW(byref(msg))


if __name__ == "__main__":
    main()
