#!/usr/bin/env python
"""R.E.L.A.Y. junction auto-calibration: traffic in, a proposed topology config out.

Topology stays config, not code. This module writes the config instead of a person clicking it:
watch a warmup window of ordinary traffic, follow every vehicle the detector reports, and let the
trajectories say where the approaches are.

  detections per frame -> centroid tracker -> trajectories
    -> cluster the inbound trajectories (heading + lateral offset from the axis) into approaches
    -> junction centre = least-squares intersection of the approach axes
    -> per approach: stop-line setback, lateral spread -> zone polygon, observed turn split
    -> a zones/lanes/meta JSON a human reviews and edits before it goes live

It proposes, it never deploys. The file it writes is the same shape `src/pipeline.py` already
loads, so a bad proposal is one text edit away from a good one, and `tools/draw_zones.py` stays
the manual path.

Honest limits: it needs a warmup window with real traffic through every approach (a few minutes at
a normal junction, longer at night on a quiet arm), and an approach nobody uses during the window
does not exist as far as this module is concerned. The tracker is a plain predictive centroid
matcher, not a MOT-benchmark tracker: identity swaps inside a motorcycle swarm are expected and
harmless, because the geometry comes from hundreds of trajectories statistically, never from one.

Run `python src/calibrate.py` for a self-check on synthetic junction traffic.
"""
import itertools, json, math
from dataclasses import dataclass, field

import numpy as np

from controller import junction_from_dirs   # the config has to build a real Junction or it is junk
from perception import point_in_poly        # single source of truth (perception.py owns it)

COMPASS = {"N": (0.0, 1.0), "S": (0.0, -1.0), "E": (-1.0, 0.0), "W": (1.0, 0.0)}
# image-space compass, matching the rest of the repo (N is the top of the frame): a vehicle
# arriving FROM the north drives DOWN the screen, so approach N's inbound heading is (0, +1).

REVERSE_GATE = math.cos(math.radians(75))   # tracker: a match may not reverse a moving track
MIN_STRAIGHTNESS = 0.55                     # clustering: displacement / path length of a real
                                            # trajectory. A right-angle turn scores 0.71; a chain of
                                            # identity swaps zigzags well below this and is dropped
                                            # rather than allowed to vote on the geometry.


# ── trajectories ───────────────────────────────────────────────
@dataclass
class Track:
    id: int
    pts: list = field(default_factory=list)    # normalized centroids, in frame order
    cls: str = ""

    def displacement(self):
        if len(self.pts) < 2:
            return 0.0
        (x0, y0), (x1, y1) = self.pts[0], self.pts[-1]
        return math.hypot(x1 - x0, y1 - y0)

    def heading(self, end="in", frac=0.3):
        """Unit direction over the first (or last) `frac` of the path. Averaging two blocks instead
        of taking two single points keeps detector jitter out of the angle that decides the label."""
        n = len(self.pts)
        if n < 2:
            return (0.0, 0.0)
        k = max(1, int(n * frac))
        p = np.asarray(self.pts, float)
        a, b = (p[:k].mean(0), p[k:2 * k].mean(0)) if end == "in" else (p[-2 * k:-k].mean(0), p[-k:].mean(0))
        if not np.isfinite(a).all() or not np.isfinite(b).all():
            return (0.0, 0.0)
        d = b - a
        n2 = math.hypot(d[0], d[1])
        return (float(d[0] / n2), float(d[1] / n2)) if n2 > 1e-9 else (0.0, 0.0)


