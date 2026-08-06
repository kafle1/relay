#!/usr/bin/env python
"""Generate every result plot in the paper from the raw CSVs produced by the experiments.

No number in any figure is typed in by hand: each panel reads the CSV that the corresponding
experiment wrote. Re-running the experiments and then this script reproduces the figures.

Usage:  .venv/bin/python experiments/make_figures.py
"""
import csv
import os
import statistics

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt      # noqa: E402
import numpy as np                   # noqa: E402
from matplotlib.ticker import FixedLocator, NullFormatter, ScalarFormatter   # noqa: E402


def tidy_log(ax, ticks):
    """Plain decimal labels on a log axis; matplotlib's default minor labels collide."""
    ax.yaxis.set_major_locator(FixedLocator(ticks))
    fmt = ScalarFormatter()
    fmt.set_scientific(False)
    ax.yaxis.set_major_formatter(fmt)
    ax.yaxis.set_minor_formatter(NullFormatter())

HERE = os.path.dirname(os.path.abspath(__file__))
RAW = os.environ.get("RELAY_OUT", os.path.join(HERE, "raw"))
FIGS = os.environ.get("RELAY_FIGS", os.path.abspath(os.path.join(HERE, "..", "figures")))
os.makedirs(FIGS, exist_ok=True)

plt.rcParams.update({
    "font.family": "serif", "font.size": 9, "axes.labelsize": 9, "axes.titlesize": 9,
    "legend.fontsize": 8, "xtick.labelsize": 8, "ytick.labelsize": 8,
    "axes.grid": True, "grid.alpha": 0.25, "grid.linewidth": 0.5,
    "axes.spines.top": False, "axes.spines.right": False, "figure.dpi": 300,
    "savefig.bbox": "tight", "savefig.pad_inches": 0.02, "errorbar.capsize": 2,
})
C = {"relay": "#1b4965", "fixed_equal": "#c1666b", "fixed_webster": "#e8a33d",
     "relay_coupled": "#1b4965", "relay_isolated": "#5fa8d3", "fixed_coord": "#7a5c99"}
LBL = {"relay": "R.E.L.A.Y.", "fixed_equal": "Fixed, equal split",
       "fixed_webster": "Fixed, Webster", "relay_coupled": "R.E.L.A.Y. (coupled)",
       "relay_isolated": "R.E.L.A.Y. (isolated)", "fixed_coord": "Fixed, coordinated"}
PROF = {"asymmetric": "Asymmetric demand", "balanced": "Balanced demand",
        "single": "Single dominant approach"}


def load(name):
    with open(os.path.join(RAW, name)) as fh:
        return list(csv.DictReader(fh))


def f(row, key):
    v = row.get(key, "")
    return float(v) if v not in ("", None) else float("nan")


# ── Figure: single-junction delay and reduction vs demand ────────────────────
def fig_exp1():
    s = load("exp1_summary.csv")
    profs = ["asymmetric", "balanced", "single"]
    fig, ax = plt.subplots(2, 3, figsize=(7.1, 4.4), sharex=True)
    fig.subplots_adjust(wspace=0.34, hspace=0.18)
    tickset = {"asymmetric": [5, 10, 20, 50], "balanced": [5, 10, 20],
               "single": [5, 10, 50, 100, 300]}
    for j, p in enumerate(profs):
        rs = sorted([r for r in s if r["profile"] == p], key=lambda r: float(r["level"]))
        x = [f(r, "total_demand_vph") for r in rs]
        for pol in ("fixed_equal", "fixed_webster", "relay"):
            y = [f(r, f"{pol}_mean_wait_mean") for r in rs]
            e = [f(r, f"{pol}_mean_wait_sd") for r in rs]
            ax[0][j].errorbar(x, y, yerr=e, marker="o", ms=3, lw=1.2, color=C[pol],
                              label=LBL[pol])
        ax[0][j].set_title(PROF[p])
        ax[0][j].set_yscale("log")
        tidy_log(ax[0][j], tickset[p])
        if j == 0:
            ax[0][j].set_ylabel("Mean delay per vehicle (s)")
        for pol, ls in (("fixed_equal", "--"), ("fixed_webster", "-")):
            y = [f(r, f"red_vs_{pol}_mean") for r in rs]
            e = [f(r, f"red_vs_{pol}_sd") for r in rs]
            ax[1][j].errorbar(x, y, yerr=e, marker="s", ms=3, lw=1.2, ls=ls, color=C[pol],
                              label=f"vs {LBL[pol]}")
        ax[1][j].axhline(0, color="0.35", lw=0.8, ls=":")
        ax[1][j].set_xlabel("Total demand (veh/h)")
        if j == 0:
            ax[1][j].set_ylabel("Delay reduction (%)")
    ax[0][0].legend(frameon=False, loc="upper left")
    ax[1][0].legend(frameon=False, loc="lower left")
    fig.savefig(os.path.join(FIGS, "exp1_delay.pdf"))
    plt.close(fig)


