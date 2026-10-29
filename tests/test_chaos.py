"""Concurrency chaos on the real threaded scheduler and the real clock."""
import random
import statistics
import threading
import time
from types import SimpleNamespace

import pytest

from pointclicker.scheduler import Scheduler, State
from pointclicker.targets import Config, Target

pytestmark = pytest.mark.chaos


class Recorder:
    """fire() for a real Scheduler: records (start, landed, target id); honors abort like the real backends."""

    def __init__(self, delay=0.0, raise_rate=0.0, seed=0):
        self.lock = threading.Lock()
        self.landed: list[tuple[float, float, str]] = []
        self.cancelled = 0
        self.raised = 0
        self.delay, self.raise_rate, self.rng = delay, raise_rate, random.Random(seed)
        self.started_hook = None

    def __call__(self, t, abort):
        start = time.monotonic()
        if self.started_hook:
            self.started_hook(t)
        if self.delay:
            time.sleep(self.delay)
        with self.lock:
            if self.raise_rate and self.rng.random() < self.raise_rate:
                self.raised += 1
                raise self.rng.choice([RuntimeError("backend exploded"), OSError(5, "Access is denied"),
                                       ValueError("bad coordinates"), KeyError("hwnd")])
            if abort is not None and abort.is_set():
                self.cancelled += 1
                return SimpleNamespace(ok=False, error="cancelled")
            self.landed.append((start, time.monotonic(), t.id))
            return SimpleNamespace(ok=True, error=None)


def wait_until(pred, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.005)
    return pred()


def test_pause_resume_spam_from_many_threads_then_stop_really_stops():
    rec = Recorder()
    s = Scheduler(rec)
    ts = [Target(interval=0.01 + i * 0.003) for i in range(20)]
    s.start(ts)
    errors = []

    def spam(seed):
        rng = random.Random(seed)
        try:
            n = 0
            t_end = time.monotonic() + 2.0
            while time.monotonic() < t_end:
                rng.choice([s.pause, s.resume, s.pause, s.resume, lambda: s.start(ts)])()
                n += 1
                time.sleep(0.0005)
            assert n > 1000  # > 500 ops/s per thread, 8 threads
        except Exception as e:  # noqa: BLE001 - surfaced below
            errors.append(e)

    threads = [threading.Thread(target=spam, args=(i,)) for i in range(8)]
    for th in threads:
        th.start()
    for th in threads:
        th.join(10)
    assert not errors and not any(th.is_alive() for th in threads)
    s.resume()
    assert wait_until(lambda: len(rec.landed) > 50)
    s.stop()
    stopped_at = time.monotonic()
    time.sleep(0.3)
    late = [x for x in rec.landed if x[0] > stopped_at]
    assert not late, f"{len(late)} clicks started after stop() returned"
    assert s.state is State.STOPPED and all(s.remaining(t.id) is None for t in ts)
    assert s._thread.is_alive(), "scheduler thread died"
    s.start(ts)  # still works after the storm
    assert wait_until(lambda: any(x[0] > stopped_at + 0.3 for x in rec.landed))
    s.shutdown()
    assert not s._thread.is_alive()
    stopped_at = time.monotonic()
    time.sleep(0.1)
    assert not [x for x in rec.landed if x[0] > stopped_at]


def test_no_click_while_paused_under_contention():
    """Pause must win against a click that is about to start: no click may *start* after pause() returned."""
    rec = Recorder()
    s = Scheduler(rec)
    ts = [Target(interval=0.005) for _ in range(10)]
    s.start(ts)
    for _ in range(200):
        s.pause()
        paused_at = time.monotonic()
        time.sleep(0.002)
        assert not [x for x in rec.landed if x[0] > paused_at], "clicked while paused"
        s.resume()
        time.sleep(0.002)
    s.shutdown()


