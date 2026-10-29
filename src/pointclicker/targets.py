"""Click targets and their JSON storage with platformdirs and migration."""
from __future__ import annotations

import json
import logging
import math
import os
import shutil
import sys
import threading
import uuid
from dataclasses import asdict, dataclass, field, fields, replace
from pathlib import Path
from typing import Any

import platformdirs

from . import clicker
from .platform import get_backend

log = logging.getLogger(__name__)

_UNIT_SECONDS = {"seconds": 1, "minutes": 60, "hours": 3600}
_CHOICES = {
    "button": tuple(clicker._BUTTONS),
    "click_type": ("single", "double"),
    "mode": tuple(m.value for m in clicker.Mode),
    "interval_unit": tuple(_UNIT_SECONDS),
}


MAX_SECONDS = 30 * 86400  # longest interval / start delay; also keeps every countdown displayable
_COORD_LIMIT = 2**31  # screen coordinates and counts must fit the platform APIs (32-bit ints)


def new_id() -> str:
    return uuid.uuid4().hex[:8]


def _as_int(name: str, v: Any) -> int:
    if isinstance(v, float) and v.is_integer():
        v = int(v)  # 10.0 from a hand-edited file is fine; 10.5 is not
    if type(v) is not int or not -_COORD_LIMIT < v < _COORD_LIMIT:
        raise ValueError(f"{name} must be a whole number, got {v!r}")
    return v


def _as_number(name: str, v: Any) -> float:
    if type(v) not in (int, float) or not math.isfinite(v):
        raise ValueError(f"{name} must be a finite number, got {v!r}")
    return float(v)


@dataclass
class Target:
    id: str = field(default_factory=new_id)
    name: str = ""
    x: int = 0  # screen point, physical pixels
    y: int = 0
    rel_x: int = 0  # offset from the target window's top-left corner
    rel_y: int = 0
    window_title: str = ""
    window_class: str = ""  # empty = plain screen point, no window tracking
    button: str = "left"
    click_type: str = "single"
    mode: str = "background"
    interval: float = 1.0
    interval_unit: str = "seconds"
    start_delay: float = 0.0  # seconds
    repeat: int = 0  # 0 = infinite
    enabled: bool = True
    jitter_pct: float = 0.0  # each interval is randomized by +/- this percent

    def __post_init__(self) -> None:
        # This is the trust boundary for hand-edited and corrupt config files: check types, not just ranges.
        for name in ("id", "name", "window_title", "window_class"):
            if not isinstance(getattr(self, name), str):
                raise ValueError(f"{name} must be text, got {getattr(self, name)!r}")
        if not self.id:
            raise ValueError("id must not be empty")
        for name in ("x", "y", "rel_x", "rel_y", "repeat"):
            setattr(self, name, _as_int(name, getattr(self, name)))
        for name in ("interval", "start_delay", "jitter_pct"):
            setattr(self, name, _as_number(name, getattr(self, name)))
        if not isinstance(self.enabled, bool):
            raise ValueError(f"enabled must be true or false, got {self.enabled!r}")
        for name, allowed in _CHOICES.items():
            if getattr(self, name) not in allowed:
                raise ValueError(f"{name} must be one of {allowed}, got {getattr(self, name)!r}")
        if self.interval <= 0 or self.start_delay < 0 or self.repeat < 0 or not 0 <= self.jitter_pct < 100:
            raise ValueError("interval must be > 0; start_delay and repeat >= 0; jitter_pct in [0, 100)")
        if self.interval_seconds > MAX_SECONDS or self.start_delay > MAX_SECONDS:
            raise ValueError("interval and start delay can be at most 30 days")

    @property
    def interval_seconds(self) -> float:
        return self.interval * _UNIT_SECONDS[self.interval_unit]

    @classmethod
    def from_point(cls, x: int, y: int, **kw: Any) -> Target:
        """Target at screen point (x, y), remembering the top-level window under it."""
        hwnd = clicker.top_window_at(x, y)
        r = clicker.window_rect(hwnd) if hwnd else None
        if r is None:
            return cls(x=x, y=y, **kw)
        return cls(x=x, y=y, rel_x=x - r.left, rel_y=y - r.top, window_title=clicker.window_text(hwnd),
                   window_class=clicker.window_class(hwnd), **kw)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Target:
        names = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in names})  # ignore keys from newer versions

    def resolve(self) -> tuple[int, int] | None:
        """Current screen point, following the window if it moved. None if the window is gone."""
        if not self.window_class:
            return self.x, self.y
        hwnd = self.find_window()
        r = clicker.window_rect(hwnd) if hwnd else None
        return (r.left + self.rel_x, r.top + self.rel_y) if r else None

    def find_window(self) -> Any:
        if not self.window_class:
            return None
        # Class AND title: falling back to any window of the class clicked into another window of the same app
        # (another browser or editor window) when this one closed. A renamed window is "not found"; edit the title.
        return clicker.FindWindowW(self.window_class, self.window_title)

    def click(self, abort: threading.Event | None = None, check_effect: bool = False) -> clicker.ClickResult:
        return get_backend().click(self, abort=abort, check_effect=check_effect)


