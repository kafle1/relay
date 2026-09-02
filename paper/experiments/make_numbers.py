#!/usr/bin/env python
"""Every number quoted in the manuscript, written as a LaTeX macro from the raw result files.

main.tex never states a result as a literal; it uses these macros. Re-running an experiment
and then this script updates the text, and verify_claims.py fails if numbers.tex is stale.

Usage:  python make_numbers.py   (writes ../numbers.tex)
"""
import csv
import json
import os
import statistics

HERE = os.path.dirname(os.path.abspath(__file__))
RAW = os.path.join(HERE, "raw")
OUT = os.path.join(os.path.dirname(HERE), "numbers.tex")


def rows(name):
    with open(os.path.join(RAW, name), newline="") as fh:
        return [{k: _num(v) for k, v in r.items()} for r in csv.DictReader(fh)]


def _num(v):
    try:
        return float(v) if "." in v or "e" in v.lower() else int(v)
    except (ValueError, AttributeError):
        return v


macros = {}


_DIGITS = {"0": "Zero", "1": "One", "2": "Two", "3": "Three", "4": "Four", "5": "Five", "6": "Six", "7": "Seven", "8": "Eight", "9": "Nine"}


def m(name, value, fmt="{:.1f}"):
    # LaTeX macro names cannot contain digits
    name = "".join(_DIGITS.get(ch, ch) for ch in name)
    macros[name] = fmt.format(value) if not isinstance(value, str) else value


def pct(x):
    return f"{x:.1f}"


# ---------------------------------------------------------------- experiment 1
e1 = rows("exp1_summary.csv")
red_w = [r["red_vs_fixed_webster_mean"] for r in e1]
red_e = [r["red_vs_fixed_equal_mean"] for r in e1]
m("ExpOneCells", len(e1), "{}")
m("ExpOneMinWebster", min(red_w)); m("ExpOneMaxWebster", max(red_w))
m("ExpOneMinEqual", min(red_e)); m("ExpOneMaxEqual", max(red_e))
m("ExpOneSeeds", e1[0]["n_seeds"], "{}")


def cell(profile, level):
    return next(r for r in e1 if r["profile"] == profile and abs(r["level"] - level) < 1e-9)


asy10 = cell("asymmetric", 1.0)
m("AsyMidDemand", asy10["total_demand_vph"], "{:.0f}")
m("AsyMidRedEqual", asy10["red_vs_fixed_equal_mean"]); m("AsyMidRedWebster", asy10["red_vs_fixed_webster_mean"])
m("AsyMidPNinetyFiveEqual", asy10["fixed_equal_p95_wait_mean"]); m("AsyMidPNinetyFiveWebster", asy10["fixed_webster_p95_wait_mean"])
m("AsyMidPNinetyFiveRelay", asy10["relay_p95_wait_mean"])
m("AsyMidMaxEqual", asy10["fixed_equal_max_wait_mean"]); m("AsyMidMaxWebster", asy10["fixed_webster_max_wait_mean"])
m("AsyMidMaxRelay", asy10["relay_max_wait_mean"])
asy14 = cell("asymmetric", 1.4)
m("AsyHighDemand", asy14["total_demand_vph"], "{:.0f}")
m("AsyHighRedEqual", asy14["red_vs_fixed_equal_mean"]); m("AsyHighRedWebster", asy14["red_vs_fixed_webster_mean"])
m("AsyHighThrWebster", asy14["fixed_webster_throughput_vph_mean"], "{:.0f}"); m("AsyHighThrRelay", asy14["relay_throughput_vph_mean"], "{:.0f}")
asy06 = cell("asymmetric", 0.6)
m("AsyLowRedWebster", asy06["red_vs_fixed_webster_mean"])
sin10 = cell("single", 1.0)
m("SinMidDelayEqual", sin10["fixed_equal_mean_wait_mean"]); m("SinMidQueueEqual", sin10["fixed_equal_mean_queue_mean"])
m("SinMidThrEqual", sin10["fixed_equal_throughput_vph_mean"], "{:.0f}"); m("SinMidDemand", sin10["total_demand_vph"], "{:.0f}")
m("SinMidPNinetyFiveEqual", sin10["fixed_equal_p95_wait_mean"]); m("SinMidPNinetyFiveWebster", sin10["fixed_webster_p95_wait_mean"])
m("SinMidPNinetyFiveRelay", sin10["relay_p95_wait_mean"])
m("SinMidMaxEqual", sin10["fixed_equal_max_wait_mean"]); m("SinMidMaxWebster", sin10["fixed_webster_max_wait_mean"])
m("SinMidMaxRelay", sin10["relay_max_wait_mean"])
sin_best = max((r for r in e1 if r["profile"] == "single"), key=lambda r: r["red_vs_fixed_webster_mean"])
m("SinBestRedWebster", sin_best["red_vs_fixed_webster_mean"]); m("SinBestDemand", sin_best["total_demand_vph"], "{:.0f}")
sin14 = cell("single", 1.4)
m("SinHighRedWebster", sin14["red_vs_fixed_webster_mean"])
bal06, bal14 = cell("balanced", 0.6), cell("balanced", 1.4)
m("BalLowRedWebster", bal06["red_vs_fixed_webster_mean"]); m("BalLowDemand", bal06["total_demand_vph"], "{:.0f}")
m("BalHighRedWebster", bal14["red_vs_fixed_webster_mean"]); m("BalHighDemand", bal14["total_demand_vph"], "{:.0f}")
m("DemandLow", min(r["total_demand_vph"] for r in e1), "{:.0f}"); m("DemandHigh", max(r["total_demand_vph"] for r in e1), "{:.0f}")
worst_p = max(r["paired_fixed_webster_p_wilcoxon"] for r in e1 if isinstance(r.get("paired_fixed_webster_p_wilcoxon"), float))
m("ExpOneWorstP", worst_p, "{:.3g}")

