#!/usr/bin/env python
"""Experiment 8: the controller in a microscopic simulation (SUMO).

Same three demand shapes as Experiment 1 and three load levels, ten seeds each, four
policies, in a SUMO junction with a motorcycle-dominated fleet, a sublane model, turning
movements and finite links. The controller sees only what a camera would: vehicles by class
within 80 m of each stop line. Delay is SUMO's per-vehicle time loss against free flow.

Usage:  RELAY_REPO=/path/to/relay .venv/bin/python paper/experiments/exp8_sumo.py
        NSEEDS=3 NPROC=4 ... for a quick pass
"""
import csv
import json
import os
import statistics
import sys
import time
from multiprocessing import Pool

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.environ.setdefault("SUMO_HOME", os.path.join(os.environ.get("RELAY_REPO", os.path.dirname(os.path.dirname(HERE))),
                                                ".venv", "lib", "python3.12", "site-packages", "sumo"))
import sumo_bench                                                       # noqa: E402
from controller import Timings                                          # noqa: E402

OUT = os.environ.get("RELAY_OUT", os.path.join(HERE, "raw"))
os.makedirs(OUT, exist_ok=True)
SEEDS = list(range(int(os.environ.get("NSEEDS", 10))))
NPROC = int(os.environ.get("NPROC", 8))
SECONDS, WARMUP = 1800.0, 300.0
PROFILES = {
    "asymmetric": {"N": 0.150, "S": 0.130, "E": 0.040, "W": 0.035},
    "balanced":   {"N": 0.089, "S": 0.089, "E": 0.089, "W": 0.089},
    "single":     {"N": 0.250, "S": 0.035, "E": 0.035, "W": 0.035},
}
LEVELS = [0.6, 1.0, 1.4]
POLICIES = ("webster", "relay", "maxpressure", "actuated")
TIMINGS = dict(min_green=5, max_green=45, yellow=3, all_red=1.5, max_wait=60)


def one(job):
    profile, level, seed, policy, sat = job
    lam = {a: v * level for a, v in PROFILES[profile].items()}
    t0 = time.time()
    r = sumo_bench.run(policy, lam, seed, seconds=SECONDS, warmup=WARMUP, timings=Timings(**TIMINGS),
                       sat_pcu_h_lane=sat)
    r.update(profile=profile, level=level, seed=seed, policy=policy,
             total_demand_vph=round(sum(lam.values()) * 3600.0, 1), wall_s=round(time.time() - t0, 1))
    return r


def main():
    t_start = time.time()
    sat = sumo_bench.measure_saturation()
    print(f"measured saturation flow {sat:.0f} PCU/h/lane", flush=True)
    jobs = [(p, l, s, pol, sat) for p in PROFILES for l in LEVELS for s in SEEDS for pol in POLICIES]
    rows = []
    with Pool(NPROC) as pool:
        for i, r in enumerate(pool.imap_unordered(one, jobs)):
            rows.append(r)
            if i % 10 == 0:
                print(f"{i + 1}/{len(jobs)}  {r['profile']} m={r['level']} seed={r['seed']} {r['policy']:<12} "
                      f"delay {r['mean_delay']:.1f}s  ({r['wall_s']}s)", flush=True)

    flat = []
    for r in rows:
        flat.append({k: (json.dumps(v) if isinstance(v, dict) else v) for k, v in r.items()})
    with open(os.path.join(OUT, "exp8_sumo_runs.csv"), "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(flat[0].keys()))
        w.writeheader(); w.writerows(flat)

    summary = []
    for p in PROFILES:
        for l in LEVELS:
            cell = {pol: sorted([r for r in rows if r["profile"] == p and r["level"] == l and r["policy"] == pol],
                                key=lambda r: r["seed"]) for pol in POLICIES}
            entry = dict(profile=p, level=l, total_demand_vph=cell["relay"][0]["total_demand_vph"], n_seeds=len(SEEDS))
            for pol in POLICIES:
                for k in ("mean_delay", "p50_delay", "p95_delay", "max_delay", "throughput_vph"):
                    vals = [r[k] for r in cell[pol]]
                    entry[f"{pol}_{k}_mean"] = round(statistics.fmean(vals), 3)
                    entry[f"{pol}_{k}_sd"] = round(statistics.stdev(vals), 3) if len(vals) > 1 else 0.0
            for b in ("webster", "maxpressure", "actuated"):
                red = [(x["mean_delay"] - y["mean_delay"]) / x["mean_delay"] * 100
                       for x, y in zip(cell[b], cell["relay"]) if x["mean_delay"] > 0]
                entry[f"red_vs_{b}_mean"] = round(statistics.fmean(red), 2)
                entry[f"red_vs_{b}_sd"] = round(statistics.stdev(red), 2) if len(red) > 1 else 0.0
                entry[f"red_vs_{b}_min"] = round(min(red), 2)
                entry[f"wins_vs_{b}"] = sum(1 for x in red if x > 0)
            summary.append(entry)
            print(f"{p:<11} m={l}  relay {entry['relay_mean_delay_mean']:5.1f}s  "
                  + "  ".join(f"vs {b} {entry[f'red_vs_{b}_mean']:+5.1f}%" for b in ("webster", "maxpressure", "actuated")))
    with open(os.path.join(OUT, "exp8_sumo_summary.csv"), "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(summary[0].keys()))
        w.writeheader(); w.writerows(summary)
    with open(os.path.join(OUT, "exp8_config.json"), "w") as fh:
        json.dump({"seconds": SECONDS, "warmup": WARMUP, "step": sumo_bench.STEP, "seeds": SEEDS,
                   "profiles": PROFILES, "levels": LEVELS, "policies": POLICIES, "timings": TIMINGS,
                   "fleet": sumo_bench.FLEET, "turns": sumo_bench.TURNS, "lanes": sumo_bench.LANES,
                   "arm_length_m": sumo_bench.ARM_LEN, "detect_m": sumo_bench.DETECT_M,
                   "saturation_pcu_h_lane": round(sat, 1), "sumo_version": _sumo_version(),
                   "wall_seconds": round(time.time() - t_start, 1)}, fh, indent=2)
    print("wrote exp8_sumo_runs.csv, exp8_sumo_summary.csv, exp8_config.json to", OUT)


def _sumo_version():
    import subprocess, sumolib
    out = subprocess.run([sumolib.checkBinary("sumo"), "--version"], capture_output=True, text=True).stdout
    return out.splitlines()[0] if out else "unknown"


if __name__ == "__main__":
    main()
