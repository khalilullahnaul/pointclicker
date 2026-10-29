"""Shared test setup: Hypothesis profiles, offscreen Qt, and a throwaway config path for every test."""
import gc
import os
import sys
import tempfile
from pathlib import Path

import pytest
from hypothesis import HealthCheck, settings

if sys.platform != "win32":
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")  # tests must never open windows on the user's desktop
# (Windows uses its real desktop: the frameless-window code needs real window handles, and CI has a desktop.)
# ...nor click, grab hotkeys or open connections there. X11 tests start their own Xvfb. (Also: python-xlib shares
# extension event tables between Display objects, so a connection to the desktop breaks a later one to Xvfb.)
for _var in ("DISPLAY", "WAYLAND_DISPLAY", "XDG_SESSION_TYPE"):
    os.environ.pop(_var, None)

settings.register_profile("quick", max_examples=400, stateful_step_count=40, deadline=None,
                          suppress_health_check=[HealthCheck.too_slow])
settings.register_profile("all", max_examples=2500, stateful_step_count=60, deadline=None,
                          suppress_health_check=[HealthCheck.too_slow])
settings.load_profile(os.environ.get("HYPOTHESIS_PROFILE", "quick"))


@pytest.fixture(autouse=True)
def _isolated_config(monkeypatch):
    """No test may read or write the user's real config.json."""
    d = Path(tempfile.mkdtemp(prefix="pointclicker-test-"))
    monkeypatch.setattr("pointclicker.targets.config_path", lambda: d / "config.json")
    yield d


@pytest.fixture(autouse=True)
def _reset_backends():
    yield
    from pointclicker.platform import set_backend, set_hotkey_backend
    set_backend(None)
    set_hotkey_backend(None)
    # Controllers sit in reference cycles (scheduler callbacks). Collect them here, on the GUI thread: left to the
    # cyclic GC they may be destroyed on some later test's worker thread, which segfaults Qt.
    gc.collect()


import sys as _sys  # noqa: E402

_sys.path.insert(0, str(Path(__file__).parent))  # tests can import helpers like xserver.py
