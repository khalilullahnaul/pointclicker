"""Soak test: the whole app (controller + Fluent window + markers + X11 hotkeys) runs 50 targets clicking real
windows on a headless X server, while memory, threads and open files are watched.

    python tests/soak.py --minutes 30             # 30 minutes, real time
    python tests/soak.py --minutes 30 --fast 60   # the same 30-minute schedule compressed 60x (30 s)

Fails (exit 1) on: a click arriving early, any click late or drifting by more than 50 ms of real time, a click
lost or landing in the wrong place, or growth in memory, threads or file handles.
"""
import argparse
import gc
import json
import os
import statistics
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent))
from xserver import ClickCounter, start_xvfb, xvfb_binary  # noqa: E402

MAX_LATE_MS = 50
MAX_RSS_GROWTH_MB = 25


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=float, default=30)
    ap.add_argument("--fast", type=float, default=1, help="time compression factor")
    ap.add_argument("--targets", type=int, default=50)
    ap.add_argument("--windows", type=int, default=6)
    ap.add_argument("--without", default="", help="debugging: comma list of gui,markers,effect to switch off")
    a = ap.parse_args()
    if not xvfb_binary():
        print("no Xvfb: install xvfb or run `make xvfb-local`")
        return 2

    xvfb = start_xvfb("2560x1440")
    os.environ.update(DISPLAY=xvfb.display, XDG_SESSION_TYPE="x11", QT_QPA_PLATFORM="xcb")
    os.environ.pop("WAYLAND_DISPLAY", None)
    patch("pointclicker.targets.config_path", lambda: Path(tempfile.mkdtemp()) / "config.json").start()
    counters = [ClickCounter(xvfb.display, f"360x240+{1000 + (i % 3) * 420}+{60 + (i // 3) * 320}", f"Soak {i}",
                             f"Soak{i}") for i in range(a.windows)]
    try:
        return run(a, counters)
    finally:
        for c in counters:
            c.close()
        xvfb.stop()