# ── Figure: wait-time distribution tail (p50 / p95 / max) ───────────────────
def fig_exp1_tail():
    s = load("exp1_summary.csv")
    profs = ["asymmetric", "balanced", "single"]
    fig, ax = plt.subplots(1, 3, figsize=(7.1, 2.5), sharey=True)
    fig.subplots_adjust(wspace=0.12)
    width = 0.26
    for j, p in enumerate(profs):
        r = next(x for x in s if x["profile"] == p and float(x["level"]) == 1.0)
        keys = ["p50_wait", "p95_wait", "max_wait"]
        idx = np.arange(len(keys))
        for k, pol in enumerate(("fixed_equal", "fixed_webster", "relay")):
            vals = [f(r, f"{pol}_{q}_mean") for q in keys]
            errs = [f(r, f"{pol}_{q}_sd") for q in keys]
            ax[j].bar(idx + (k - 1) * width, vals, width, yerr=errs, color=C[pol],
                      label=LBL[pol], edgecolor="none")
        ax[j].set_xticks(idx)
        ax[j].set_xticklabels(["median", "95th pct", "worst"])
        ax[j].set_title(f"{PROF[p]}\n({f(r, 'total_demand_vph'):.0f} veh/h)")
        ax[j].set_yscale("log")
    tidy_log(ax[0], [5, 10, 20, 50, 100, 300])
    ax[0].set_ylabel("Wait per vehicle (s)")
    ax[2].legend(frameon=False, loc="upper left")
    fig.savefig(os.path.join(FIGS, "exp1_tail.pdf"))
    plt.close(fig)


# ── Figure: switching frequency and green-time fraction ─────────────────────
def fig_exp1b():
    s = load("exp1b_summary.csv")
    profs = ["asymmetric", "balanced", "single"]
    fig, ax = plt.subplots(1, 2, figsize=(7.1, 2.4))
    marks = {"asymmetric": "o", "balanced": "s", "single": "^"}
    cols = {"asymmetric": "#1b4965", "balanced": "#c1666b", "single": "#3f7d5b"}
    for p in profs:
        rs = sorted([r for r in s if r["profile"] == p], key=lambda r: float(r["level"]))
        x = [float(r["level"]) for r in rs]
        ax[0].errorbar(x, [f(r, "sw_relay_per_h_mean") for r in rs],
                       yerr=[f(r, "sw_relay_per_h_sd") for r in rs],
                       marker=marks[p], ms=3.5, lw=1.2, color=cols[p], label=PROF[p])
        ax[1].errorbar(x, [f(r, "green_frac_relay_mean") for r in rs],
                       yerr=[f(r, "green_frac_relay_sd") for r in rs],
                       marker=marks[p], ms=3.5, lw=1.2, color=cols[p], label=PROF[p])
    r0 = s[0]
    ax[0].axhline(f(r0, "sw_fixed_per_h_mean"), color="0.4", ls="--", lw=1,
                  label="Fixed, equal split")
    ax[1].axhline(f(r0, "green_frac_fixed_mean"), color="0.4", ls="--", lw=1,
                  label="Fixed, equal split")
    ax[0].set_xlabel("Demand multiplier")
    ax[0].set_ylabel("Phase switches per hour")
    ax[1].set_xlabel("Demand multiplier")
    ax[1].set_ylabel("Fraction of time with a green")
    ax[0].legend(frameon=False, loc="lower right")
    fig.savefig(os.path.join(FIGS, "exp1b_switching.pdf"))
    plt.close(fig)


