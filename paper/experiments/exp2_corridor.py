#!/usr/bin/env python
"""Experiment 2: three-junction arterial corridor.

The corridor reproduces the topology, the phase structure, the coordination rule and the timing
constants of the repository's browser network demo (``sim/network.js``): a straight arterial with
a T junction, a four-way junction and a second T junction at 90 m spacing, each running its own
instance of ``src/controller.py``, and each discounting its own pressure by 0.4 times the queue
on the downstream approach it feeds. What is re-implemented here is the *traffic* layer only: the
browser demo animates individual vehicles in 3D, which cannot be driven headlessly, so vehicles
are carried in per-approach point queues with a fixed free-flow link travel time between
junctions. The control logic itself is the repository's, imported unchanged.

Policies:
  relay_coupled     one Controller per junction, downstream coupling active (COUPLE = 0.4)
  relay_isolated    the same, coupling disabled (COUPLE = 0.0). The ablation that isolates how
                    much of the corridor result comes from coordination rather than from local
                    adaptivity.
  fixed_webster     independent Webster fixed-time plan per junction, no progression
  fixed_coord       common-cycle Webster plan with eastbound progression offsets, i.e. a
                    conventional coordinated signal plan and the strongest fixed baseline here

Usage:  RELAY_REPO=/path/to/relay .venv/bin/python experiments/exp2_corridor.py
"""
import csv
import json
import os
import random
import statistics
import sys
from collections import deque

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from relay_bench import (Controller, Junction, SAT, Timings, WebsterTimer, pct,   # noqa: E402
                         poisson)

OUT = os.environ.get("RELAY_OUT", os.path.join(os.path.dirname(os.path.abspath(__file__)), "raw"))
os.makedirs(OUT, exist_ok=True)

# ── corridor definition (from sim/network.js) ───────────────────────────────
JUNCTIONS = {
    "J1": {"approaches": ["W", "E", "S"], "phases": {"WE": ["W", "E"], "S": ["S"]}},
    "J2": {"approaches": ["W", "E", "N", "S"], "phases": {"WE": ["W", "E"], "N": ["N"], "S": ["S"]}},
    "J3": {"approaches": ["W", "E", "N"], "phases": {"WE": ["W", "E"], "N": ["N"]}},
}
ORDER = ["J1", "J2", "J3"]
# arm -> the downstream (junction, arm) its discharge feeds; the coordination link
DOWN = {("J1", "W"): ("J2", "W"), ("J2", "W"): ("J3", "W"),
        ("J2", "E"): ("J1", "E"), ("J3", "E"): ("J2", "E")}
ROUTES = {
    "W_E":  [("J1", "W"), ("J2", "W"), ("J3", "W")],
    "E_W":  [("J3", "E"), ("J2", "E"), ("J1", "E")],
    "S1_E": [("J1", "S"), ("J2", "W"), ("J3", "W")],
    "S2_E": [("J2", "S"), ("J3", "W")],
    "N2_W": [("J2", "N"), ("J1", "E")],
    "N3_W": [("J3", "N"), ("J2", "E"), ("J1", "E")],
}
BASE_LAM = {"W_E": 0.130, "E_W": 0.110, "S1_E": 0.030, "S2_E": 0.030,
            "N2_W": 0.028, "N3_W": 0.028}          # veh/s per route at level 1.0
LEVELS = [0.6, 1.0, 1.4]

SPACING_M = 90.0            # sim/network.js anchor spacing between adjacent junctions
FREE_FLOW_MS = 12.5         # 45 km/h; the link travel time is SPACING_M / FREE_FLOW_MS
LINK_T = SPACING_M / FREE_FLOW_MS
COUPLE = 0.4                # sim/network.js: pressure -= 0.4 * downstream queue
PRESENCE_EPS = 0.05         # coupling discounts pressure magnitude; it must not erase presence
# sim/network.js timing constants
T_NET = dict(min_green=5, max_green=34, yellow=2, all_red=2.5, max_wait=26)

SECONDS = 2400.0
WARMUP = 400.0
DT = 0.5
SEEDS = list(range(20))
POLICIES = ("relay_coupled", "relay_isolated", "fixed_webster", "fixed_coord")
AMB_TIME = 1200.0           # when the emergency vehicle enters the corridor from the west


def arm_lam():
    """Mean arrival rate on every (junction, arm) approach, needed by the Webster plans."""
    out = {}
    for route, stops in ROUTES.items():
        for key in stops:
            out[key] = out.get(key, 0.0) + BASE_LAM[route]
    return out


class Veh:
    __slots__ = ("route", "idx", "t_in", "t_q", "wait", "stops", "amb")

    def __init__(self, route, t_in, amb=False):
        self.route, self.t_in, self.amb = route, t_in, amb
        self.idx, self.wait, self.stops = 0, 0.0, 0
        self.t_q = t_in


