#!/usr/bin/env python
"""Experiment 7: the controller against adaptive baselines, not only fixed plans.

Experiments 1 and 6 compare against two fixed-time plans. A reviewer's fair question is
whether a simpler adaptive rule does as well. This runs the 72-cell grid of Experiment 6
with three more policies on the same realised arrivals:

  maxpressure   plain queue-argmax with a minimum green and clearance, no fairness bound
  sotl          self-organising traffic lights (Gershenson), threshold on integrated demand
  actuated      vehicle-actuated control with passage-time extension and gap-out

Every policy sees exact queues. Reported per cell: mean delay, p95 wait, worst wait,
throughput, and the paired reduction of the controller against each baseline.

Usage:  RELAY_REPO=/path/to/relay .venv/bin/python paper/experiments/exp7_policies.py
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
from baselines import MaxPressure, SOTL, Actuated                       # noqa: E402
from exp6_robustness import FOUR_WAY, TEE, LEVELS, TIMINGS, FIXED_GREEN, SECONDS, WARMUP, DT   # noqa: E402

REPO = os.environ.get("RELAY_REPO", os.getcwd())
sys.path.insert(0, os.path.join(REPO, "src"))
from controller import junction_from_dirs                                # noqa: E402

OUT = os.environ.get("RELAY_OUT", os.path.join(os.path.dirname(os.path.abspath(__file__)), "raw"))
os.makedirs(OUT, exist_ok=True)
SEEDS = list(range(int(os.environ.get("NSEEDS", 25))))
POLICIES = ("relay", "fixed_equal", "fixed_webster", "maxpressure", "sotl", "actuated")
STAGE = dict(yellow=TIMINGS["yellow"], all_red=TIMINGS["all_red"],
             min_green=TIMINGS["min_green"], max_green=TIMINGS["max_green"])


def make(policy, junction, lam):
    if policy == "relay":
        return Controller(junction, Timings(**TIMINGS))
    if policy == "fixed_equal":
        return FixedTimer(junction, green=FIXED_GREEN, yellow=STAGE["yellow"], all_red=STAGE["all_red"])
    if policy == "fixed_webster":
        return WebsterTimer(junction, lam, yellow=STAGE["yellow"], all_red=STAGE["all_red"],
                            min_green=STAGE["min_green"])
    if policy == "maxpressure":
        return MaxPressure(junction, **STAGE)
    if policy == "sotl":
        return SOTL(junction, **STAGE)
    return Actuated(junction, **STAGE)


def run_all(junction, lam, seed):
    random.seed(seed)
    ctrl = {p: make(p, junction, lam) for p in POLICIES}
    qs = {p: QueueSystem(junction.approaches, warmup=WARMUP) for p in POLICIES}
    for i in range(int(SECONDS / DT)):
        t_now = i * DT
        warm = t_now >= WARMUP
        for a in junction.approaches:
            n = poisson(lam[a] * DT)
            for p in POLICIES:
                qs[p].arrive(a, n, t_now)
        for p in POLICIES:
            out = ctrl[p].tick({a: qs[p].q[a] for a in junction.approaches}, (), DT)
            green = {a for a, s in out["signals"].items() if s == "green"} if isinstance(out, dict) else out
            qs[p].discharge(green, t_now, DT, record=warm)
            if warm:
                qs[p].sample()
    return {p: qs[p].metrics(SECONDS) for p in POLICIES}


def main():
    rows = []
    for group, junction, topo in ((FOUR_WAY, four_way(), "4-way"),
                                  (TEE, junction_from_dirs(["N", "E", "W"]), "T")):
        for shape, base in group.items():
            for level in LEVELS:
                lam = {a: v * level for a, v in base.items()}
                cell = [run_all(junction, lam, s) for s in SEEDS]
                row = dict(topology=topo, shape=shape, level=level,
                           total_demand_vph=round(sum(lam.values()) * 3600.0, 1), n_seeds=len(SEEDS))
                for p in POLICIES:
                    for k in ("mean_wait", "p95_wait", "max_wait", "throughput_vph"):
                        row[f"{p}_{k}"] = round(statistics.fmean(c[p][k] for c in cell), 3)
                for b in POLICIES[1:]:
                    red = [(c[b]["mean_wait"] - c["relay"]["mean_wait"]) / c[b]["mean_wait"] * 100
                           for c in cell if c[b]["mean_wait"] > 0]
                    row[f"red_vs_{b}"] = round(statistics.fmean(red), 2) if red else 0.0
                    row[f"wins_vs_{b}"] = sum(1 for r in red if r > 0)
                rows.append(row)
                print(f"{topo:<6}{shape:<13}m={level:<4}{row['total_demand_vph']:>7.0f} veh/h  "
                      f"relay {row['relay_mean_wait']:6.1f}s  "
                      + "  ".join(f"{b[:6]} {row[f'red_vs_{b}']:+6.1f}%" for b in POLICIES[1:]), flush=True)

    with open(os.path.join(OUT, "exp7_policies.csv"), "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader(); w.writerows(rows)
    with open(os.path.join(OUT, "exp7_config.json"), "w") as fh:
        json.dump({"seconds": SECONDS, "warmup": WARMUP, "dt": DT, "seeds": SEEDS, "levels": LEVELS,
                   "four_way_shapes": FOUR_WAY, "t_shapes": TEE, "timings": TIMINGS,
                   "fixed_green": FIXED_GREEN, "policies": POLICIES,
                   "sotl": {"theta": 40.0, "mu": 3}, "actuated": {"passage": 3.0}}, fh, indent=2)
    for b in POLICIES[1:]:
        worst = min(rows, key=lambda r: r[f"red_vs_{b}"])
        print(f"vs {b:<14} worst {worst[f'red_vs_{b}']:+6.1f}% ({worst['topology']} {worst['shape']} m={worst['level']})  "
              f"cells won {sum(1 for r in rows if r[f'red_vs_{b}'] > 0)}/{len(rows)}")
    print("wrote exp7_policies.csv to", OUT)


if __name__ == "__main__":
    main()
