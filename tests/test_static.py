"""Static checks: ruff, mypy --strict on the core, and importing every module on Linux never touches Win32."""
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BIN = Path(sys.executable).parent

# Runs in a fresh interpreter: tripwires on everything Win32, then imports every pointclicker module.
IMPORT_ALL = textwrap.dedent("""
    import ctypes, importlib, importlib.abc, pkgutil, sys
    touched = []

    class Tripwire:
        def __init__(self, name): self._name = name
        def __getattr__(self, attr): touched.append(f"{self._name}.{attr}"); raise AttributeError(attr)
        def __call__(self, *a, **k): touched.append(f"{self._name}()"); raise OSError("Win32 touched on Linux")

    for name in ("windll", "oledll", "WinDLL", "OleDLL", "WINFUNCTYPE", "WinError", "FormatError", "GetLastError"):
        setattr(ctypes, name, Tripwire("ctypes." + name))
    # (not msvcrt/_winapi: the stdlib itself probes those inside try/except)
    WIN_MODULES = ("win32api", "win32con", "win32gui", "win32process", "win32ui", "pywintypes", "winreg",
                   "win32com", "pythoncom", "pynput._util.win32")

    class Block(importlib.abc.MetaPathFinder):
        def find_spec(self, fullname, path=None, target=None):
            if fullname in WIN_MODULES or fullname.split(".")[0] in WIN_MODULES:
                touched.append("import " + fullname)
            return None
    sys.meta_path.insert(0, Block())

    import pointclicker
    names = [m.name for m in pkgutil.walk_packages(pointclicker.__path__, "pointclicker.")
             if not m.name.endswith("__main__")]  # __main__ starts the app
    for name in names:
        importlib.import_module(name)
    print(len(names), "modules")
    assert not touched, touched
""")


@pytest.mark.skipif(sys.platform == "win32", reason="Linux-only guarantee")
def test_importing_every_module_never_touches_win32():
    out = subprocess.run([sys.executable, "-c", IMPORT_ALL], cwd=ROOT, capture_output=True, text=True, timeout=120,
                         env={"PATH": "/usr/bin:/bin", "PYTHONPATH": str(ROOT / "src"), "QT_QPA_PLATFORM": "offscreen"})
    assert out.returncode == 0, out.stderr
    assert int(out.stdout.split()[0]) >= 25, out.stdout  # really walked the whole package


def _tool(name):
    return str(BIN / name) if (BIN / name).exists() else shutil.which(name)


def test_ruff():
    out = subprocess.run([_tool("ruff"), "check", "."], cwd=ROOT, capture_output=True, text=True)
    assert out.returncode == 0, out.stdout + out.stderr


def test_mypy_strict_core():
    out = subprocess.run([_tool("mypy")], cwd=ROOT, capture_output=True, text=True, timeout=600)
    assert out.returncode == 0, out.stdout + out.stderr
