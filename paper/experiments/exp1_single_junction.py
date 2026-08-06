#!/usr/bin/env python
"""Experiment 1: single-junction A/B, R.E.L.A.Y. vs two fixed-time baselines.

Policies compared, all on identical realised arrival streams:
  relay          the repository's adaptive controller (src/controller.py), unmodified
  fixed_equal    the repository's own equal-split round-robin baseline (src/microsim.py)
  fixed_webster  a fixed-time plan whose cycle and splits come from Webster's formulae
                 evaluated on the TRUE mean demand of the run (see relay_bench.WebsterTimer).
                 This baseline is deliberately favourable to fixed-time control: it is given
                 perfect knowledge of the average demand, which no field installation has.

Arrivals are drawn once per time step from a single seeded generator and pushed into all three
queue systems, so the comparison is paired at the level of individual vehicles. The first
WARMUP seconds are excluded from every statistic because all queues start empty.

Usage:  RELAY_REPO=/path/to/relay .venv/bin/python experiments/exp1_single_junction.py
"""
import csv
import json
import math
import os
import random
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from relay_bench import (Controller, FixedTimer, QueueSystem, SAT, Timings,   # noqa: E402
                         WebsterTimer, four_way, pct, poisson)

OUT = os.environ.get("RELAY_OUT", os.path.join(os.path.dirname(os.path.abspath(__file__)), "raw"))
os.makedirs(OUT, exist_ok=True)

SECONDS = 1800.0
WARMUP = 300.0
DT = 0.5
SEEDS = list(range(30))

# Deterministic demand profiles, vehicles per second per approach. "asymmetric" is the shape
# src/microsim.py samples at random (one busy axis, one light cross street) fixed here so that a
# demand level is a reportable number; "balanced" is the case a fixed-time plan is designed for
# and is the honest hard case for a max-pressure policy; "single" loads one approach only.
PROFILES = {
    "asymmetric": {"N": 0.150, "S": 0.130, "E": 0.040, "W": 0.035},
    "balanced":   {"N": 0.089, "S": 0.089, "E": 0.089, "W": 0.089},
    "single":     {"N": 0.250, "S": 0.035, "E": 0.035, "W": 0.035},
}
LEVELS = [0.6, 0.8, 1.0, 1.2, 1.4]

TIMINGS = dict(min_green=5, max_green=30, yellow=3, all_red=1.5, max_wait=60)
FIXED_GREEN = 13.0
POLICIES = ("relay", "fixed_equal", "fixed_webster")
_pct = pct        # re-exported for the other experiment scripts


def run_all(lam, seed):
    """Run every policy on one identical realised arrival stream."""
    random.seed(seed)                    # microsim.poisson() draws from the module-level RNG
    J = four_way()
    ctrl = {
        "relay": Controller(J, Timings(**TIMINGS)),
        "fixed_equal": FixedTimer(J, green=FIXED_GREEN, yellow=TIMINGS["yellow"],
                                  all_red=TIMINGS["all_red"]),
        "fixed_webster": WebsterTimer(J, lam, yellow=TIMINGS["yellow"], all_red=TIMINGS["all_red"],
                                      min_green=TIMINGS["min_green"]),
    }
    qs = {p: QueueSystem(J.approaches, warmup=WARMUP) for p in POLICIES}

    for i in range(int(SECONDS / DT)):
        t_now = i * DT
        warm = t_now >= WARMUP
        for a in J.approaches:
            n = poisson(lam[a] * DT)
            for p in POLICIES:
                qs[p].arrive(a, n, t_now)
        for p in POLICIES:
            out = ctrl[p].tick({a: qs[p].q[a] for a in J.approaches}, (), DT)
            green = ({a for a, s in out["signals"].items() if s == "green"}
                     if isinstance(out, dict) else out)
            qs[p].discharge(green, t_now, DT, record=warm)
            if warm:
                qs[p].sample()
    res = {p: qs[p].metrics(SECONDS) for p in POLICIES}
    res["_webster_plan"] = ctrl["fixed_webster"].plan()
    return res