# ── Figure: corridor delay, through travel time, ambulance traversal ────────
def fig_exp2():
    s = sorted(load("exp2_summary.csv"), key=lambda r: float(r["level"]))
    pols = ["fixed_webster", "fixed_coord", "relay_isolated", "relay_coupled"]
    x = np.arange(len(s))
    width = 0.2
    fig, ax = plt.subplots(1, 3, figsize=(7.1, 2.7))
    fig.subplots_adjust(wspace=0.42, top=0.78)
    for k, pol in enumerate(pols):
        off = (k - 1.5) * width
        ax[0].bar(x + off, [f(r, f"{pol}_mean_delay_mean") for r in s], width,
                  yerr=[f(r, f"{pol}_mean_delay_sd") for r in s], color=C[pol],
                  label=LBL[pol], edgecolor="none")
        ax[1].bar(x + off, [f(r, f"{pol}_mean_through_travel_mean") for r in s], width,
                  yerr=[f(r, f"{pol}_mean_through_travel_sd") for r in s], color=C[pol],
                  edgecolor="none")
        amb = [f(r, f"{pol}_amb_corridor_mean") for r in s]
        ax[2].bar(x + off, amb, width, color=C[pol], edgecolor="none")
    labels = [f"{f(r, 'entering_vph'):.0f}" for r in s]
    for a, ylab in zip(ax, ("Mean delay per vehicle (s)", "Through travel time (s)",
                            "Emergency traversal (s)")):
        a.set_xticks(x)
        a.set_xticklabels(labels)
        a.set_xlabel("Corridor demand (veh/h)")
        a.set_ylabel(ylab)
    ax[2].axhline(21.6, color="0.3", ls=":", lw=1)
    ax[2].annotate("free flow", (1.55, 23.5), fontsize=7, color="0.3")
    ax[2].set_ylim(0, 60)
    handles, labels_ = ax[0].get_legend_handles_labels()
    fig.legend(handles, labels_, frameon=False, ncol=4, loc="upper center",
               bbox_to_anchor=(0.5, 1.03), columnspacing=1.4, handlelength=1.2)
    fig.savefig(os.path.join(FIGS, "exp2_corridor.pdf"))
    plt.close(fig)


# ── Figure: preemption latency distribution ─────────────────────────────────
def fig_exp3():
    rows = load("exp3_preemption.csv")
    la = [float(r["latency_relay"]) for r in rows if r["latency_relay"]not in ("", None)]
    lf = [float(r["latency_fixed"]) for r in rows if r["latency_fixed"] not in ("", None)]
    fig, ax = plt.subplots(1, 2, figsize=(7.1, 2.4))
    bins = np.arange(0, 25.5, 1.0)
    ax[0].hist(lf, bins=bins, color=C["fixed_equal"], alpha=0.75, label="Fixed, equal split")
    ax[0].hist(la, bins=bins, color=C["relay"], alpha=0.8, label="R.E.L.A.Y. preemption")
    ax[0].set_xlabel("Time to green for the emergency approach (s)")
    ax[0].set_ylabel("Trials")
    ax[0].legend(frameon=False)
    for v, c, lb in ((la, C["relay"], "R.E.L.A.Y. preemption"),
                     (lf, C["fixed_equal"], "Fixed, equal split")):
        xs = np.sort(v)
        ax[1].step(xs, np.arange(1, len(xs) + 1) / len(xs), color=c, lw=1.4, label=lb)
    ax[1].axvline(14.0, color="0.3", ls=":", lw=1)
    ax[1].annotate("analytical\nbound 14.0 s", (14.4, 0.25), fontsize=7, color="0.3")
    ax[1].set_xlabel("Time to green (s)")
    ax[1].set_ylabel("Empirical CDF")
    ax[1].set_xlim(0, 25)
    ax[1].legend(frameon=False, loc="lower right")
    fig.savefig(os.path.join(FIGS, "exp3_preemption.pdf"))
    plt.close(fig)


