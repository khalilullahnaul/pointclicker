from __future__ import annotations

import functools
import logging
import sys
import threading
import time
from typing import Any

from ..base import InputBackend, Rect

log = logging.getLogger(__name__)

# All Win32 imports and ctypes setup are strictly guarded by sys.platform
if sys.platform == "win32":
    import ctypes
    from ctypes import byref, wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    shcore = ctypes.WinDLL("shcore", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)

    def _fn(dll, name, restype, *argtypes):
        f = getattr(dll, name)
        f.restype, f.argtypes = restype, argtypes
        return f

    _PPOINT = ctypes.POINTER(wintypes.POINT)
    _PUINT = ctypes.POINTER(wintypes.UINT)

    SetProcessDpiAwarenessContext = _fn(user32, "SetProcessDpiAwarenessContext", wintypes.BOOL, ctypes.c_void_p)
    WindowFromPoint = _fn(user32, "WindowFromPoint", wintypes.HWND, wintypes.POINT)
    ChildWindowFromPointEx = _fn(user32, "ChildWindowFromPointEx", wintypes.HWND, wintypes.HWND, wintypes.POINT, wintypes.UINT)
    ScreenToClient = _fn(user32, "ScreenToClient", wintypes.BOOL, wintypes.HWND, _PPOINT)
    GetDpiForWindow = _fn(user32, "GetDpiForWindow", wintypes.UINT, wintypes.HWND)
    MonitorFromWindow = _fn(user32, "MonitorFromWindow", wintypes.HANDLE, wintypes.HWND, wintypes.DWORD)
    GetDpiForMonitor = _fn(shcore, "GetDpiForMonitor", ctypes.HRESULT, wintypes.HANDLE, ctypes.c_int, _PUINT, _PUINT)
    PostMessageW = _fn(user32, "PostMessageW", wintypes.BOOL, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
    GetAsyncKeyState = _fn(user32, "GetAsyncKeyState", ctypes.c_short, ctypes.c_int)
    GetCursorPos = _fn(user32, "GetCursorPos", wintypes.BOOL, _PPOINT)
    SetCursorPos = _fn(user32, "SetCursorPos", wintypes.BOOL, ctypes.c_int, ctypes.c_int)
    GetAncestor = _fn(user32, "GetAncestor", wintypes.HWND, wintypes.HWND, wintypes.UINT)
    GetWindowRect = _fn(user32, "GetWindowRect", wintypes.BOOL, wintypes.HWND, ctypes.POINTER(wintypes.RECT))
    GetWindowTextW = _fn(user32, "GetWindowTextW", ctypes.c_int, wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
    GetClassNameW = _fn(user32, "GetClassNameW", ctypes.c_int, wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
    FindWindowW = _fn(user32, "FindWindowW", wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR)
    GetWindowThreadProcessId = _fn(user32, "GetWindowThreadProcessId", wintypes.DWORD, wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
    OpenProcess = _fn(kernel32, "OpenProcess", wintypes.HANDLE, wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    CloseHandle = _fn(kernel32, "CloseHandle", wintypes.BOOL, wintypes.HANDLE)
    OpenProcessToken = _fn(advapi32, "OpenProcessToken", wintypes.BOOL, wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE))
    GetTokenInformation = _fn(advapi32, "GetTokenInformation", wintypes.BOOL, wintypes.HANDLE, ctypes.c_int,
                              ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD))
    IsIconic = _fn(user32, "IsIconic", wintypes.BOOL, wintypes.HWND)
    GetDC = _fn(user32, "GetDC", wintypes.HDC, wintypes.HWND)
    ReleaseDC = _fn(user32, "ReleaseDC", ctypes.c_int, wintypes.HWND, wintypes.HDC)
    PrintWindow = _fn(user32, "PrintWindow", wintypes.BOOL, wintypes.HWND, wintypes.HDC, wintypes.UINT)
    CreateCompatibleDC = _fn(gdi32, "CreateCompatibleDC", wintypes.HDC, wintypes.HDC)
    CreateCompatibleBitmap = _fn(gdi32, "CreateCompatibleBitmap", wintypes.HBITMAP, wintypes.HDC, ctypes.c_int, ctypes.c_int)
    SelectObject = _fn(gdi32, "SelectObject", wintypes.HGDIOBJ, wintypes.HDC, wintypes.HGDIOBJ)
    DeleteObject = _fn(gdi32, "DeleteObject", wintypes.BOOL, wintypes.HGDIOBJ)
    DeleteDC = _fn(gdi32, "DeleteDC", wintypes.BOOL, wintypes.HDC)
    GetDIBits = _fn(gdi32, "GetDIBits", ctypes.c_int, wintypes.HDC, wintypes.HBITMAP, wintypes.UINT, wintypes.UINT,
                    ctypes.c_void_p, ctypes.c_void_p, wintypes.UINT)
    SetWindowPos = _fn(user32, "SetWindowPos", wintypes.BOOL, wintypes.HWND, wintypes.HWND,
                       ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT)

    class MOUSEINPUT(ctypes.Structure):
        _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                    ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]

    class INPUT(ctypes.Structure):
        _fields_ = [("type", wintypes.DWORD), ("mi", MOUSEINPUT)]

    SendInput = _fn(user32, "SendInput", wintypes.UINT, wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int)

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
                    ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                    ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                    ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD)]

    WM_MOUSEMOVE = 0x0200
    CWP_SKIP = 0x1 | 0x2 | 0x4  # CWP_SKIPINVISIBLE | CWP_SKIPDISABLED | CWP_SKIPTRANSPARENT
    MONITOR_DEFAULTTONEAREST = 2
    INPUT_MOUSE = 0
    _VK_MOUSE = (0x01, 0x02, 0x04)  # VK_LBUTTON, VK_RBUTTON, VK_MBUTTON

    _BUTTONS = {
        "left": (0x0201, 0x0202, 0x0203, 0x0001, 0x0002, 0x0004),
        "right": (0x0204, 0x0205, 0x0206, 0x0002, 0x0008, 0x0010),
        "middle": (0x0207, 0x0208, 0x0209, 0x0010, 0x0020, 0x0040),
    }

    _ERRORS = {
        5: "access denied (target is probably elevated; run PointClicker as administrator)",
        1400: "target window is gone",
    }

    FOREGROUND_SETTLE_S = 0.008
    EFFECT_SETTLE_S = 0.5
    CANCELLED = "cancelled"
    _click_lock = threading.Lock()

    def _winerr(code: int) -> str:
        return _ERRORS.get(code) or f"{ctypes.FormatError(code).strip()} (error {code})"

    def _make_dpi_aware() -> None:
        if not SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):  # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2
            log.debug("SetProcessDpiAwarenessContext: %s", _winerr(ctypes.get_last_error()))

    _make_dpi_aware()

    def _wait_for_user_release(timeout: float, abort: threading.Event | None) -> bool:
        """Wait while the user physically holds any mouse button. False if still held after timeout or aborted."""
        deadline = time.monotonic() + timeout
        while any(GetAsyncKeyState(vk) & 0x8000 for vk in _VK_MOUSE):
            if time.monotonic() >= deadline or (abort and abort.is_set()):
                return False
            time.sleep(0.02)
        return True

    def _window_at(x: int, y: int, root: int | None = None) -> int | None:
        hwnd = root or WindowFromPoint(wintypes.POINT(x, y))
        while hwnd:
            pt = wintypes.POINT(x, y)
            if not ScreenToClient(hwnd, byref(pt)):
                break
            child = ChildWindowFromPointEx(hwnd, pt, CWP_SKIP)
            if not child or child == hwnd:
                break
            hwnd = child
        return hwnd

    def _cursor_pos() -> tuple[int, int]:
        pt = wintypes.POINT()
        GetCursorPos(byref(pt))
        return pt.x, pt.y

    def _top_window_at(x: int, y: int) -> int | None:
        hwnd = WindowFromPoint(wintypes.POINT(x, y))
        return GetAncestor(hwnd, 2) if hwnd else None  # GA_ROOT

    def _window_rect(hwnd: int) -> wintypes.RECT | None:
        r = wintypes.RECT()
        return r if GetWindowRect(hwnd, byref(r)) else None

    def _window_text(hwnd: int) -> str:
        buf = ctypes.create_unicode_buffer(512)
        GetWindowTextW(hwnd, buf, len(buf))
        return buf.value

    def _window_class(hwnd: int) -> str:
        buf = ctypes.create_unicode_buffer(256)
        GetClassNameW(hwnd, buf, len(buf))
        return buf.value

    def _process_elevated(pid: int) -> bool:
        proc = OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not proc:
            return ctypes.get_last_error() == 5
        try:
            token = wintypes.HANDLE()
            if not OpenProcessToken(proc, 0x0008, byref(token)):  # TOKEN_QUERY
                return ctypes.get_last_error() == 5
            try:
                elevated, size = wintypes.DWORD(), wintypes.DWORD()
                GetTokenInformation(token, 20, byref(elevated), ctypes.sizeof(elevated), byref(size))
                return bool(elevated.value)
            finally:
                CloseHandle(token)
        finally:
            CloseHandle(proc)

    @functools.cache
    def _is_admin() -> bool:
        return _process_elevated(kernel32.GetCurrentProcessId())

    def _blocked_by_elevation(hwnd: int) -> bool:
        pid = wintypes.DWORD()
        GetWindowThreadProcessId(hwnd, byref(pid))
        return bool(pid.value) and not _is_admin() and _process_elevated(pid.value)

    def _snapshot(hwnd: int) -> bytes | None:
        r = _window_rect(hwnd)
        if not r or IsIconic(hwnd) or r.right <= r.left or r.bottom <= r.top:
            return None
        w, h = r.right - r.left, r.bottom - r.top
        screen = GetDC(None)
        mem = CreateCompatibleDC(screen)
        bmp = CreateCompatibleBitmap(screen, w, h)
        try:
            old = SelectObject(mem, bmp)
            ok = PrintWindow(hwnd, mem, 2)
            SelectObject(mem, old)
            info = BITMAPINFOHEADER(biSize=ctypes.sizeof(BITMAPINFOHEADER), biWidth=w, biHeight=-h, biPlanes=1, biBitCount=32)
            buf = ctypes.create_string_buffer(w * h * 4)
            return buf.raw if ok and GetDIBits(mem, bmp, 0, h, buf, byref(info), 0) else None
        finally:
            DeleteObject(bmp)
            DeleteDC(mem)
            ReleaseDC(None, screen)

    def _static_snapshot(hwnd: int) -> bytes | None:
        a = _snapshot(hwnd)
        time.sleep(0.3)
        b = _snapshot(hwnd)
        return b if a is not None and a == b else None

    def _client_point(hwnd: int, x: int, y: int) -> wintypes.POINT | None:
        pt = wintypes.POINT(x, y)
        if not ScreenToClient(hwnd, byref(pt)):
            return None
        mon_dpi, _ = wintypes.UINT(), wintypes.UINT()
        GetDpiForMonitor(MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST), 0, byref(mon_dpi), byref(_))
        win_dpi = GetDpiForWindow(hwnd)
        if win_dpi and mon_dpi.value and win_dpi != mon_dpi.value:
            pt.x, pt.y = round(pt.x * win_dpi / mon_dpi.value), round(pt.y * win_dpi / mon_dpi.value)
        return pt

    def _background(x: int, y: int, button: str, double: bool, root: int | None) -> tuple[str | None, int | None]:
        hwnd = _window_at(x, y, root)
        if not hwnd:
            return f"no window at ({x}, {y})", None
        pt = _client_point(hwnd, x, y)
        if pt is None:
            return _winerr(ctypes.get_last_error()), hwnd
        down, up, dbl, mk, _, _ = _BUTTONS[button]
        lparam = ((pt.y & 0xFFFF) << 16) | (pt.x & 0xFFFF)
        msgs = [(WM_MOUSEMOVE, 0), (down, mk), (up, 0)]
        if double:
            msgs += [(dbl, mk), (up, 0)]
        for msg, wparam in msgs:
            if not PostMessageW(hwnd, msg, wparam, lparam):
                return _winerr(ctypes.get_last_error()), hwnd
        return None, hwnd

    def _foreground(x: int, y: int, button: str, double: bool, _root: int | None) -> tuple[str | None, int | None]:
        *_, ev_down, ev_up = _BUTTONS[button]
        saved = wintypes.POINT()
        if not GetCursorPos(byref(saved)):
            return _winerr(ctypes.get_last_error()), None
        if not SetCursorPos(x, y):
            return _winerr(ctypes.get_last_error()), None
        flags = [ev_down, ev_up] * (2 if double else 1)
        inputs = (INPUT * len(flags))(*(INPUT(INPUT_MOUSE, MOUSEINPUT(dwFlags=f)) for f in flags))
        try:
            sent = SendInput(len(flags), inputs, ctypes.sizeof(INPUT))
            err = ctypes.get_last_error()
            time.sleep(FOREGROUND_SETTLE_S)
        finally:
            SetCursorPos(saved.x, saved.y)
        return (None if sent == len(flags) else _winerr(err)), None