# ---------------------------------------------------------------- experiment 1b
e1b = rows("exp1b_summary.csv")
bal = [r for r in e1b if r["profile"] == "balanced"]
m("SwitchBalMin", min(r["sw_relay_per_h_mean"] for r in bal), "{:.0f}"); m("SwitchBalMax", max(r["sw_relay_per_h_mean"] for r in bal), "{:.0f}")
m("SwitchFixed", bal[0]["sw_fixed_per_h_mean"], "{:.0f}")
m("GreenFracBalMin", min(r["green_frac_relay_mean"] for r in bal), "{:.3f}"); m("GreenFracBalMax", max(r["green_frac_relay_mean"] for r in bal), "{:.3f}")
m("GreenFracFixed", bal[0]["green_frac_fixed_mean"], "{:.3f}")
sin14b = next(r for r in e1b if r["profile"] == "single" and abs(r["level"] - 1.4) < 1e-9)
m("SwitchSinHigh", sin14b["sw_relay_per_h_mean"], "{:.0f}"); m("GreenFracSinHigh", sin14b["green_frac_relay_mean"], "{:.3f}")
balr = [cell("balanced", lv)["red_vs_fixed_webster_mean"] for lv in (0.6, 0.8, 1.0, 1.2, 1.4)]
m("BalRedWebsterMin", min(balr)); m("BalRedWebsterMax", max(balr))

# ---------------------------------------------------------------- experiment 2
e2 = rows("exp2_summary.csv")
for r in e2:
    tag = {0.6: "Low", 1.0: "Mid", 1.4: "High"}[round(r["level"], 1)]
    m(f"CorrDemand{tag}", r["entering_vph"], "{:.0f}")
    m(f"CorrRedWebster{tag}", r["red_vs_fixed_webster_mean"]); m(f"CorrRedCoord{tag}", r["red_vs_fixed_coord_mean"])
    m(f"CorrRedIsolated{tag}", r["red_vs_relay_isolated_mean"], "{:+.1f}")
    m(f"CorrAmbRelay{tag}", r["relay_coupled_amb_corridor_mean"]); m(f"CorrAmbWebster{tag}", r["fixed_webster_amb_corridor_mean"])
    m(f"CorrAmbCoord{tag}", r["fixed_coord_amb_corridor_mean"])
    m(f"CorrTravelRelay{tag}", r["relay_coupled_mean_through_travel_mean"]); m(f"CorrTravelCoord{tag}", r["fixed_coord_mean_through_travel_mean"])
    m(f"CorrStopsRelay{tag}", r["relay_coupled_mean_stops_mean"], "{:.2f}"); m(f"CorrStopsCoord{tag}", r["fixed_coord_mean_stops_mean"], "{:.2f}")