class Tracker:
    """Predictive greedy centroid tracker over normalized detection boxes.

    Deliberately small: calibration needs the SHAPE of a few hundred trajectories, not identity
    preservation through an occlusion. Constant-velocity prediction is what makes it survive a
    motorcycle at 40 km/h between two 3 Hz detections."""

    def __init__(self, max_dist=0.06, max_age=8, min_pts=6):
        self.max_dist, self.max_age, self.min_pts = max_dist, max_age, min_pts
        self._open, self._done, self._next = [], [], 1

    def update(self, boxes):
        dets = []
        for b in boxes or ():
            try:
                cx = float(b["x"]) + float(b["w"]) / 2
                cy = float(b["y"]) + float(b["h"]) / 2
            except (KeyError, TypeError, ValueError):
                continue
            if not (math.isfinite(cx) and math.isfinite(cy)):
                continue
            dets.append((min(max(cx, 0.0), 1.0), min(max(cy, 0.0), 1.0), str(b.get("cls", ""))))
        pairs = []
        for i, tr in enumerate(self._open):
            px, py = tr["pred"]
            vx, vy = tr["vel"]
            vmag = math.hypot(vx, vy)
            lx, ly = tr["pts"][-1]
            for j, (cx, cy, _) in enumerate(dets):
                d = math.hypot(cx - px, cy - py)
                if d > self.max_dist:
                    continue
                # a moving vehicle does not reverse between two frames. Without this gate a passing
                # motorcycle steals a stopped car's identity and the trajectory zigzags, which then
                # poisons the heading the approach clustering runs on.
                if vmag > 0.004:
                    sx, sy = cx - lx, cy - ly
                    smag = math.hypot(sx, sy)
                    if smag > 1e-6 and (sx * vx + sy * vy) / (smag * vmag) < REVERSE_GATE:
                        continue
                pairs.append((d, i, j))
        pairs.sort()
        taken_t, taken_d = set(), set()
        for _, i, j in pairs:
            if i in taken_t or j in taken_d:
                continue
            taken_t.add(i); taken_d.add(j)
            tr, (cx, cy, cls) = self._open[i], dets[j]
            last = tr["pts"][-1]
            tr["vel"] = (0.6 * (cx - last[0]) + 0.4 * tr["vel"][0],
                         0.6 * (cy - last[1]) + 0.4 * tr["vel"][1])
            tr["pts"].append((cx, cy))
            tr["pred"] = (cx + tr["vel"][0], cy + tr["vel"][1])
            tr["age"] = 0
            tr["cls"][cls] = tr["cls"].get(cls, 0) + 1
        for j, (cx, cy, cls) in enumerate(dets):
            if j not in taken_d:
                self._open.append({"id": self._next, "pts": [(cx, cy)], "vel": (0.0, 0.0),
                                   "pred": (cx, cy), "age": 0, "cls": {cls: 1}})
                self._next += 1
        keep = []
        for i, tr in enumerate(self._open):
            if i in taken_t:
                keep.append(tr); continue
            tr["age"] += 1
            tr["pred"] = (tr["pred"][0] + tr["vel"][0], tr["pred"][1] + tr["vel"][1])
            if tr["age"] <= self.max_age:
                keep.append(tr)
            else:
                self._retire(tr)
        self._open = keep

    def _retire(self, tr):
        if len(tr["pts"]) >= self.min_pts:
            self._done.append(Track(tr["id"], tr["pts"], max(tr["cls"], key=tr["cls"].get)))

    def tracks(self):
        """Everything finished so far plus whatever is still open (end of the warmup window)."""
        out = list(self._done)
        for tr in self._open:
            if len(tr["pts"]) >= self.min_pts:
                out.append(Track(tr["id"], tr["pts"], max(tr["cls"], key=tr["cls"].get)))
        return out


# ── approaches ─────────────────────────────────────────────────
def _ang(a, b):
    return math.degrees(math.acos(min(1.0, max(-1.0, a[0] * b[0] + a[1] * b[1]))))


def _straightness(t):
    p = np.asarray(t.pts, float)
    path = float(np.hypot(*np.diff(p, axis=0).T).sum())
    return t.displacement() / path if path > 1e-9 else 0.0


def _lateral(point, anchor, head):
    """Distance from a point to the axis line through `anchor` in direction `head`."""
    d = np.asarray(point, float) - np.asarray(anchor, float)
    return abs(float(d[0] * -head[1] + d[1] * head[0]))


