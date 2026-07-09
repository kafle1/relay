#!/usr/bin/env python
"""R.E.L.A.Y. adaptive signal controller — weighted max-pressure with the safety + fairness rules.

One score, three weights:  score(phase) = PCU-demand + w_wait*oldest_wait + emergency_boost
Guards: empty-phase skip · gap-out · min/max-green · max-wait force-serve · yellow+all-red clearance.

Topology-agnostic: give it any set of conflict-free phases (2/3/4-arm, N lanes). It only consumes
per-approach counts, so the same engine drives the sim and real CCTV.

Run `python src/controller.py` for a self-check that asserts the core invariants.
"""
from dataclasses import dataclass, field

# passenger-car-unit weights (Kathmandu mix — motorcycle far below a car)
PCU = {"car": 1.0, "motorcycle": 0.3, "bus": 2.5, "truck": 2.5, "ambulance": 2.0, "bicycle": 0.2}
EMERGENCY_CLASSES = {"ambulance"}


@dataclass
class Timings:
    min_green: float = 5.0       # never flicker / strand a vehicle mid-box
    max_green: float = 45.0      # anti-hog: yield even if still busiest
    yellow: float = 3.0
    all_red: float = 1.5         # clearance — no conflicting greens ever overlap
    gap_time: float = 2.5        # end green early if served lanes stay empty this long
    max_wait: float = 90.0       # hard anti-starvation: force-serve past this
    max_preempt: float = 60.0    # cap on holding green for an emergency (D38: never freeze the junction)
    w_wait: float = 0.4          # aging weight (fairness)
    hysteresis: float = 1.0      # challenger must beat incumbent by this to switch (anti-thrash)
    emergency_boost: float = 1e6 # emergency dominates the score


@dataclass
class Junction:
    approaches: list                       # e.g. ["N","S","E","W"]  (T-junction: ["N","E","W"], etc.)
    phases: dict                           # name -> approaches served together (must be conflict-free)
                                           # e.g. {"NS": ["N","S"], "EW": ["E","W"]}


def four_way():
    return Junction(["N", "S", "E", "W"], {"NS": ["N", "S"], "EW": ["E", "W"]})


def three_way_T():
    # T: through road N-S plus a stem E. Conflict-free phases: N-S through, or E turning.
    return Junction(["N", "S", "E"], {"NS": ["N", "S"], "E": ["E"]})


def two_way():
    # a signalized crossing on one road (e.g. pedestrian/side access) — one vehicle phase.
    return Junction(["N", "S"], {"NS": ["N", "S"]})