class Corridor:
    def __init__(self, policy, lam_route, seed):
        self.policy = policy
        self.lam = lam_route
        self.j = {k: Junction(v["approaches"], dict(v["phases"])) for k, v in JUNCTIONS.items()}
        la = {k: v * (lam_route["W_E"] / BASE_LAM["W_E"]) for k, v in arm_lam().items()}
        self.plans = {}
        if policy.startswith("relay"):
            self.ctrl = {k: Controller(self.j[k], Timings(**T_NET)) for k in ORDER}
            self.couple = COUPLE if policy == "relay_coupled" else 0.0
        else:
            timers = {}
            for k in ORDER:
                lam_k = {a: la[(k, a)] for a in self.j[k].approaches}
                timers[k] = WebsterTimer(self.j[k], lam_k, yellow=T_NET["yellow"],
                                         all_red=T_NET["all_red"], min_green=T_NET["min_green"])
            if policy == "fixed_coord":
                # common cycle = the longest Webster cycle on the corridor, splits rescaled to it,
                # then an eastbound progression offset of one link travel time per junction
                common = max(t.cycle for t in timers.values())
                for n, k in enumerate(ORDER):
                    lam_k = {a: la[(k, a)] for a in self.j[k].approaches}
                    t = WebsterTimer(self.j[k], lam_k, yellow=T_NET["yellow"],
                                     all_red=T_NET["all_red"], min_green=T_NET["min_green"],
                                     c_min=common, c_max=common)
                    for _ in range(int(((common - n * LINK_T) % common) / DT)):
                        t.tick(None, (), DT)         # pre-advance to realise the offset
                    timers[k] = t
            self.ctrl = timers
            self.plans = {k: t.plan() for k, t in timers.items()}
        self.q = {(k, a): deque() for k in ORDER for a in self.j[k].approaches}
        self.carry = {key: 0.0 for key in self.q}
        self.transit = []
        self.qlen_samples = []
        self.delays, self.trav_through, self.stops_hist = [], [], []
        self.exited = 0
        self.amb_log = []
        random.seed(seed)

    def _counts(self, k):
        raw = {a: len(self.q[(k, a)]) for a in self.j[k].approaches}
        if not self.policy.startswith("relay") or self.couple == 0.0:
            return raw
        out = {}
        for a, n in raw.items():
            dn = DOWN.get((k, a))
            dq = len(self.q[dn]) if dn else 0
            eff = n - self.couple * dq
            out[a] = max(eff, PRESENCE_EPS) if n > 0 else 0.0
        return out

    def step(self, t, warm, amb_pending):
        # arrivals
        for route, lam in self.lam.items():
            n = poisson(lam * DT)
            for _ in range(n):
                self.q[ROUTES[route][0]].append(Veh(route, t))
        if amb_pending:
            v = Veh("W_E", t, amb=True)
            self.q[ROUTES["W_E"][0]].appendleft(v)      # traffic yields to an emergency vehicle
        # link arrivals
        still = []
        for t_arr, key, v in self.transit:
            if t_arr <= t:
                v.t_q = t_arr
                if v.amb:
                    self.q[key].appendleft(v)
                else:
                    self.q[key].append(v)
            else:
                still.append((t_arr, key, v))
        self.transit = still

        # control
        green = {}
        for k in ORDER:
            if self.policy.startswith("relay"):
                em = {a for a in self.j[k].approaches
                      if self.q[(k, a)] and self.q[(k, a)][0].amb}
                st = self.ctrl[k].tick(self._counts(k), em, DT)
                green[k] = {a for a, s in st["signals"].items() if s == "green"}
            else:
                green[k] = self.ctrl[k].tick(None, (), DT)

        # discharge
        for k in ORDER:
            for a in self.j[k].approaches:
                key = (k, a)
                dq = self.q[key]
                if not dq:
                    self.carry[key] = 0.0
                    continue
                if a not in green[k]:
                    continue
                self.carry[key] += SAT * DT
                while self.carry[key] >= 1 and dq:
                    self.carry[key] -= 1
                    v = dq.popleft()
                    w = t - v.t_q
                    v.wait += w
                    if w > 2.0:
                        v.stops += 1
                    if v.amb:
                        self.amb_log.append({"junction": k, "arm": a, "wait_s": round(w, 2),
                                             "t": round(t, 2)})
                    v.idx += 1
                    if v.idx < len(ROUTES[v.route]):
                        self.transit.append((t + LINK_T, ROUTES[v.route][v.idx], v))
                    else:
                        if warm and v.t_in >= WARMUP and not v.amb:
                            self.exited += 1
                            self.delays.append(v.wait)
                            self.stops_hist.append(v.stops)
                            if v.route in ("W_E", "E_W"):
                                self.trav_through.append(t - v.t_in)
                        if v.amb:
                            self.amb_log.append({"junction": "exit", "arm": "-",
                                                 "wait_s": round(v.wait, 2),
                                                 "t": round(t, 2),
                                                 "corridor_time_s": round(t - v.t_in, 2)})
        if warm:
            self.qlen_samples.append(sum(len(d) for d in self.q.values()))

    def run(self):
        amb_step = int(AMB_TIME / DT)
        for i in range(int(SECONDS / DT)):
            t = i * DT
            self.step(t, t >= WARMUP, i == amb_step)
        measured = SECONDS - WARMUP
        # vehicles still in the network have waited at least this long
        resid = [v for d in self.q.values() for v in d if v.t_in >= WARMUP and not v.amb]
        delays = self.delays + [v.wait + (SECONDS - v.t_q) for v in resid]
        amb_exit = next((r for r in self.amb_log if r["junction"] == "exit"), None)
        return {
            "mean_delay": statistics.fmean(delays) if delays else 0.0,
            "p50_delay": pct(delays, 50), "p95_delay": pct(delays, 95),
            "max_delay": max(delays) if delays else 0.0,
            "mean_through_travel": statistics.fmean(self.trav_through) if self.trav_through else 0.0,
            "p95_through_travel": pct(self.trav_through, 95),
            "mean_stops": statistics.fmean(self.stops_hist) if self.stops_hist else 0.0,
            "mean_network_queue": statistics.fmean(self.qlen_samples) if self.qlen_samples else 0.0,
            "max_network_queue": max(self.qlen_samples) if self.qlen_samples else 0,
            "throughput_vph": self.exited / measured * 3600.0,
            "residual": len(resid),
            "amb_corridor_time": amb_exit["corridor_time_s"] if amb_exit else None,
            "amb_total_wait": amb_exit["wait_s"] if amb_exit else None,
        }