# ── Figure: detector per-class AP and resolution sensitivity ────────────────
def fig_detector():
    dv = load("detector_val.csv")
    row = next(r for r in dv if r["model"] == "relay_ft_mixed")
    classes = ["car", "motorcycle", "bus", "truck", "ambulance"]
    aps = [f(row, f"mAP50_{c}") for c in classes]
    res = load("exp5b_resolution.csv")
    fig, ax = plt.subplots(1, 2, figsize=(7.1, 2.4))
    ax[0].barh(classes, aps, color="#1b4965", edgecolor="none", height=0.6)
    ax[0].axvline(f(row, "mAP50"), color=C["fixed_equal"], ls="--", lw=1,
                  label=f"overall {f(row, 'mAP50'):.3f}")
    for i, v in enumerate(aps):
        ax[0].annotate(f"{v:.3f}", (v + 0.012, i), va="center", fontsize=7)
    ax[0].set_xlim(0, 1.12)
    ax[0].set_xlabel("AP@50 on the held-out split")
    ax[0].legend(frameon=False, loc="lower right")
    marks = {"levanhien": "o", "hcmc": "s"}
    names = {"levanhien": "Boulevard clip", "hcmc": "Dense mixed-traffic clip"}
    cols = {"levanhien": "#1b4965", "hcmc": "#c1666b"}
    for clip in ("levanhien", "hcmc"):
        rs = sorted([r for r in res if r["clip"] == clip], key=lambda r: int(r["imgsz"]))
        base = f(rs[0], "veh_mean")
        ax[1].plot([int(r["imgsz"]) for r in rs], [f(r, "veh_mean") / base for r in rs],
                   marker=marks[clip], ms=4, lw=1.3, color=cols[clip], label=names[clip])
    ax[1].axhline(1.0, color="0.4", ls=":", lw=0.9)
    ax[1].set_xlabel("Inference resolution (px)")
    ax[1].set_ylabel("Vehicle count, relative to 640 px")
    ax[1].set_xticks([640, 960, 1280, 1600])
    ax[1].legend(frameon=False, loc="upper left")
    fig.savefig(os.path.join(FIGS, "detector.pdf"))
    plt.close(fig)


# ── Figure: paired per-seed scatter, the honest view of variability ─────────
def fig_paired():
    rows = load("exp1_runs.csv")
    fig, ax = plt.subplots(1, 3, figsize=(7.1, 2.4))
    for j, p in enumerate(["asymmetric", "balanced", "single"]):
        sel = [r for r in rows if r["profile"] == p]
        by = {}
        for r in sel:
            by.setdefault((float(r["level"]), int(r["seed"])), {})[r["policy"]] = f(r, "mean_wait")
        levels = sorted({k[0] for k in by})
        cmap = plt.get_cmap("viridis")
        for i, lv in enumerate(levels):
            xs = [v["fixed_webster"] for k, v in by.items() if k[0] == lv]
            ys = [v["relay"] for k, v in by.items() if k[0] == lv]
            ax[j].scatter(xs, ys, s=8, color=cmap(i / max(1, len(levels) - 1)),
                          label=f"m={lv}", alpha=0.85, edgecolors="none")
        lo = min(min(v.values()) for v in by.values()) * 0.8
        hi = max(max(v.values()) for v in by.values()) * 1.2
        ax[j].plot([lo, hi], [lo, hi], color="0.4", ls="--", lw=0.9)
        ax[j].set_xscale("log")
        ax[j].set_yscale("log")
        ax[j].set_title(PROF[p])
        ax[j].set_xlabel("Fixed, Webster: mean delay (s)")
        if j == 0:
            ax[j].set_ylabel("R.E.L.A.Y.: mean delay (s)")
    ax[0].legend(frameon=False, fontsize=6.5, loc="upper left")
    fig.savefig(os.path.join(FIGS, "exp1_paired.pdf"))
    plt.close(fig)


if __name__ == "__main__":
    for fn in (fig_exp1, fig_exp1_tail, fig_exp1b, fig_exp2, fig_exp3, fig_detector, fig_paired):
        fn()
        print("ok", fn.__name__)
    print("figures written to", FIGS)
