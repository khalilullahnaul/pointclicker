"""In-app update check against the latest GitHub release. Runs only when the user asks; nothing is sent but the
request itself."""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass

from . import __version__

REPO = "khalilullahnaul/pointclicker"
LATEST_API = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES_PAGE = f"https://github.com/{REPO}/releases/latest"


@dataclass
class Update:
    version: str
    url: str


def parse_version(text: str) -> tuple[int, int, int]:
    """'v1.2' -> (1, 2, 0). Raises ValueError for anything that isn't a version."""
    m = re.fullmatch(r"v?(\d+)(?:\.(\d+))?(?:\.(\d+))?", text.strip())
    if not m:
        raise ValueError(f"not a version: {text!r}")
    major, minor, patch = (int(g or 0) for g in m.groups())
    return major, minor, patch


def check(current: str = __version__, timeout: float = 10) -> Update | None:
    """The newer release, or None if `current` is the latest. Raises OSError/ValueError if it can't tell."""
    req = urllib.request.Request(LATEST_API, headers={"Accept": "application/vnd.github+json",
                                                      "User-Agent": f"PointClicker/{current}"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            release = json.load(r)
    except urllib.error.HTTPError as e:
        if e.code == 404:  # no release published yet
            return None
        raise
    tag = release["tag_name"]
    if parse_version(tag) > parse_version(current):
        return Update(tag.lstrip("v"), release.get("html_url") or RELEASES_PAGE)
    return None
