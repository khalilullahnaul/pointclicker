"""Build dist/PointClicker.exe (+ dist/PointClicker-Admin.exe with --admin), then check the exe starts.

Run on Windows inside the venv:  python build.py [--admin]
"""
import os
import subprocess
import sys
import time
from pathlib import Path

import PyInstaller.__main__

import platformdirs

ROOT = Path(__file__).resolve().parent
ICON = ROOT / "src" / "pointclicker" / "pointclicker.ico"
if sys.platform == "win32":
    LOG = Path(os.environ.get("APPDATA", platformdirs.user_config_dir("PointClicker", appauthor=False))) / "PointClicker" / "logs" / "pointclicker.log"
else:
    LOG = Path(platformdirs.user_config_dir("pointclicker", appauthor=False)) / "logs" / "pointclicker.log"
COMMON = [
    str(ROOT / "src" / "pointclicker" / "__main__.py"),
    "--onefile", "--windowed", "--noconfirm", "--clean",
    "--paths", str(ROOT / "src"),
    "--icon", str(ICON),
    "--add-data", f"{ICON}{os.pathsep}pointclicker",  # runtime window/tray icon
    "--add-data", f"{ROOT / 'src' / 'pointclicker' / 'ui' / 'icons'}{os.pathsep}pointclicker/ui/icons",
    "--collect-data", "qfluentwidgets",  # its QSS, fonts and icons are package data
    # pynput picks its backend at runtime, so PyInstaller can't see these imports
    "--hidden-import", "pynput.keyboard._win32", "--hidden-import", "pynput.mouse._win32",
]


def launches(exe: Path) -> bool:
    """Start the exe, wait for its startup line in the log, then close it."""
    size = LOG.stat().st_size if LOG.exists() else 0
    proc = subprocess.Popen([str(exe)])
    try:
        for _ in range(60):  # onefile unpacks itself first, allow ~30 s
            time.sleep(0.5)
            if proc.poll() is not None:
                print(f"{exe.name} exited early with code {proc.returncode}; see {LOG}")
                return False
            if LOG.exists() and LOG.stat().st_size > size:
                with LOG.open(encoding="utf-8", errors="replace") as f:
                    f.seek(size)
                    if "PointClicker starting" in f.read():
                        time.sleep(2)  # still alive after the GUI came up?
                        return proc.poll() is None
        print(f"{exe.name}: no startup line in {LOG} after 30 s")
        return False
    finally:
        proc.kill()


if __name__ == "__main__":
    variants = [("PointClicker", [])]
    if "--admin" in sys.argv:  # manifest asks for elevation (UAC prompt on every start), for elevated targets
        variants.append(("PointClicker-Admin", ["--uac-admin"]))
    for name, extra in variants:
        PyInstaller.__main__.run([*COMMON, "--name", name, *extra])
    exe = ROOT / "dist" / "PointClicker.exe"
    ok = launches(exe)
    print(f"\n{exe}: {'launches OK' if ok else 'LAUNCH FAILED'}")
    sys.exit(0 if ok else 1)
