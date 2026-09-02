#!/usr/bin/env python
"""Experiment 9: the three-junction corridor in SUMO, with links that can fill.

The point-queue corridor (Experiment 2) found the downstream coupling term does nothing, and
said why that result was inconclusive: a point queue has no spatial extent, so a downstream
jam never blocks an upstream discharge. Here the same T, four-way, T arterial is built in
SUMO with short links, so spillback is physical. Policies: the controller with coupling,
without coupling, an independent Webster plan per junction and a coordinated Webster plan
with eastbound progression offsets.

Usage:  RELAY_REPO=/path/to/relay .venv/bin/python paper/experiments/exp9_sumo_corridor.py
"""
from __future__ import annotations

import csv
import json
import os
import random
import statistics
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from multiprocessing import Pool

import sumolib
import traci

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.environ.setdefault("SUMO_HOME", os.path.join(os.environ.get("RELAY_REPO", os.path.dirname(os.path.dirname(HERE))),
                                                ".venv", "lib", "python3.12", "site-packages", "sumo"))
import sumo_bench as sb                                                  # noqa: E402
from sumo_bench import FLEET, STEP, LANES, PCU                            # noqa: E402
from controller import Controller, Timings, Junction                     # noqa: E402

OUT = os.environ.get("RELAY_OUT", os.path.join(HERE, "raw"))
os.makedirs(OUT, exist_ok=True)
SEEDS = list(range(int(os.environ.get("NSEEDS", 10))))
NPROC = int(os.environ.get("NPROC", 8))
SECONDS, WARMUP = 2400.0, 400.0
SPACING = 150.0          # metres between junctions: short enough to spill back under load
OUTER = 300.0
DETECT = 80.0
COUPLE = 0.4
EPS = 0.05
T_NET = Timings(min_green=5, max_green=34, yellow=2, all_red=2.5, max_wait=26)
LEVELS = [0.6, 1.0, 1.4]
POLICIES = ("relay_coupled", "relay_isolated", "webster", "webster_coord")
# per-route demand at level 1.0, veh/s; same shape as Experiment 2
ROUTES = {"W_E": 0.130, "E_W": 0.110, "S1_E": 0.030, "S2_E": 0.030, "N2_W": 0.028, "N3_W": 0.028}
# junction -> arms; the arterial runs west to east through J1, J2, J3
JUNCTIONS = {"J1": ["W", "E", "S"], "J2": ["W", "E", "N", "S"], "J3": ["W", "E", "N"]}
PHASES = {"J1": {"WE": ["W", "E"], "S": ["S"]}, "J2": {"WE": ["W", "E"], "N": ["N"], "S": ["S"]},
          "J3": {"WE": ["W", "E"], "N": ["N"]}}
DOWN = {("J1", "W"): ("J2", "W"), ("J2", "W"): ("J3", "W"), ("J2", "E"): ("J1", "E"), ("J3", "E"): ("J2", "E")}


