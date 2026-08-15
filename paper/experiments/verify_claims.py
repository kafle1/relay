#!/usr/bin/env python3
"""Check every number main.tex asserts against the raw result files.

Two figures in the manuscript did not match the data and nobody caught them by reading prose
against a table. This reads the claims out of the tex and recomputes each one from
paper/experiments/raw.

    python3 paper/experiments/verify_claims.py

Exits non-zero if a claim drifts, if the tex is unreadable, or if a claim it expects to find has
gone missing. A missing claim is a failure, not a skip: a checker that quietly verifies nothing
is worse than no checker.
"""

import csv
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
RAW = os.path.join(HERE, "raw")
TEX = os.path.normpath(os.path.join(HERE, "..", "main.tex"))

TOL = 0.05      # claims are quoted to one decimal place
VPH_TOL = 1.0   # demand is quoted whole


def load(name):
    with open(os.path.join(RAW, name), encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        sys.exit(f"{name} is empty")
    return rows


def col(rows, key):
    try:
        return [float(r[key]) for r in rows]
    except (KeyError, TypeError, ValueError):
        sys.exit(f"column {key!r} missing or non-numeric in {len(rows)} raw rows")


def claims():
    """Every number the manuscript prints that this file knows how to re-derive."""
    if not os.path.exists(TEX):
        sys.exit(f"{TEX} not found. Run this from a full clone of the repository.")
    with open(TEX, encoding="utf-8") as f:
        tex = f.read()
    patterns = {
        "webster": r"by ([\d.]+)\\% to ([\d.]+)\\% against the Webster plan",
        "equal": r"by ([\d.]+)\\% to ([\d.]+)\\% against the equal-split plan",
        "grid": r"spanning (\d+) to (\d+)~veh/h",
        "cells": r"in (\d+) of (\d+) cells",
        "preempt_mean": r"([\d.]+)~s on\s*average over (\d+) trials",
        "preempt_max": r"worst case of ([\d.]+)~s against an analytical bound of ([\d.]+)~s",
    }
    out = {}
    for name, pat in patterns.items():
        m = re.search(pat, tex)
        if not m:
            sys.exit(f"main.tex no longer contains the {name} claim (pattern: {pat}).\n"
                     f"The paper changed shape. Update this file rather than deleting the check.")
        out[name] = tuple(float(g) for g in m.groups())
    return out


def main():
    c = claims()
    e1, e2, e3 = load("exp1_summary.csv"), load("exp2_summary.csv"), load("exp3_preemption.csv")
    e6 = {"clean": load("exp6_robustness.csv"), "noise": load("exp6_robustness_noise0.2.csv")}
    lat = col(e3, "latency_relay")

    checks = [
        ("exp1 cells", len(e1), 15.0, 0),
        ("exp1 seeds per cell", float(sorted({r["n_seeds"] for r in e1})[0]), 30.0, 0),
        ("min reduction vs Webster", min(col(e1, "red_vs_fixed_webster_mean")), c["webster"][0], TOL),
        ("max reduction vs Webster", max(col(e1, "red_vs_fixed_webster_mean")), c["webster"][1], TOL),
        ("min reduction vs equal-split", min(col(e1, "red_vs_fixed_equal_mean")), c["equal"][0], TOL),
        ("max reduction vs equal-split", max(col(e1, "red_vs_fixed_equal_mean")), c["equal"][1], TOL),
        ("every exp1 cell significant", max(col(e1, "paired_fixed_webster_p_wilcoxon")) < 0.05, True, 0),
        ("corridor max gain vs Webster", max(col(e2, "red_vs_fixed_webster_mean")), 57.9, TOL),
        ("corridor min gain vs Webster", min(col(e2, "red_vs_fixed_webster_mean")), 44.6, TOL),
        ("coupling term, largest effect", max(abs(x) for x in col(e2, "red_vs_relay_isolated_mean")), 0.9, TOL),
        ("preemption trials", float(len(e3)), c["preempt_mean"][1], 0),
        ("preemption mean latency", sum(lat) / len(lat), c["preempt_mean"][0], 0.005),
        ("preemption worst latency", max(lat), c["preempt_max"][0], 0.005),
        ("trials breaching the bound", float(sum(1 for x in lat if x > c["preempt_max"][1])), 0.0, 0),
    ]
    for tag, rows in e6.items():
        wins = sum(1 for r in rows
                   if float(r["red_vs_fixed_equal"]) > 0 and float(r["red_vs_fixed_webster"]) > 0)
        floor = 27.1 if tag == "clean" else 26.1
        checks += [
            (f"{tag}: cells", float(len(rows)), c["cells"][1], 0),
            (f"{tag}: cells beating both", float(wins), c["cells"][0], 0),
            (f"{tag}: worst margin vs equal-split", min(col(rows, "red_vs_fixed_equal")), floor, TOL),
            (f"{tag}: grid lower bound", min(col(rows, "total_demand_vph")), c["grid"][0], VPH_TOL),
            (f"{tag}: grid upper bound", max(col(rows, "total_demand_vph")), c["grid"][1], VPH_TOL),
        ]

    failed = []
    for label, got, want, tol in checks:
        ok = got == want if tol == 0 else abs(got - want) <= tol
        print(f"  {'ok  ' if ok else 'FAIL'}  {label:36} data={got!s:<10.10} paper={want}")
        if not ok:
            failed.append(f"{label}: data {got}, paper {want}")

    print(f"\n{len(checks) - len(failed)}/{len(checks)} claims verified against the raw data.")
    if failed:
        print("\nDRIFTED:")
        for f in failed:
            print("  -", f)
        return 1
    print("No drift.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