def main():
    rows, summary, plan_dump = [], [], {}
    for level in LEVELS:
        lam_route = {k: v * level for k, v in BASE_LAM.items()}
        entering_vph = sum(lam_route.values()) * 3600.0
        cell = {p: [] for p in POLICIES}
        for seed in SEEDS:
            for p in POLICIES:
                c = Corridor(p, lam_route, seed)
                m = c.run()
                if c.plans:
                    plan_dump[f"{p}_m{level}"] = c.plans
                cell[p].append(m)
                rows.append(dict(level=level, entering_vph=round(entering_vph, 1), seed=seed,
                                 policy=p, **{k: (round(v, 4) if isinstance(v, float) else v)
                                              for k, v in m.items()}))
            print(f"m={level} seed={seed}: "
                  + "  ".join(f"{p}={cell[p][-1]['mean_delay']:.1f}s" for p in POLICIES), flush=True)

        entry = dict(level=level, entering_vph=round(entering_vph, 1), n_seeds=len(SEEDS))
        for p in POLICIES:
            for key in ("mean_delay", "p95_delay", "max_delay", "mean_through_travel",
                        "p95_through_travel", "mean_stops", "mean_network_queue",
                        "max_network_queue", "throughput_vph"):
                vals = [m[key] for m in cell[p]]
                entry[f"{p}_{key}_mean"] = round(statistics.fmean(vals), 3)
                entry[f"{p}_{key}_sd"] = round(statistics.stdev(vals), 3)
            amb = [m["amb_corridor_time"] for m in cell[p] if m["amb_corridor_time"] is not None]
            entry[f"{p}_amb_corridor_mean"] = round(statistics.fmean(amb), 3) if amb else ""
            entry[f"{p}_amb_corridor_sd"] = round(statistics.stdev(amb), 3) if len(amb) > 1 else ""
            entry[f"{p}_amb_n"] = len(amb)
        for base in ("fixed_webster", "fixed_coord", "relay_isolated"):
            red = [(b["mean_delay"] - a["mean_delay"]) / b["mean_delay"] * 100
                   for a, b in zip(cell["relay_coupled"], cell[base]) if b["mean_delay"]]
            entry[f"red_vs_{base}_mean"] = round(statistics.fmean(red), 2) if red else ""
            entry[f"red_vs_{base}_sd"] = round(statistics.stdev(red), 2) if len(red) > 1 else ""
        summary.append(entry)
        print(f"  >> m={level} ({entering_vph:.0f} veh/h entering): "
              f"vs webster {entry['red_vs_fixed_webster_mean']:+.1f}%  "
              f"vs coordinated {entry['red_vs_fixed_coord_mean']:+.1f}%  "
              f"vs isolated {entry['red_vs_relay_isolated_mean']:+.1f}%", flush=True)

    for name, data in (("exp2_runs.csv", rows), ("exp2_summary.csv", summary)):
        with open(os.path.join(OUT, name), "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=list(data[0].keys()))
            w.writeheader()
            w.writerows(data)
    with open(os.path.join(OUT, "exp2_config.json"), "w") as fh:
        json.dump({"junctions": JUNCTIONS, "routes": ROUTES, "down": {f"{k[0]}.{k[1]}": v
                                                                     for k, v in DOWN.items()},
                   "base_lam_veh_s": BASE_LAM, "levels": LEVELS, "spacing_m": SPACING_M,
                   "free_flow_ms": FREE_FLOW_MS, "link_travel_s": LINK_T, "couple": COUPLE,
                   "timings": T_NET, "seconds": SECONDS, "warmup": WARMUP, "dt": DT,
                   "seeds": SEEDS, "sat_flow_veh_per_s": SAT, "amb_time_s": AMB_TIME,
                   "webster_plans": plan_dump}, fh, indent=2)
    print("wrote exp2_runs.csv, exp2_summary.csv, exp2_config.json to", OUT)


if __name__ == "__main__":
    main()
