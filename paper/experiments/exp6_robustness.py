#!/usr/bin/env python
"""Experiment 6: does the controller beat both fixed-time baselines in every cell, not just on average?

Experiment 1 sweeps three demand shapes on a four-way junction. This one is deliberately wider,
because a mean over favourable cells hides the cells that matter:

  * seven demand shapes on a four-way junction, from perfectly balanced to one dominant approach
  * two shapes on a three-arm T junction, which exercises a different phase set
  * eight load levels per shape, running past the point where a fixed plan saturates
  * optionally, Gaussian miscounting applied to what the controller sees while the queues it is
    actually managing stay exact, which separates control behaviour from detector error

Every cell is a paired comparison on one realised arrival stream, as in Experiment 1.

Usage:  RELAY_REPO=/path/to/relay .venv/bin/python experiments/exp6_robustness.py
        NOISE=0.20 ... same, with 20% per-count Gaussian miscounting
"""
import csv
import json
import os
import random
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from relay_bench import (Controller, FixedTimer, QueueSystem, Timings,   # noqa: E402
                         WebsterTimer, four_way, poisson)

REPO = os.environ.get("RELAY_REPO", os.getcwd())
sys.path.insert(0, os.path.join(REPO, "src"))
from controller import junction_from_dirs                                # noqa: E402

OUT = os.environ.get("RELAY_OUT", os.path.join(os.path.dirname(os.path.abspath(__file__)), "raw"))
os.makedirs(OUT, exist_ok=True)

SECONDS, WARMUP, DT = 1800.0, 300.0, 0.5
SEEDS = list(range(int(os.environ.get("NSEEDS", 25))))
NOISE = float(os.environ.get("NOISE", 0.0))
TIMINGS = dict(min_green=5, max_green=45, yellow=3, all_red=1.5, max_wait=60)
FIXED_GREEN = 13.0
LEVELS = [0.4, 0.6, 0.8, 1.0, 1.2, 1.4, 1.6, 1.8]

FOUR_WAY = {
    "asymmetric":  {"N": 0.150, "S": 0.130, "E": 0.040, "W": 0.035},
    "balanced":    {"N": 0.089, "S": 0.089, "E": 0.089, "W": 0.089},
    "single":      {"N": 0.250, "S": 0.035, "E": 0.035, "W": 0.035},
    "one_axis":    {"N": 0.140, "S": 0.140, "E": 0.020, "W": 0.020},
    "three_heavy": {"N": 0.110, "S": 0.110, "E": 0.110, "W": 0.025},
    "lopsided":    {"N": 0.200, "S": 0.060, "E": 0.060, "W": 0.030},
    "near_equal":  {"N": 0.095, "S": 0.085, "E": 0.090, "W": 0.086},
}
TEE = {
    "t_balanced":  {"N": 0.100, "E": 0.100, "W": 0.100},
    "t_arterial":  {"N": 0.050, "E": 0.170, "W": 0.160},
}
POLICIES = ("relay", "fixed_equal", "fixed_webster")


def miscount(counts, frac):
    """What the detector reports, not what is on the road."""
    if frac <= 0:
        return counts
    return {a: max(0, int(round(q + random.gauss(0.0, frac * max(1.0, q)))))
            for a, q in counts.items()}


def run_all(junction, lam, seed):
    random.seed(seed)
    ctrl = {
        "relay": Controller(junction, Timings(**TIMINGS)),
        "fixed_equal": FixedTimer(junction, green=FIXED_GREEN, yellow=TIMINGS["yellow"],
                                  all_red=TIMINGS["all_red"]),
        "fixed_webster": WebsterTimer(junction, lam, yellow=TIMINGS["yellow"],
                                      all_red=TIMINGS["all_red"], min_green=TIMINGS["min_green"]),
    }
    qs = {p: QueueSystem(junction.approaches, warmup=WARMUP) for p in POLICIES}
    for i in range(int(SECONDS / DT)):
        t_now = i * DT
        warm = t_now >= WARMUP
        for a in junction.approaches:
            n = poisson(lam[a] * DT)
            for p in POLICIES:
                qs[p].arrive(a, n, t_now)
        for p in POLICIES:
            seen = {a: qs[p].q[a] for a in junction.approaches}
            if p == "relay":
                seen = miscount(seen, NOISE)
            out = ctrl[p].tick(seen, (), DT)
            green = ({a for a, s in out["signals"].items() if s == "green"}
                     if isinstance(out, dict) else out)
            qs[p].discharge(green, t_now, DT, record=warm)
            if warm:
                qs[p].sample()
    return {p: qs[p].metrics(SECONDS) for p in POLICIES}


def main():
    rows, losing = [], []
    for group, junction, topo in ((FOUR_WAY, four_way(), "4-way"),
                                  (TEE, junction_from_dirs(["N", "E", "W"]), "T")):
        for shape, base in group.items():
            for level in LEVELS:
                lam = {a: v * level for a, v in base.items()}
                cell = [run_all(junction, lam, s) for s in SEEDS]
                red = {}
                for b in ("fixed_equal", "fixed_webster"):
                    red[b] = statistics.fmean(
                        (c[b]["mean_wait"] - c["relay"]["mean_wait"]) / c[b]["mean_wait"] * 100
                        for c in cell)
                row = dict(topology=topo, shape=shape, level=level,
                           total_demand_vph=round(sum(lam.values()) * 3600.0, 1),
                           n_seeds=len(SEEDS), noise=NOISE,
                           red_vs_fixed_equal=round(red["fixed_equal"], 2),
                           red_vs_fixed_webster=round(red["fixed_webster"], 2))
                for p in POLICIES:
                    for k in ("mean_wait", "p95_wait", "throughput_vph"):
                        row[f"{p}_{k}"] = round(statistics.fmean(c[p][k] for c in cell), 3)
                rows.append(row)
                if min(red.values()) <= 0:
                    losing.append(row)
                print(f"{topo:<6}{shape:<13}m={level:<4}{row['total_demand_vph']:>7.0f} veh/h  "
                      f"relay {row['relay_mean_wait']:6.1f}s  vs equal {red['fixed_equal']:+6.1f}%  "
                      f"vs webster {red['fixed_webster']:+6.1f}%"
                      f"{'   <-- LOSES' if min(red.values()) <= 0 else ''}", flush=True)

    suffix = f"_noise{NOISE:g}" if NOISE else ""
    with open(os.path.join(OUT, f"exp6_robustness{suffix}.csv"), "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    with open(os.path.join(OUT, f"exp6_config{suffix}.json"), "w") as fh:
        json.dump({"seconds": SECONDS, "warmup": WARMUP, "dt": DT, "seeds": SEEDS,
                   "levels": LEVELS, "four_way_shapes": FOUR_WAY, "t_shapes": TEE,
                   "timings": TIMINGS, "fixed_green": FIXED_GREEN, "noise": NOISE,
                   "cells": len(rows), "losing_cells": len(losing)}, fh, indent=2)

    print(f"\ncells {len(rows)}   cells losing to either baseline {len(losing)}")
    for r in losing:
        print(f"  {r['topology']} {r['shape']} m={r['level']}: "
              f"vs equal {r['red_vs_fixed_equal']:+.1f}%  vs webster {r['red_vs_fixed_webster']:+.1f}%")
    print(f"worst vs equal   {min(r['red_vs_fixed_equal'] for r in rows):+.1f}%")
    print(f"worst vs webster {min(r['red_vs_fixed_webster'] for r in rows):+.1f}%")
    print("wrote", f"exp6_robustness{suffix}.csv", "to", OUT)


if __name__ == "__main__":
    main()
