"""Shipping concerns: version, update check, and --diagnose output in a windowed exe."""
import io
import json
import sys
import urllib.error
from pathlib import Path

import pytest

from pointclicker import __version__, updates


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


def serve(monkeypatch, tag=None, error=None):
    seen = []

    def urlopen(req, timeout):
        seen.append((req.full_url, req.get_header("User-agent"), timeout))
        if error:
            raise error
        return FakeResponse(json.dumps({"tag_name": tag, "html_url": f"https://example/{tag}"}).encode())
    monkeypatch.setattr(updates.urllib.request, "urlopen", urlopen)
    return seen


def test_version_is_single_sourced():
    import tomllib
    from pathlib import Path
    pyproject = tomllib.loads((Path(__file__).parents[1] / "pyproject.toml").read_text())
    assert "version" in pyproject["project"]["dynamic"]
    assert pyproject["tool"]["setuptools"]["dynamic"]["version"] == {"attr": "pointclicker.__version__"}
    assert updates.parse_version(__version__)


@pytest.mark.parametrize("text,expected", [("v1.2.3", (1, 2, 3)), ("1.2", (1, 2, 0)), ("v10", (10, 0, 0)),
                                           (" 0.1.0 ", (0, 1, 0))])
def test_parse_version(text, expected):
    assert updates.parse_version(text) == expected


@pytest.mark.parametrize("bad", ["", "latest", "v1.2.3.4", "1.x", "v1.2-beta"])
def test_parse_version_rejects_junk(bad):
    with pytest.raises(ValueError):
        updates.parse_version(bad)


def test_newer_release_is_offered(monkeypatch):
    seen = serve(monkeypatch, tag="v0.10.0")
    up = updates.check(current="0.9.1")
    assert (up.version, up.url) == ("0.10.0", "https://example/v0.10.0")  # numeric, not string, comparison
    assert seen == [(updates.LATEST_API, "PointClicker/0.9.1", 10)]


@pytest.mark.parametrize("tag", ["v0.9.1", "v0.9", "v0.2.0"])
def test_same_or_older_release_means_up_to_date(monkeypatch, tag):
    serve(monkeypatch, tag=tag)
    assert updates.check(current="0.9.1") is None


def test_no_release_yet_means_up_to_date(monkeypatch):
    serve(monkeypatch, error=urllib.error.HTTPError(updates.LATEST_API, 404, "Not Found", {}, None))
    assert updates.check() is None


@pytest.mark.parametrize("error", [urllib.error.HTTPError(updates.LATEST_API, 403, "rate limited", {}, None),
                                   urllib.error.URLError("offline"), TimeoutError("timed out")])
def test_network_problems_raise_so_the_ui_can_say_so(monkeypatch, error):
    serve(monkeypatch, error=error)
    with pytest.raises(OSError):
        updates.check()


@pytest.mark.gui
def test_about_page_check_for_updates_button(qtbot, monkeypatch):
    from pointclicker.controller import AppController
    from pointclicker.platform import FakeBackend, FakeHotkeyBackend, set_backend, set_hotkey_backend
    from pointclicker.targets import Config
    from pointclicker.ui import pages
    set_backend(FakeBackend())
    set_hotkey_backend(FakeHotkeyBackend())
    c = AppController(Config())
    page = pages.AboutPage(c)
    qtbot.addWidget(page)
    toasts, opened, asked = [], [], []
    c.notify.connect(lambda *a: toasts.append(a[:2]))
    monkeypatch.setattr(pages.QDesktopServices, "openUrl", lambda url: opened.append(url.toString()))
    monkeypatch.setattr(pages.MessageBox, "exec", lambda self: asked.append(self) or 1)

    for answer, expect in [(None, ("success", "You're up to date")),
                           (OSError("offline"), ("error", "Couldn't check for updates"))]:
        monkeypatch.setattr(updates, "check", lambda a=answer: (_ for _ in ()).throw(a) if a else None)
        page.update_btn.click()
        assert not page.update_btn.isEnabled()  # one check at a time
        qtbot.waitUntil(lambda e=expect: e in toasts)
        assert page.update_btn.isEnabled()
    monkeypatch.setattr(updates, "check", lambda: updates.Update("9.9.9", "https://example/v9.9.9"))
    page.update_btn.click()
    qtbot.waitUntil(lambda: opened == ["https://example/v9.9.9"])
    assert len(asked) == 1
    c.shutdown()


