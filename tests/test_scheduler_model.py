"""Model-based scheduler tests (Hypothesis): thousands of random operation sequences on a fake monotonic clock.

The reference model is intentionally different from the implementation: it measures time only while
RUNNING ("active time"), so "paused time never counts" holds by construction. After every step the real
Scheduler must match the model exactly:
  - every target fires exactly when due: never early, never skipped, never late
  - click counts are exact and never exceed the repeat limit
  - countdowns (remaining) match; after stop nothing is scheduled and nothing fires
"""
import itertools
import math
import random
from dataclasses import replace
from types import SimpleNamespace

import pytest
from hypothesis import given
from hypothesis import strategies as st
from hypothesis.stateful import RuleBasedStateMachine, invariant, precondition, rule

from pointclicker.scheduler import Scheduler, State
from pointclicker.targets import Target

TOL = 1e-6
UNITS = {"seconds": 1, "minutes": 60, "hours": 3600}

seconds = st.one_of(st.sampled_from([0.1, 0.25, 1.0, 2.5, 30.0, 60.0, 300.0, 3600.0, 86400.0]),
                    st.floats(min_value=0.1, max_value=86400.0, allow_nan=False, allow_infinity=False))


@st.composite
def intervals(draw):
    """(value, unit) for an interval between 0.1 s and 24 h, in any unit."""
    secs = draw(seconds)
    unit = draw(st.sampled_from([u for u, m in UNITS.items() if secs / m >= 1e-3]))
    return secs / UNITS[unit], unit


def ok():
    return SimpleNamespace(ok=True, error=None)


class SchedulerModel(RuleBasedStateMachine):
    def __init__(self):
        super().__init__()
        self.t = 0.0
        self.fired: list[tuple[float, str]] = []
        self.s = Scheduler(self._fire, clock=lambda: self.t, rng=random.Random(0), threaded=False)
        self.targets: list[Target] = []  # shared with the scheduler, like the controller's list
        # model
        self.state = State.STOPPED
        self.active = 0.0  # time that passed while RUNNING
        self.due: dict[str, float] = {}  # target id -> due, in active time
        self.count: dict[str, int] = {}
        self.expected: list[tuple[float, str]] = []

    def _fire(self, target, abort):
        assert not abort.is_set(), "fire() called while paused/stopped"
        self.fired.append((self.t, target.id))
        return ok()

    # --- model helpers ---

    def _target(self, i):
        return self.targets[i % len(self.targets)]

    def _done(self, t):
        return bool(t.repeat) and self.count.get(t.id, 0) >= t.repeat

    def _model_sync(self):
        """What the scheduler's sync must do: drop removed/disabled/done targets, schedule newly eligible ones."""
        ids = {t.id for t in self.targets}
        for tid in list(self.due):
            t = next((x for x in self.targets if x.id == tid), None)
            if tid not in ids or not t.enabled or self._done(t):
                del self.due[tid]
        if self.state is not State.STOPPED:
            for t in self.targets:
                self.count.setdefault(t.id, 0)
                if t.enabled and t.id not in self.due and not self._done(t):
                    self.due[t.id] = self.active + t.start_delay

    def _changed(self):
        self.s.sync(self.targets)
        self._model_sync()

    # --- operations ---

    @rule(iv=intervals(), delay=st.one_of(st.just(0.0), seconds), repeat=st.integers(0, 6), enabled=st.booleans())
    @precondition(lambda self: len(self.targets) < 8)
    def add(self, iv, delay, repeat, enabled):
        self.targets.append(Target(interval=iv[0], interval_unit=iv[1], start_delay=delay, repeat=repeat,
                                   enabled=enabled))
        self._changed()

    @rule(i=st.integers(0, 99))
    @precondition(lambda self: self.targets)
    def delete(self, i):
        t = self._target(i)
        self.targets.remove(t)
        self.due.pop(t.id, None)
        self._changed()

    @rule(i=st.integers(0, 99), iv=intervals(), repeat=st.integers(0, 6))
    @precondition(lambda self: self.targets)
    def edit(self, i, iv, repeat):
        old = self._target(i)
        self.targets[self.targets.index(old)] = replace(old, interval=iv[0], interval_unit=iv[1], repeat=repeat)
        self._changed()

    @rule(i=st.integers(0, 99), on=st.booleans())
    @precondition(lambda self: self.targets)
    def set_enabled(self, i, on):
        self._target(i).enabled = on
        self._changed()

    @rule()
    def start(self):
        if self.state is State.STOPPED:
            self.count = {t.id: 0 for t in self.targets}
            self.due = {}
            self.state = State.RUNNING
            self._model_sync()
        elif self.state is State.PAUSED:
            self.state = State.RUNNING
        self.s.start(self.targets)

    @rule()
    def pause(self):
        if self.state is State.RUNNING:
            self.state = State.PAUSED
        self.s.pause()

    @rule()
    def resume(self):
        if self.state is State.PAUSED:
            self.state = State.RUNNING
        self.s.resume()

    @rule()
    def stop(self):
        self.state = State.STOPPED
        self.due = {}
        self.s.stop()

    @rule(frac=st.floats(0, 1), jump=st.booleans())
    def advance(self, frac, jump):
        shortest = min((t.interval_seconds for t in self.targets), default=60.0)
        dt = frac * min(400 * shortest, 3 * 86400)  # bounded so one step is at most a few hundred clicks
        if jump:
            dt = round(dt, 3)
        self._model_advance(dt)
        self._impl_advance(dt)

    def _model_advance(self, dt):
        if self.state is State.RUNNING:
            wall0, active0, end = self.t, self.active, self.active + dt
            while self.due:
                tid, due = min(self.due.items(), key=lambda kv: kv[1])
                if due > end + TOL:
                    break
                t = next(x for x in self.targets if x.id == tid)
                self.expected.append((wall0 + (due - active0), tid))
                self.count[tid] += 1
                if self._done(t):
                    del self.due[tid]
                else:
                    self.due[tid] = due + t.interval_seconds
            self.active = end

    def _impl_advance(self, dt):
        """Step time exactly the way the scheduler thread waits."""
        end = self.t + dt
        while (wait := self.s.tick()) is not None and self.t + wait <= end + TOL:
            nxt = self.t + wait
            self.t = nxt if nxt > self.t else math.nextafter(self.t, math.inf)
        self.t = max(self.t, end)

    # --- invariants ---

    @invariant()
    def fires_match_model(self):
        by_target = lambda evs: {tid: [t for t, i in evs if i == tid] for tid in {i for _, i in evs}}  # noqa: E731
        got, want = by_target(self.fired), by_target(self.expected)
        assert set(got) == set(want), f"fired targets {set(got)} != expected {set(want)}"
        for tid, times in want.items():
            assert len(got[tid]) == len(times), f"{tid}: fired {len(got[tid])}x, expected {len(times)}x"
            for g, w in zip(got[tid], times, strict=True):
                assert g >= w - TOL, f"{tid} fired early: at {g}, due {w}"
                assert g <= w + TOL, f"{tid} fired late/skipped: at {g}, due {w}"

    @invariant()
    def counts_exact(self):
        for t in self.targets:
            st_ = self.s.stats.get(t.id)
            clicks = st_.clicks if st_ else 0
            assert clicks == self.count.get(t.id, 0), f"{t.id}: {clicks} clicks, expected {self.count.get(t.id, 0)}"
            if t.repeat:
                assert clicks <= max(t.repeat, self.count.get(t.id, 0))

    @invariant()
    def countdowns_match(self):
        assert self.s.state is self.state
        for t in self.targets:
            left = self.s.remaining(t.id)
            if self.state is State.STOPPED or t.id not in self.due:
                assert left is None, f"{t.id} still scheduled ({left}) but shouldn't be"
            else:
                assert left is not None and abs(left - (self.due[t.id] - self.active)) <= TOL


