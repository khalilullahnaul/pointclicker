"""Hostile config files: load() must never raise, and whatever it returns must be fully valid and usable
(schedulable, displayable, savable). Plus corrupt/empty/huge files and a real disk-full on save."""
import errno
import json
import math
import os
import time
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from pointclicker import targets
from pointclicker.scheduler import Scheduler
from pointclicker.targets import Config, Target

json_scalars = st.one_of(st.none(), st.booleans(), st.integers(-2**70, 2**70), st.text(max_size=8),
                         st.floats(allow_nan=True, allow_infinity=True))
json_values = st.recursive(json_scalars, lambda inner: st.one_of(st.lists(inner, max_size=3),
                                                                  st.dictionaries(st.text(max_size=6), inner, max_size=3)),
                           max_leaves=6)
FIELDS = [f for f in Target.__dataclass_fields__ if f != "id"]
target_dicts = st.dictionaries(st.sampled_from(FIELDS + ["id", "future_field"]),
                               st.one_of(json_values, st.sampled_from(["left", "double", "foreground", "minutes"])),
                               max_size=12)
configs = st.fixed_dictionaries({}, optional={
    "active_profile": json_values, "show_markers": json_values, "minimize_to_tray": json_values,
    "hotkeys": st.one_of(json_values, st.dictionaries(st.text(max_size=12), json_values, max_size=4)),
    "window_geometry": json_values, "theme": json_values, "accent": json_values, "default_mode": json_values,
    "start_minimized": json_values, "launch_at_login": json_values, "tip_shown": json_values,
    "profiles": st.one_of(json_values, st.dictionaries(st.text(max_size=8),
                                                       st.one_of(json_values, st.lists(target_dicts, max_size=4)),
                                                       max_size=3)),
})


def assert_valid(cfg: Config):
    """Every value has the type and range the rest of the app assumes."""
    for name in ("active_profile", "window_geometry", "theme", "accent", "default_mode"):
        assert isinstance(getattr(cfg, name), str), name
    for name in ("show_markers", "minimize_to_tray", "start_minimized", "start_clicking_on_launch",
                 "launch_at_login", "tip_shown", "setup_guide_shown"):
        assert isinstance(getattr(cfg, name), bool), name
    assert isinstance(cfg.hotkeys, dict) and all(isinstance(k, str) and isinstance(v, str)
                                                 for k, v in cfg.hotkeys.items())
    assert isinstance(cfg.profiles, dict) and cfg.profiles
    for name, ts in cfg.profiles.items():
        assert isinstance(name, str) and isinstance(ts, list)
        for t in ts:
            for f in ("x", "y", "rel_x", "rel_y", "repeat"):
                assert type(getattr(t, f)) is int, f  # no bools, floats or huge values sneaking in
                assert abs(getattr(t, f)) < 2**31, f
            for f in ("interval", "start_delay", "jitter_pct"):
                v = getattr(t, f)
                assert type(v) in (int, float) and math.isfinite(v), f
            assert 0 < t.interval_seconds <= targets.MAX_SECONDS and t.start_delay <= targets.MAX_SECONDS
            assert isinstance(t.name, str) and isinstance(t.window_title, str) and isinstance(t.window_class, str)
            assert isinstance(t.enabled, bool) and isinstance(t.id, str) and t.id
    ids = [t.id for ts in cfg.profiles.values() for t in ts]
    assert len(ids) == len(set(ids)), "duplicate target ids"


def assert_usable(cfg: Config, path: Path):
    from pointclicker.ui.widgets import fmt_seconds
    clock = [0.0]
    s = Scheduler(lambda t, a: type("R", (), {"ok": True, "error": None})(), clock=lambda: clock[0], threaded=False)
    s.start(cfg.targets)
    for _ in range(3):
        wait = s.tick()
        for t in cfg.targets:
            fmt_seconds(s.remaining(t.id))
        if wait is None:
            break
        clock[0] += wait
    targets.save(cfg, path)
    assert targets.load(path) == cfg


