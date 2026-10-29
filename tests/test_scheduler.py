"""Scheduler timing tests with a fake clock. Run: python -m unittest discover tests"""
import itertools
import random
import threading
import unittest
from types import SimpleNamespace

from pointclicker.scheduler import Scheduler, State
from pointclicker.targets import Target


class Sim:
    """Fake clock + recording fire(); advance() steps time exactly the way the scheduler thread would wait."""

    def __init__(self, fail=None, during=None):
        self.t = 0.0
        self.fired: list[tuple[float, str]] = []
        self.fail = fail or {}  # target name -> error message
        self.during = during or {}  # target name -> callable run inside its click, before it lands
        self.s = Scheduler(self.fire, clock=lambda: self.t, rng=random.Random(1), threaded=False)

    def fire(self, target, abort):
        if hook := self.during.pop(target.name, None):
            hook()
        if abort.is_set():  # what clicker.click does when pause/stop lands before the click
            return SimpleNamespace(ok=False, error="cancelled")
        self.fired.append((self.t, target.name))
        err = self.fail.get(target.name)
        return SimpleNamespace(ok=err is None, error=err)

    def advance(self, dt):
        end = self.t + dt
        while (wait := self.s.tick()) is not None and self.t + wait <= end:
            self.t += wait
        self.t = end

    def times(self, name):
        return [t for t, n in self.fired if n == name]