def run(a, counters) -> int:
    import psutil
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    app = QApplication([])
    from pointclicker import ui  # noqa: F401
    from pointclicker.controller import AppController
    from pointclicker.scheduler import Scheduler
    from pointclicker.targets import Config, Target
    from pointclicker.ui.style import apply_fonts, apply_theme
    from pointclicker.ui.window import MainWindow

    speed, t_real0 = a.fast, time.monotonic()
    clock = lambda: (time.monotonic() - t_real0) * speed  # noqa: E731  simulated seconds since start

    off = set(filter(None, a.without.split(",")))
    cfg = Config(tip_shown=True, setup_guide_shown=True, show_markers="markers" not in off)
    plan = {}  # target id -> (counter index, (rel_x, rel_y), mode, interval, delay)
    for k in range(a.targets):
        ci = k % len(counters)
        rel = (12 + (k // len(counters)) * 37, 12 + (k * 13) % 200)
        mode = "background" if k % 2 else "foreground"
        interval = 5.0 + (k * 7) % 113  # 5..117 s, mixed so clicks rarely coincide
        delay = 0.5 + k * 0.37
        t = Target(name=f"soak {k}", rel_x=rel[0], rel_y=rel[1], window_title=counters[ci].title,
                   window_class=counters[ci].cls, mode=mode, interval=interval, start_delay=delay)
        cfg.targets.append(t)
        plan[t.id] = (ci, rel, mode, interval, delay)
    apply_fonts(app)
    apply_theme(cfg)
    c = AppController(cfg)
    fired: dict[str, list[float]] = {t.id: [] for t in cfg.targets}
    fire_ms: list[float] = []  # real time each click held the scheduler thread

    if "effect" in off:
        c._effect_checked = type("All", (set,), {"__contains__": lambda self, x: True})()

    def fire(t, abort):
        at, t0 = clock(), time.perf_counter()
        res = c._fire(t, abort)
        fire_ms.append((time.perf_counter() - t0) * 1000)
        if res.ok:
            fired[t.id].append(at)
        return res
    c.scheduler = Scheduler(fire, clock=clock, speed=speed, on_click=lambda t: c._clicked.emit(t.id))
    win = MainWindow(c)
    win.resize(900, 640)
    win.move(0, 0)
    if "gui" not in off:
        win.show()
    c.start_services()
    t_start = clock()  # countdowns begin here, not at process start
    c.start()

    proc = psutil.Process()
    gc_ms: list[float] = []  # every garbage-collection pause: they stop the clicking thread too
    gc_t0 = [0.0]
    gc.callbacks.append(lambda phase, info: gc_t0.__setitem__(0, time.perf_counter()) if phase == "start"
                        else gc_ms.append((time.perf_counter() - gc_t0[0]) * 1000))
    samples = []

    def sample():
        samples.append((time.monotonic() - t_real0, proc.memory_info().rss, proc.num_threads(), proc.num_fds()))
    sample_timer = QTimer(interval=1000, timeout=sample)
    sample_timer.start()
    real_ms = int(a.minutes * 60 / speed * 1000)
    QTimer.singleShot(real_ms, lambda: (c.scheduler.stop(), app.quit()))
    app.exec()
    sample_timer.stop()
    sim_end = clock()
    time.sleep(0.5)  # let the last events reach the click counters

    problems, lateness = [], []
    for tid, times in fired.items():
        ci, rel, mode, interval, delay = plan[tid]
        for k, at in enumerate(times):
            late_ms = (at - (t_start + delay + k * interval)) / speed * 1000
            lateness.append(late_ms)
            if late_ms < -1:
                problems.append(f"{tid} click {k} early by {-late_ms:.1f} ms")
        expected = int((sim_end - t_start - delay) // interval) + 1 if sim_end - t_start >= delay else 0
        if abs(len(times) - expected) > 1:
            problems.append(f"{tid}: {len(times)} clicks, schedule says {expected}")
    worst = max(lateness, default=0)
    if worst > MAX_LATE_MS:
        problems.append(f"worst lateness {worst:.1f} ms > {MAX_LATE_MS} ms")

    by_window = {i: [] for i in range(len(counters))}
    for i, counter in enumerate(counters):
        by_window[i] = counter.presses(settle=0.5)
    for i, presses in by_window.items():
        mine = {plan[tid][1]: (tid, plan[tid][2]) for tid in plan if plan[tid][0] == i}
        sent = sum(len(fired[tid]) for tid, *_ in mine.values())
        if len(presses) != sent:
            problems.append(f"window {i}: {len(presses)} clicks arrived, {sent} were sent")
        for p in presses:
            hit = mine.get((p["x"], p["y"]))
            if hit is None:
                problems.append(f"window {i}: click at unexpected spot ({p['x']}, {p['y']})")
            elif p["synthetic"] != (hit[1] == "background"):
                problems.append(f"window {i}: {hit[1]} click arrived as synthetic={p['synthetic']}")

    warm = [s for s in samples if s[0] >= samples[-1][0] * 0.2] if samples else []
    if len(warm) >= 4:
        # A leak is growth over time; transient pool threads are not. Compare the steady state at the start of
        # the run (after warm-up) with the end.
        q = max(1, len(warm) // 4)
        head, tail = warm[:q], warm[-q:]
        med = lambda rows, i: statistics.median(r[i] for r in rows)  # noqa: E731
        rss_growth = (med(tail, 1) - med(head, 1)) / 2**20
        if rss_growth > MAX_RSS_GROWTH_MB:
            problems.append(f"memory grew {rss_growth:.1f} MB after warm-up")
        if med(tail, 2) > med(head, 2) + 1:
            problems.append(f"threads grew {med(head, 2)} -> {med(tail, 2)}")
        if med(tail, 3) > med(head, 3) + 2:
            problems.append(f"open files grew {med(head, 3)} -> {med(tail, 3)}")
    report = {
        "simulated_minutes": round(sim_end / 60, 2), "real_seconds": round(time.monotonic() - t_real0, 1),
        "clicks": sum(len(v) for v in fired.values()), "worst_late_ms": round(worst, 2),
        "click_ms_p50_p99_max": [round(statistics.median(fire_ms), 2), round(sorted(fire_ms)[int(len(fire_ms) * .99)], 2),
                                 round(max(fire_ms), 2)] if fire_ms else None,
        "median_late_ms": round(statistics.median(lateness), 2) if lateness else None,
        "gc_pauses_count_max_ms": [len(gc_ms), round(max(gc_ms, default=0), 2)],
        "rss_mb": [round(s[1] / 2**20, 1) for s in (warm[0], warm[-1])] if warm else None,
        "threads": sorted({s[2] for s in warm}), "fds": sorted({s[3] for s in warm}),
        "problems": problems[:25], "problem_count": len(problems),
    }
    print(json.dumps(report, indent=2))
    win.quit_app()
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