def cluster_approaches(tracks, min_disp=0.10, min_pts=6, ang_tol=30.0, lat_tol=0.10):
    """Group trajectories that travel the same road in the same direction.

    Two tests, both physical: the headings agree to within ang_tol, and the new trajectory sits
    within lat_tol of the cluster's axis LINE, not of its entry point. Same-road/same-direction is
    a lateral question; where a vehicle happened to be first detected is not. Distance ALONG the
    axis is deliberately ignored, so a queue tail two hundred metres back joins its own approach.
    A divided road's two carriageways separate on heading (180 degrees apart); two parallel one-way
    streets separate on the lateral term.

    Greedy single pass with running means, longest trajectories first, then merge passes."""
    usable = [t for t in tracks
              if len(t.pts) >= min_pts and t.displacement() >= min_disp
              and t.heading() != (0.0, 0.0) and _straightness(t) >= MIN_STRAIGHTNESS]
    usable.sort(key=lambda t: -t.displacement())
    cl = []
    for t in usable:
        e = np.asarray(t.pts[0], float)
        h = np.asarray(t.heading(), float)
        best, best_score = None, float("inf")
        for c in cl:
            da, dp = _ang(h, c["head"]), _lateral(e, c["entry"], c["head"])
            if da <= ang_tol and dp <= lat_tol:
                score = da / ang_tol + dp / lat_tol
                if score < best_score:
                    best, best_score = c, score
        if best is None:
            cl.append({"tracks": [t], "entry": e, "head": h})
        else:
            n = len(best["tracks"])
            best["entry"] = (best["entry"] * n + e) / (n + 1)
            best["head"] = (best["head"] * n + h) / (n + 1)
            best["head"] /= max(np.linalg.norm(best["head"]), 1e-9)
            best["tracks"].append(t)
    merged = True
    while merged and len(cl) > 1:
        merged = False
        for i in range(len(cl)):
            for j in range(i + 1, len(cl)):
                if _ang(cl[i]["head"], cl[j]["head"]) <= ang_tol and \
                        _lateral(cl[j]["entry"], cl[i]["entry"], cl[i]["head"]) <= lat_tol:
                    ni, nj = len(cl[i]["tracks"]), len(cl[j]["tracks"])
                    cl[i]["entry"] = (cl[i]["entry"] * ni + cl[j]["entry"] * nj) / (ni + nj)
                    cl[i]["head"] = cl[i]["head"] * ni + cl[j]["head"] * nj
                    cl[i]["head"] /= max(np.linalg.norm(cl[i]["head"]), 1e-9)
                    cl[i]["tracks"] += cl[j]["tracks"]
                    cl.pop(j); merged = True
                    break
            if merged:
                break
    for c in cl:
        pts = np.concatenate([np.asarray(t.pts, float)[:max(2, len(t.pts) // 2)] for t in c["tracks"]])
        c["mean"] = pts.mean(0)
    return sorted(cl, key=lambda c: -len(c["tracks"])), len(usable)


def junction_center(clusters, fallback=(0.5, 0.5)):
    """Least-squares intersection of the approach axes: the point closest to every arm's line of
    travel. Two arms are enough; parallel arms (an arterial camera seeing one street) leave the
    system singular, and then the mean of the traffic itself is the honest answer."""
    if len(clusters) < 2:
        return tuple(np.asarray(fallback, float))
    A = np.zeros((2, 2)); b = np.zeros(2)
    for c in clusters:
        h = np.asarray(c["head"], float)
        P = np.eye(2) - np.outer(h, h)
        A += P; b += P @ np.asarray(c["mean"], float)
    try:
        if np.linalg.cond(A) > 1e6:
            raise np.linalg.LinAlgError
        p = np.linalg.solve(A, b)
    except np.linalg.LinAlgError:
        return tuple(np.mean([c["mean"] for c in clusters], axis=0))
    if not np.isfinite(p).all() or not (-0.2 <= p[0] <= 1.2 and -0.2 <= p[1] <= 1.2):
        return tuple(np.mean([c["mean"] for c in clusters], axis=0))
    return (float(p[0]), float(p[1]))


def label_clusters(clusters):
    """Give each approach the compass key the controller phases on.

    Solved as a small assignment problem (24 permutations at most), not greedily. Greedy by cluster
    size hands the last arm whatever key is left over: on the Le Van Hien camera, where every street
    runs diagonally, that named a south-west bound stream "W" and missed by 117 degrees. Maximising
    the total alignment instead keeps every label defensible, and the residual error per approach is
    reported so a reviewer can still overrule it."""
    cl = list(clusters)
    head = cl[:len(COMPASS)]
    best = max(itertools.permutations(COMPASS, len(head)),
               key=lambda perm: sum(c["head"][0] * COMPASS[k][0] + c["head"][1] * COMPASS[k][1]
                                    for c, k in zip(head, perm)),
               default=())
    return list(zip(best, head)) + [(None, c) for c in cl[len(COMPASS):]]


def _turn_split(cluster):
    """What the traffic actually did: through, left, right, U. Image coordinates are left-handed
    (y grows downward), so a driver's LEFT turn is a NEGATIVE cross product."""
    n = {"through": 0, "left": 0, "right": 0, "u": 0}
    for t in cluster["tracks"]:
        hi, ho = t.heading("in"), t.heading("out")
        if ho == (0.0, 0.0) or hi == (0.0, 0.0):
            continue
        dot = hi[0] * ho[0] + hi[1] * ho[1]
        if dot >= math.cos(math.radians(35)):
            n["through"] += 1
        elif dot <= math.cos(math.radians(150)):
            n["u"] += 1
        elif hi[0] * ho[1] - hi[1] * ho[0] < 0:
            n["left"] += 1
        else:
            n["right"] += 1
    tot = sum(n.values()) or 1
    return {k: round(v / tot, 2) for k, v in n.items()}


def zone_overlap(a, b, n=48):
    """Share of zone a's area that also lies inside zone b (grid sample). Two zones over the same
    asphalt would double-count the same vehicles into two phases, so this is a guard, not a metric."""
    xs = np.linspace(min(p[0] for p in a), max(p[0] for p in a), n)
    ys = np.linspace(min(p[1] for p in a), max(p[1] for p in a), n)
    inside = both = 0
    for x in xs:
        for y in ys:
            if point_in_poly(x, y, a):
                inside += 1
                both += point_in_poly(x, y, b)
    return both / inside if inside else 0.0


@dataclass
class Proposal:
    zones: dict = field(default_factory=dict)
    lanes: dict = field(default_factory=dict)
    center: tuple = (0.5, 0.5)
    approaches: dict = field(default_factory=dict)
    members: dict = field(default_factory=dict)    # label -> the Tracks behind it (review, not config)
    notes: list = field(default_factory=list)
    tracks_used: int = 0


def propose(tracks, min_disp=0.10, min_pts=6, ang_tol=30.0, lat_tol=0.10, min_share=0.04,
            setback=None):
    """Trajectories -> Proposal. Never raises: no traffic means an empty proposal and a note."""
    prop = Proposal()
    clusters, usable = cluster_approaches(tracks, min_disp, min_pts, ang_tol, lat_tol)
    prop.tracks_used = usable
    if not clusters:
        prop.notes.append(f"no usable trajectories ({len(list(tracks))} tracks seen, none long enough) "
                          f"(extend the warmup window, or check that the detector sees this scene)")
        return prop
    center = np.asarray(junction_center(clusters), float)

    # an approach comes TOWARD the junction. A cluster whose traffic only ever gets further away is
    # an exit lane (the far carriageway of a divided road, seen from the same camera) and must not
    # become an approach, since a green for an exit is worse than a green for an empty lane.
    inbound = []
    for c in clusters:
        closing = 0
        for t in c["tracks"]:
            p = np.asarray(t.pts, float)
            mid = p[max(1, len(p) // 2)]
            if np.linalg.norm(mid - center) < np.linalg.norm(p[0] - center):
                closing += 1
        if closing >= 0.5 * len(c["tracks"]):
            inbound.append(c)
    dropped_exits = len(clusters) - len(inbound)
    if dropped_exits:
        prop.notes.append(f"{dropped_exits} outbound stream(s) ignored: traffic moving away from the "
                          f"junction centre is an exit, not an approach")
    if not inbound:
        prop.notes.append("every stream moved away from the estimated centre, so the camera "
                          "geometry is unclear: draw the zones by hand (tools/draw_zones.py)")
        return prop
    floor = max(4, int(min_share * usable))
    kept = [c for c in inbound if len(c["tracks"]) >= floor]
    if len(inbound) - len(kept):
        prop.notes.append(f"{len(inbound) - len(kept)} stream(s) below {floor} trajectories ignored as noise")
    if not kept:
        prop.notes.append(f"no stream reached {floor} trajectories: warmup window too short")
        return prop

    # geometry per cluster, in its own (along-flow, lateral) frame around the junction centre
    geo = []
    for c in kept:
        h = np.asarray(c["head"], float)
        perp = np.array([-h[1], h[0]])
        p = np.concatenate([np.asarray(t.pts, float) for t in c["tracks"]]) - center
        s, t_ = p @ h, p @ perp
        lo, hi = float(np.percentile(t_, 4)), float(np.percentile(t_, 96))
        geo.append({"c": c, "h": h, "perp": perp, "s": s, "t": t_, "half": (hi - lo) / 2,
                    "t_lo": lo, "t_hi": hi})
    # the junction box is as wide as its widest road: that is the setback the stop line needs
    box = setback if setback is not None else min(0.30, max(0.05, 1.15 * max(g["half"] for g in geo)))

    # zone geometry first, compass keys last: the shape of an approach comes from its own axis and
    # owes nothing to its name, and a zone that will not survive must not consume a key the
    # survivors need (that is how a diagonal camera ended up with a "W" pointing south-west).
    built = []
    for g in geo:
        h, perp, c = g["h"], g["perp"], g["c"]
        sel = g["s"] <= -box
        if sel.sum() < 8:                                # short approach: keep whatever is upstream
            sel = g["s"] <= -min(box, 0.04)
        if sel.sum() < 8:
            prop.notes.append(f"a stream of {len(c['tracks'])} trajectories had nothing upstream of "
                              f"the junction box and was skipped")
            continue
        s_near = -min(box, float(-np.percentile(g["s"][sel], 96)))
        s_far = max(-0.75, float(np.percentile(g["s"][sel], 6)))
        if s_far >= s_near - 0.03:
            s_far = s_near - 0.10
        t_lo = float(np.percentile(g["t"][sel], 4)) - 0.015
        t_hi = float(np.percentile(g["t"][sel], 96)) + 0.015

        def xy(s, t, h=h, perp=perp):
            p = center + s * h + t * perp
            return (round(float(min(max(p[0], 0.0), 1.0)), 4), round(float(min(max(p[1], 0.0), 1.0)), 4))

        built.append({
            "c": c, "h": h,
            "zone": [xy(s_far, t_lo), xy(s_near, t_lo), xy(s_near, t_hi), xy(s_far, t_hi)],
            "stop_line": [xy(s_near, t_lo), xy(s_near, t_hi)],
            "depth": round(float(s_near - s_far), 3),
        })

    # Over-splitting one approach is harmless; over-MERGING two conflicting streams into one zone
    # would hand them a shared green, so the clustering stays deliberately conservative and this
    # runs afterwards: where two zones sit on the same asphalt, keep the busier one.
    survivors = []
    for b in sorted(built, key=lambda b: -len(b["c"]["tracks"])):
        clash = max((max(zone_overlap(b["zone"], k["zone"]), zone_overlap(k["zone"], b["zone"]))
                     for k in survivors), default=0.0)
        if clash > 0.35:
            prop.notes.append(f"a stream of {len(b['c']['tracks'])} trajectories was dropped: its zone "
                              f"overlapped a busier one by {clash:.0%} (the same asphalt read twice "
                              f"would count the same vehicles as demand for two phases)")
            continue
        survivors.append(b)

    for label, c in label_clusters([b["c"] for b in survivors]):
        b = next(b for b in survivors if b["c"] is c)
        if label is None:
            prop.notes.append(f"a further approach ({len(c['tracks'])} trajectories) was observed and "
                              f"dropped: the controller phases N/S/E/W keys")
            continue
        prop.zones[label] = b["zone"]
        prop.members[label] = list(c["tracks"])
        prop.approaches[label] = {
            "trajectories": len(c["tracks"]),
            "heading": [round(float(b["h"][0]), 3), round(float(b["h"][1]), 3)],
            "heading_error_deg": round(_ang(tuple(b["h"]), COMPASS[label]), 1),
            "stop_line": b["stop_line"],
            "depth": b["depth"],
            "turns": _turn_split(c),
        }
    prop.center = (round(float(center[0]), 4), round(float(center[1]), 4))
    if len(prop.zones) < 2:
        prop.notes.append("fewer than two approaches proposed: a signal needs at least two "
                          "conflicting streams, so review this config before running it")
    for label, a in prop.approaches.items():
        if a["heading_error_deg"] > 35:
            prop.notes.append(f"{label}: inbound heading is {a['heading_error_deg']}deg off the "
                              f"compass key it was given, check the label before deploying")
    return prop


# ── the config file ────────────────────────────────────────────
def validate_zones(zones):
    """The rule src/pipeline.py enforces, in one place so the calibrator cannot emit a file the
    pipeline would reject: [] disables an approach, anything else is a real normalized polygon."""
    if not isinstance(zones, dict) or not zones:
        return "no zones"
    for k, poly in zones.items():
        if not poly:
            continue
        if len(poly) < 3 or any(len(p) != 2 or not (0 <= p[0] <= 1 and 0 <= p[1] <= 1) for p in poly):
            return f"bad zone {k!r}: need >= 3 points with normalized 0..1 coords (or [] to disable)"
    return None


def save_topology(path, zones, lanes=None, meta=None):
    json.dump({"zones": {k: [list(p) for p in v] for k, v in zones.items()},
               "lanes": {k: [[list(p) for p in poly] for poly in v] for k, v in (lanes or {}).items()},
               "meta": meta or {}},
              open(path, "w"), indent=1)


def load_topology(path):
    """-> (zones, lanes, meta). Reads either supported topology file: the wrapped one
    tools/autocalibrate.py proposes, or the flat {"N": [[x,y],...]} one tools/draw_zones.py writes
    when you click the zones yourself."""
    raw = json.load(open(path))
    if isinstance(raw, dict) and isinstance(raw.get("zones"), dict):
        zones, lanes, meta = raw["zones"], raw.get("lanes") or {}, raw.get("meta") or {}
    else:
        zones, lanes, meta = raw, {}, {}
    zones = {k: [tuple(p) for p in v] for k, v in zones.items()}
    lanes = {k: [[tuple(p) for p in poly] for poly in v] for k, v in lanes.items() if v}
    return zones, lanes, meta


# ───────────────────────── self-check ─────────────────────────
def _synthetic_traffic(frames=240, per_arm=9, exits=True, arms=("N", "S", "E", "W"), seed=0):
    """A junction shot from above: arms enter at the frame edges, cross the middle, and leave. Some
    vehicles turn. With exits=True two streams also run purely OUTWARD, which the proposal has to
    reject."""
    rng = np.random.default_rng(seed)
    C = np.array([0.5, 0.5])
    veh = []
    for label in arms:
        h = COMPASS[label]
        h = np.array(h, float)
        perp = np.array([-h[1], h[0]])
        for k in range(per_arm):
            lat = float(rng.uniform(-0.05, 0.05))
            turn = ["through", "left", "right"][k % 3]
            start = int(rng.integers(0, frames - 90))
            veh.append((start, C - 0.48 * h + lat * perp, h, perp, lat, turn))
    if exits:
        for h in ((0.0, 1.0), (1.0, 0.0)):               # traffic leaving the junction outward
            h = np.array(h, float)
            perp = np.array([-h[1], h[0]])
            for k in range(per_arm):
                start = int(rng.integers(0, frames - 90))
                veh.append((start, C + 0.12 * h + 0.10 * perp, h, perp, 0.10, "exit"))
    per_frame = [[] for _ in range(frames)]
    for start, p0, h, perp, lat, turn in veh:
        pos = p0.copy()
        step = 0.014
        for i in range(80):
            f = start + i
            if f >= frames:
                break
            d = h.copy()
            along = float((pos - C) @ h)
            if turn in ("left", "right") and along > 0.0:
                # after the middle, swing onto the crossing street
                d = perp * (-1.0 if turn == "left" else 1.0)
            pos = pos + step * d
            if not (-0.02 <= pos[0] <= 1.02 and -0.02 <= pos[1] <= 1.02):
                break
            jit = rng.normal(0, 0.002, 2)
            per_frame[f].append({"x": float(pos[0] + jit[0] - 0.02), "y": float(pos[1] + jit[1] - 0.02),
                                 "w": 0.04, "h": 0.04, "cls": "car"})
    return per_frame


def demo():
    import os, tempfile

    tk = Tracker()
    for boxes in _synthetic_traffic():
        tk.update(boxes)
    tracks = tk.tracks()
    assert len(tracks) >= 30, f"tracker lost the traffic: {len(tracks)} tracks from ~45 vehicles"
    prop = propose(tracks)

    # the four arms come back with the right compass keys and the right way round
    assert set(prop.zones) == {"N", "S", "E", "W"}, f"expected 4 approaches, got {sorted(prop.zones)}"
    for label, a in prop.approaches.items():
        assert a["heading_error_deg"] <= 20, f"{label}: inbound heading {a['heading_error_deg']}deg off"
    # outbound streams rejected, not phased
    assert any("exit" in n for n in prop.notes), f"exit streams were not rejected: {prop.notes}"

    # the junction centre lands on the junction
    assert math.dist(prop.center, (0.5, 0.5)) < 0.08, f"junction centre off at {prop.center}"

    # zones are upstream of the junction, on the right side of it, and never cover the centre
    for label, poly in prop.zones.items():
        assert len(poly) == 4 and all(0 <= x <= 1 and 0 <= y <= 1 for x, y in poly)
        assert not point_in_poly(prop.center[0], prop.center[1], poly), \
            f"VIOLATION: {label} zone covers the junction box: crossing vehicles would count as queued"
        cx = sum(p[0] for p in poly) / 4; cy = sum(p[1] for p in poly) / 4
        h = COMPASS[label]
        assert (cx - 0.5) * h[0] + (cy - 0.5) * h[1] < 0, f"{label} zone sits downstream of the junction"
    # no two approach zones share asphalt: the same vehicle must never be demand for two phases
    for l1 in prop.zones:
        for l2 in prop.zones:
            if l1 != l2:
                ov = zone_overlap(prop.zones[l1], prop.zones[l2])
                assert ov <= 0.35, f"VIOLATION: {l1} and {l2} zones overlap by {ov:.0%}"

    # the emitted config must satisfy the pipeline's own rule AND build a real Junction
    assert validate_zones(prop.zones) is None, validate_zones(prop.zones)
    j = junction_from_dirs(prop.zones.keys())
    assert len(j.phases) >= 2, f"controller got no usable phases from {sorted(prop.zones)}"

    # turn split adds up and is reported per approach
    for label, a in prop.approaches.items():
        assert abs(sum(a["turns"].values()) - 1.0) < 0.06, f"{label} turn split {a['turns']}"

    # LABELS ARE ASSIGNED, NOT GRABBED: a big diagonal arm and a small arm pointing exactly north.
    # Greedy by size lets the diagonal take N (45 degrees off) and leaves the northbound arm with E
    # (90 degrees off). The assignment has to give N to the arm that actually points north.
    pair = [{"head": np.array([0.7071, 0.7071]), "tracks": [None] * 100},
            {"head": np.array([0.0, 1.0]), "tracks": [None] * 50}]
    got = dict((k, c) for k, c in label_clusters(pair))
    assert _ang(tuple(got["N"]["head"]), COMPASS["N"]) < 1e-6, \
        f"N went to the wrong arm: {[(k, list(c['head'])) for k, c in got.items()]}"
    assert max(_ang(tuple(c["head"]), COMPASS[k]) for k, c in got.items()) <= 46, \
        "a label landed further than 46 degrees from its own stream"

    # a T junction: three arms in, three approaches out, still a valid controller topology
    tk3 = Tracker()
    for boxes in _synthetic_traffic(exits=False, arms=("N", "E", "W"), seed=3):
        tk3.update(boxes)
    p3 = propose(tk3.tracks())
    assert set(p3.zones) == {"N", "E", "W"}, f"T junction read as {sorted(p3.zones)}"
    assert validate_zones(p3.zones) is None
    assert len(junction_from_dirs(p3.zones.keys()).phases) >= 2

    # determinism: same traffic in, same config out
    tkA, tkB = Tracker(), Tracker()
    feed = _synthetic_traffic(seed=7)
    for boxes in feed:
        tkA.update(boxes)
    for boxes in feed:
        tkB.update(boxes)
    assert propose(tkA.tracks()).zones == propose(tkB.tracks()).zones, "calibration is not deterministic"

    # garbage in: no traffic, one point, NaN, junk box shapes -> a note, never an exception
    assert propose([]).zones == {} and propose([]).notes
    assert propose([Track(1, [(0.5, 0.5)] * 20)]).zones == {}
    tj = Tracker()
    for junk in ([{"x": float("nan"), "y": 0.5, "w": 0.1, "h": 0.1}],
                 [{"x": 0.5}], [{"cls": "car"}], [None], [], [{"x": 1e9, "y": -1e9, "w": 0.1, "h": 0.1}]):
        tj.update(junk)
    assert propose(tj.tracks()).zones == {}
    for bad in (None, {}, {"N": [(0, 0), (1, 0)]}, {"N": [(0, 0), (1, 0), (2, 5)]}, {"N": [(0, 0, 0)] * 3}):
        assert validate_zones(bad) is not None, f"validate_zones accepted {bad!r}"
    assert validate_zones({"N": [], "S": [(0, 0), (1, 0), (1, 1)]}) is None   # [] disables an approach

    # both topology formats load: the wrapped one and draw_zones.py's flat one
    d = tempfile.mkdtemp()
    p = os.path.join(d, "topo.json")
    save_topology(p, prop.zones, {"N": [[(0.1, 0.1), (0.2, 0.1), (0.2, 0.2), (0.1, 0.2)]]},
                  {"source": "self-check"})
    z, ln, meta = load_topology(p)
    assert z == {k: [tuple(pt) for pt in v] for k, v in prop.zones.items()}, "zones changed on round-trip"
    assert list(ln) == ["N"] and len(ln["N"][0]) == 4 and meta["source"] == "self-check"
    flat = os.path.join(d, "clicked.json")
    json.dump({"N": [[0.3, 0.02], [0.7, 0.02], [0.62, 0.42]]}, open(flat, "w"))
    z2, ln2, meta2 = load_topology(flat)
    assert z2 == {"N": [(0.3, 0.02), (0.7, 0.02), (0.62, 0.42)]} and ln2 == {} and meta2 == {}

    print("junction auto-calibration self-check PASSED:")
    print(f"  ✓ 4-way synthetic traffic -> {sorted(prop.zones)} from {prop.tracks_used} trajectories "
          f"(max heading error {max(a['heading_error_deg'] for a in prop.approaches.values())}deg)")
    print(f"  ✓ junction centre from the approach axes: {prop.center} (true 0.5, 0.5)")
    print("  ✓ outbound streams rejected as exits, never given an approach zone")
    print("  ✓ zones sit upstream of the junction box, do not cover it, and do not overlap")
    print("  ✓ compass keys assigned by best total alignment, not grabbed biggest-first")
    print("  ✓ emitted config passes the pipeline's zone rule and builds a controller Junction")
    print(f"  ✓ T junction (one arm removed) -> {sorted(p3.zones)}, still >= 2 phases")
    print("  ✓ observed turn split reported per approach (through / left / right / U)")
    print("  ✓ deterministic (same traffic in, same config out)")
    print("  ✓ garbage in (no traffic, single point, NaN, junk boxes, bad zones) never raises")
    print("  ✓ config round-trips, and hand-clicked flat zone files load too")


if __name__ == "__main__":
    demo()