class Controller:
    GREEN, YELLOW, ALLRED = "GREEN", "YELLOW", "ALLRED"

    def __init__(self, junction: Junction, timings: Timings = None):
        self.j = junction
        self.t = timings or Timings()
        self.phase = next(iter(junction.phases))     # nominal current phase
        self.target = None                            # chosen at first ALLRED evaluation
        self.stage = self.ALLRED                      # start resting; pick the busiest phase, never flash an empty green
        self.elapsed = self.t.all_red
        self.gap_timer = 0.0
        self.preempt_timer = 0.0
        self.wait = {a: 0.0 for a in junction.approaches}   # time since approach was last green

    # ── helpers ────────────────────────────────────────────────
    def _served(self, phase):
        return set(self.j.phases[phase])

    def _pcu(self, approach, counts):
        """counts[approach] may be a {class:n} dict or a plain number."""
        c = counts.get(approach, 0)
        if isinstance(c, dict):
            return sum(PCU.get(k, 1.0) * n for k, n in c.items())
        return float(c)

    def _has_emergency(self, approach, emergencies):
        return approach in emergencies

    def _demand(self, phase, counts):
        return sum(self._pcu(a, counts) for a in self._served(phase))

    def _score(self, phase, counts, emergencies):
        demand = self._demand(phase, counts)
        oldest = max((self.wait[a] for a in self._served(phase)), default=0.0)
        boost = self.t.emergency_boost if any(self._has_emergency(a, emergencies) for a in self._served(phase)) else 0.0
        return demand + self.t.w_wait * oldest + boost

    def _phase_for_emergency(self, emergencies):
        for name in self.j.phases:
            if any(a in emergencies for a in self._served(name)):
                return name
        return None

    def _phase_for_starved(self, counts):
        # a lane that has REAL vehicles waiting past max_wait → force-serve it.
        # an empty lane can't starve, so it never triggers this (no green for an empty lane, ever).
        starved = [a for a in self.j.approaches
                   if self.wait[a] >= self.t.max_wait and self._pcu(a, counts) > 0]
        if not starved:
            return None
        worst = max(starved, key=lambda a: self.wait[a])
        for name in self.j.phases:
            if worst in self._served(name):
                return name
        return None

    def _best_phase(self, counts, emergencies):
        cands = [p for p in self.j.phases if self._demand(p, counts) > 0]
        if not cands:
            return None                                  # all empty → nothing to serve
        return max(cands, key=lambda p: self._score(p, counts, emergencies))

    # ── main tick ──────────────────────────────────────────────
    def tick(self, counts, emergencies, dt):
        """counts: {approach: {class:n} | number}; emergencies: iterable of approaches with an emergency.
        Returns the signal state dict {approach: 'green'|'yellow'|'red'} plus phase/stage."""
        emergencies = set(emergencies or ())
        served_now = self._served(self.phase) if self.stage == self.GREEN else set()

        # accrue waiting time; a served (green) approach's wait resets
        for a in self.j.approaches:
            self.wait[a] = 0.0 if a in served_now else self.wait[a] + dt

        self.elapsed += dt

        if self.stage == self.GREEN:
            self._green_tick(counts, emergencies, dt)
        elif self.stage == self.YELLOW:
            if self.elapsed >= self.t.yellow:
                self.stage, self.elapsed = self.ALLRED, 0.0
        elif self.stage == self.ALLRED:
            self._allred_tick(counts, emergencies)

        return self.signals()

    def _select_target(self, counts, emergencies):
        return (self._phase_for_emergency(emergencies)
                or self._phase_for_starved(counts)
                or self._best_phase(counts, emergencies))

    def _allred_tick(self, counts, emergencies):
        if self.target is None:
            self.target = self._select_target(counts, emergencies)   # None → keep resting (all approaches empty)
        if self.target is not None and self.elapsed >= self.t.all_red:
            self.phase, self.stage, self.elapsed = self.target, self.GREEN, 0.0
            self.gap_timer = self.preempt_timer = 0.0
            self.target = None

    def _begin_switch(self, target):
        if target is None or target == self.phase:
            return
        self.target = target
        self.stage, self.elapsed = self.YELLOW, 0.0

    def _green_tick(self, counts, emergencies, dt):
        t = self.t
        served = self._served(self.phase)
        served_demand = sum(self._pcu(a, counts) for a in served)
        self.gap_timer = self.gap_timer + dt if served_demand < 0.5 else 0.0
        empty_now = served_demand < 0.5
        best = self._best_phase(counts, emergencies)

        # EMERGENCY preempt (respect min-green unless our lane is already empty; never skip clearance)
        em_phase = self._phase_for_emergency(emergencies)
        if em_phase and em_phase != self.phase and (self.elapsed >= t.min_green or empty_now):
            self._begin_switch(em_phase); return
        if em_phase == self.phase:
            self.preempt_timer += dt                      # holding for an emergency on our own phase
            if self.preempt_timer < t.max_preempt:
                return
        else:
            self.preempt_timer = 0.0

        # EMPTY-PHASE SKIP: green on an empty movement while someone else has demand → yield now.
        # min-green protects vehicles that were served; an empty phase has none to strand.
        if empty_now and best and best != self.phase:
            self._begin_switch(best); return

        if self.elapsed < t.min_green:
            return                                        # min-green is sacred for a phase actually serving traffic

        # hard anti-starvation
        starved = self._phase_for_starved(counts)
        if starved and starved != self.phase:
            self._begin_switch(starved); return

        # gap-out: served lanes drained and someone else wants it
        if self.gap_timer >= t.gap_time and best and best != self.phase:
            self._begin_switch(best); return

        # max-green: yield even if still busiest
        if self.elapsed >= t.max_green and best and best != self.phase:
            self._begin_switch(best); return

        # demand-driven switch with hysteresis (anti-thrash); empty-phase skip is implicit (best has demand>0)
        if best and best != self.phase and \
           self._score(best, counts, emergencies) > self._score(self.phase, counts, emergencies) + t.hysteresis:
            self._begin_switch(best)

    def signals(self):
        served = self._served(self.phase)
        out = {}
        for a in self.j.approaches:
            if a in served and self.stage == self.GREEN:
                out[a] = "green"
            elif a in served and self.stage == self.YELLOW:
                out[a] = "yellow"
            else:
                out[a] = "red"
        return {"signals": out, "phase": self.phase, "stage": self.stage, "elapsed": round(self.elapsed, 2)}