def build_network(work):
    """Three signalised nodes on a straight arterial; side arms as stubs."""
    xs = {"J1": 0.0, "J2": SPACING, "J3": 2 * SPACING}
    nodes = ["<nodes>"]
    edges = ["<edges>"]
    for j, x in xs.items():
        nodes.append(f'  <node id="{j}" x="{x}" y="0" type="traffic_light" tlType="static"/>')
    nodes.append(f'  <node id="Wend" x="{-OUTER}" y="0" type="priority"/>')
    nodes.append(f'  <node id="Eend" x="{2 * SPACING + OUTER}" y="0" type="priority"/>')
    for j, arms in JUNCTIONS.items():
        for a in arms:
            if a == "S":
                nodes.append(f'  <node id="{j}S" x="{xs[j]}" y="{-OUTER}" type="priority"/>')
            if a == "N":
                nodes.append(f'  <node id="{j}N" x="{xs[j]}" y="{OUTER}" type="priority"/>')
    nodes.append("</nodes>")

    def edge(eid, frm, to):
        edges.append(f'  <edge id="{eid}" from="{frm}" to="{to}" numLanes="{LANES}" speed="12.5"/>')

    # arterial: the westbound approach of J1 comes from Wend, eastbound approach of J3 from Eend
    edge("J1_W_in", "Wend", "J1"); edge("J1_E_out", "J1", "Wend")
    edge("J1_E_in", "J2", "J1"); edge("J1_W_out", "J1", "J2")      # J1 -> J2 eastbound is also J2's W approach
    edge("J2_E_in", "J3", "J2"); edge("J2_W_out", "J2", "J3")
    edge("J3_E_in", "Eend", "J3"); edge("J3_W_out", "J3", "Eend")
    for j, arms in JUNCTIONS.items():
        for a in arms:
            if a in ("N", "S"):
                edge(f"{j}_{a}_in", f"{j}{a}", j); edge(f"{j}_{a}_out", j, f"{j}{a}")
    edges.append("</edges>")
    nod, edg, net = (os.path.join(work, f) for f in ("c.nod.xml", "c.edg.xml", "c.net.xml"))
    open(nod, "w").write("\n".join(nodes)); open(edg, "w").write("\n".join(edges))
    cmd = (f'"{sumolib.checkBinary("netconvert")}" --node-files="{nod}" --edge-files="{edg}" --output-file="{net}" '
           f'--no-turnarounds --tls.default-type=static --no-warnings 2>&1')
    if os.system(cmd) != 0:
        raise RuntimeError("netconvert failed")
    return net


# incoming edge of each (junction, arm) approach; the arterial approaches are shared links
APPROACH_EDGE = {("J1", "W"): "J1_W_in", ("J1", "E"): "J1_E_in", ("J1", "S"): "J1_S_in",
                 ("J2", "W"): "J1_W_out", ("J2", "E"): "J2_E_in", ("J2", "N"): "J2_N_in", ("J2", "S"): "J2_S_in",
                 ("J3", "W"): "J2_W_out", ("J3", "E"): "J3_E_in", ("J3", "N"): "J3_N_in"}
ROUTE_EDGES = {"W_E": ["J1_W_in", "J1_W_out", "J2_W_out", "J3_W_out"],
               "E_W": ["J3_E_in", "J2_E_in", "J1_E_in", "J1_E_out"],
               "S1_E": ["J1_S_in", "J1_W_out", "J2_W_out", "J3_W_out"],
               "S2_E": ["J2_S_in", "J2_W_out", "J3_W_out"],
               "N2_W": ["J2_N_in", "J1_E_in", "J1_E_out"],
               "N3_W": ["J3_N_in", "J2_E_in", "J1_E_in", "J1_E_out"]}


def write_routes(path, level, seed):
    rng = random.Random(seed)
    lines = ["<routes>"]
    for name, f in FLEET.items():
        attrs = " ".join(f'{k}="{v}"' for k, v in f.items() if k != "share")
        lines.append(f'  <vType id="{name}" {attrs} lcStrategic="1" lcCooperative="1" lcSublane="1"/>')
    for r, e in ROUTE_EDGES.items():
        lines.append(f'  <route id="{r}" edges="{" ".join(e)}"/>')
    classes, shares = zip(*[(k, v["share"]) for k, v in FLEET.items()])
    veh = []
    pcu = {k: 0.0 for k in APPROACH_EDGE}
    for r, lam in ROUTES.items():
        lam *= level
        t = 0.0
        while True:
            t += rng.expovariate(lam)
            if t >= SECONDS:
                break
            cls = rng.choices(classes, shares)[0]
            veh.append((t, r, cls))
            for key, e in APPROACH_EDGE.items():
                if e in ROUTE_EDGES[r]:
                    pcu[key] += PCU[cls]
    veh.sort()
    for i, (t, r, cls) in enumerate(veh):
        lines.append(f'  <vehicle id="v{i}" type="{cls}" route="{r}" depart="{t:.2f}" departLane="best" departSpeed="max"/>')
    lines.append("</routes>")
    open(path, "w").write("\n".join(lines))
    return {k: v / SECONDS * 3600.0 for k, v in pcu.items()}


