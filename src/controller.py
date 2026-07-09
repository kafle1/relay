#!/usr/bin/env python
"""R.E.L.A.Y. adaptive signal controller — weighted max-pressure with the safety + fairness rules.

One score, three weights:  score(phase) = PCU-demand + w_wait*oldest_wait + emergency_boost
Guards: empty-phase skip · gap-out · min/max-green · max-wait force-serve · yellow+all-red clearance.

Topology-agnostic: give it any set of conflict-free phases (2/3/4-arm, N lanes). It only consumes
per-approach counts, so the same engine drives the sim and real CCTV.

Run `python src/controller.py` for a self-check that asserts the core invariants.
"""
from dataclasses import dataclass

# passenger-car-unit weights (Kathmandu mix — motorcycle far below a car)
# single source of truth: perception.py imports this rather than keeping its own copy.
PCU = {"car": 1.0, "motorcycle": 0.3, "bus": 2.5, "truck": 2.5, "ambulance": 2.0, "bicycle": 0.2,
       "autorickshaw": 0.8}


@dataclass
class Timings:
    min_green: float = 5.0       # never flicker / strand a vehicle mid-box
    max_green: float = 45.0      # anti-hog: yield even if still busiest
    yellow: float = 3.0
    all_red: float = 1.5         # clearance — no conflicting greens ever overlap
    gap_time: float = 2.5        # end green early if served lanes stay empty this long
    max_wait: float = 90.0       # hard anti-starvation: force-serve past this
    max_preempt: float = 60.0    # cap on holding green for an emergency (D38: never freeze the junction)
    emergency_hold: float = 2.5  # a detection latches "emergency present" for this long — survives a dropped frame
    w_wait: float = 0.4          # aging weight (fairness)
    hysteresis: float = 1.0      # challenger must beat incumbent by this to switch (anti-thrash)
    emergency_boost: float = 1e6 # emergency dominates the score
    w_ped: float = 0.3           # a waiting pedestrian ≈ a motorcycle in pressure; aging closes the rest
    ped_max_wait: float = 60.0   # hard bound: no pedestrian waits longer than this for a walk window


@dataclass
class Junction:
    approaches: list                       # e.g. ["N","S","E","W"]  (T-junction: ["N","E","W"], etc.)
    phases: dict                           # name -> approaches served together (must be conflict-free)
                                           # e.g. {"NS": ["N","S"], "EW": ["E","W"]}


def four_way():
    return Junction(["N", "S", "E", "W"], {"NS": ["N", "S"], "EW": ["E", "W"]})


