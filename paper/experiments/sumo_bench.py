#!/usr/bin/env python
"""SUMO harness: the controller driving a signal in a microscopic simulation.

The point-queue experiments drive the controller on identical arrival streams, which is the
right tool for a paired comparison but abstracts away everything spatial. This harness puts
the same controller code in front of a SUMO junction with car-following, lane changing, a
sublane model so motorcycles filter through gaps, turning movements that yield to opposing
traffic, and a finite link that can spill back.

What the controller sees is what a camera would see: the vehicles standing or crawling on
each approach within a fixed distance of the stop line, by class. It never reads SUMO's
internal queue length. The signal state it decides is imposed on the SUMO traffic light each
step, so the yellow and all-red intervals are the controller's own.

Policies:
  relay          src/controller.py, unchanged
  webster        fixed-time plan from Webster's formulae on the true mean PCU demand, using a
                 saturation flow measured in this same simulation (see measure_saturation)
  maxpressure    queue argmax with minimum green (paper/experiments/baselines.py)
  actuated       SUMO's own gap-actuated controller with the same green bounds

Requires the eclipse-sumo wheel (pip install eclipse-sumo traci sumolib).
"""
from __future__ import annotations

import os
import random
import statistics
import sys
import tempfile
import xml.etree.ElementTree as ET

import sumolib
import traci

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.environ.get("RELAY_REPO", os.path.dirname(os.path.dirname(HERE)))
sys.path.insert(0, os.path.join(REPO, "src"))
sys.path.insert(0, HERE)
from controller import Controller, Timings, PCU, four_way, junction_from_dirs   # noqa: E402
from baselines import MaxPressure                                             # noqa: E402
from relay_bench import _critical_sum                                         # noqa: E402

ARM_LEN = 300.0          # metres of approach road per arm
DETECT_M = 80.0          # how far back from the stop line the camera counts
STEP = 0.5               # simulation and control step, seconds
LANES = 2

# Kathmandu-style fleet. Shares by vehicle count; lengths and dynamics per SUMO's defaults
# for the class, narrowed so two-wheelers can share a lane.
FLEET = {
    "motorcycle": dict(share=0.62, vClass="motorcycle", length=2.1, width=0.8, accel=3.0, decel=5.0,
                       maxSpeed=16.0, minGap=1.0, sigma=0.5),
    "car":        dict(share=0.30, vClass="passenger", length=4.5, width=1.8, accel=2.4, decel=4.5,
                       maxSpeed=14.0, minGap=2.0, sigma=0.5),
    "bus":        dict(share=0.05, vClass="bus", length=11.0, width=2.5, accel=1.2, decel=4.0,
                       maxSpeed=12.0, minGap=2.5, sigma=0.5),
    "truck":      dict(share=0.03, vClass="truck", length=8.0, width=2.4, accel=1.3, decel=4.0,
                       maxSpeed=12.0, minGap=2.5, sigma=0.5),
}
TURNS = {"through": 0.80, "left": 0.10, "right": 0.10}
ARMS = {"N": (0.0, ARM_LEN), "S": (0.0, -ARM_LEN), "E": (ARM_LEN, 0.0), "W": (-ARM_LEN, 0.0)}
# clockwise order so "right" is the next arm clockwise for a driver entering from `arm`
CW = ["N", "E", "S", "W"]


def _dest(arm, turn):
    i = CW.index(arm)
    if turn == "through":
        return CW[(i + 2) % 4]
    # driver from N heading south: right turn goes to W (clockwise from the driver's view)
    return CW[(i + 3) % 4] if turn == "right" else CW[(i + 1) % 4]


def build_network(workdir: str, arms=("N", "S", "E", "W")) -> str:
    """Write node/edge files for one junction and run netconvert. Returns the .net.xml path."""
    nodes = ['<nodes>', '  <node id="C" x="0" y="0" type="traffic_light" tlType="static"/>']
    edges = ['<edges>']
    for a in arms:
        x, y = ARMS[a]
        nodes.append(f'  <node id="{a}" x="{x}" y="{y}" type="priority"/>')
        edges.append(f'  <edge id="{a}_in" from="{a}" to="C" numLanes="{LANES}" speed="13.9"/>')
        edges.append(f'  <edge id="{a}_out" from="C" to="{a}" numLanes="{LANES}" speed="13.9"/>')
    nodes.append('</nodes>'); edges.append('</edges>')
    nod, edg, net = (os.path.join(workdir, f) for f in ("j.nod.xml", "j.edg.xml", "j.net.xml"))
    open(nod, "w").write("\n".join(nodes)); open(edg, "w").write("\n".join(edges))
    netconvert = sumolib.checkBinary("netconvert")
    cmd = (f'"{netconvert}" --node-files="{nod}" --edge-files="{edg}" --output-file="{net}" '
           f'--no-turnarounds --tls.default-type=static --no-internal-links=false '
           f'--junctions.corner-detail=0 --tls.yellow.time=3 --no-warnings 2>&1')
    if os.system(cmd) != 0:
        raise RuntimeError("netconvert failed")
    return net


