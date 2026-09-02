#!/usr/bin/env python
"""Experiment 10: the two parameters the paper says it is most sensitive to.

  R      the vehicle maximum-wait bound, from 30 s to unbounded, on the single-dominant shape
         at the two highest loads. An earlier version of the paper blamed this bound for the
         one cell a fixed plan won; this sweep is what showed that explanation was wrong.
  w_moto the motorcycle PCU weight, with a class-mixed queue whose two-wheeler share differs
         by axis (80% on N-S, 30% on E-W), so the weight changes which phase looks heavier.

Both on the point-queue harness, identical arrivals, 25 seeds.

Usage:  RELAY_REPO=/path/to/relay .venv/bin/python paper/experiments/exp10_sensitivity.py
"""
import csv
import json
import os
import random
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from relay_bench import Controller, QueueSystem, Timings, WebsterTimer, four_way, poisson   # noqa: E402
from exp6_robustness import FOUR_WAY, TIMINGS, SECONDS, WARMUP, DT                          # noqa: E402

REPO = os.environ.get("RELAY_REPO", os.getcwd())
sys.path.insert(0, os.path.join(REPO, "src"))
import controller as ctl                                                                     # noqa: E402

OUT = os.environ.get("RELAY_OUT", os.path.join(os.path.dirname(os.path.abspath(__file__)), "raw"))
os.makedirs(OUT, exist_ok=True)
SEEDS = list(range(25))
R_VALUES = [30.0, 45.0, 60.0, 90.0, 120.0, 180.0, 1e9]
MOTO_WEIGHTS = [0.2, 0.3, 0.5, 1.0]
# two-wheeler share differs by axis, so the weight changes which phase looks heavier
MIX = {"NS": {"motorcycle": 0.80, "car": 0.15, "bus": 0.03, "truck": 0.02},
       "EW": {"motorcycle": 0.30, "car": 0.60, "bus": 0.06, "truck": 0.04}}


def run(lam, seed, *, timings, classed=False):
    random.seed(seed)
    J = four_way()
    relay, web = Controller(J, timings), WebsterTimer(J, lam, yellow=timings.yellow, all_red=timings.all_red,
                                                     min_green=timings.min_green)
    qs = {"relay": QueueSystem(J.approaches, warmup=WARMUP), "webster": QueueSystem(J.approaches, warmup=WARMUP)}
    cls_q = {a: [] for a in J.approaches}           # class of each queued vehicle, relay side
    mix_of = {a: MIX["NS" if a in "NS" else "EW"] for a in J.approaches}
    for i in range(int(SECONDS / DT)):
        t_now = i * DT
        warm = t_now >= WARMUP
        for a in J.approaches:
            n = poisson(lam[a] * DT)
            for q in qs.values():
                q.arrive(a, n, t_now)
            if classed:
                cls_q[a].extend(random.choices(list(mix_of[a]), list(mix_of[a].values()), k=n))
        if classed:
            seen = {a: {c: cls_q[a].count(c) for c in mix_of[a]} for a in J.approaches}
        else:
            seen = {a: qs["relay"].q[a] for a in J.approaches}
        before = {a: qs["relay"].q[a] for a in J.approaches}
        g = {a for a, s in relay.tick(seen, (), DT)["signals"].items() if s == "green"}
        qs["relay"].discharge(g, t_now, DT, record=warm)
        if classed:
            for a in J.approaches:
                left = before[a] - qs["relay"].q[a]
                del cls_q[a][:left]
        qs["webster"].discharge(web.tick(None, (), DT), t_now, DT, record=warm)
        if warm:
            for q in qs.values():
                q.sample()
    return {k: q.metrics(SECONDS) for k, q in qs.items()}


def main():
    rows = []
    # R frontier on the single-dominant shape at the two highest loads
    for level in (1.4, 1.8):
        lam = {a: v * level for a, v in FOUR_WAY["single"].items()}
        for R in R_VALUES:
            T = Timings(**dict(TIMINGS, max_wait=R))
            cell = [run(lam, s, timings=T) for s in SEEDS]
            row = dict(sweep="max_wait", value=R if R < 1e8 else "inf", shape="single", level=level,
                       total_demand_vph=round(sum(lam.values()) * 3600, 1))
            for k in ("mean_wait", "p95_wait", "max_wait", "throughput_vph"):
                row[f"relay_{k}"] = round(statistics.fmean(c["relay"][k] for c in cell), 3)
                row[f"webster_{k}"] = round(statistics.fmean(c["webster"][k] for c in cell), 3)
            row["red_vs_webster"] = round(statistics.fmean(
                (c["webster"]["mean_wait"] - c["relay"]["mean_wait"]) / c["webster"]["mean_wait"] * 100 for c in cell), 2)
            rows.append(row)
            print(f"R={row['value']:<6} m={level}  relay mean {row['relay_mean_wait']:.1f}s  max {row['relay_max_wait']:.0f}s  "
                  f"vs webster {row['red_vs_webster']:+.1f}%", flush=True)
    # motorcycle weight with a class-mixed queue, three shapes at level 1.0
    original = ctl.PCU["motorcycle"]
    try:
        for shape in ("asymmetric", "balanced", "single"):
            lam = FOUR_WAY[shape]
            for w in MOTO_WEIGHTS:
                ctl.PCU["motorcycle"] = w
                cell = [run(lam, s, timings=Timings(**TIMINGS), classed=True) for s in SEEDS]
                row = dict(sweep="w_motorcycle", value=w, shape=shape, level=1.0,
                           total_demand_vph=round(sum(lam.values()) * 3600, 1))
                for k in ("mean_wait", "p95_wait", "max_wait", "throughput_vph"):
                    row[f"relay_{k}"] = round(statistics.fmean(c["relay"][k] for c in cell), 3)
                    row[f"webster_{k}"] = round(statistics.fmean(c["webster"][k] for c in cell), 3)
                row["red_vs_webster"] = round(statistics.fmean(
                    (c["webster"]["mean_wait"] - c["relay"]["mean_wait"]) / c["webster"]["mean_wait"] * 100 for c in cell), 2)
                rows.append(row)
                print(f"w_moto={w:<4} {shape:<11} relay mean {row['relay_mean_wait']:.2f}s  vs webster {row['red_vs_webster']:+.1f}%", flush=True)
    finally:
        ctl.PCU["motorcycle"] = original
    with open(os.path.join(OUT, "exp10_sensitivity.csv"), "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys())); w.writeheader(); w.writerows(rows)
    with open(os.path.join(OUT, "exp10_config.json"), "w") as fh:
        json.dump({"seeds": SEEDS, "R_values": R_VALUES, "moto_weights": MOTO_WEIGHTS, "mix": MIX,
                   "timings": TIMINGS, "seconds": SECONDS, "warmup": WARMUP, "dt": DT}, fh, indent=2)
    print("wrote exp10_sensitivity.csv to", OUT)


if __name__ == "__main__":
    main()