class SchedulerTest(unittest.TestCase):
    def test_independent_intervals(self):
        sim = Sim()
        a = Target(name="a", interval=5, interval_unit="minutes")
        b = Target(name="b", interval=30)
        sim.s.start([a, b])
        sim.advance(600)
        self.assertEqual(sim.times("a"), [0, 300, 600])
        self.assertEqual(sim.times("b"), list(range(0, 601, 30)))

    def test_start_delay_then_interval(self):
        sim = Sim()
        sim.s.start([Target(name="a", interval=10, start_delay=4)])
        sim.advance(30)
        self.assertEqual(sim.times("a"), [4, 14, 24])

    def test_repeat_count(self):
        sim = Sim()
        t = Target(name="a", interval=1, repeat=3)
        sim.s.start([t])
        sim.advance(100)
        self.assertEqual(sim.times("a"), [0, 1, 2])
        self.assertIsNone(sim.s.remaining(t.id))
        self.assertEqual(sim.s.stats[t.id].clicks, 3)

    def test_pause_freezes_and_resume_continues(self):
        sim = Sim()
        t = Target(name="a", interval=3600)
        sim.s.start([t])
        sim.advance(1000)  # fired at 0, next at 3600
        sim.s.pause()
        self.assertEqual(sim.s.remaining(t.id), 2600)
        sim.advance(50_000)
        self.assertEqual(sim.s.remaining(t.id), 2600)
        self.assertEqual(sim.times("a"), [0])
        sim.s.resume()
        sim.advance(2600)
        self.assertEqual(sim.times("a"), [0, 53_600])

    def test_stop_resets_countdowns_and_start_resets_stats(self):
        sim = Sim()
        t = Target(name="a", interval=10, start_delay=5)
        sim.s.start([t])
        sim.advance(17)
        sim.s.stop()
        self.assertEqual(sim.s.state, State.STOPPED)
        self.assertIsNone(sim.s.remaining(t.id))
        self.assertEqual(sim.s.stats[t.id].clicks, 2)  # stats stay visible after stop
        sim.advance(100)
        sim.s.start([t])
        self.assertEqual(sim.s.remaining(t.id), 5)
        self.assertEqual(sim.s.stats[t.id].clicks, 0)

    def test_enable_disable_while_running(self):
        sim = Sim()
        a, b = Target(name="a", interval=10), Target(name="b", interval=10, enabled=False)
        sim.s.start([a, b])
        sim.advance(15)
        a.enabled, b.enabled = False, True
        sim.s.sync([a, b])
        sim.advance(25)  # t = 40
        self.assertEqual(sim.times("a"), [0, 10])
        self.assertEqual(sim.times("b"), [15, 25, 35])

    def test_disable_without_sync_still_skips(self):
        sim = Sim()
        a = Target(name="a", interval=10)
        sim.s.start([a])
        sim.advance(5)
        a.enabled = False
        sim.advance(50)
        self.assertEqual(sim.times("a"), [0])

    def test_jitter_stays_in_bounds(self):
        sim = Sim()
        sim.s.start([Target(name="a", interval=100, jitter_pct=20)])
        sim.advance(100_000)
        gaps = [b - a for a, b in itertools.pairwise(sim.times("a"))]
        self.assertTrue(all(80 <= g <= 120 for g in gaps), gaps)
        self.assertGreater(len(set(gaps)), 10)  # actually random

    def test_no_burst_after_stall(self):
        sim = Sim()
        sim.s.start([Target(name="a", interval=10)])
        sim.s.tick()
        sim.t = 1000  # machine slept: one click now, then normal spacing
        sim.advance(20)
        self.assertEqual(sim.times("a"), [0, 1000, 1010, 1020])

    def test_failures_recorded(self):
        sim = Sim(fail={"a": "target window is gone"})
        t = Target(name="a", interval=10)
        notified = []
        sim.s._on_click = notified.append
        sim.s.start([t])
        sim.advance(25)
        st = sim.s.stats[t.id]
        self.assertEqual((st.clicks, st.failures, st.last_ok, st.last_error), (3, 3, False, "target window is gone"))
        self.assertEqual(notified, [t, t, t])

    def test_fire_exception_does_not_kill_scheduler(self):
        sim = Sim()
        t = Target(name="a", interval=10)
        sim.s._fire = lambda _, __: 1 / 0
        sim.s.start([t])
        sim.advance(25)
        self.assertEqual((sim.s.stats[t.id].clicks, sim.s.stats[t.id].last_ok), (3, False))

    def test_stop_during_click_cancels_other_due_clicks(self):
        sim = Sim()
        a, b = Target(id="1", name="a", interval=10), Target(id="2", name="b", interval=10)  # both due at t=0, a first
        sim.during["a"] = sim.s.stop
        sim.s.start([a, b])
        sim.advance(100)
        self.assertEqual(sim.fired, [])
        self.assertEqual(sim.s.stats[a.id].clicks + sim.s.stats[b.id].clicks, 0)

    def test_pause_during_click_refires_on_resume(self):
        sim = Sim()
        t = Target(name="a", interval=60, repeat=1)
        sim.during["a"] = sim.s.pause  # e.g. F6 pressed while the click waits for the user's mouse button
        sim.s.start([t])
        sim.advance(500)
        self.assertEqual((sim.fired, sim.s.stats[t.id].clicks, sim.s.remaining(t.id)), ([], 0, 0))
        sim.s.resume()
        sim.advance(1)
        self.assertEqual(sim.times("a"), [500])  # repeat=1 still gets its one click
        self.assertEqual(sim.s.stats[t.id].clicks, 1)

    def test_abort_event_tracks_state(self):
        s = Sim().s
        self.assertTrue(s.abort.is_set())
        s.start([])
        self.assertFalse(s.abort.is_set())
        s.pause()
        self.assertTrue(s.abort.is_set())
        s.resume()
        self.assertFalse(s.abort.is_set())
        s.stop()
        self.assertTrue(s.abort.is_set())

    def test_thread_wakes_instantly_on_control(self):
        fired = threading.Event()
        s = Scheduler(lambda t, abort: fired.set() or SimpleNamespace(ok=True, error=None))
        t = Target(name="a", interval=1, interval_unit="hours")
        s.start([t])
        self.assertTrue(fired.wait(1), "first click did not fire")
        s.pause()
        self.assertEqual(s.state, State.PAUSED)
        s.shutdown()  # thread is inside a ~1 h wait; must still exit at once
        self.assertFalse(s._thread.is_alive())


if __name__ == "__main__":
    unittest.main()