class CorridorSignal:
    def __init__(self, j):
        self.id = j
        self.arm_of = []
        for group in traci.trafficlight.getControlledLinks(j):
            src_lane = group[0][0]
            src = traci.lane.getEdgeID(src_lane)
            arm = next(a for (jj, a), e in APPROACH_EDGE.items() if jj == j and e == src)
            self.arm_of.append(arm)

    def apply(self, state):
        s = "".join({"green": "G", "yellow": "y"}.get(state.get(a, "red"), "r") for a in self.arm_of)
        traci.trafficlight.setRedYellowGreenState(self.id, s)


def counts_for(j):
    out = {}
    for a in JUNCTIONS[j]:
        e = APPROACH_EDGE[(j, a)]
        length = traci.lane.getLength(e + "_0")
        c = {}
        for vid in traci.edge.getLastStepVehicleIDs(e):
            if traci.vehicle.getLanePosition(vid) >= length - DETECT:
                cls = traci.vehicle.getTypeID(vid)
                c[cls] = c.get(cls, 0) + 1
        out[a] = c
    return out


def run(policy, level, seed, sat_pcu_h_lane):
    work = tempfile.mkdtemp(prefix="relay_corr_")
    net = build_network(work)
    rou = os.path.join(work, "c.rou.xml")
    pcu_h = write_routes(rou, level, seed)
    trip = os.path.join(work, "tripinfo.xml")
    traci.start([sumolib.checkBinary("sumo"), "-n", net, "-r", rou, "--tripinfo-output", trip,
                 "--step-length", str(STEP), "--lateral-resolution", "0.8", "--seed", str(seed),
                 "--no-warnings", "--no-step-log", "--time-to-teleport", "-1", "--duration-log.disable"])
    try:
        sig = {j: CorridorSignal(j) for j in JUNCTIONS}
        junctions = {j: Junction(JUNCTIONS[j], dict(PHASES[j])) for j in JUNCTIONS}
        if policy.startswith("relay"):
            ctrl = {j: Controller(junctions[j], T_NET) for j in JUNCTIONS}
            couple = COUPLE if policy == "relay_coupled" else 0.0
        else:
            sat = sat_pcu_h_lane
            plans = {}
            for j in JUNCTIONS:
                lam = {a: pcu_h[(j, a)] for a in JUNCTIONS[j]}
                plans[j] = sb.webster_plan(lam, junctions[j], sat, T_NET.yellow, T_NET.all_red, T_NET.min_green)
            if policy == "webster_coord":
                common = max(sum(g) + len(p) * (T_NET.yellow + T_NET.all_red) for p, g, _ in plans.values())
                ctrl = {}
                for n, j in enumerate(JUNCTIONS):
                    lam = {a: pcu_h[(j, a)] for a in JUNCTIONS[j]}
                    p, g, _ = sb.webster_plan(lam, junctions[j], sat, T_NET.yellow, T_NET.all_red, T_NET.min_green,
                                              c_min=common, c_max=common)
                    fp = sb.FixedPlan(junctions[j], p, g, T_NET.yellow, T_NET.all_red)
                    offset = (n * SPACING / 12.5) % common
                    for _ in range(int(offset / STEP)):
                        fp.tick(None, (), STEP)
                    ctrl[j] = fp
            else:
                ctrl = {j: sb.FixedPlan(junctions[j], plans[j][0], plans[j][1], T_NET.yellow, T_NET.all_red)
                        for j in JUNCTIONS}
        t = 0.0
        while t < SECONDS:
            if policy.startswith("relay"):
                raw = {j: counts_for(j) for j in JUNCTIONS}
                for j in JUNCTIONS:
                    seen = {}
                    for a, c in raw[j].items():
                        n = sum(PCU[k] * v for k, v in c.items())
                        dn = DOWN.get((j, a))
                        if dn and couple > 0 and n > 0:
                            dq = sum(PCU[k] * v for k, v in raw[dn[0]][dn[1]].items())
                            seen[a] = max(n - couple * dq, EPS)
                        else:
                            seen[a] = c
                    out = ctrl[j].tick(seen, (), STEP)
                    sig[j].apply(out["signals"])
            else:
                for j in JUNCTIONS:
                    sig[j].apply(ctrl[j].tick(None, (), STEP))
            traci.simulationStep(); t += STEP
    finally:
        traci.close()
    return _stats(trip)