def write_routes(path: str, lam: dict, seconds: float, seed: int, arms) -> dict:
    """Poisson departures per arm at rate lam[arm] veh/s, class by fleet share, turn by ratio.
    Returns the realised PCU demand per arm per hour for the Webster plan."""
    rng = random.Random(seed)
    lines = ['<routes>']
    for name, f in FLEET.items():
        attrs = " ".join(f'{k}="{v}"' for k, v in f.items() if k != "share")
        lines.append(f'  <vType id="{name}" {attrs} lcStrategic="1" lcCooperative="1" lcSublane="1"/>')
    for a in arms:
        for b in arms:
            if a != b:
                lines.append(f'  <route id="{a}_{b}" edges="{a}_in {b}_out"/>')
    classes, shares = zip(*[(k, v["share"]) for k, v in FLEET.items()])
    turns, tshares = zip(*TURNS.items())
    veh, pcu_h = [], {a: 0.0 for a in arms}
    for a in arms:
        t = 0.0
        while True:
            t += rng.expovariate(lam[a]) if lam[a] > 0 else seconds + 1
            if t >= seconds:
                break
            cls = rng.choices(classes, shares)[0]
            turn = rng.choices(turns, tshares)[0]
            dest = _dest(a, turn)
            if dest not in arms:
                dest = _dest(a, "through") if _dest(a, "through") in arms else next(b for b in arms if b != a)
            veh.append((t, a, dest, cls))
            pcu_h[a] += PCU[cls]
    veh.sort()
    for i, (t, a, b, cls) in enumerate(veh):
        lines.append(f'  <vehicle id="v{i}" type="{cls}" route="{a}_{b}" depart="{t:.2f}" '
                     f'departLane="best" departSpeed="max"/>')
    lines.append('</routes>')
    open(path, "w").write("\n".join(lines))
    return {a: v / seconds * 3600.0 for a, v in pcu_h.items()}


class Signal:
    """Maps the controller's per-arm state onto the SUMO link state string."""

    def __init__(self, tls_id="C"):
        self.id = tls_id
        links = traci.trafficlight.getControlledLinks(tls_id)
        self.arm_of = []           # per link index: (arm, is_turn_across)
        for group in links:
            src, dst, _ = group[0]
            arm = src.split("_")[0]
            dest = dst.split("_")[0]
            across = _dest(arm, "right") == dest   # crosses the opposing stream (left-hand traffic)
            self.arm_of.append((arm, across))

    def apply(self, state: dict):
        s = []
        for arm, across in self.arm_of:
            st = state.get(arm, "red")
            if st == "green":
                s.append("g" if across else "G")
            elif st == "yellow":
                s.append("y")
            else:
                s.append("r")
        traci.trafficlight.setRedYellowGreenState(self.id, "".join(s))


def camera_counts(arms) -> dict:
    """Per-arm, per-class count of vehicles within DETECT_M of the stop line."""
    out = {}
    for a in arms:
        edge = f"{a}_in"
        counts = {}
        for vid in traci.edge.getLastStepVehicleIDs(edge):
            if traci.vehicle.getLanePosition(vid) >= ARM_LEN - DETECT_M:
                cls = traci.vehicle.getTypeID(vid)
                counts[cls] = counts.get(cls, 0) + 1
        out[a] = counts
    return out


