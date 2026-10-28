"""Click targets and their JSON storage with platformdirs and migration."""
from __future__ import annotations

import json
import logging
import os
import shutil
import sys
import uuid
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

import platformdirs

from . import clicker

log = logging.getLogger(__name__)

_UNIT_SECONDS = {"seconds": 1, "minutes": 60, "hours": 3600}
_CHOICES = {
    "button": tuple(clicker._BUTTONS),
    "click_type": ("single", "double"),
    "mode": tuple(m.value for m in clicker.Mode),
    "interval_unit": tuple(_UNIT_SECONDS),
}


def new_id() -> str:
    return uuid.uuid4().hex[:8]


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

    def __post_init__(self):
        for name, allowed in _CHOICES.items():
            if getattr(self, name) not in allowed:
                raise ValueError(f"{name} must be one of {allowed}, got {getattr(self, name)!r}")
        if self.interval <= 0 or self.start_delay < 0 or self.repeat < 0 or not 0 <= self.jitter_pct < 100:
            raise ValueError("interval must be > 0; start_delay and repeat >= 0; jitter_pct in [0, 100)")

    @property
    def interval_seconds(self) -> float:
        return self.interval * _UNIT_SECONDS[self.interval_unit]

    @classmethod
    def from_point(cls, x: int, y: int, **kw) -> Target:
        """Target at screen point (x, y), remembering the top-level window under it."""
        hwnd = clicker.top_window_at(x, y)
        r = clicker.window_rect(hwnd) if hwnd else None
        if r is None:
            return cls(x=x, y=y, **kw)
        return cls(x=x, y=y, rel_x=x - r.left, rel_y=y - r.top, window_title=clicker.window_text(hwnd),
                   window_class=clicker.window_class(hwnd), **kw)

    @classmethod
    def from_dict(cls, d: dict) -> Target:
        names = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in names})  # ignore keys from newer versions

    def resolve(self) -> tuple[int, int] | None:
        """Current screen point, following the window if it moved. None if the window is gone."""
        if not self.window_class:
            return self.x, self.y
        hwnd = self.find_window()
        r = clicker.window_rect(hwnd) if hwnd else None
        return (r.left + self.rel_x, r.top + self.rel_y) if r else None

    def find_window(self) -> int | None:
        if not self.window_class:
            return None
        return clicker.FindWindowW(self.window_class, self.window_title) or clicker.FindWindowW(self.window_class, None)

    def click(self, abort=None, check_effect: bool = False) -> clicker.ClickResult:
        return clicker.get_backend().click(self, abort=abort, check_effect=check_effect)


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

    def __post_init__(self):
        self.hotkeys = {**DEFAULT_HOTKEYS, **self.hotkeys}  # new actions get defaults in old configs
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


def load(path: Path | None = None) -> Config:
    path = path or config_path()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        profiles = {name: [Target.from_dict(d) for d in ts] for name, ts in raw.pop("profiles").items()}
        names = {f.name for f in fields(Config)}
        return Config(**{k: v for k, v in raw.items() if k in names}, profiles=profiles)
    except FileNotFoundError:
        return Config()
    except (ValueError, TypeError, KeyError, AttributeError) as e:
        # Never silently overwrite a file we couldn't read: move it aside so the user can recover it.
        bad = path.with_suffix(".bad.json")
        os.replace(path, bad)
        log.error("could not read %s (%s); moved it to %s and started fresh", path, e, bad)
        return Config()


def save(cfg: Config, path: Path | None = None) -> None:
    path = path or config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(asdict(cfg), indent=2), encoding="utf-8")
    os.replace(tmp, path)  # atomic: a crash mid-save can't leave a half-written config


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