def junction_from_dirs(dirs):
    """Build a conflict-free Junction from whichever approaches a camera can see (2/3/4-arm)."""
    dirs = set(dirs)
    phases = {}
    ns = [d for d in ("N", "S") if d in dirs]
    ew = [d for d in ("E", "W") if d in dirs]
    if ns:
        phases["NS" if len(ns) == 2 else ns[0]] = ns
    if ew:
        phases["EW" if len(ew) == 2 else ew[0]] = ew
    if not phases:
        raise ValueError(
            f"junction_from_dirs: no usable approaches in {sorted(dirs) or ['(empty)']} "
            f"— accepted keys are N, S, E, W"
        )
    return Junction(sorted(dirs), phases)


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
        self.em_hold = {a: 0.0 for a in junction.approaches}  # debounce: survives a dropped detection frame

    # ── helpers ────────────────────────────────────────────────
    def _served(self, phase):
        return set(self.j.phases[phase])

    def _pcu(self, approach, counts):
        """counts[approach] may be a {class:n} dict or a plain number."""
        c = counts.get(approach, 0)
        if isinstance(c, dict):
            return sum(PCU.get(k, 1.0) * n for k, n in c.items())
        return float(c)

    def _present(self, approach, counts):
        """Any vehicle at all — PCU weighs road space, not existence: a lone motorcycle (0.3 PCU)
        is not an empty lane and must get its full green window."""
        c = counts.get(approach, 0)
        if isinstance(c, dict):
            return any(n > 0 for n in c.values())
        return c > 0

    def _has_emergency(self, approach, emergencies):
        return approach in emergencies

    def _demand(self, phase, counts):
        return sum(self._pcu(a, counts) for a in self._served(phase))

    # ── pedestrians ────────────────────────────────────────────
    # People crossing arm X walk while X's vehicle signal is red — so any phase that does NOT
    # serve X serves them. Ped input is transponder-style ({arm: {n, wait, crossing}}), like a
    # push-button or ped-detector; the controller never trusts it blindly (_clean_peds).

    def _clean_peds(self, peds):
        if not isinstance(peds, dict):
            return {}
        out = {}
        for a, p in peds.items():
            if a not in self.j.approaches or not isinstance(p, dict):
                continue
            try:
                n = min(max(int(p.get("n", 0)), 0), 200)
                crossing = min(max(int(p.get("crossing", 0)), 0), 200)
                wait = float(p.get("wait", 0.0))
            except (TypeError, ValueError):
                continue
            wait = min(max(wait, 0.0), 900.0) if wait == wait else 0.0   # NaN → 0
            if n or crossing:
                out[a] = {"n": n, "wait": wait, "crossing": crossing}
        return out

    def _ped_demand(self, phase, peds):
        # waiting + still-on-the-zebra people whose arm this phase holds red
        return sum(p["n"] + p["crossing"] for a, p in peds.items() if a not in self._served(phase))

    def _ped_crossing(self, phase, peds):
        # people physically ON a zebra this phase holds red for them
        return sum(p["crossing"] for a, p in peds.items() if a not in self._served(phase))

    def _ped_oldest(self, phase, peds):
        return max((p["wait"] for a, p in peds.items() if a not in self._served(phase) and p["n"]),
                   default=0.0)

    def _phase_for_ped_starved(self, peds):
        # someone has waited past ped_max_wait to cross arm X → force a phase with X red.
        # Single-phase junctions have no such phase (needs a dedicated ped stage) — returns None.
        starved = [a for a, p in peds.items() if p["n"] and p["wait"] >= self.t.ped_max_wait]
        if not starved:
            return None
        worst = max(starved, key=lambda a: peds[a]["wait"])
        cands = [n for n in self.j.phases if worst not in self._served(n)]
        return max(cands, key=lambda n: self._ped_demand(n, peds)) if cands else None

    def _score(self, phase, counts, emergencies, peds=None):
        peds = peds or {}
        demand = self._demand(phase, counts)
        oldest = max((self.wait[a] for a in self._served(phase)), default=0.0)
        boost = self.t.emergency_boost if any(self._has_emergency(a, emergencies) for a in self._served(phase)) else 0.0
        ped = self.t.w_ped * self._ped_demand(phase, peds) + self.t.w_wait * self._ped_oldest(phase, peds)
        return demand + self.t.w_wait * oldest + boost + ped

    def _phase_for_emergency(self, emergencies):
        # simultaneous emergencies on conflicting phases: serve the longest-waiting one,
        # not whichever phase happens to come first in dict order
        cands = [(max((self.wait[a] for a in self._served(n) if a in emergencies), default=0.0), n)
                 for n in self.j.phases if any(a in emergencies for a in self._served(n))]
        return max(cands)[1] if cands else None

    def _phase_for_starved(self, counts, factor=1.0):
        # a lane that has REAL vehicles waiting past max_wait (x factor) → force-serve it.
        # an empty lane can't starve, so it never triggers this (no green for an empty lane, ever).
        starved = [a for a in self.j.approaches
                   if self.wait[a] >= self.t.max_wait * factor and self._pcu(a, counts) > 0]
        if not starved:
            return None
        worst = max(starved, key=lambda a: self.wait[a])
        for name in self.j.phases:
            if worst in self._served(name):
                return name
        return None

    def _best_phase(self, counts, emergencies, peds=None):
        peds = peds or {}
        cands = [p for p in self.j.phases if self._demand(p, counts) > 0 or self._ped_demand(p, peds) > 0]
        if not cands:
            return None                                  # all empty → nothing to serve
        return max(cands, key=lambda p: self._score(p, counts, emergencies, peds))

    # ── main tick ──────────────────────────────────────────────
    def tick(self, counts, emergencies, dt, peds=None):
        """counts: {approach: {class:n} | number}; emergencies: iterable of approaches with an emergency
        detected THIS frame. A hit latches that approach's hold for emergency_hold seconds, decaying by dt
        each tick — a single dropped detection mid-hold can't make the controller abandon the preempt.
        peds (optional): {approach: {"n": waiting, "wait": longest_wait_s, "crossing": on_zebra}} —
        pedestrian demand to CROSS that arm's road (served by any phase holding that arm red).
        Returns the signal state dict {approach: 'green'|'yellow'|'red'} plus phase/stage."""
        peds = self._clean_peds(peds)
        seen = set(emergencies or ())
        for a in self.j.approaches:
            self.em_hold[a] = self.t.emergency_hold if a in seen else max(0.0, self.em_hold[a] - dt)
        emergencies = {a for a in self.j.approaches if self.em_hold[a] > 0}
        served_now = self._served(self.phase) if self.stage == self.GREEN else set()

        # accrue waiting time; a served (green) approach's wait resets
        for a in self.j.approaches:
            self.wait[a] = 0.0 if a in served_now else self.wait[a] + dt

        self.elapsed += dt

        if self.stage == self.GREEN:
            self._green_tick(counts, emergencies, dt, peds)
        elif self.stage == self.YELLOW:
            if self.elapsed >= self.t.yellow:
                self.stage, self.elapsed = self.ALLRED, 0.0
        elif self.stage == self.ALLRED:
            self._allred_tick(counts, emergencies, peds)

        return self.signals()

    def _select_target(self, counts, emergencies, peds=None):
        peds = peds or {}
        return (self._phase_for_emergency(emergencies)
                or self._phase_for_starved(counts)
                or self._phase_for_ped_starved(peds)
                or self._best_phase(counts, emergencies, peds))

    def _allred_tick(self, counts, emergencies, peds=None):
        if self.target is None:
            self.target = self._select_target(counts, emergencies, peds)   # None → keep resting (all approaches empty)
        if self.target is not None and self.elapsed >= self.t.all_red:
            self.phase, self.stage, self.elapsed = self.target, self.GREEN, 0.0
            self.gap_timer = self.preempt_timer = 0.0
            self.target = None

    def _begin_switch(self, target):
        if target is None or target == self.phase:
            return
        self.target = target
        self.stage, self.elapsed = self.YELLOW, 0.0

    def _green_tick(self, counts, emergencies, dt, peds=None):
        t = self.t
        peds = peds or {}
        served = self._served(self.phase)
        # "empty" means no vehicles AT ALL — PCU-weighted demand would flag a lone motorcycle
        # (0.3 PCU) as empty and flash its green away, stranding it forever.
        # A phase whose red arms have people waiting/on the zebra is serving THEM — not empty:
        # gap-out must not cut a walk window while someone is still crossing.
        empty_now = (not any(self._present(a, counts) for a in served)
                     and self._ped_demand(self.phase, peds) == 0)
        self.gap_timer = self.gap_timer + dt if empty_now else 0.0

        # EMERGENCY preempt (respect min-green unless our lane is already empty; never skip clearance)
        em_phase = self._phase_for_emergency(emergencies)
        if em_phase and em_phase != self.phase and (self.elapsed >= t.min_green or empty_now):
            self._begin_switch(em_phase); return
        if em_phase == self.phase:
            self.preempt_timer += dt                      # holding for an emergency on our own phase
            # anti-starvation is bounded, not suspended: a lane waiting 2x max_wait forces service
            # even over an active ambulance hold. Below that bound, ambulance priority wins outright.
            hard_starved = self._phase_for_starved(counts, factor=2.0)
            if hard_starved and hard_starved != self.phase and self.elapsed >= t.min_green:
                self._begin_switch(hard_starved); return
            if self.preempt_timer < t.max_preempt:
                return
            # preempt cap hit (D38: never freeze the junction) — drop the boost so the normal
            # rules below can actually move the green; with it, our own phase always scores best
            emergencies = ()
        else:
            self.preempt_timer = 0.0
        best = self._best_phase(counts, emergencies, peds)

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

        # pedestrian anti-starvation: below vehicle starvation and emergencies, above everything else —
        # a person waiting past ped_max_wait forces the phase that turns their arm red (walk window)
        ped_starved = self._phase_for_ped_starved(peds)
        if ped_starved and ped_starved != self.phase:
            self._begin_switch(ped_starved); return

        # hold the walk while someone is still ON the zebra — score-driven switches must not hand
        # green back to a busy road mid-crossing. Bounded by max_green (never freeze the junction);
        # emergency and vehicle starvation sit above this line and still override.
        if self._ped_crossing(self.phase, peds) > 0 and self.elapsed < t.max_green:
            return

        # gap-out: served lanes drained and someone else wants it
        if self.gap_timer >= t.gap_time and best and best != self.phase:
            self._begin_switch(best); return

        # max-green: yield even if still busiest — to the best OTHER phase with demand
        # (gating on best != phase makes the cap dead whenever we still score highest)
        if self.elapsed >= t.max_green:
            others = [p for p in self.j.phases if p != self.phase
                      and (self._demand(p, counts) > 0 or self._ped_demand(p, peds) > 0)]
            if others:
                self._begin_switch(max(others, key=lambda p: self._score(p, counts, emergencies, peds))); return

        # demand-driven switch with hysteresis (anti-thrash); empty-phase skip is implicit (best has demand>0)
        if best and best != self.phase and \
           self._score(best, counts, emergencies, peds) > self._score(self.phase, counts, emergencies, peds) + t.hysteresis:
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
    saw_clearance = True
    def feed_full(_):  # everyone busy
        return {"N": {"car": 5}, "S": {"car": 5}, "E": {"car": 5}, "W": {"car": 5}}
    hist = _run(c, feed_full, steps=400)
    switches = 0
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
    green_durations = []
    dur = 0.0
    for h in hist:
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

    # EMERGENCY DEBOUNCE — ambulance detected every tick except every 10th (dropout) → hold must not
    # be abandoned mid-preempt just because one frame missed the detection.
    c = Controller(four_way(), T)
    def feed_amb_flicker(_):
        return {"N": {"ambulance": 1}, "S": {}, "E": {"car": 6}, "W": {"car": 6}}
    def emerg_flicker(i):
        return () if i % 10 == 9 else {"N"}
    # first get N to green under the emergency
    for i in range(30):
        c.tick(feed_amb_flicker(0), emerg_flicker(i), 0.5)
    assert c.signals()["signals"]["N"] == "green", "setup: N should be green under sustained emergency"
    abandoned = False
    for i in range(30, 90):                      # continue with a flickering detection
        s = c.tick(feed_amb_flicker(0), emerg_flicker(i), 0.5)
        if s["signals"]["N"] != "green":
            abandoned = True; break
    assert not abandoned, "VIOLATION: a single dropped detection frame abandoned the emergency preempt"

    # LOW-PCU PRESENCE — a lone motorcycle (0.3 PCU) is not an "empty" lane: it must get a full
    # min_green window, not a one-tick flash (empty-phase skip must key on presence, not PCU)
    c = Controller(four_way(), T)
    def feed_moto(_):
        return {"N": {"motorcycle": 1}, "S": {}, "E": {"car": 6}, "W": {"car": 6}}
    hist = _run(c, feed_moto, steps=600)
    ns_runs, dur = [], 0.0
    for h in hist:
        if h["stage"] == "GREEN" and h["phase"] == "NS":
            dur += 0.5
        elif dur:
            ns_runs.append(dur); dur = 0.0
    assert ns_runs, "VIOLATION: lone motorcycle never served"
    assert all(d >= T.min_green - 1e-6 for d in ns_runs), f"VIOLATION: motorcycle green flashed: {ns_runs}"

    # MAX-PREEMPT bound (D38) — a continuous emergency must not freeze the junction past max_preempt
    Tp = Timings(min_green=5, max_green=30, yellow=3, all_red=1.5, max_wait=300, w_wait=0.4, max_preempt=15)
    c = Controller(four_way(), Tp)
    held = 0.0
    for i in range(400):
        s = c.tick({"N": {"ambulance": 1}, "S": {}, "E": {"car": 4}, "W": {"car": 4}}, {"N"}, 0.5)
        held = held + 0.5 if s["signals"]["N"] == "green" else 0.0
        assert held <= Tp.max_preempt + Tp.min_green + 2, f"VIOLATION: emergency froze the junction {held:.1f}s"

    # MAX-GREEN cap — yields even while still the busiest phase
    c = Controller(four_way(), T)
    def feed_hog(_):
        return {"N": {"car": 9}, "S": {"car": 9}, "E": {"car": 2}, "W": {}}
    hist = _run(c, feed_hog, steps=600)
    runs, dur = [], 0.0
    for h in hist:
        if h["stage"] == "GREEN" and h["phase"] == "NS":
            dur += 0.5
        elif dur:
            runs.append(dur); dur = 0.0
    assert all(d <= T.max_green + 1.0 for d in runs), f"VIOLATION: max_green not enforced: {runs}"

    # PEDESTRIAN WAIT bound — heavy N-S flow, empty cross street, one person waiting to cross
    # the N-S road (arm N). Their walk window (EW phase → N red) must open within ped_max_wait+slack.
    c = Controller(four_way(), T)
    ped_wait, walked_at = 0.0, None
    for i in range(400):
        s = c.tick({"N": {"car": 8}, "S": {"car": 8}, "E": {}, "W": {}}, (),
                   0.5, peds={"N": {"n": 1, "wait": ped_wait}})
        if s["phase"] == "EW" and s["stage"] == "GREEN":
            walked_at = ped_wait; break
        ped_wait += 0.5
    slack = T.yellow + T.all_red + T.min_green + 2
    assert walked_at is not None and walked_at <= T.ped_max_wait + slack, \
        f"VIOLATION: pedestrian waited {ped_wait:.1f}s with no walk window"

    # WALK HOLD — while that person is ON the zebra, the busy road must not get green back
    # (bounded by max_green; here the crossing takes ~8s, far below it)
    cut = False
    for j in range(16):
        s = c.tick({"N": {"car": 8}, "S": {"car": 8}, "E": {}, "W": {}}, (),
                   0.5, peds={"N": {"crossing": 1}})
        if not (s["phase"] == "EW" and s["stage"] == "GREEN"):
            cut = True; break
    assert not cut, "VIOLATION: walk window cut while someone was still on the zebra"

    # GARBAGE PED INPUT — junk shapes/values must be ignored, never crash or distort
    c = Controller(four_way(), T)
    for junk in ({"N": {"n": -5, "wait": float("nan")}}, {"X": {"n": 3}}, {"N": "lots"},
                 {"N": {"n": 1e9, "wait": -1}}, "peds", 42):
        c.tick(feed_full(0), (), 0.5, peds=junk)         # must not raise

    print("controller self-check PASSED:")
    print("  ✓ no green->green without yellow+all-red clearance")
    print("  ✓ empty-phase skip (no green wasted on an empty lane while another waits)")
    print(f"  ✓ no starvation (max N wait {max_n_wait:.1f}s <= {T.max_wait}s + slack)")
    print(f"  ✓ min-green honored (all green runs >= {T.min_green}s: {[round(d,1) for d in green_durations[:6]]}...)")
    print("  ✓ emergency preemption serves the ambulance")
    print("  ✓ emergency debounce survives a single dropped detection frame (flickering ambulance)")
    print("  ✓ lone motorcycle gets a full green window (presence, not PCU, decides 'empty')")
    print("  ✓ max_preempt bounds a continuous emergency hold")
    print("  ✓ max_green caps a phase even while it is still the busiest")
    print(f"  ✓ pedestrian wait bounded (walk window at {walked_at:.1f}s <= {T.ped_max_wait}s + slack)")
    print("  ✓ walk window held while someone is on the zebra")
    print("  ✓ garbage pedestrian input ignored (junk shapes, NaN, negatives, out-of-range)")


if __name__ == "__main__":
    demo()