def webster_plan(pcu_h: dict, junction, sat_pcu_h_lane: float, yellow, all_red, min_green=5.0,
                 c_min=30.0, c_max=120.0):
    """Cycle and greens from Webster on the true PCU demand, saturation flow per lane
    scaled by the lane count."""
    phases = list(junction.phases)
    L = len(phases) * (yellow + all_red)
    y = [max(pcu_h.get(a, 0.0) for a in junction.phases[p]) / (sat_pcu_h_lane * LANES) for p in phases]
    Y = _critical_sum(junction, dict(zip(phases, y)))
    C = c_max if Y >= 0.95 else min(max((1.5 * L + 5.0) / (1.0 - Y), c_min), c_max)
    total = max(C - L, len(phases) * min_green)
    greens = [max(min_green, total * (yi / Y if Y > 0 else 1.0 / len(y))) for yi in y]
    return phases, greens, Y


class FixedPlan:
    def __init__(self, junction, phases, greens, yellow, all_red):
        self.j, self.phases, self.greens = junction, phases, greens
        self.durs = {"YELLOW": yellow, "ALLRED": all_red}
        self.i, self.stage, self.t = 0, "GREEN", 0.0

    def tick(self, counts, emergencies, dt):
        self.t += dt
        lim = self.greens[self.i] if self.stage == "GREEN" else self.durs[self.stage]
        if self.t >= lim:
            self.t = 0.0
            self.stage = {"GREEN": "YELLOW", "YELLOW": "ALLRED", "ALLRED": "GREEN"}[self.stage]
            if self.stage == "GREEN":
                self.i = (self.i + 1) % len(self.phases)
        return self.state()

    def state(self):
        served = set(self.j.phases[self.phases[self.i]])
        st = {"GREEN": "green", "YELLOW": "yellow", "ALLRED": "red"}[self.stage]
        return {a: (st if a in served else "red") for a in self.j.approaches}


def _as_state(out, junction):
    """Normalise a policy's tick output to {arm: green|yellow|red}."""
    if isinstance(out, dict) and "signals" in out:
        return out["signals"]
    if isinstance(out, dict):
        return out
    return {a: ("green" if a in out else "red") for a in junction.approaches}


def run(policy: str, lam: dict, seed: int, *, seconds=1800.0, warmup=300.0, timings=None,
        sat_pcu_h_lane=1800.0, arms=("N", "S", "E", "W")) -> dict:
    """One simulation. Returns delay statistics over vehicles departing after warm-up."""
    T = timings or Timings(min_green=5, max_green=45, yellow=3, all_red=1.5, max_wait=60)
    junction = four_way() if set(arms) == {"N", "S", "E", "W"} else junction_from_dirs(list(arms))
    work = tempfile.mkdtemp(prefix="relay_sumo_")
    net = build_network(work, arms)
    rou = os.path.join(work, "j.rou.xml")
    pcu_h = write_routes(rou, lam, seconds, seed, arms)
    trip = os.path.join(work, "tripinfo.xml")
    add = ""
    if policy == "actuated":
        add = os.path.join(work, "actuated.add.xml")
        _write_actuated(add, junction, T)
    cmd = [sumolib.checkBinary("sumo"), "-n", net, "-r", rou, "--tripinfo-output", trip,
           "--step-length", str(STEP), "--lateral-resolution", "0.8", "--seed", str(seed),
           "--no-warnings", "--no-step-log", "--time-to-teleport", "-1", "--duration-log.disable",
           "--collision.action", "warn", "--waiting-time-memory", "10000"]
    if add:
        cmd += ["-a", add]
    traci.start(cmd)
    try:
        sig = Signal("C")
        if policy == "relay":
            ctrl = Controller(junction, T)
        elif policy == "maxpressure":
            ctrl = MaxPressure(junction, yellow=T.yellow, all_red=T.all_red, min_green=T.min_green,
                               max_green=T.max_green)
        elif policy == "webster":
            phases, greens, Y = webster_plan(pcu_h, junction, sat_pcu_h_lane, T.yellow, T.all_red, T.min_green)
            ctrl = FixedPlan(junction, phases, greens, T.yellow, T.all_red)
        elif policy == "actuated":
            ctrl = None
            traci.trafficlight.setProgram("C", "act")
            if traci.trafficlight.getProgram("C") != "act":
                raise RuntimeError("actuated program not active")
        else:
            raise ValueError(policy)
        t = 0.0
        while t < seconds:
            if ctrl is not None:
                counts = camera_counts(arms)
                out = ctrl.tick(counts, (), STEP)
                sig.apply(_as_state(out, junction))
            traci.simulationStep()
            t += STEP
    finally:
        traci.close()
    return _tripinfo_stats(trip, warmup, seconds, pcu_h)


