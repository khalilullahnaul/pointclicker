"""Build PointClicker with PyInstaller as an app folder (--onedir) for this OS, then smoke-test it.

    python build.py            # Windows: dist/PointClicker/PointClicker.exe   Linux: dist/pointclicker/pointclicker
    python build.py --smoke    # only smoke-test an existing build

--onedir, not --onefile: a onefile exe unpacks itself to a temp folder on every start (slow) and that pattern is
what antivirus heuristics flag. The Windows installer, portable zip and release are made by CI
(.github/workflows/ci.yml); Linux packages by `make build-linux` (packaging/linux/build.sh).
"""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src" / "pointclicker"
ICON = SRC / "pointclicker.ico"
WINDOWS = sys.platform == "win32"
NAME = "PointClicker" if WINDOWS else "pointclicker"
APP_DIR = ROOT / "dist" / NAME
EXE = APP_DIR / (NAME + (".exe" if WINDOWS else ""))
BUILD = ROOT / "build"


def version() -> str:
    ns: dict = {}
    exec((SRC / "__init__.py").read_text(encoding="utf-8"), ns)
    return ns["__version__"]


def version_info(v: str) -> str:
    """PyInstaller --version-file: the Details tab of the exe's Properties dialog."""
    nums = tuple(int(x) for x in v.split(".")) + (0,) * (4 - len(v.split(".")))
    strings = {"CompanyName": "PointClicker", "FileDescription": "PointClicker", "FileVersion": v,
               "InternalName": "PointClicker", "LegalCopyright": "© Khalil Ullah Naul",
               "OriginalFilename": "PointClicker.exe", "ProductName": "PointClicker", "ProductVersion": v}
    table = ", ".join(f"StringStruct({k!r}, {s!r})" for k, s in strings.items())
    return (f"VSVersionInfo(ffi=FixedFileInfo(filevers={nums}, prodvers={nums}, mask=0x3f, flags=0x0, OS=0x40004, "
            f"fileType=0x1, subtype=0x0, date=(0, 0)), kids=[StringFileInfo([StringTable('040904B0', [{table}])]), "
            f"VarFileInfo([VarStruct('Translation', [1033, 1200])])])\n")


def pyinstaller_args() -> list[str]:
    args = [str(SRC / "__main__.py"), "--name", NAME, "--onedir", "--windowed", "--noconfirm", "--clean",
            "--distpath", str(ROOT / "dist"), "--workpath", str(BUILD), "--specpath", str(BUILD),
            "--paths", str(ROOT / "src"), "--icon", str(ICON),
            "--add-data", f"{ICON}{os.pathsep}pointclicker",  # runtime window/tray icon
            "--add-data", f"{SRC / 'ui' / 'icons'}{os.pathsep}pointclicker/ui/icons",
            "--collect-data", "qfluentwidgets"]  # its QSS, fonts and icons are package data
    # pynput picks its backend at runtime, so PyInstaller can't see these imports
    backend = "win32" if WINDOWS else "xorg"
    args += ["--hidden-import", f"pynput.keyboard._{backend}", "--hidden-import", f"pynput.mouse._{backend}"]
    if WINDOWS:
        BUILD.mkdir(exist_ok=True)
        (BUILD / "version_info.txt").write_text(version_info(version()), encoding="utf-8")
        args += ["--version-file", str(BUILD / "version_info.txt"),
                 "--manifest", str(ROOT / "packaging" / "windows" / "pointclicker.manifest")]
    return args


def smoke() -> bool:
    """Run the built app's --diagnose. It must get through every check and print its result line: a crash
    (missing module, Qt plugin, DLL...) shows up as a traceback or no result. On Linux it runs in its own Xvfb."""
    env, server = dict(os.environ), None
    if not WINDOWS:
        sys.path.insert(0, str(ROOT / "tests"))
        from xserver import start_xvfb, xvfb_binary
        if not xvfb_binary():
            print("smoke test needs Xvfb: install xvfb or run `make xvfb-local`")
            return False
        server = start_xvfb("1280x800")
        env.pop("WAYLAND_DISPLAY", None)
        env.update(DISPLAY=server.display, XDG_SESSION_TYPE="x11")
    try:
        out = subprocess.run([str(EXE), "--diagnose"], capture_output=True, text=True, encoding="utf-8",
                             errors="replace", timeout=180, env=env)
    finally:
        if server:
            server.stop()
    text = out.stdout + out.stderr
    print(text)
    result = next((line for line in text.splitlines() if line.startswith("Result:")), None)
    ok = result is not None and "Traceback" not in text
    print(f"\nsmoke test: exit code {out.returncode}, {result or 'no result line'} -> {'OK' if ok else 'CRASHED'}")
    return ok


if __name__ == "__main__":
    if "--smoke" not in sys.argv:
        import PyInstaller.__main__
        PyInstaller.__main__.run(pyinstaller_args())
        print(f"\nbuilt {APP_DIR} (version {version()})")
    sys.exit(0 if smoke() else 1)