TestSchedulerModel = SchedulerModel.TestCase


@given(iv=intervals(), jitter=st.floats(0, 99), delay=st.floats(0, 1000), n=st.integers(2, 60), seed=st.integers())
def test_jitter_stays_within_bounds_and_first_click_is_exact(iv, jitter, delay, n, seed):
    clock = [0.0]
    fired = []
    s = Scheduler(lambda t, a: fired.append(clock[0]) or ok(), clock=lambda: clock[0],
                  rng=random.Random(seed), threaded=False)
    t = Target(interval=iv[0], interval_unit=iv[1], jitter_pct=jitter, start_delay=delay, repeat=n)
    s.start([t])
    while (wait := s.tick()) is not None:
        clock[0] += wait
    assert len(fired) == n and fired[0] == pytest.approx(delay, abs=TOL)
    j, base = jitter / 100, t.interval_seconds
    for a, b in itertools.pairwise(fired):
        assert base * (1 - j) - TOL <= b - a <= base * (1 + j) + TOL


@pytest.mark.slow
def test_long_run_has_no_cumulative_drift():
    """24 h at 0.1 s: the 864,000th click is due exactly at 86,400 s (due+interval, never now+interval)."""
    clock = [0.0]
    count = [0]
    s = Scheduler(lambda t, a: count.__setitem__(0, count[0] + 1) or ok(), clock=lambda: clock[0], threaded=False)
    s.start([Target(interval=0.1)])
    while clock[0] < 86_400 - 0.05:
        clock[0] += s.tick()
    assert count[0] == 864_000
    assert s._heap[0][0] == pytest.approx(86_400, abs=1e-4)  # float error only; no per-click drift