def _stats(path):
    tree = ET.parse(path)
    loss, through, n = [], [], 0
    for ti in tree.getroot().iter("tripinfo"):
        if float(ti.get("depart")) < WARMUP:
            continue
        loss.append(float(ti.get("timeLoss")))
        if ti.get("id") and ti.get("departLane", "").startswith(("J1_W_in", "J3_E_in")):
            through.append(float(ti.get("duration")))
        n += 1
    loss.sort()
    p = lambda q: loss[min(len(loss) - 1, round(q * (len(loss) - 1)))] if loss else 0.0
    return {"mean_delay": statistics.fmean(loss) if loss else 0.0, "p95_delay": p(0.95),
            "max_delay": max(loss) if loss else 0.0,
            "through_travel": statistics.fmean(through) if through else 0.0,
            "n_vehicles": n, "throughput_vph": n / (SECONDS - WARMUP) * 3600.0}


def one(job):
    policy, level, seed, sat = job
    t0 = time.time()
    r = run(policy, level, seed, sat)
    r.update(policy=policy, level=level, seed=seed, wall_s=round(time.time() - t0, 1))
    return r


def main():
    sat = sb.measure_saturation()
    print(f"saturation {sat:.0f} PCU/h/lane", flush=True)
    jobs = [(p, l, s, sat) for l in LEVELS for s in SEEDS for p in POLICIES]
    rows = []
    with Pool(NPROC) as pool:
        for i, r in enumerate(pool.imap_unordered(one, jobs)):
            rows.append(r)
            if i % 8 == 0:
                print(f"{i + 1}/{len(jobs)} {r['policy']:<15} m={r['level']} seed={r['seed']} delay {r['mean_delay']:.1f}s", flush=True)
    with open(os.path.join(OUT, "exp9_sumo_corridor_runs.csv"), "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys())); w.writeheader(); w.writerows(rows)
    summary = []
    for l in LEVELS:
        cell = {p: sorted([r for r in rows if r["level"] == l and r["policy"] == p], key=lambda r: r["seed"]) for p in POLICIES}
        e = dict(level=l, entering_vph=round(sum(ROUTES.values()) * l * 3600.0, 1), n_seeds=len(SEEDS))
        for p in POLICIES:
            for k in ("mean_delay", "p95_delay", "max_delay", "through_travel", "throughput_vph"):
                vals = [r[k] for r in cell[p]]
                e[f"{p}_{k}_mean"] = round(statistics.fmean(vals), 3)
                e[f"{p}_{k}_sd"] = round(statistics.stdev(vals), 3) if len(vals) > 1 else 0.0
        for b in ("webster", "webster_coord", "relay_isolated"):
            red = [(x["mean_delay"] - y["mean_delay"]) / x["mean_delay"] * 100 for x, y in zip(cell[b], cell["relay_coupled"]) if x["mean_delay"] > 0]
            e[f"red_vs_{b}_mean"] = round(statistics.fmean(red), 2)
            e[f"red_vs_{b}_sd"] = round(statistics.stdev(red), 2) if len(red) > 1 else 0.0
        summary.append(e)
        print(f"m={l}  coupled {e['relay_coupled_mean_delay_mean']:.1f}s  isolated {e['relay_isolated_mean_delay_mean']:.1f}s  "
              f"webster {e['webster_mean_delay_mean']:.1f}s  coord {e['webster_coord_mean_delay_mean']:.1f}s  "
              f"coupling effect {e['red_vs_relay_isolated_mean']:+.1f}%")
    with open(os.path.join(OUT, "exp9_sumo_corridor_summary.csv"), "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(summary[0].keys())); w.writeheader(); w.writerows(summary)
    with open(os.path.join(OUT, "exp9_config.json"), "w") as fh:
        json.dump({"seconds": SECONDS, "warmup": WARMUP, "spacing_m": SPACING, "outer_m": OUTER, "detect_m": DETECT,
                   "couple": COUPLE, "levels": LEVELS, "seeds": SEEDS, "routes_veh_s": ROUTES,
                   "timings": T_NET.__dict__, "saturation_pcu_h_lane": round(sat, 1)}, fh, indent=2)
    print("wrote exp9_sumo_corridor_*.csv to", OUT)


if __name__ == "__main__":
    main()