@given(raw=configs)
def test_any_config_loads_valid_and_usable(raw, tmp_path_factory):
    path = tmp_path_factory.mktemp("cfg") / "config.json"
    path.write_text(json.dumps(raw, allow_nan=True), encoding="utf-8")
    cfg = targets.load(path)
    assert_valid(cfg)
    assert_usable(cfg, path)


@given(raw=json_values)
def test_any_json_document_loads(raw, tmp_path_factory):
    path = tmp_path_factory.mktemp("cfg") / "config.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    assert_valid(targets.load(path))


@pytest.mark.parametrize("content", [b"", b"   \n", b"\x00\xff\xfe garbage \x80", b"{", b'{"profiles": {"a": [',
                                     b"null", b"[]", b'"just a string"', b"\xef\xbb\xbf{}"])
def test_corrupt_files_are_moved_aside_not_overwritten(tmp_path, content):
    path = tmp_path / "config.json"
    path.write_bytes(content)
    cfg = targets.load(path)
    assert_valid(cfg)
    if content.strip(b"\xef\xbb\xbf \n") not in (b"{}",):
        assert (tmp_path / "config.bad.json").read_bytes() == content  # the user's data is kept for recovery


def test_bad_target_is_dropped_but_its_profile_survives(tmp_path):
    path = tmp_path / "config.json"
    good = {"name": "keep me", "x": 10, "y": 20}
    path.write_text(json.dumps({"profiles": {"Main": [good, {"interval": "NaN?"}, {"x": 1.5}],
                                             "Other": [good | {"id": "abc"}]}}))
    cfg = targets.load(path)
    assert_valid(cfg)
    assert [t.name for t in cfg.profiles["Main"]] == ["keep me"]
    assert not (tmp_path / "config.bad.json").exists()  # salvageable files are repaired, not discarded


def test_huge_config_loads_quickly(tmp_path):
    path = tmp_path / "config.json"
    raw = {"profiles": {f"p{i}": [{"name": f"t{j}", "x": j, "y": i, "interval": 0.1 + j}
                                  for j in range(1000)] for i in range(60)}}
    path.write_text(json.dumps(raw))
    assert path.stat().st_size > 3_000_000
    t0 = time.perf_counter()
    cfg = targets.load(path)
    assert time.perf_counter() - t0 < 5
    assert sum(len(v) for v in cfg.profiles.values()) == 60_000


def test_disk_full_when_saving_keeps_the_old_config(tmp_path):
    """A real ENOSPC from the kernel: the temp file is a symlink to /dev/full."""
    if not os.path.exists("/dev/full"):
        pytest.skip("needs /dev/full")
    path = tmp_path / "config.json"
    old = Config(active_profile="Old")
    targets.save(old, path)
    before = path.read_bytes()
    path.with_suffix(".tmp").symlink_to("/dev/full")
    with pytest.raises(OSError) as e:
        targets.save(Config(active_profile="New"), path)
    assert e.value.errno == errno.ENOSPC
    assert path.read_bytes() == before and targets.load(path).active_profile == "Old"


def test_controller_survives_disk_full_and_tells_the_user(tmp_path, monkeypatch):
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from pointclicker.controller import AppController
    from pointclicker.platform import FakeBackend, FakeHotkeyBackend, set_backend, set_hotkey_backend
    set_backend(FakeBackend())
    set_hotkey_backend(FakeHotkeyBackend())
    c = AppController(Config())
    notes = []
    c.notify.connect(lambda *a: notes.append(a))

    def full(*a, **k):
        raise OSError(errno.ENOSPC, "No space left on device")
    monkeypatch.setattr(targets, "save", full)
    c.add_target(Target(name="a"))  # must not raise into Qt's event loop
    assert c.targets[0].name == "a"
    assert any(level == "error" and "space" in msg.lower() for level, _, msg in notes)
    c.shutdown()