def test_diagnose_output_survives_a_windowed_exe(monkeypatch, tmp_path):
    """No console: output goes to a file. Redirected cp1252 output: unencodable characters don't crash."""
    from pointclicker import diagnose
    monkeypatch.setattr("pointclicker.targets.config_path", lambda: tmp_path / "config.json")
    monkeypatch.setattr(sys, "platform", "linux")  # the no-console fallback, wherever this runs
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)
    diagnose._ensure_output()
    print("expected ≈(1, 2) → done")
    sys.stdout.flush()
    assert "expected ≈(1, 2) → done" in (tmp_path / "logs" / "diagnose.txt").read_text(encoding="utf-8")
    sys.stdout.close()

    raw = io.BytesIO()
    monkeypatch.setattr(sys, "stdout", io.TextIOWrapper(raw, encoding="cp1252"))
    monkeypatch.setattr(sys, "stderr", sys.stdout)
    diagnose._ensure_output()
    print("expected ≈(1, 2)")
    sys.stdout.flush()
    assert raw.getvalue().rstrip() == b"expected ?(1, 2)"


# --- packaging files must agree with the app ---

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))  # build.py
PKG = ROOT / "packaging"


def test_uinput_helper_installs_exactly_what_the_app_checks_for():
    pytest.importorskip("grp")  # the Linux backends are POSIX-only
    from pointclicker.platform.linux import setup
    script = (PKG / "linux" / "setup-uinput.sh").read_text()
    assert f"RULE='{setup.UDEV_RULE}'" in script and f"RULE_FILE={setup.UDEV_RULE_FILE}" in script
    assert "read -r answer" in script  # asks before granting input-group powers


def test_windows_manifest_is_per_monitor_dpi_aware_and_never_elevates():
    import xml.etree.ElementTree as ET
    xml = ET.parse(PKG / "windows" / "pointclicker.manifest").getroot()
    text = {el.tag.rsplit("}", 1)[-1]: (el.text or "").strip() for el in xml.iter()}
    level = next(el.get("level") for el in xml.iter() if el.tag.endswith("requestedExecutionLevel"))
    assert text["dpiAwareness"] == "PerMonitorV2" and text["dpiAware"] == "true/pm" and level == "asInvoker"


def test_exe_version_resource_matches_the_app_version():
    import build
    assert build.version() == __version__
    info = build.version_info("1.2.3")
    assert "filevers=(1, 2, 3, 0)" in info and "StringStruct('ProductVersion', '1.2.3')" in info
    compile(info, "version_info", "eval")  # PyInstaller evaluates it as a Python expression


def test_installer_agrees_with_the_app():
    from pointclicker.controller import RUN_KEY
    iss = (PKG / "windows" / "pointclicker.iss").read_text()
    assert "PrivilegesRequired=lowest" in iss  # per-user, no admin
    assert f'#define RunKey "{RUN_KEY}"' in iss and 'ValueName: "PointClicker"' in iss  # same switch as the app
    # platformdirs puts the Windows config in %LOCALAPPDATA%\PointClicker (not roaming): that's what uninstall offers
    assert "ExpandConstant('{localappdata}\\PointClicker')" in iss
    assert 'Source: "..\\..\\dist\\PointClicker\\*"' in iss  # build.py's Windows output folder


def test_linux_desktop_entry():
    entry = (PKG / "linux" / "pointclicker.desktop").read_text()
    assert "Exec=pointclicker\n" in entry and "Icon=pointclicker\n" in entry and "Terminal=false" in entry