# ───────────────────────── self-check ─────────────────────────
def _run(ctrl, feed, emerg_feed=None, dt=0.5, steps=2000):
    """feed(t)->counts, emerg_feed(t)->emergencies. Returns history of signal states."""
    hist = []
    for i in range(steps):
        counts = feed(i * dt)
        emg = emerg_feed(i * dt) if emerg_feed else ()
        hist.append(ctrl.tick(counts, emg, dt))
    return hist


def demo():
    T = Timings(min_green=5, max_green=30, yellow=3, all_red=1.5, max_wait=60, w_wait=0.4)

    # invariant: never two conflicting greens; never green->green without yellow+all-red between
    c = Controller(four_way(), T)
    prev_phase, prev_stage = c.phase, c.stage
    saw_clearance = True
    def feed_full(_):  # everyone busy
        return {"N": {"car": 5}, "S": {"car": 5}, "E": {"car": 5}, "W": {"car": 5}}
    hist = _run(c, feed_full, steps=400)
    greens = set()
    switches, clean_switches = 0, 0
    last = hist[0]
    for h in hist[1:]:
        # only one phase's approaches ever green at once → conflict-free by construction, assert stage sanity
        if h["phase"] != last["phase"]:
            switches += 1
        # a phase change must be preceded by leaving GREEN via YELLOW/ALLRED
        last = h
    # check no GREEN of new phase directly follows GREEN of old phase
    for a, b in zip(hist, hist[1:]):
        if a["stage"] == "GREEN" and b["stage"] == "GREEN" and a["phase"] != b["phase"]:
            saw_clearance = False
    assert saw_clearance, "VIOLATION: green->green without clearance"
    assert switches > 0, "should alternate phases under full demand"

    # invariant: EMPTY-PHASE SKIP — NS empty, EW busy → NS never green while EW has demand
    c = Controller(four_way(), T)
    def feed_ew_only(_):
        return {"N": {"car": 0}, "S": {"car": 0}, "E": {"car": 6}, "W": {"car": 6}}
    hist = _run(c, feed_ew_only, steps=400)
    ns_green_while_ew_waiting = any(
        h["signals"]["N"] == "green" or h["signals"]["S"] == "green" for h in hist
    )
    assert not ns_green_while_ew_waiting, "VIOLATION: gave green to an empty lane while a full lane waited"

    # invariant: NO STARVATION — E continuously fed, N has 2 cars waiting → N served within max_wait(+slack)
    c = Controller(four_way(), T)
    def feed_starve(_):
        return {"N": {"car": 2}, "S": {"car": 0}, "E": {"car": 8}, "W": {"car": 0}}
    max_n_wait = 0.0
    for i in range(1000):
        c.tick(feed_starve(0), (), 0.5)
        max_n_wait = max(max_n_wait, c.wait["N"])
    assert max_n_wait <= T.max_wait + T.yellow + T.all_red + 6, f"VIOLATION: N starved, waited {max_n_wait:.1f}s"

    # invariant: MIN-GREEN honored — a green phase always lasts >= min_green
    c = Controller(four_way(), T)
    hist = _run(c, feed_full, dt=0.5, steps=600)
    run_len, cur, viol = 0.0, hist[0]["phase"] + hist[0]["stage"], False
    green_durations = []
    dur = 0.0
    for h in hist:
        key = h["phase"] + h["stage"]
        if h["stage"] == "GREEN":
            dur += 0.5
        else:
            if dur > 0:
                green_durations.append(dur); dur = 0.0
    assert all(d >= T.min_green - 1e-6 for d in green_durations), f"VIOLATION: green shorter than min_green: {green_durations}"

    # EMERGENCY preempt — ambulance on N (while EW green) → N gets green within a few seconds
    c = Controller(four_way(), T)
    def feed_amb(_):
        return {"N": {"ambulance": 1}, "S": {}, "E": {"car": 6}, "W": {"car": 6}}
    served_n = False
    for i in range(60):  # 30s
        s = c.tick(feed_amb(0), {"N"}, 0.5)
        if s["signals"]["N"] == "green":
            served_n = True; break
    assert served_n, "VIOLATION: ambulance not served promptly"

    print("controller self-check PASSED:")
    print("  ✓ no green->green without yellow+all-red clearance")
    print("  ✓ empty-phase skip (no green wasted on an empty lane while another waits)")
    print(f"  ✓ no starvation (max N wait {max_n_wait:.1f}s <= {T.max_wait}s + slack)")
    print(f"  ✓ min-green honored (all green runs >= {T.min_green}s: {[round(d,1) for d in green_durations[:6]]}...)")
    print("  ✓ emergency preemption serves the ambulance")


if __name__ == "__main__":
    demo()