def paired_stats(diffs):
    n = len(diffs)
    m = statistics.fmean(diffs)
    sd = statistics.stdev(diffs) if n > 1 else 0.0
    out = {"n": n, "mean_diff": m, "sd_diff": sd}
    if sd > 0:
        t = m / (sd / math.sqrt(n))
        out["t"] = t
        try:
            from scipy import stats
            out["p_ttest"] = float(stats.t.sf(abs(t), n - 1) * 2)
            out["p_wilcoxon"] = float(stats.wilcoxon(diffs).pvalue)
        except Exception:
            pass
    return out


def main():
    rows, summary, plans = [], [], {}
    for prof_name, base in PROFILES.items():
        for level in LEVELS:
            lam = {a: v * level for a, v in base.items()}
            total_vph = sum(lam.values()) * 3600.0
            cell = {}
            for seed in SEEDS:
                r = run_all(lam, seed)
                plans[f"{prof_name}_m{level}"] = r.pop("_webster_plan")
                cell[seed] = r
                for policy in POLICIES:
                    rows.append(dict(profile=prof_name, level=level,
                                     total_demand_vph=round(total_vph, 1), seed=seed, policy=policy,
                                     **{k: (round(v, 4) if isinstance(v, float) else v)
                                        for k, v in r[policy].items()}))
                print(f"{prof_name} m={level} seed={seed}: "
                      + "  ".join(f"{p}={r[p]['mean_wait']:.1f}s" for p in POLICIES), flush=True)

            entry = dict(profile=prof_name, level=level, total_demand_vph=round(total_vph, 1),
                         n_seeds=len(SEEDS))
            for policy in POLICIES:
                for key in ("mean_wait", "p50_wait", "p95_wait", "max_wait", "mean_queue",
                            "max_queue", "throughput_vph"):
                    vals = [cell[s][policy][key] for s in SEEDS]
                    entry[f"{policy}_{key}_mean"] = round(statistics.fmean(vals), 3)
                    entry[f"{policy}_{key}_sd"] = round(statistics.stdev(vals), 3)
            for base_p in ("fixed_equal", "fixed_webster"):
                red = [(cell[s][base_p]["mean_wait"] - cell[s]["relay"]["mean_wait"])
                       / cell[s][base_p]["mean_wait"] * 100
                       if cell[s][base_p]["mean_wait"] else 0.0 for s in SEEDS]
                entry[f"red_vs_{base_p}_mean"] = round(statistics.fmean(red), 2)
                entry[f"red_vs_{base_p}_sd"] = round(statistics.stdev(red), 2)
                entry[f"red_vs_{base_p}_min"] = round(min(red), 2)
                entry[f"red_vs_{base_p}_max"] = round(max(red), 2)
                st = paired_stats([cell[s][base_p]["mean_wait"] - cell[s]["relay"]["mean_wait"]
                                   for s in SEEDS])
                for k, v in st.items():
                    entry[f"paired_{base_p}_{k}"] = round(v, 6) if isinstance(v, float) else v
            summary.append(entry)
            print(f"  >> {prof_name} m={level} ({total_vph:.0f} veh/h): "
                  f"vs equal {entry['red_vs_fixed_equal_mean']:+.1f}%+/-{entry['red_vs_fixed_equal_sd']:.1f}  "
                  f"vs webster {entry['red_vs_fixed_webster_mean']:+.1f}%+/-{entry['red_vs_fixed_webster_sd']:.1f}",
                  flush=True)

    with open(os.path.join(OUT, "exp1_runs.csv"), "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    with open(os.path.join(OUT, "exp1_summary.csv"), "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(summary[0].keys()))
        w.writeheader()
        w.writerows(summary)
    with open(os.path.join(OUT, "exp1_config.json"), "w") as fh:
        json.dump({"seconds": SECONDS, "warmup": WARMUP, "dt": DT, "seeds": SEEDS,
                   "profiles": PROFILES, "levels": LEVELS, "timings": TIMINGS,
                   "fixed_green": FIXED_GREEN, "sat_flow_veh_per_s": SAT,
                   "webster_plans": plans}, fh, indent=2)
    print("wrote exp1_runs.csv, exp1_summary.csv, exp1_config.json to", OUT)


if __name__ == "__main__":
    main()
