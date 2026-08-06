#!/usr/bin/env python
"""Experiment 3: emergency-vehicle preemption latency.

For each trial the junction is first driven to a loaded steady state by the same seeded
arrival process used in Experiment 1. At a randomly chosen instant an emergency vehicle is
announced on a randomly chosen approach and the announcement is then repeated every step
(as a real detector would, frame after frame). We record the wall-clock simulated delay from
the first announcement until that approach's signal reads green.

The same trial is replayed against the fixed-time baseline, which has no preemption path:
there the emergency simply waits for its phase to come round in the cycle. Both policies see
the identical arrival stream and the identical announcement instant, so the two latencies are
paired.

We additionally record the signal stage at the instant of announcement, because the lower
bound on latency is set by the clearance interval the controller must still serve
(yellow + all-red) plus, when the conflicting phase has only just turned green, the remainder
of its minimum green.

Usage:  RELAY_REPO=/path/to/relay .venv/bin/python experiments/exp3_preemption.py
"""
import csv
import json
import os
import random
import statistics
import sys

REPO = os.environ.get("RELAY_REPO", os.getcwd())
sys.path.insert(0, os.path.join(REPO, "src"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from controller import Controller, Timings, four_way          # noqa: E402
from microsim import FixedTimer, SAT, poisson                 # noqa: E402
from exp1_single_junction import QueueSystem, TIMINGS, FIXED_GREEN, PROFILES, DT, _pct  # noqa: E402

OUT = os.environ.get("RELAY_OUT", os.path.join(os.path.dirname(os.path.abspath(__file__)), "raw"))
os.makedirs(OUT, exist_ok=True)

N_TRIALS = 300
SETTLE_MIN, SETTLE_MAX = 40.0, 160.0     # simulated seconds of background traffic before the call
HORIZON = 200.0                          # give up after this long (never reached in practice)
LEVELS = {"light": 0.6, "medium": 1.0, "heavy": 1.4}


def one_trial(seed, level_mult, profile="asymmetric"):
    random.seed(seed)
    lam = {a: v * level_mult for a, v in PROFILES[profile].items()}
    J = four_way()
    adaptive = Controller(J, Timings(**TIMINGS))
    fixed = FixedTimer(J, green=FIXED_GREEN, yellow=TIMINGS["yellow"], all_red=TIMINGS["all_red"])
    sys_a, sys_f = QueueSystem(J.approaches), QueueSystem(J.approaches)

    settle = random.uniform(SETTLE_MIN, SETTLE_MAX)
    arm = random.choice(J.approaches)
    t, lat_a, lat_f = 0.0, None, None
    stage_at_call, phase_at_call, elapsed_at_call, arm_green_at_call = None, None, None, None
    hold_a = 0.0

    while t < settle + HORIZON:
        called = t >= settle
        for a in J.approaches:
            n = poisson(lam[a] * DT)
            sys_a.arrive(a, n, t)
            sys_f.arrive(a, n, t)
        # the ambulance itself occupies its approach once it has arrived
        counts_a = {a: dict(car=sys_a.q[a]) for a in J.approaches}
        counts_f = {a: dict(car=sys_f.q[a]) for a in J.approaches}
        if called:
            counts_a[arm]["ambulance"] = 1
            counts_f[arm]["ambulance"] = 1
        state = adaptive.tick(counts_a, {arm} if called else (), DT)
        green_a = {a for a, s in state["signals"].items() if s == "green"}
        green_f = fixed.tick(counts_f, (), DT)

        if called and stage_at_call is None:
            stage_at_call = state["stage"]
            phase_at_call = state["phase"]
            elapsed_at_call = state["elapsed"]
            arm_green_at_call = arm in green_a
        if called and lat_a is None and arm in green_a:
            lat_a = t - settle
        if called and lat_f is None and arm in green_f:
            lat_f = t - settle
        if called and lat_a is not None and arm in green_a:
            hold_a += DT
        sys_a.discharge(green_a, t, DT, False)
        sys_f.discharge(green_f, t, DT, False)
        if lat_a is not None and lat_f is not None and t - settle > 90:
            break
        t += DT

    return dict(seed=seed, level=level_mult, arm=arm, settle=round(settle, 2),
                stage_at_call=stage_at_call, phase_at_call=phase_at_call,
                elapsed_at_call=elapsed_at_call, already_green=int(bool(arm_green_at_call)),
                latency_relay=lat_a, latency_fixed=lat_f, green_hold_relay=round(hold_a, 2))


def main():
    rows = []
    for name, mult in LEVELS.items():
        for k in range(N_TRIALS):
            r = one_trial(seed=10_000 + k, level_mult=mult)
            r["level_name"] = name
            rows.append(r)
        got = [r["latency_relay"] for r in rows if r["level_name"] == name and r["latency_relay"] is not None]
        print(f"{name}: n={len(got)} mean={statistics.fmean(got):.2f}s max={max(got):.2f}s", flush=True)

    with open(os.path.join(OUT, "exp3_preemption.csv"), "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    summary = []
    for name in list(LEVELS) + ["all"]:
        sel = rows if name == "all" else [r for r in rows if r["level_name"] == name]
        la = [r["latency_relay"] for r in sel if r["latency_relay"] is not None]
        lf = [r["latency_fixed"] for r in sel if r["latency_fixed"] is not None]
        paired = [(r["latency_fixed"], r["latency_relay"]) for r in sel
                  if r["latency_relay"] is not None and r["latency_fixed"] is not None]
        entry = dict(level=name, n=len(sel), n_resolved_relay=len(la), n_resolved_fixed=len(lf),
                     relay_mean=round(statistics.fmean(la), 3),
                     relay_sd=round(statistics.stdev(la), 3) if len(la) > 1 else 0.0,
                     relay_p50=round(_pct(la, 50), 2), relay_p95=round(_pct(la, 95), 2),
                     relay_max=round(max(la), 2), relay_min=round(min(la), 2),
                     fixed_mean=round(statistics.fmean(lf), 3),
                     fixed_sd=round(statistics.stdev(lf), 3) if len(lf) > 1 else 0.0,
                     fixed_p50=round(_pct(lf, 50), 2), fixed_p95=round(_pct(lf, 95), 2),
                     fixed_max=round(max(lf), 2),
                     already_green_frac=round(statistics.fmean([r["already_green"] for r in sel]), 4))
        if paired:
            entry["mean_saving_s"] = round(statistics.fmean([f - a for f, a in paired]), 3)
            entry["saving_sd_s"] = round(statistics.stdev([f - a for f, a in paired]), 3) if len(paired) > 1 else 0.0
        summary.append(entry)
        print(entry, flush=True)

    with open(os.path.join(OUT, "exp3_summary.csv"), "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(summary[0].keys()))
        w.writeheader()
        w.writerows(summary)
    with open(os.path.join(OUT, "exp3_config.json"), "w") as fh:
        json.dump({"n_trials_per_level": N_TRIALS, "settle_range_s": [SETTLE_MIN, SETTLE_MAX],
                   "levels": LEVELS, "profile": "asymmetric", "timings": TIMINGS,
                   "fixed_green": FIXED_GREEN, "dt": DT, "sat_flow_veh_per_s": SAT}, fh, indent=2)
    print("wrote exp3_preemption.csv, exp3_summary.csv to", OUT)


if __name__ == "__main__":
    main()