def _write_actuated(path, junction, T):
    """SUMO's built-in gap-actuated program with the same bounds as the controller. Link order
    comes from the net netconvert wrote, so the phase states are built directly."""
    tree = ET.parse(os.path.join(os.path.dirname(path), "j.net.xml"))
    conns = sorted((c for c in tree.getroot().iter("connection") if c.get("tl") == "C"),
                   key=lambda c: int(c.get("linkIndex")))
    order = [(c.get("from").split("_")[0], _dest(c.get("from").split("_")[0], "right") == c.get("to").split("_")[0])
             for c in conns]
    lines = ['<additional>', '  <tlLogic id="C" type="actuated" programID="act" offset="0">',
             '    <param key="max-gap" value="3.0"/>', '    <param key="detector-gap" value="2.0"/>',
             '    <param key="show-detectors" value="false"/>']
    for p, served in junction.phases.items():
        green = "".join(("g" if across else "G") if arm in served else "r" for arm, across in order)
        yellow = "".join("y" if arm in served else "r" for arm, _ in order)
        lines.append(f'    <phase duration="{T.max_green}" minDur="{T.min_green}" maxDur="{T.max_green}" state="{green}"/>')
        lines.append(f'    <phase duration="{T.yellow}" state="{yellow}"/>')
        lines.append(f'    <phase duration="{T.all_red}" state="{"r" * len(order)}"/>')
    lines += ['  </tlLogic>', '</additional>']
    open(path, "w").write("\n".join(lines))


def _tripinfo_stats(path, warmup, seconds, pcu_h):
    tree = ET.parse(path)
    loss, wait, n = [], [], 0
    for ti in tree.getroot().iter("tripinfo"):
        if float(ti.get("depart")) < warmup:
            continue
        loss.append(float(ti.get("timeLoss")))
        wait.append(float(ti.get("waitingTime")))
        n += 1
    loss.sort()
    p = lambda q: loss[min(len(loss) - 1, round(q * (len(loss) - 1)))] if loss else 0.0
    # vehicles still inside at the end are not in tripinfo; count them as residual
    return {"mean_delay": statistics.fmean(loss) if loss else 0.0, "p50_delay": p(0.5), "p95_delay": p(0.95),
            "max_delay": max(loss) if loss else 0.0, "mean_wait": statistics.fmean(wait) if wait else 0.0,
            "n_vehicles": n, "throughput_vph": n / (seconds - warmup) * 3600.0,
            "pcu_demand_h": {k: round(v, 1) for k, v in pcu_h.items()}}


def measure_saturation(seed=0, seconds=400.0) -> float:
    """Hold one approach green with a standing queue and count PCU discharged per lane-hour."""
    work = tempfile.mkdtemp(prefix="relay_sat_")
    net = build_network(work)
    rou = os.path.join(work, "j.rou.xml")
    write_routes(rou, {"N": 1.2, "S": 0.0, "E": 0.0, "W": 0.0}, seconds, seed, ("N", "S", "E", "W"))
    traci.start([sumolib.checkBinary("sumo"), "-n", net, "-r", rou, "--step-length", str(STEP),
                 "--lateral-resolution", "0.8", "--no-warnings", "--no-step-log", "--time-to-teleport", "-1"])
    sig = Signal("C")
    left = 0.0; t = 0.0; window = 0.0
    on_approach: dict[str, str] = {}
    try:
        while t < seconds:
            sig.apply({"N": "green" if t >= 60 else "red", "S": "red", "E": "red", "W": "red"})
            traci.simulationStep(); t += STEP
            now = {vid: traci.vehicle.getTypeID(vid) for vid in traci.edge.getLastStepVehicleIDs("N_in")}
            if t >= 120:                       # queue is standing and flowing by now
                for vid, cls in on_approach.items():
                    if vid not in now:         # crossed the stop line this step
                        left += PCU[cls]
                window += STEP
            on_approach = now
    finally:
        traci.close()
    return left / window * 3600.0 / LANES


if __name__ == "__main__":
    s = measure_saturation()
    print(f"measured saturation flow: {s:.0f} PCU/h/lane")
    lam = {"N": 0.15, "S": 0.13, "E": 0.04, "W": 0.035}
    for pol in ("webster", "relay", "maxpressure", "actuated"):
        r = run(pol, lam, 0, seconds=900, warmup=200, sat_pcu_h_lane=s)
        print(pol, {k: (round(v, 2) if isinstance(v, float) else v) for k, v in r.items()})
