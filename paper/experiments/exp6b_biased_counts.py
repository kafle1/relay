#!/usr/bin/env python
"""Experiment 6b: the controller under the error the detector actually makes.

Experiment 6 corrupts counts with zero-mean Gaussian noise, which a 60 s estimator window
averages away. Experiment 4 shows the real failure is a biased undercount that grows with
density. This repeats the 72-cell grid under three count models applied to what the
controller sees, while the queues it manages stay exact:

  saturating   reported = q / (1 + q / q_half): the fraction seen falls as the queue grows,
               matching a detector that loses occluded two-wheelers in a dense queue
  scale        reported = round(f q): a flat multiplicative loss; at 0.4 a lone vehicle rounds to zero
  floor        as scale, but a queue of one vehicle always reads zero: the approach reads empty
  keep1        as scale or floor, with an occupied approach never reading below one

Usage:  RELAY_REPO=/path/to/relay .venv/bin/python paper/experiments/exp6b_biased_counts.py
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
from exp6_robustness import FOUR_WAY, TEE, LEVELS, TIMINGS, FIXED_GREEN, SECONDS, WARMUP, DT   # noqa: E402

REPO = os.environ.get("RELAY_REPO", os.getcwd())
sys.path.insert(0, os.path.join(REPO, "src"))
from controller import junction_from_dirs                                # noqa: E402

OUT = os.environ.get("RELAY_OUT", os.path.join(os.path.dirname(os.path.abspath(__file__)), "raw"))
os.makedirs(OUT, exist_ok=True)
SEEDS = list(range(int(os.environ.get("NSEEDS", 25))))
POLICIES = ("relay", "fixed_equal", "fixed_webster")

MODELS = {
    "saturating_q8":  lambda q: q / (1.0 + q / 8.0),
    "saturating_q4":  lambda q: q / (1.0 + q / 4.0),
    "scale_0.8":      lambda q: round(0.8 * q),
    "scale_0.6":      lambda q: round(0.6 * q),
    "scale_0.4":      lambda q: round(0.4 * q),
    "scale_0.4_keep1": lambda q: max(1, round(0.4 * q)) if q > 0 else 0,
    "floor_0.8":      lambda q: 0 if q <= 1 else round(0.8 * q),
    "floor_0.6":      lambda q: 0 if q <= 1 else round(0.6 * q),
    "floor_0.8_keep1": lambda q: max(1, round(0.8 * q)) if q > 0 else 0,
    "floor_0.6_keep1": lambda q: max(1, round(0.6 * q)) if q > 0 else 0,
}


def run_all(junction, lam, seed, model):
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
                seen = {a: max(0.0, float(model(q))) for a, q in seen.items()}
            out = ctrl[p].tick(seen, (), DT)
            green = {a for a, s in out["signals"].items() if s == "green"} if isinstance(out, dict) else out
            qs[p].discharge(green, t_now, DT, record=warm)
            if warm:
                qs[p].sample()
    return {p: qs[p].metrics(SECONDS) for p in POLICIES}


def main():
    rows = []
    for model_name, model in MODELS.items():
        for group, junction, topo in ((FOUR_WAY, four_way(), "4-way"),
                                      (TEE, junction_from_dirs(["N", "E", "W"]), "T")):
            for shape, base in group.items():
                for level in LEVELS:
                    lam = {a: v * level for a, v in base.items()}
                    cell = [run_all(junction, lam, s, model) for s in SEEDS]
                    row = dict(model=model_name, topology=topo, shape=shape, level=level,
                               total_demand_vph=round(sum(lam.values()) * 3600.0, 1), n_seeds=len(SEEDS))
                    for p in POLICIES:
                        for k in ("mean_wait", "p95_wait", "max_wait", "throughput_vph"):
                            row[f"{p}_{k}"] = round(statistics.fmean(c[p][k] for c in cell), 3)
                    for b in ("fixed_equal", "fixed_webster"):
                        red = [(c[b]["mean_wait"] - c["relay"]["mean_wait"]) / c[b]["mean_wait"] * 100
                               for c in cell if c[b]["mean_wait"] > 0]
                        row[f"red_vs_{b}"] = round(statistics.fmean(red), 2)
                    rows.append(row)
        sub = [r for r in rows if r["model"] == model_name]
        wins = sum(1 for r in sub if min(r["red_vs_fixed_equal"], r["red_vs_fixed_webster"]) > 0)
        print(f"{model_name:<18} cells won {wins}/{len(sub)}  worst vs webster "
              f"{min(r['red_vs_fixed_webster'] for r in sub):+.1f}%  mean vs webster "
              f"{statistics.fmean(r['red_vs_fixed_webster'] for r in sub):+.1f}%", flush=True)

    with open(os.path.join(OUT, "exp6b_biased_counts.csv"), "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader(); w.writerows(rows)
    with open(os.path.join(OUT, "exp6b_config.json"), "w") as fh:
        json.dump({"seconds": SECONDS, "warmup": WARMUP, "dt": DT, "seeds": SEEDS, "levels": LEVELS,
                   "four_way_shapes": FOUR_WAY, "t_shapes": TEE, "timings": TIMINGS,
                   "fixed_green": FIXED_GREEN, "models": list(MODELS)}, fh, indent=2)
    print("wrote exp6b_biased_counts.csv to", OUT)


if __name__ == "__main__":
    main()