m("CorrCoupleAbsMax", max(abs(r["red_vs_relay_isolated_mean"]) for r in e2))
amb_r = [r["relay_coupled_amb_corridor_mean"] for r in e2]
m("CorrAmbRelayMin", min(amb_r)); m("CorrAmbRelayMax", max(amb_r))
amb_w = [r["fixed_webster_amb_corridor_mean"] for r in e2]
m("CorrAmbWebsterMin", min(amb_w)); m("CorrAmbWebsterMax", max(amb_w))
amb_c = [r["fixed_coord_amb_corridor_mean"] for r in e2]
m("CorrAmbCoordMin", min(amb_c)); m("CorrAmbCoordMax", max(amb_c))
m("CorrRedWebsterMin", min(r["red_vs_fixed_webster_mean"] for r in e2)); m("CorrRedWebsterMax", max(r["red_vs_fixed_webster_mean"] for r in e2))
m("CorrRedCoordMin", min(r["red_vs_fixed_coord_mean"] for r in e2)); m("CorrRedCoordMax", max(r["red_vs_fixed_coord_mean"] for r in e2))

# ---------------------------------------------------------------- experiment 3
e3 = [r for r in rows("exp3_summary.csv") if r["level"] != "all"]
tot = sum(r["n"] for r in e3)
m("PreTrials", tot, "{}")
allw = sum(r["relay_mean"] * r["n"] for r in e3) / tot
m("PreMeanRelay", allw, "{:.2f}"); m("PreMeanFixed", sum(r["fixed_mean"] * r["n"] for r in e3) / tot, "{:.2f}")
m("PreWorstRelay", max(r["relay_max"] for r in e3), "{:.2f}"); m("PreWorstFixed", max(r["fixed_max"] for r in e3), "{:.2f}")
m("PreLightMean", next(r["relay_mean"] for r in e3 if r["level"] == "light"), "{:.2f}")
m("PreHeavyMean", next(r["relay_mean"] for r in e3 if r["level"] == "heavy"), "{:.2f}")
m("PreAlreadyGreenPct", 100 * sum(r["already_green_frac"] * r["n"] for r in e3) / tot)
for r in e3:
    tag = r["level"].capitalize()
    for k in ("mean", "sd", "p50", "p95", "max"):
        m(f"Pre{tag}Relay{k.capitalize()}", r[f"relay_{k}"], "{:.2f}")
        m(f"Pre{tag}Fixed{k.capitalize()}", r[f"fixed_{k}"], "{:.2f}")
runs3 = rows("exp3_preemption.csv")
lat_r = sorted(x["latency_relay"] for x in runs3 if isinstance(x.get("latency_relay"), (int, float)))
lat_f = sorted(x["latency_fixed"] for x in runs3 if isinstance(x.get("latency_fixed"), (int, float)))
if lat_r:
    q = lambda xs, p: xs[min(len(xs) - 1, round(p * (len(xs) - 1)))]
    m("PreAllRelayMedian", q(lat_r, 0.5), "{:.2f}"); m("PreAllRelayPNinetyFive", q(lat_r, 0.95), "{:.2f}")
    m("PreAllRelaySd", statistics.pstdev(lat_r), "{:.2f}")
    m("PreAllFixedMedian", q(lat_f, 0.5), "{:.2f}"); m("PreAllFixedPNinetyFive", q(lat_f, 0.95), "{:.2f}")
    m("PreAllFixedSd", statistics.pstdev(lat_f), "{:.2f}")
    m("PreFixedSoonerPct", 100 * sum(1 for x in runs3 if isinstance(x.get("latency_fixed"), (int, float)) and isinstance(x.get("latency_relay"), (int, float)) and x["latency_fixed"] < x["latency_relay"]) / len(runs3))
    m("PreAboveThirteenFourAllYellow", "", "{}")

# ---------------------------------------------------------------- experiment 4 and 5 (perception)
det = next(r for r in rows("detector_val.csv") if r["model"] == "relay_ft_mixed")
m("DetMapFifty", det["mAP50"], "{:.3f}"); m("DetMapFiftyNinetyFive", det["mAP50_95"], "{:.3f}")
m("DetPrecision", det["precision"], "{:.3f}"); m("DetRecall", det["recall"], "{:.3f}")
for c in ("car", "motorcycle", "bus", "truck", "ambulance"):
    m(f"DetAp{c.capitalize()}", det[f"mAP50_{c}"], "{:.3f}")
