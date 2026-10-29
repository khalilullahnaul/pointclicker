"""One scheduler thread + a heap of next-fire times for all targets.

Timing logic lives in tick(), which the thread calls and tests call directly with a fake clock.
"""
from __future__ import annotations

import heapq
import logging
import random
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from typing import Any

from .targets import Target

log = logging.getLogger(__name__)


class State(Enum):
    STOPPED = "stopped"
    RUNNING = "running"
    PAUSED = "paused"


@dataclass
class Stats:
    clicks: int = 0  # clicks performed this run (cancelled ones excluded), counted toward repeat
    failures: int = 0
    last_ok: bool | None = None
    last_error: str | None = None


class Scheduler:
    def __init__(self, fire: Callable[[Target, threading.Event], Any], clock: Callable[[], float] = time.monotonic,
                 rng: random.Random | None = None, threaded: bool = True,
                 on_click: Callable[[Target], None] | None = None, speed: float = 1.0) -> None:
        """fire(target, abort) performs the click and returns something with .ok and .error (a ClickResult).
        abort is set by pause/stop; fire should give up (ok=False) if it is set before the click happens.
        on_click(target) runs on the scheduler thread after each click's stats are recorded.
        speed: how much faster than real time `clock` runs (soak tests compress hours into minutes); the thread's
        real-time waits are divided by it so they stay in step with the clock."""
        self._fire, self._clock, self._rng, self._on_click = fire, clock, rng or random.Random(), on_click
        self._speed = speed
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self.abort = threading.Event()  # set whenever we are not RUNNING
        self.abort.set()
        self._quit = False
        self._thread = threading.Thread(target=self._loop, name="scheduler", daemon=True) if threaded else None
        self.state = State.STOPPED
        self.stats: dict[str, Stats] = {}
        self._targets: dict[str, Target] = {}
        self._due: dict[str, float] = {}  # target id -> next fire time; the heap mirrors this
        self._heap: list[tuple[float, str]] = []
        self._paused_at = 0.0

    # --- control (any thread) ---

    def start(self, targets: list[Target]) -> None:
        """STOPPED -> RUNNING with fresh countdowns and stats. Resumes if PAUSED."""
        with self._lock:
            if self.state is State.PAUSED:
                self._resume()
            elif self.state is State.STOPPED:
                self.stats, self._due, self._targets = {}, {}, {}
                self._set_state(State.RUNNING)
                self._sync(targets, self._clock())
        if self._thread and not self._thread.is_alive():
            self._thread.start()
        self._wake.set()

    def pause(self) -> None:
        with self._lock:
            if self.state is State.RUNNING:
                self._paused_at = self._clock()
                self._set_state(State.PAUSED)
        self._wake.set()

    def resume(self) -> None:
        with self._lock:
            if self.state is State.PAUSED:
                self._resume()
        self._wake.set()

    def stop(self) -> None:
        with self._lock:
            self._set_state(State.STOPPED)
            self._due, self._heap = {}, []
        self._wake.set()

    def sync(self, targets: list[Target]) -> None:
        """Call after targets are added, removed, enabled or disabled while running or paused."""
        with self._lock:
            if self.state is not State.STOPPED:
                self._sync(targets, self._paused_at if self.state is State.PAUSED else self._clock())
        self._wake.set()

    def shutdown(self) -> None:
        self.stop()
        self._quit = True
        self._wake.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2)

    def remaining(self, target_id: str) -> float | None:
        """Seconds until the target's next click, or None if it is not scheduled."""
        with self._lock:
            due = self._due.get(target_id)
            if due is None:
                return None
            now = self._paused_at if self.state is State.PAUSED else self._clock()
            return max(0.0, due - now)

    # --- timing core ---

    def tick(self) -> float | None:
        """Fire due clicks one at a time. Returns seconds until the next due click (None = nothing scheduled)."""
        while True:
            # State is re-checked before every click, so Pause/Stop take effect between two due clicks.
            with self._lock:
                if self.state is not State.RUNNING:
                    return None
                now = self._clock()
                if not self._heap or self._heap[0][0] > now:
                    return self._heap[0][0] - now if self._heap else None
                due, tid = heapq.heappop(self._heap)
                del self._due[tid]
                t, st = self._targets[tid], self.stats[tid]
                if not t.enabled:
                    continue
                if not t.repeat or st.clicks + 1 < t.repeat:
                    iv = self._interval(t)
                    self._schedule(tid, due + iv if due + iv > now else now + iv)  # no burst after a stall
            self._click(t, st)  # outside the lock so pause/stop/GUI reads never wait on a click

    def _click(self, t: Target, st: Stats) -> None:
        try:
            res = self._fire(t, self.abort)
            ok, err, raised = bool(res.ok), res.error, False
        except Exception as e:  # never let one bad target kill the scheduler thread
            log.exception("click for target %s raised", t.id)
            ok, err, raised = False, str(e), True
        with self._lock:
            # A raise is a failure even if pause/stop raced it: we can't know the click didn't happen.
            if not ok and not raised and self.abort.is_set():
                # Cancelled by pause/stop before it happened: not counted. After a pause it fires on resume.
                if self.state is not State.STOPPED and t.id in self._targets:
                    self._due[t.id] = self._paused_at if self.state is State.PAUSED else self._clock()
                    self._rebuild()
                return
        st.clicks += 1
        st.last_ok, st.last_error = ok, err
        st.failures += not ok
        if self._on_click:
            try:
                self._on_click(t)
            except Exception:
                log.exception("on_click callback raised")

    def _loop(self) -> None:
        while not self._quit:
            self._wake.clear()  # clear before tick: a control call during tick still wakes the wait below
            try:
                timeout = self.tick()
            except Exception:  # a bug here must not silently end all clicking
                log.exception("scheduler tick failed")
                timeout = 1.0
            self._wake.wait(None if timeout is None else timeout / self._speed)

    # --- helpers (lock held) ---

    def _set_state(self, state: State) -> None:
        if state is not self.state:
            log.info("scheduler %s -> %s", self.state.value, state.value)
        self.state = state
        (self.abort.clear if state is State.RUNNING else self.abort.set)()

    def _resume(self) -> None:
        shift = self._clock() - self._paused_at  # countdowns were frozen for this long
        self._due = {i: d + shift for i, d in self._due.items()}
        self._rebuild()
        self._set_state(State.RUNNING)

    def _sync(self, targets: list[Target], now: float) -> None:
        self._targets = {t.id: t for t in targets}
        for t in targets:
            self.stats.setdefault(t.id, Stats())
        done = {t.id for t in targets if t.repeat and self.stats[t.id].clicks >= t.repeat}
        for tid in list(self._due):
            # removed, disabled, or edited to a repeat limit it has already reached: no more clicks
            if tid not in self._targets or not self._targets[tid].enabled or tid in done:
                del self._due[tid]
        for t in targets:
            if t.enabled and t.id not in self._due and t.id not in done:
                self._due[t.id] = now + t.start_delay
        self._rebuild()

    def _schedule(self, tid: str, due: float) -> None:
        self._due[tid] = due
        heapq.heappush(self._heap, (due, tid))

    def _rebuild(self) -> None:
        self._heap = [(d, i) for i, d in self._due.items()]
        heapq.heapify(self._heap)

    def _interval(self, t: Target) -> float:
        j = t.jitter_pct / 100
        return t.interval_seconds * (1 + self._rng.uniform(-j, j))
