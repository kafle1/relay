#!/usr/bin/env python
"""Shared benchmark harness for the R.E.L.A.Y. experiments.

What is imported unchanged from the repository under test:
  * ``controller.Controller``   the adaptive policy being evaluated
  * ``controller.Timings``      its parameter set
  * ``microsim.FixedTimer``     the repository's own equal-split fixed-time baseline
  * ``microsim.poisson``        the arrival process
  * ``microsim.SAT``            the saturation discharge rate (veh/s/approach)

What this module adds for the paper:
  * ``QueueSystem``   a point-queue accounting layer with warm-up handling, per-vehicle wait
                      records, queue-length sampling and throughput accounting
  * ``WebsterTimer``  a fixed-time baseline whose cycle length and green splits are computed
                      from Webster's (1958) formulae using the TRUE mean demand. This is an
                      idealised fixed-time plan: it is granted perfect knowledge of the average
                      demand it will face, which no real fixed-time installation has. It exists
                      so that the adaptive policy is not compared against a strawman.
"""
import os
import statistics
import sys
from collections import deque

REPO = os.environ.get("RELAY_REPO", os.getcwd())
if os.path.join(REPO, "src") not in sys.path:
    sys.path.insert(0, os.path.join(REPO, "src"))

from controller import Controller, Timings, Junction, four_way   # noqa: E402,F401
from microsim import FixedTimer, SAT, poisson                    # noqa: E402,F401


def pct(xs, p):
    if not xs:
        return 0.0
    xs = sorted(xs)
    return xs[min(len(xs) - 1, round(p / 100 * (len(xs) - 1)))]


class WebsterTimer:
    """Fixed-time plan from Webster (1958), given the true mean demand.

    Cycle length          C = (1.5 L + 5) / (1 - Y)
    Effective green       g_i = (C - L) y_i / Y
    with lost time        L = n_phases * (yellow + all_red)
    and critical ratios   y_i = max_{a in phase i} lambda_a / s.

    The cycle is clamped to [C_min, C_max] and every green to at least ``min_green``; when Y >= 1
    the junction is oversaturated and no finite Webster cycle exists, so the cycle is set to
    C_max and greens are split in proportion to y_i, which is the usual practical fallback.
    """

    def __init__(self, junction, lam, yellow=3.0, all_red=1.5, sat=SAT,
                 min_green=5.0, c_min=30.0, c_max=120.0):
        self.j = junction
        self.phases = list(junction.phases)
        L = len(self.phases) * (yellow + all_red)
        y = []
        for p in self.phases:
            y.append(max(lam.get(a, 0.0) for a in junction.phases[p]) / sat)
        Y = sum(y)
        if Y >= 0.95:                      # oversaturated (or nearly): Webster's C diverges
            C = c_max
        else:
            C = (1.5 * L + 5.0) / (1.0 - Y)
            C = min(max(C, c_min), c_max)
        total_green = max(C - L, len(self.phases) * min_green)
        g = [max(min_green, total_green * (yi / Y if Y > 0 else 1.0 / len(y))) for yi in y]
        self.cycle = sum(g) + L
        self.greens = g
        self.durs = {"YELLOW": yellow, "ALLRED": all_red}
        self.i, self.stage, self.t = 0, "GREEN", 0.0
        self.critical_ratios = y
        self.degree_of_saturation = Y

    def _limit(self):
        return self.greens[self.i] if self.stage == "GREEN" else self.durs[self.stage]

    def tick(self, counts, emergencies, dt):
        self.t += dt
        if self.t >= self._limit():
            self.t = 0.0
            self.stage = {"GREEN": "YELLOW", "YELLOW": "ALLRED", "ALLRED": "GREEN"}[self.stage]
            if self.stage == "GREEN":
                self.i = (self.i + 1) % len(self.phases)
        return self.green_set()

    def green_set(self):
        return set(self.j.phases[self.phases[self.i]]) if self.stage == "GREEN" else set()

    def plan(self):
        return {"cycle_s": round(self.cycle, 2),
                "greens_s": {p: round(g, 2) for p, g in zip(self.phases, self.greens)},
                "critical_ratios": {p: round(y, 4) for p, y in zip(self.phases, self.critical_ratios)},
                "Y": round(self.degree_of_saturation, 4)}


class QueueSystem:
    """Point (vertical) queues for one signalised junction.

    Discharge follows the repository's model: an approach with green releases SAT vehicles per
    second, accumulated as a fractional carry so a green shorter than one saturation headway does
    not release a vehicle, and unused capacity is not banked while the queue is empty.
    """

    def __init__(self, approaches, warmup=0.0):
        self.q = {a: 0 for a in approaches}
        self.carry = {a: 0.0 for a in approaches}
        self.stamps = {a: deque() for a in approaches}
        self.waits = []
        self.served = 0
        self.q_samples = []
        self.warmup = warmup
        self._final = False

    def arrive(self, a, n, t_now):
        self.q[a] += n
        for _ in range(n):
            self.stamps[a].append(t_now)

    def discharge(self, green, t_now, dt, record=True):
        for a in self.q:
            if self.q[a] == 0:
                self.carry[a] = 0.0
                continue
            if a not in green:
                continue
            self.carry[a] += SAT * dt
            while self.carry[a] >= 1 and self.q[a] > 0:
                self.q[a] -= 1
                self.carry[a] -= 1
                arrived = self.stamps[a].popleft()
                if record:
                    self.served += 1
                    if arrived >= self.warmup:
                        self.waits.append(t_now - arrived)

    def sample(self):
        self.q_samples.append(sum(self.q.values()))

    def metrics(self, end):
        if not self._final:                  # residual queues count with their wait so far
            self._final = True
            self.residual = 0
            for dq in self.stamps.values():
                for t in dq:
                    if t >= self.warmup:
                        self.waits.append(end - t)
                    self.residual += 1
        measured = end - self.warmup
        return {
            "mean_wait": statistics.fmean(self.waits) if self.waits else 0.0,
            "p50_wait": pct(self.waits, 50),
            "p95_wait": pct(self.waits, 95),
            "max_wait": max(self.waits) if self.waits else 0.0,
            "mean_queue": statistics.fmean(self.q_samples) if self.q_samples else 0.0,
            "max_queue": max(self.q_samples) if self.q_samples else 0,
            "throughput_vph": self.served / measured * 3600.0 if measured > 0 else 0.0,
            "residual_queue": self.residual,
            "n_vehicles": len(self.waits),
        }