def test_hotkey_spam_keeps_controller_consistent(qapp):
    from pointclicker.controller import AppController
    from pointclicker.platform import FakeBackend, FakeHotkeyBackend, set_backend, set_hotkey_backend
    hk = FakeHotkeyBackend()
    set_backend(FakeBackend())
    set_hotkey_backend(hk)
    cfg = Config()
    cfg.targets.extend(Target(name=f"t{i}", interval=0.05) for i in range(5))
    c = AppController(cfg)
    c.start_services()
    threads = [threading.Thread(target=lambda: [hk.trigger("F6") for _ in range(500)]) for _ in range(6)]
    for th in threads:
        th.start()
    end = time.monotonic() + 10
    while any(th.is_alive() for th in threads) and time.monotonic() < end:
        qapp.processEvents()
    for _ in range(200):
        qapp.processEvents()
    assert c.state in (State.RUNNING, State.PAUSED) and c.state is c.scheduler.state
    hk.trigger("F7")
    for _ in range(50):
        qapp.processEvents()
    assert c.state is State.STOPPED
    c.shutdown()


def test_delete_target_while_its_click_is_in_flight(qapp):
    from pointclicker.controller import AppController
    from pointclicker.platform import FakeBackend, FakeHotkeyBackend, set_backend, set_hotkey_backend

    class Blocking(FakeBackend):
        def __init__(self):
            super().__init__()
            self.entered, self.release = threading.Event(), threading.Event()

        def click(self, target, abort=None, check_effect=False, **kw):
            if target.name == "victim":
                self.entered.set()
                self.release.wait(5)
            return super().click(target, abort=abort, check_effect=check_effect, **kw)

    fake = Blocking()
    set_backend(fake)
    set_hotkey_backend(FakeHotkeyBackend())
    cfg = Config()
    victim, other = Target(name="victim", interval=0.05), Target(name="other", interval=0.05)
    cfg.targets.extend([victim, other])
    c = AppController(cfg)
    c.start()
    assert fake.entered.wait(5)
    c.delete_target(victim.id)  # while its click is still running on the scheduler thread
    fake.release.set()
    deadline = time.monotonic() + 0.6
    while time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.01)
    n_victim = sum(1 for k in fake.clicks if k.target.name == "victim")
    assert n_victim <= 1, "deleted target kept clicking"
    assert sum(1 for k in fake.clicks if k.target.name == "other") >= 5
    assert c.find(victim.id) is None and c.remaining(victim.id) is None
    c.shutdown()


def test_backend_raising_randomly_never_kills_the_scheduler():
    rec = Recorder(raise_rate=0.3, seed=7)
    s = Scheduler(rec)
    ts = [Target(interval=0.01) for _ in range(10)]
    s.start(ts)
    time.sleep(1.5)
    s.stop()
    time.sleep(0.05)
    assert s._thread.is_alive()
    assert rec.raised > 50 and len(rec.landed) > 100
    total = sum(s.stats[t.id].clicks for t in ts)
    failures = sum(s.stats[t.id].failures for t in ts)
    assert total == len(rec.landed) + rec.raised  # every attempt counted exactly once
    assert failures == rec.raised
    s.shutdown()


@pytest.mark.slow
def test_500_targets_at_100ms_none_early_none_starved():
    rec = Recorder()
    s = Scheduler(rec)
    ts = [Target(interval=0.1) for _ in range(500)]
    t0 = time.monotonic()
    s.start(ts)
    time.sleep(3.0)
    s.shutdown()
    by_id: dict[str, list[float]] = {}
    for start, _, tid in rec.landed:
        by_id.setdefault(tid, []).append(start)
    lateness = []
    for tid, times in by_id.items():
        for k, at in enumerate(times):
            due = t0 + k * 0.1
            assert at >= due - 0.002, f"{tid} click {k} early by {(due - at) * 1000:.1f} ms"
            lateness.append(at - due)
    counts = [len(v) for v in by_id.values()]
    assert len(by_id) == 500 and min(counts) >= 28, f"starved targets: min {min(counts)} clicks in 3 s"
    assert statistics.quantiles(lateness, n=100)[98] < 0.05, "p99 lateness above 50 ms"
