#!/usr/bin/env python
"""Reference signal policies the paper compares against, on the same tick interface as the
controller under test: ``tick(counts, emergencies, dt)`` returning the set of green approaches.

  MaxPressure    Varaiya's policy as usually simulated: at every decision point take the phase
                 with the largest queue, subject only to a minimum green and a clearance
                 interval. No fairness bound, no gap-out, no cycle.
  SOTL           Self-organising traffic lights (Gershenson 2005; Cools, Gershenson and
                 D'Hooghe 2013): a red phase accumulates a counter of waiting vehicles times
                 seconds and takes green when the counter passes a threshold, unless the
                 current green is discharging a short platoon.
  Actuated       Conventional vehicle-actuated control: green extends by a passage time on
                 each arrival up to a maximum, ends on gap-out, and phases are served in a
                 fixed sequence, skipping phases with no call.

The fixed-time plans (equal split and Webster) live in ``microsim.py`` and ``relay_bench.py``.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.environ.get("RELAY_REPO", os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))), "src"))
from controller import PCU   # noqa: E402


class _Staged:
    """Green / yellow / all-red machinery shared by the three policies."""

    def __init__(self, junction, yellow=3.0, all_red=1.5, min_green=5.0, max_green=45.0):
        self.j = junction
        self.phases = list(junction.phases)
        self.yellow, self.all_red = yellow, all_red
        self.min_green, self.max_green = min_green, max_green
        self.phase = self.phases[0]
        self.stage, self.elapsed, self.target = "GREEN", 0.0, None

    def _demand(self, p, counts):
        # counts are plain numbers in the point-queue harness and per-class dicts from a camera
        total = 0.0
        for a in self.j.phases[p]:
            c = counts.get(a, 0)
            total += sum(PCU.get(k, 1.0) * n for k, n in c.items()) if isinstance(c, dict) else float(c)
        return total

    def green_set(self):
        return set(self.j.phases[self.phase]) if self.stage == "GREEN" else set()

    def _advance(self, dt, want):
        """Common stage machine: ``want`` is the phase the policy would like next, or None."""
        self.elapsed += dt
        if self.stage == "YELLOW":
            if self.elapsed >= self.yellow:
                self.stage, self.elapsed = "ALLRED", 0.0
        elif self.stage == "ALLRED":
            if self.elapsed >= self.all_red:
                self.phase, self.stage, self.elapsed = self.target, "GREEN", 0.0
        elif want is not None and want != self.phase:
            self.target, self.stage, self.elapsed = want, "YELLOW", 0.0
        return self.green_set()


class MaxPressure(_Staged):
    def tick(self, counts, emergencies, dt):
        want = None
        if self.stage == "GREEN" and self.elapsed >= self.min_green:
            best = max(self.phases, key=lambda p: self._demand(p, counts))
            if self._demand(best, counts) > self._demand(self.phase, counts):
                want = best
        return self._advance(dt, want)


class SOTL(_Staged):
    """Threshold theta in vehicle-seconds; a green shorter than min_green is never cut, and a
    green is kept while fewer than ``mu`` vehicles are approaching (platoon is let through)
    unless the red side's integrated demand has grown past the threshold anyway."""

    def __init__(self, junction, theta=40.0, mu=3, **kw):
        super().__init__(junction, **kw)
        self.theta, self.mu = theta, mu
        self.kappa = {p: 0.0 for p in self.phases}

    def tick(self, counts, emergencies, dt):
        want = None
        for p in self.phases:
            if p == self.phase and self.stage == "GREEN":
                self.kappa[p] = 0.0
            else:
                self.kappa[p] += self._demand(p, counts) * dt
        if self.stage == "GREEN" and self.elapsed >= self.min_green:
            reds = [p for p in self.phases if p != self.phase]
            cand = max(reds, key=lambda p: self.kappa[p])
            served = self._demand(self.phase, counts)
            if self.kappa[cand] >= self.theta and (served == 0 or served >= self.mu or self.elapsed >= self.max_green):
                want = cand
            elif served == 0 and self._demand(cand, counts) > 0:
                want = cand
        return self._advance(dt, want)


class Actuated(_Staged):
    """Passage time extends green after each arrival; the phase ends on gap-out or at
    max_green and the next phase with a call is served in sequence."""

    def __init__(self, junction, passage=3.0, **kw):
        super().__init__(junction, **kw)
        self.passage = passage
        self.since_arrival = 0.0
        self.last_seen = None

    def tick(self, counts, emergencies, dt):
        want = None
        served = self._demand(self.phase, counts)
        if self.stage == "GREEN":
            if self.last_seen is not None and served > self.last_seen - 1e-9 and served > 0:
                self.since_arrival = 0.0          # a vehicle joined (queue did not shrink)
            else:
                self.since_arrival += dt
            self.last_seen = served
            gap_out = served == 0 or self.since_arrival >= self.passage
            if self.elapsed >= self.min_green and (gap_out or self.elapsed >= self.max_green):
                i = self.phases.index(self.phase)
                order = self.phases[i + 1:] + self.phases[:i]
                nxt = next((p for p in order if self._demand(p, counts) > 0), None)
                if nxt is not None:
                    want = nxt
                    self.since_arrival, self.last_seen = 0.0, None
        return self._advance(dt, want)