def resolve_all(ts: list[Target]) -> dict[str, tuple[int, int] | None]:
    """Target.resolve() for many targets at once, looking each window up only once."""
    origins: dict[tuple[str, str], tuple[int, int] | None] = {}
    out: dict[str, tuple[int, int] | None] = {}
    for t in ts:
        if not t.window_class:
            out[t.id] = (t.x, t.y)
            continue
        key = (t.window_class, t.window_title)
        if key not in origins:
            hwnd = t.find_window()
            r = clicker.window_rect(hwnd) if hwnd else None
            origins[key] = (r.left, r.top) if r else None
        o = origins[key]
        out[t.id] = None if o is None else (o[0] + t.rel_x, o[1] + t.rel_y)
    return out


DEFAULT_HOTKEYS = {"start_pause": "F6", "stop": "F7", "emergency_stop": "F8", "pick": "F10"}


@dataclass
class Config:
    active_profile: str = "Default"
    show_markers: bool = True
    minimize_to_tray: bool = True
    hotkeys: dict[str, str] = field(default_factory=dict)  # action -> key combo like "F6" or "Ctrl+Alt+S"
    window_geometry: str = ""  # base64 QWidget.saveGeometry()
    setup_guide_shown: bool = False  # Linux permission guide was shown once
    theme: str = "system"  # system | light | dark
    accent: str = ""  # "#rrggbb", or "" for the OS accent (Windows) / Fluent blue
    default_mode: str = "background"  # mode for newly picked targets (foreground where background is unavailable)
    start_minimized: bool = False
    start_clicking_on_launch: bool = False
    launch_at_login: bool = False
    tip_shown: bool = False  # first-launch "how to pick a point" tip
    profiles: dict[str, list[Target]] = field(default_factory=lambda: {"Default": []})

    def __post_init__(self) -> None:
        self.hotkeys = {**DEFAULT_HOTKEYS, **self.hotkeys}  # new actions get defaults in old configs
        if not self.profiles:
            self.profiles = {"Default": []}
        # cosmetic settings: fall back instead of rejecting the whole file over a typo
        if self.theme not in ("system", "light", "dark"):
            self.theme = "system"
        if self.default_mode not in _CHOICES["mode"]:
            self.default_mode = "background"

    @property
    def targets(self) -> list[Target]:
        return self.profiles.setdefault(self.active_profile, [])


def _migrate_legacy_config(target_path: Path) -> None:
    """Migrate legacy configuration files if target does not exist."""
    if target_path.exists():
        return

    legacy_candidates = [
        Path(os.environ.get("APPDATA", "")) / "PointClicker" / "config.json" if os.environ.get("APPDATA") else None,
        Path.home() / "PointClicker" / "config.json",
        Path.home() / ".config" / "PointClicker" / "config.json",
        Path.home() / ".pointclicker" / "config.json",
    ]

    for candidate in legacy_candidates:
        if candidate and candidate != target_path and candidate.exists():
            try:
                target_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(candidate, target_path)
                log.info("Migrated legacy configuration from %s to %s", candidate, target_path)
                bad_candidate = candidate.with_suffix(".bad.json")
                if bad_candidate.exists():
                    shutil.copy2(bad_candidate, target_path.with_suffix(".bad.json"))
                break
            except Exception as e:
                log.warning("Could not migrate legacy config from %s: %s", candidate, e)