m("DetParams", det["params_M"], "{:.2f}")
stats = json.load(open(os.path.join(RAW, "detector_dataset_stats.json")))
m("DetValImages", stats["val"]["images"], "{}"); m("DetValInstances", sum(stats["val"]["instances"].values()), "{}")
m("DetTrainImages", stats["train"]["images"], "{}"); m("DetTrainInstances", sum(stats["train"]["instances"].values()), "{}")
e5 = {r["preset"]: r for r in rows("exp5_summary.csv")}
m("LatLevMedian", e5["levanhien"]["infer_ms_mean"], "{:.1f}"); m("LatLevFps", e5["levanhien"]["throughput_fps"]); m("LatLevPNinetyFive", e5["levanhien"]["infer_ms_p95"])
m("LatHcmcMedian", e5["hcmc"]["infer_ms_mean"], "{:.1f}"); m("LatHcmcFps", e5["hcmc"]["throughput_fps"]); m("LatHcmcPNinetyFive", e5["hcmc"]["infer_ms_p95"])
m("LatFirstFrame", e5["levanhien"]["infer_ms_max"], "{:.0f}")
e5b = rows("exp5b_resolution.csv")
h = {r["imgsz"]: r for r in e5b if r["clip"] == "hcmc"}
lv = {r["imgsz"]: r for r in e5b if r["clip"] == "levanhien"}
for s in (640, 960, 1280, 1600):
    m(f"ResHcmc{s}", h[s]["veh_mean"], "{:.1f}"); m(f"ResLev{s}", lv[s]["veh_mean"], "{:.2f}")
    m(f"ResHcmcMs{s}", h[s]["infer_ms_median"], "{:.1f}")
m("ResHcmcRisePct", 100 * (h[1600]["veh_mean"] / h[640]["veh_mean"] - 1), "{:.0f}")
m("ResHcmcMotoRisePct", 100 * (h[1600]["moto_mean"] / h[640]["moto_mean"] - 1), "{:.0f}")

# ---------------------------------------------------------------- experiment 6
e6 = rows("exp6_robustness.csv")
m("GridCells", len(e6), "{}"); m("GridSeeds", e6[0]["n_seeds"], "{}")
m("GridDemandLow", min(r["total_demand_vph"] for r in e6), "{:.0f}"); m("GridDemandHigh", max(r["total_demand_vph"] for r in e6), "{:.0f}")
wins6 = sum(1 for r in e6 if min(r["red_vs_fixed_equal"], r["red_vs_fixed_webster"]) > 0)
m("GridWins", wins6, "{}")
m("GridWorstEqual", min(r["red_vs_fixed_equal"] for r in e6)); m("GridWorstWebster", min(r["red_vs_fixed_webster"] for r in e6))
worst6 = min(e6, key=lambda r: r["red_vs_fixed_webster"])
m("GridWorstCellDemand", worst6["total_demand_vph"], "{:.0f}"); m("GridWorstCellShape", worst6["shape"].replace("_", " "), "{}")
m("GridWorstCellRelayThr", worst6["relay_throughput_vph"], "{:.0f}"); m("GridWorstCellWebsterThr", worst6["fixed_webster_throughput_vph"], "{:.0f}")
m("GridWorstCellRelayDelay", worst6["relay_mean_wait"]); m("GridWorstCellWebsterDelay", worst6["fixed_webster_mean_wait"])
e6n = rows("exp6_robustness_noise0.2.csv")
m("GridNoiseWins", sum(1 for r in e6n if min(r["red_vs_fixed_equal"], r["red_vs_fixed_webster"]) > 0), "{}")
m("GridNoiseWorstEqual", min(r["red_vs_fixed_equal"] for r in e6n)); m("GridNoiseWorstWebster", min(r["red_vs_fixed_webster"] for r in e6n))
for shape in sorted({r["shape"] for r in e6}):
    sub = [r for r in e6 if r["shape"] == shape]
    key = "".join(w.capitalize() for w in shape.split("_"))
    m(f"Grid{key}WorstEqual", min(r["red_vs_fixed_equal"] for r in sub), "{:+.1f}")
    m(f"Grid{key}WorstWebster", min(r["red_vs_fixed_webster"] for r in sub), "{:+.1f}")
    m(f"Grid{key}MedianWebster", statistics.median(r["red_vs_fixed_webster"] for r in sub), "{:+.1f}")