class WindowsInputBackend(InputBackend):
    """Windows input backend using Win32 API."""

    def __init__(self):
        if sys.platform != "win32":
            raise RuntimeError("WindowsInputBackend is only supported on Windows")

    def click(self, target: Any, abort: threading.Event | None = None,
              check_effect: bool = False, **kwargs) -> Any:
        from ...clicker import ClickResult, Mode

        if abort and abort.is_set():
            mode_val = getattr(target, "mode", kwargs.get("mode", Mode.BACKGROUND))
            mode = Mode(mode_val) if isinstance(mode_val, str) else mode_val
            return ClickResult(False, mode, CANCELLED)

        # Unpack target if it is a Target instance
        if hasattr(target, "resolve"):
            pos = target.resolve()
            hwnd = target.find_window()
            mode = Mode(target.mode)
            button = target.button
            double = (target.click_type == "double")
            if pos is None:
                return ClickResult(False, mode, f"window '{target.window_title}' ({target.window_class}) not found")
            if hwnd and IsIconic(hwnd):
                return ClickResult(False, mode, "target window is minimized")
            x, y = pos
        elif isinstance(target, (tuple, list)) and len(target) >= 2:
            x, y = int(target[0]), int(target[1])
            mode = Mode(kwargs.get("mode", Mode.BACKGROUND))
            button = kwargs.get("button", "left")
            double = kwargs.get("double", False)
            hwnd = kwargs.get("hwnd", None)
        else:
            x = getattr(target, "x", kwargs.get("x", 0))
            y = getattr(target, "y", kwargs.get("y", 0))
            mode = Mode(getattr(target, "mode", kwargs.get("mode", Mode.BACKGROUND)))
            button = getattr(target, "button", kwargs.get("button", "left"))
            double = (getattr(target, "click_type", "single") == "double") or kwargs.get("double", False)
            hwnd = kwargs.get("hwnd", None)

        busy_timeout = kwargs.get("busy_timeout", 1.0)
        if button not in _BUTTONS:
            raise ValueError(f"button must be one of {list(_BUTTONS)}, got {button!r}")

        if not _wait_for_user_release(busy_timeout, abort):
            if abort and abort.is_set():
                return ClickResult(False, mode, CANCELLED)
            log.warning("click at (%d, %d) skipped: user is holding a mouse button", x, y)
            return ClickResult(False, mode, "user is holding a mouse button")

        probe = (hwnd or _top_window_at(x, y)) if check_effect else None
        before = _static_snapshot(probe) if probe else None

        with _click_lock:
            if abort and abort.is_set():
                return ClickResult(False, mode, CANCELLED)
            t0 = time.monotonic()
            try:
                error, ret_target = (_background if mode is Mode.BACKGROUND else _foreground)(x, y, button, double, hwnd)
            except OSError as e:
                error, ret_target = str(e), None
            elapsed = (time.monotonic() - t0) * 1000

        result = ClickResult(error is None, mode, error, ret_target, elapsed)
        if error:
            log.error("%s click at (%d, %d) failed: %s", mode.value, x, y, error)
        elif before is not None:
            time.sleep(EFFECT_SETTLE_S)
            after = _snapshot(probe)
            result.changed = None if after is None else after != before
            log.info("%s click at (%d, %d): window %s", mode.value, x, y,
                     {True: "changed", False: "unchanged", None: "not capturable"}[result.changed])
        return result

    def get_cursor_pos(self) -> tuple[int, int]:
        return _cursor_pos()

    def window_at(self, x: int, y: int, root: Any = None) -> Any:
        return _window_at(x, y, root)

    def window_rect(self, handle: Any) -> Rect | None:
        r = _window_rect(handle)
        if r is None:
            return None
        return Rect(left=r.left, top=r.top, right=r.right, bottom=r.bottom)

    def is_mouse_button_held(self) -> bool:
        return any(GetAsyncKeyState(vk) & 0x8000 for vk in _VK_MOUSE)

    def supports_background_mode(self) -> bool:
        return True

    def capabilities(self) -> dict[str, Any]:
        return {
            "platform_name": "Windows",
            "session_type": "win32",
            "background_mode": True,
            "window_tracking": True,
            "cursor_positioning": True,
            "global_hotkeys": True,
            "elevation_detection": True,
            "pixel_inspection": True,
            "unavailable_features": [],
        }

    def top_window_at(self, x: int, y: int) -> Any:
        return _top_window_at(x, y)

    def window_text(self, handle: Any) -> str:
        return _window_text(handle)

    def window_class(self, handle: Any) -> str:
        return _window_class(handle)

    def find_window(self, class_name: str | None, title: str | None = None) -> Any:
        return FindWindowW(class_name, title) or (FindWindowW(class_name, None) if title else None)

    def is_window_minimized(self, handle: Any) -> bool:
        return bool(IsIconic(handle))

    def is_admin(self) -> bool:
        return _is_admin()

    def blocked_by_elevation(self, handle: Any) -> bool:
        return _blocked_by_elevation(handle)

    def snapshot(self, handle: Any) -> bytes | None:
        return _snapshot(handle)

    def set_window_pos(self, handle: Any, *args, **kwargs) -> bool:
        return bool(SetWindowPos(handle, *args, **kwargs))