def config_path() -> Path:
    app_name = "PointClicker" if sys.platform == "win32" else "pointclicker"
    target_path = Path(platformdirs.user_config_dir(app_name, appauthor=False)) / "config.json"
    _migrate_legacy_config(target_path)
    return target_path


def _config_from_raw(raw: dict[str, Any]) -> tuple[Config, list[str]]:
    """Salvage everything valid from a parsed config; returns (config, what was dropped)."""
    dropped: list[str] = []
    defaults = Config()
    kwargs = {}
    for f in fields(Config):
        if f.name == "profiles" or f.name not in raw:
            continue
        value, default = raw[f.name], getattr(defaults, f.name)
        if f.name == "hotkeys":
            ok = isinstance(value, dict)
            value = {k: v for k, v in value.items() if isinstance(k, str) and isinstance(v, str)} if ok else {}
        elif type(value) is not type(default):
            dropped.append(f"setting {f.name}={value!r}")
            continue
        kwargs[f.name] = value
    profiles: dict[str, list[Target]] = {}
    seen: set[str] = set()
    raw_profiles = raw.get("profiles")
    for name, items in (raw_profiles.items() if isinstance(raw_profiles, dict) else ()):
        if not isinstance(items, list):
            dropped.append(f"profile {name!r}")
            continue
        profiles[name] = []
        for d in items:
            try:
                t = Target.from_dict(d)
            except (ValueError, TypeError, AttributeError) as e:
                dropped.append(f"target in {name!r}: {e}")
                continue
            if t.id in seen:  # copied by hand: two targets must never share stats and schedules
                t = replace(t, id=new_id())
            seen.add(t.id)
            profiles[name].append(t)
    if raw_profiles is not None and not isinstance(raw_profiles, dict):
        dropped.append("profiles")
    return Config(**kwargs, profiles=profiles or {"Default": []}), dropped


def load(path: Path | None = None) -> Config:
    path = path or config_path()
    try:
        raw = json.loads(path.read_text(encoding="utf-8-sig"))  # -sig: tolerate a BOM from Windows editors
        if not isinstance(raw, dict):
            raise ValueError(f"expected a JSON object, got {type(raw).__name__}")
    except FileNotFoundError:
        return Config()
    except (OSError, ValueError) as e:
        # Never silently overwrite a file we couldn't read: move it aside so the user can recover it.
        bad = path.with_suffix(".bad.json")
        os.replace(path, bad)
        log.error("could not read %s (%s); moved it to %s and started fresh", path, e, bad)
        return Config()
    cfg, dropped = _config_from_raw(raw)
    for what in dropped:
        log.warning("config: ignored invalid %s", what)
    return cfg


def save(cfg: Config, path: Path | None = None) -> None:
    path = path or config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    try:
        tmp.write_text(json.dumps(asdict(cfg), indent=2, allow_nan=False), encoding="utf-8")
        os.replace(tmp, path)  # atomic: a crash or full disk mid-save can't leave a half-written config
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


if __name__ == "__main__":  # self-check: python -m pointclicker.targets
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "config.json"
        assert load(p) == Config()
        cfg = Config(active_profile="Game")
        cfg.targets.append(Target(name="a", x=10, y=20, mode="foreground", interval=5, interval_unit="minutes"))
        cfg.profiles["Other"] = [Target(name="b", repeat=3, enabled=False)]
        save(cfg, p)
        assert load(p) == cfg
        assert Target.from_dict({**asdict(cfg.targets[0]), "future_key": 1}) == cfg.targets[0]
        assert Target(x=5, y=6).resolve() == (5, 6)
        assert Target(interval=2, interval_unit="hours").interval_seconds == 7200
        for bad in ({"button": "side"}, {"interval": 0}, {"repeat": -1}, {"jitter_pct": 100}):
            try:
                Target(**bad)
                raise AssertionError(f"accepted {bad}")
            except ValueError:
                pass
        p.write_text('{"profiles": {"x": [{"mode": "nope"}]}}', encoding="utf-8")
        assert load(p) == Config() and p.with_suffix(".bad.json").exists() and not p.exists()
    print("targets self-check OK")