# ---------------------------------------------------------------- experiment 6b
e6b = rows("exp6b_biased_counts.csv")
for model in sorted({r["model"] for r in e6b}):
    sub = [r for r in e6b if r["model"] == model]
    key = "".join(w.capitalize() for w in model.replace(".", "").replace("_", " ").split())
    m(f"Bias{key}Wins", sum(1 for r in sub if min(r["red_vs_fixed_equal"], r["red_vs_fixed_webster"]) > 0), "{}")
    m(f"Bias{key}WorstWebster", min(r["red_vs_fixed_webster"] for r in sub), "{:+.1f}")
    m(f"Bias{key}MeanWebster", statistics.fmean(r["red_vs_fixed_webster"] for r in sub), "{:+.1f}")
m("BiasModels", len({r["model"] for r in e6b}), "{}")

# ---------------------------------------------------------------- experiment 7
e7 = rows("exp7_policies.csv")
for b in ("maxpressure", "sotl", "actuated"):
    key = b.capitalize()
    m(f"Pol{key}Wins", sum(1 for r in e7 if r[f"red_vs_{b}"] > 0), "{}")
    m(f"Pol{key}Worst", min(r[f"red_vs_{b}"] for r in e7), "{:+.1f}")
    m(f"Pol{key}Mean", statistics.fmean(r[f"red_vs_{b}"] for r in e7)); m(f"Pol{key}Median", statistics.median(r[f"red_vs_{b}"] for r in e7))
    m(f"Pol{key}PNinetyFive", statistics.fmean(r[f"{b}_p95_wait"] for r in e7)); m(f"Pol{key}Max", statistics.fmean(r[f"{b}_max_wait"] for r in e7))
m("PolRelayPNinetyFive", statistics.fmean(r["relay_p95_wait"] for r in e7)); m("PolRelayMax", statistics.fmean(r["relay_max_wait"] for r in e7))
m("PolWebsterPNinetyFive", statistics.fmean(r["fixed_webster_p95_wait"] for r in e7)); m("PolWebsterMax", statistics.fmean(r["fixed_webster_max_wait"] for r in e7))
lose_act = [r for r in e7 if r["red_vs_actuated"] <= 0]
m("PolActuatedLosses", len(lose_act), "{}")
m("PolActuatedLossMax", max((-r["red_vs_actuated"] for r in lose_act), default=0.0))

# ---------------------------------------------------------------- experiment 8 (SUMO)
e8 = rows("exp8_sumo_summary.csv")
cfg8 = json.load(open(os.path.join(RAW, "exp8_config.json")))
m("SumoSat", cfg8["saturation_pcu_h_lane"], "{:.0f}"); m("SumoSeeds", len(cfg8["seeds"]), "{}"); m("SumoVersion", cfg8["sumo_version"].replace("Eclipse SUMO sumo ", ""), "{}")
m("SumoCells", len(e8), "{}")
for b in ("webster", "maxpressure", "actuated"):
    key = b.capitalize()
    m(f"Sumo{key}Min", min(r[f"red_vs_{b}_mean"] for r in e8)); m(f"Sumo{key}Max", max(r[f"red_vs_{b}_mean"] for r in e8))
    m(f"Sumo{key}Wins", sum(r[f"wins_vs_{b}"] for r in e8), "{}"); m(f"Sumo{key}Runs", sum(r["n_seeds"] for r in e8), "{}")
worst8 = min(e8, key=lambda r: r["red_vs_actuated_mean"])
m("SumoActuatedWorstShape", worst8["profile"], "{}"); m("SumoActuatedWorstDemand", worst8["total_demand_vph"], "{:.0f}")
high8 = [r for r in e8 if abs(r["level"] - 1.4) < 1e-9]
m("SumoHighMaxRelay", statistics.fmean(r["relay_max_delay_mean"] for r in high8)); m("SumoHighMaxWebster", statistics.fmean(r["webster_max_delay_mean"] for r in high8))
m("SumoHighMaxActuated", statistics.fmean(r["actuated_max_delay_mean"] for r in high8))
m("SumoHighPNinetyFiveRelay", statistics.fmean(r["relay_p95_delay_mean"] for r in high8)); m("SumoHighPNinetyFiveWebster", statistics.fmean(r["webster_p95_delay_mean"] for r in high8))
low8 = [r for r in e8 if abs(r["level"] - 0.6) < 1e-9]
m("SumoLowPNinetyFiveRelay", statistics.fmean(r["relay_p95_delay_mean"] for r in low8)); m("SumoLowPNinetyFiveWebster", statistics.fmean(r["webster_p95_delay_mean"] for r in low8))
for r in e8:
    tag = r["profile"].capitalize() + {0.6: "Low", 1.0: "Mid", 1.4: "High"}[round(r["level"], 1)]
    for p in ("relay", "webster", "maxpressure", "actuated"):
        m(f"Sumo{tag}{p.capitalize()}", r[f"{p}_mean_delay_mean"])
        m(f"Sumo{tag}{p.capitalize()}Sd", r[f"{p}_mean_delay_sd"])
    m(f"Sumo{tag}Demand", r["total_demand_vph"], "{:.0f}")
    for b in ("webster", "maxpressure", "actuated"):
        m(f"Sumo{tag}Red{b.capitalize()}", r[f"red_vs_{b}_mean"], "{:+.1f}")

