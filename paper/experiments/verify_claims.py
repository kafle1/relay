#!/usr/bin/env python3
"""Recompute every number the paper asserts, straight from the raw result files.

Two numbers in main.tex did not match the data and nobody caught them by reading: the
vs-Webster minimum was the equal-split column's minimum copied one sentence across, and the
robustness grid's lower bound was quoted 115 vph too high. Reading prose against a table does
not catch that. This does.

    python3 paper/experiments/verify_claims.py

Exits non-zero if any claim drifts from the data. Run it before every submission, and after
any edit that touches a number.
"""

import csv
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
RAW = os.path.join(HERE, "raw")
TEX = os.path.normpath(os.path.join(HERE, "..", "main.tex"))

TOL = 0.05          # a claim is quoted to 1 d.p., so allow half a decimal place


def load(name):
    with open(os.path.join(RAW, name), encoding="utf-8") as f:
        return list(csv.DictReader(f))


def num(row, key):
    """Missing or non-numeric cells are a data problem, not something to paper over."""
    try:
        return float(row[key])
    except (KeyError, TypeError, ValueError):
        raise SystemExit(f"column {key!r} missing or non-numeric in a raw row")


class Check:
    def __init__(self):
        self.fails = []
        self.n = 0

    def close(self, label, got, claimed, tol=TOL):
        self.n += 1
        ok = abs(got - claimed) <= tol
        print(f"  {'ok  ' if ok else 'FAIL'}  {label:52} data={got:<10.4g} paper={claimed:g}")
        if not ok:
            self.fails.append(f"{label}: data says {got:.4g}, paper says {claimed:g}")

    def equal(self, label, got, claimed):
        self.n += 1
        ok = got == claimed
        print(f"  {'ok  ' if ok else 'FAIL'}  {label:52} data={got!s:<10} paper={claimed}")
        if not ok:
            self.fails.append(f"{label}: data says {got}, paper says {claimed}")


def tex_claims():
    """Pull the numbers the manuscript actually prints, so the two can't drift apart."""
    try:
        with open(TEX, encoding="utf-8") as f:
            t = f.read()
    except OSError:
        print(f"note: {TEX} not readable, checking raw data only\n")
        return {}
    out = {}
    m = re.search(r"by ([\d.]+)\\% to ([\d.]+)\\% against the Webster plan", t)
    if m:
        out["webster_min"], out["webster_max"] = float(m.group(1)), float(m.group(2))
    m = re.search(r"by ([\d.]+)\\% to ([\d.]+)\\% against the equal-split plan", t)
    if m:
        out["equal_min"], out["equal_max"] = float(m.group(1)), float(m.group(2))
    m = re.search(r"spanning (\d+) to (\d+)~veh/h", t)
    if m:
        out["grid_lo"], out["grid_hi"] = float(m.group(1)), float(m.group(2))
    m = re.search(r"in (\d+) of (\d+) cells", t)
    if m:
        out["wins"], out["cells"] = int(m.group(1)), int(m.group(2))
    m = re.search(r"([\d.]+)~s on\s*average over (\d+) trials", t)
    if m:
        out["preempt_mean"], out["preempt_n"] = float(m.group(1)), int(m.group(2))
    m = re.search(r"measured worst case of ([\d.]+)~s against an analytical bound of ([\d.]+)~s", t)
    if m:
        out["preempt_max"], out["preempt_bound"] = float(m.group(1)), float(m.group(2))
    return out


def main():
    c = Check()
    claim = tex_claims()

    print("Experiment 1, single junction")
    e1 = load("exp1_summary.csv")
    web = [num(r, "red_vs_fixed_webster_mean") for r in e1]
    eq = [num(r, "red_vs_fixed_equal_mean") for r in e1]
    c.equal("exp1 cells", len(e1), 15)
    c.equal("exp1 seeds per cell", {r["n_seeds"] for r in e1}, {"30"})
    if "webster_min" in claim:
        c.close("min reduction vs Webster", min(web), claim["webster_min"])
        c.close("max reduction vs Webster", max(web), claim["webster_max"])
    if "equal_min" in claim:
        c.close("min reduction vs equal-split", min(eq), claim["equal_min"])
        c.close("max reduction vs equal-split", max(eq), claim["equal_max"])
    worst_p = max(num(r, "paired_fixed_webster_p_wilcoxon") for r in e1)
    c.equal("every cell significant at p<0.05", worst_p < 0.05, True)

    print("\nExperiment 2, corridor")
    e2 = load("exp2_summary.csv")
    c.close("corridor max gain vs Webster",
            max(num(r, "red_vs_fixed_webster_mean") for r in e2), 57.9)
    c.close("corridor min gain vs Webster",
            min(num(r, "red_vs_fixed_webster_mean") for r in e2), 44.6)
    c.close("coupling term, largest effect either way",
            max(abs(num(r, "red_vs_relay_isolated_mean")) for r in e2), 0.9, tol=0.05)

    print("\nExperiment 3, emergency preemption")
    p3 = load("exp3_preemption.csv")
    lat = [num(r, "latency_relay") for r in p3]
    if "preempt_n" in claim:
        c.equal("preemption trials", len(p3), claim["preempt_n"])
        c.close("mean latency", sum(lat) / len(lat), claim["preempt_mean"], tol=0.005)
    if "preempt_max" in claim:
        c.close("worst latency", max(lat), claim["preempt_max"], tol=0.005)
        c.equal("trials breaching the analytical bound",
                sum(1 for x in lat if x > claim["preempt_bound"]), 0)

    print("\nExperiment 6, robustness grid")
    for fname, floor in (("exp6_robustness.csv", 27.1), ("exp6_robustness_noise0.2.csv", 26.1)):
        e6 = load(fname)
        wins = sum(1 for r in e6
                   if num(r, "red_vs_fixed_equal") > 0 and num(r, "red_vs_fixed_webster") > 0)
        tag = "clean" if "noise" not in fname else "noise"
        if "cells" in claim:
            c.equal(f"{tag}: cells", len(e6), claim["cells"])
            c.equal(f"{tag}: cells beating both baselines", wins, claim["wins"])
        c.close(f"{tag}: worst margin vs equal-split",
                min(num(r, "red_vs_fixed_equal") for r in e6), floor)
        if "grid_lo" in claim:
            c.close(f"{tag}: grid lower bound (veh/h)",
                    min(num(r, "total_demand_vph") for r in e6), claim["grid_lo"], tol=1.0)
            c.close(f"{tag}: grid upper bound (veh/h)",
                    max(num(r, "total_demand_vph") for r in e6), claim["grid_hi"], tol=1.0)

    print(f"\n{c.n - len(c.fails)}/{c.n} claims verified against the raw data.")
    if c.fails:
        print("\nDRIFTED:")
        for f in c.fails:
            print("  -", f)
        return 1
    print("No drift.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
