#!/usr/bin/env python
"""Experiment 1b: switching frequency and lost time, which explain the Experiment 1 result.

Max-pressure policies without a cycle constraint are free to switch as often as the minimum
green allows. Every switch costs a fixed clearance interval (yellow + all-red) during which no
approach discharges. This script measures, for both policies and every demand cell of
Experiment 1, how many phase switches occur per hour and what fraction of wall time is spent in
clearance rather than in green. It is the diagnostic behind the balanced-demand regression
reported in the paper, and it is measured, not argued.

Usage:  RELAY_REPO=/path/to/relay .venv/bin/python experiments/exp1b_switching.py
"""
import csv
import os
import random
import statistics
import sys

REPO = os.environ.get("RELAY_REPO", os.getcwd())
sys.path.insert(0, os.path.join(REPO, "src"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from controller import Controller, Timings, four_way          # noqa: E402
from microsim import FixedTimer, poisson                      # noqa: E402
from exp1_single_junction import (QueueSystem, TIMINGS, FIXED_GREEN, PROFILES, LEVELS,
                                  DT, SECONDS, WARMUP)        # noqa: E402

OUT = os.environ.get("RELAY_OUT", os.path.join(os.path.dirname(os.path.abspath(__file__)), "raw"))
os.makedirs(OUT, exist_ok=True)
SEEDS = list(range(10))


def run(lam, seed):
    random.seed(seed)
    J = four_way()
    adaptive = Controller(J, Timings(**TIMINGS))
    fixed = FixedTimer(J, green=FIXED_GREEN, yellow=TIMINGS["yellow"], all_red=TIMINGS["all_red"])
    sys_a, sys_f = QueueSystem(J.approaches), QueueSystem(J.approaches)
    sw_a = sw_f = 0
    green_steps_a = green_steps_f = steps = 0
    prev_a, prev_f = None, None
    greens_a, cur_a = [], 0.0

    for i in range(int(SECONDS / DT)):
        t = i * DT
        warm = t >= WARMUP
        for a in J.approaches:
            n = poisson(lam[a] * DT)
            sys_a.arrive(a, n, t)
            sys_f.arrive(a, n, t)
        st = adaptive.tick({a: sys_a.q[a] for a in J.approaches}, (), DT)
        ga = {a for a, s in st["signals"].items() if s == "green"}
        gf = fixed.tick({a: sys_f.q[a] for a in J.approaches}, (), DT)
        if warm:
            steps += 1
            if ga:
                green_steps_a += 1
                cur_a += DT
            elif cur_a:
                greens_a.append(cur_a)
                cur_a = 0.0
            if gf:
                green_steps_f += 1
            key_a = st["phase"] if ga else None
            key_f = frozenset(gf) if gf else None
            if key_a is not None and prev_a is not None and key_a != prev_a:
                sw_a += 1
            if key_f is not None and prev_f is not None and key_f != prev_f:
                sw_f += 1
            if key_a is not None:
                prev_a = key_a
            if key_f is not None:
                prev_f = key_f
        sys_a.discharge(ga, t, DT, warm)
        sys_f.discharge(gf, t, DT, warm)
    if cur_a:
        greens_a.append(cur_a)
    hours = (SECONDS - WARMUP) / 3600.0
    return dict(sw_relay_per_h=sw_a / hours, sw_fixed_per_h=sw_f / hours,
                green_frac_relay=green_steps_a / steps, green_frac_fixed=green_steps_f / steps,
                mean_green_relay=statistics.fmean(greens_a) if greens_a else 0.0)


def main():
    rows, summary = [], []
    for prof, base in PROFILES.items():
        for level in LEVELS:
            lam = {a: v * level for a, v in base.items()}
            cell = [run(lam, s) for s in SEEDS]
            for s, c in zip(SEEDS, cell):
                rows.append(dict(profile=prof, level=level, seed=s,
                                 **{k: round(v, 4) for k, v in c.items()}))
            entry = dict(profile=prof, level=level, n_seeds=len(SEEDS))
            for k in cell[0]:
                entry[f"{k}_mean"] = round(statistics.fmean([c[k] for c in cell]), 4)
                entry[f"{k}_sd"] = round(statistics.stdev([c[k] for c in cell]), 4)
            summary.append(entry)
            print(f"{prof:<11} m={level}: relay {entry['sw_relay_per_h_mean']:.0f} sw/h "
                  f"(green {entry['green_frac_relay_mean']:.3f}, mean green "
                  f"{entry['mean_green_relay_mean']:.1f}s) | fixed {entry['sw_fixed_per_h_mean']:.0f} sw/h "
                  f"(green {entry['green_frac_fixed_mean']:.3f})", flush=True)

    for name, data in (("exp1b_runs.csv", rows), ("exp1b_summary.csv", summary)):
        with open(os.path.join(OUT, name), "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=list(data[0].keys()))
            w.writeheader()
            w.writerows(data)
    print("wrote exp1b_runs.csv, exp1b_summary.csv to", OUT)


if __name__ == "__main__":
    main()