# ---------------------------------------------------------------- experiment 9 (SUMO corridor)
e9 = rows("exp9_sumo_corridor_summary.csv")
for r in e9:
    tag = {0.6: "Low", 1.0: "Mid", 1.4: "High"}[round(r["level"], 1)]
    m(f"Scorr{tag}Demand", r["entering_vph"], "{:.0f}")
    for p in ("relay_coupled", "relay_isolated", "webster", "webster_coord"):
        key = "".join(w.capitalize() for w in p.split("_"))
        m(f"Scorr{tag}{key}", r[f"{p}_mean_delay_mean"]); m(f"Scorr{tag}{key}Sd", r[f"{p}_mean_delay_sd"])
        m(f"Scorr{tag}{key}Travel", r[f"{p}_through_travel_mean"])
    m(f"Scorr{tag}RedIsolated", r["red_vs_relay_isolated_mean"], "{:+.1f}")
    m(f"Scorr{tag}RedWebster", r["red_vs_webster_mean"]); m(f"Scorr{tag}RedCoord", r["red_vs_webster_coord_mean"])
m("ScorrCoupleAbsMax", max(abs(r["red_vs_relay_isolated_mean"]) for r in e9))
m("ScorrRedCoordMin", min(r["red_vs_webster_coord_mean"] for r in e9)); m("ScorrRedCoordMax", max(r["red_vs_webster_coord_mean"] for r in e9))
cfg9 = json.load(open(os.path.join(RAW, "exp9_config.json")))
m("ScorrSpacing", cfg9["spacing_m"], "{:.0f}")

# ---------------------------------------------------------------- experiment 10
e10 = rows("exp10_sensitivity.csv")
rw = [r for r in e10 if r["sweep"] == "max_wait"]
for lv in (1.4, 1.8):
    sub = [r for r in rw if abs(r["level"] - lv) < 1e-9]
    tag = "Mid" if lv == 1.4 else "High"
    m(f"Sens{tag}Demand", sub[0]["total_demand_vph"], "{:.0f}")
    m(f"Sens{tag}RedMin", min(r["red_vs_webster"] for r in sub), "{:+.1f}"); m(f"Sens{tag}RedMax", max(r["red_vs_webster"] for r in sub), "{:+.1f}")
    m(f"Sens{tag}MaxWaitMin", min(r["relay_max_wait"] for r in sub), "{:.0f}"); m(f"Sens{tag}MaxWaitMax", max(r["relay_max_wait"] for r in sub), "{:.0f}")
    m(f"Sens{tag}MeanMin", min(r["relay_mean_wait"] for r in sub)); m(f"Sens{tag}MeanMax", max(r["relay_mean_wait"] for r in sub))
wm = [r for r in e10 if r["sweep"] == "w_motorcycle"]
for shape in ("asymmetric", "balanced", "single"):
    sub = [r for r in wm if r["shape"] == shape]
    key = shape.capitalize()
    m(f"SensMoto{key}RedMin", min(r["red_vs_webster"] for r in sub), "{:+.1f}"); m(f"SensMoto{key}RedMax", max(r["red_vs_webster"] for r in sub), "{:+.1f}")
    m(f"SensMoto{key}MeanMin", min(r["relay_mean_wait"] for r in sub), "{:.2f}"); m(f"SensMoto{key}MeanMax", max(r["relay_mean_wait"] for r in sub), "{:.2f}")
m("SensMotoSpread", max(abs(a["red_vs_webster"] - b["red_vs_webster"]) for a in wm for b in wm if a["shape"] == b["shape"]))

with open(OUT, "w") as fh:
    fh.write("% generated by experiments/make_numbers.py from experiments/raw; do not edit\n")
    for k, v in macros.items():
        fh.write(f"\\newcommand{{\\{k}}}{{{v}}}\n")
print(f"wrote {len(macros)} macros to {OUT}")
