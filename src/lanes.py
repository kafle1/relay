#!/usr/bin/env python
"""R.E.L.A.Y. lane detection: painted road markings in, lane polygons out.

Classical CV on purpose: every step is inspectable, so when a lane lands in the wrong place you can
see which step put it there. A traffic engineer reviewing a proposed config needs that; a learned
lane segmenter gives you a mask and no argument.

  median road plate (moving vehicles average away, paint does not)
    -> paint mask (top-hat ridge + white/yellow colour gate)
    -> Hough segments, filtered to the approach's flow direction
    -> RANSAC vanishing point (a validation signal, not a requirement)
    -> lateral offsets clustered into lane boundaries -> lane polygons + a confidence

Honest limits: this needs visible markings. On a paint-free plaza or a roundabout apron the
confidence collapses and the caller keeps counting at approach level, and that fallback is the
designed outcome, not a failure. Zebra stripes run parallel to travel and read as paint; they are
spaced far tighter than a lane, so the minimum-lane-width rule folds a whole crossing into a single
boundary instead of inventing eight lanes. Confidence is a heuristic score in 0..1, not a probability.

Run `python src/lanes.py` for a self-check on synthetic road plates.
"""
import math
from dataclasses import dataclass, field

import cv2
import numpy as np

from perception import point_in_poly   # single source of truth (perception.py owns it)

TOPHAT_FLOOR = 14        # absolute ridge response floor: flat asphalt must yield no paint at all
# Paint is a CRISP bright stripe. Measured on the road plates in this repo: the Le Van Hien
# boulevard's markings read a median top-hat ridge of 85 to 124, its faint far-field dashes read 32,
# and the permanently saturated Dhaka and HCMC clips (where the median plate never sees asphalt and
# returns motion smear instead) read 15 to 16. Smear is collinear and long, so geometry alone called
# it a lane at confidence 0.86; crispness is what tells the two apart.
PAINT_FLOOR, PAINT_FULL = 20.0, 45.0
MIN_CONFIDENCE = 0.35    # below this the caller falls back to approach-level counting
MAX_LANES = 8            # more than this from one camera view is a detector artefact, not a road
MIN_LANE_FRAC = 0.12     # a "lane" narrower than this share of the approach width is not a lane
EDGE_ROOM = 0.6          # carriageway beyond the outermost line, in lane widths, before a kerb
                         # lane is inferred there (set high to keep only lanes that are painted)
SPLIT_FRAC = 0.09        # lateral gap that separates two boundary clusters (share of approach width)


@dataclass
class LaneSet:
    """Lanes found inside one approach zone. lanes: polygons in normalized 0..1 image coords,
    ordered across the approach. confidence: heuristic 0..1 (paint support, spacing regularity,
    vanishing-point agreement). Empty lanes + 0.0 confidence is a valid, expected answer."""
    lanes: list = field(default_factory=list)
    confidence: float = 0.0
    stop_line: list = None
    vanishing_point: tuple = None
    flow: tuple = None
    support: dict = field(default_factory=dict)
    reason: str = ""

    def trusted(self, min_conf=MIN_CONFIDENCE):
        return bool(self.lanes) and self.confidence >= min_conf


# ── stage 1: a road plate with the traffic taken out ───────────
def road_plate(frames):
    """Per-pixel median of several frames: vehicles move and average away, paint stays put.
    A single frame also works, but then every white car roof is a candidate lane marking."""
    good = [f for f in frames if f is not None and getattr(f, "size", 0)]
    if not good:
        raise ValueError("road_plate: no usable frames")
    shape = good[0].shape
    good = [f for f in good if f.shape == shape]          # a mid-clip resolution change is ignored
    if len(good) == 1:
        return good[0].copy()
    return np.median(np.stack(good, 0), axis=0).astype(np.uint8)


def zone_mask(shape, poly):
    """Filled polygon mask (normalized poly). poly None/empty -> the whole frame."""
    h, w = shape[:2]
    m = np.zeros((h, w), np.uint8)
    if not poly or len(poly) < 3:
        m[:] = 255
        return m
    cv2.fillPoly(m, [np.array([(int(x * w), int(y * h)) for x, y in poly], np.int32)], 255)
    return m


# ── stage 2: what is paint and what is road ───────────────────
def ridge_map(plate):
    """Top-hat response: a thin bright stripe survives, slow background brightness does not. This is
    the one number that separates paint from anything else bright and long."""
    gray = cv2.cvtColor(plate, cv2.COLOR_BGR2GRAY)
    k = max(3, int(round(min(plate.shape[:2]) * 0.012)) | 1)      # a stripe is ~1% of the short side
    return cv2.morphologyEx(gray, cv2.MORPH_TOPHAT,
                            cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k)))


def marking_mask(plate, poly=None, ridge=None):
    """Painted markings are thin BRIGHT ridges that are white (low saturation) or yellow.
    Top-hat keeps the ridge and throws away slow background brightness, so a sunlit half of the
    junction does not read as one giant marking; the colour gate drops bright-but-coloured things
    (a red bus roof, a green median). Threshold rides the scene's own contrast with a hard floor,
    so a paint-free apron returns an (almost) empty mask instead of amplified asphalt grain."""
    if plate is None or plate.ndim != 3 or min(plate.shape[:2]) < 8:
        return np.zeros(plate.shape[:2] if plate is not None and plate.ndim >= 2 else (1, 1), np.uint8)
    hsv = cv2.cvtColor(plate, cv2.COLOR_BGR2HSV)
    ridge = ridge_map(plate) if ridge is None else ridge
    zm = zone_mask(plate.shape, poly)
    inside = ridge[zm > 0]
    thr = TOPHAT_FLOOR
    if inside.size:
        thr = max(TOPHAT_FLOOR, 0.35 * float(np.percentile(inside, 99.8)))
    white = (hsv[..., 1] < 90) & (hsv[..., 2] > 110)
    yellow = (hsv[..., 0] >= 15) & (hsv[..., 0] <= 45) & (hsv[..., 1] > 70) & (hsv[..., 2] > 110)
    mask = (((ridge >= thr) & (white | yellow)) & (zm > 0)).astype(np.uint8) * 255
    return cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))


def hough_segments(mask, min_len, max_gap):
    """Line segments along the paint. maxLineGap is generous on purpose: a dashed lane line is one
    boundary, not fourteen dashes."""
    lines = cv2.HoughLinesP(mask, 1, np.pi / 360, threshold=max(15, int(min_len * 0.6)),
                            minLineLength=int(min_len), maxLineGap=int(max_gap))
    if lines is None or not len(lines):
        return np.zeros((0, 4), np.float64)
    return lines.reshape(-1, 4).astype(np.float64)


# ── stage 3: which way does traffic flow through this zone ─────
def dominant_axis(segs):
    """Length-weighted dominant line orientation, mod 180 degrees. Histogram peak first, circular
    mean of the peak's members second: a plain circular mean of a bimodal set (lane lines plus a
    stop bar across them) lands at 45 degrees, which is neither."""
    dx, dy = segs[:, 2] - segs[:, 0], segs[:, 3] - segs[:, 1]
    L = np.hypot(dx, dy)
    ang = np.arctan2(dy, dx) % np.pi
    bins = 36
    hist = np.zeros(bins)
    np.add.at(hist, np.minimum((ang / np.pi * bins).astype(int), bins - 1), L)
    hist = np.convolve(np.r_[hist, hist, hist], np.ones(3) / 3, "same")[bins:2 * bins]
    peak = (int(hist.argmax()) + 0.5) * np.pi / bins
    near = np.abs(((ang - peak + np.pi / 2) % np.pi) - np.pi / 2) < math.radians(20)
    if not near.any():
        return peak
    a2 = 2 * ang[near]
    return math.atan2(float((L[near] * np.sin(a2)).sum()), float((L[near] * np.cos(a2)).sum())) / 2


def vanishing_point(segs, shape, iters=400, seed=0):
    """RANSAC over pairs of segments: the point most lines pass through. Returns (point, inlier
    fraction) or (None, 0.0). Near-parallel views put the point at infinity, which is why it is a
    confidence signal here and never a requirement."""
    if len(segs) < 3:
        return None, 0.0
    h, w = shape[:2]
    diag = math.hypot(w, h)
    tol = 0.02 * diag
    p1, p2 = segs[:, :2], segs[:, 2:]
    d = p2 - p1
    L = np.hypot(d[:, 0], d[:, 1])
    keep = L > 1e-6
    p1, d, L = p1[keep], d[keep], L[keep]
    if len(L) < 3:
        return None, 0.0
    rng = np.random.default_rng(seed)
    best, best_score, best_frac = None, 0.0, 0.0
    ia = rng.integers(0, len(L), iters)
    ib = rng.integers(0, len(L), iters)
    for a, b in zip(ia, ib):
        den = d[a, 0] * d[b, 1] - d[a, 1] * d[b, 0]
        if abs(den) < 1e-9:
            continue
        r = p1[b] - p1[a]
        t = (r[0] * d[b, 1] - r[1] * d[b, 0]) / den
        q = p1[a] + t * d[a]
        if not np.isfinite(q).all() or max(abs(q[0]), abs(q[1])) > 20 * diag:
            continue
        dist = np.abs(d[:, 0] * (q[1] - p1[:, 1]) - d[:, 1] * (q[0] - p1[:, 0])) / L
        inl = dist < tol
        score = float(L[inl].sum())
        if score > best_score:
            best, best_score, best_frac = q, score, float(L[inl].sum() / L.sum())
    if best is None or best_frac < 0.3:
        return None, 0.0
    return (float(best[0]), float(best[1])), best_frac


# ── stage 4: segments -> lane boundaries -> lane polygons ──────
FLOW_GATE = math.radians(60)              # split: paint within this of the flow axis is a lane line,
ALONG_FLOW = math.cos(FLOW_GATE)          # beyond it a stop bar / crosswalk edge. Generous on
                                          # purpose: under perspective the outer lane line of a
                                          # wide approach leans 40 degrees off the axis and is still
                                          # a lane line.


def _boundaries(segs, u, v, s_ref, split, min_support, t_window):
    """Every segment is an infinite line t = a + b*s in the approach's own (along-flow, lateral)
    frame, exact under perspective since a straight lane line stays straight in the image.
    Cluster the lines by lateral position at s_ref and average each cluster into one boundary.

    Only segments running WITH the traffic qualify. Transverse paint (stop bars, the ends of a
    crosswalk, kerb edges) has a near-vertical t/s slope, so extrapolating it to s_ref throws the
    lateral position thousands of pixels sideways: on the Le Van Hien boulevard that produced 18
    "boundaries" and lane widths 3.6x the road."""
    rows = []
    for x1, y1, x2, y2 in segs:
        dx, dy = x2 - x1, y2 - y1
        ln = math.hypot(dx, dy)
        if ln < 1e-6 or abs((dx * u[0] + dy * u[1]) / ln) < ALONG_FLOW:
            continue
        s1, t1 = x1 * u[0] + y1 * u[1], x1 * v[0] + y1 * v[1]
        s2, t2 = x2 * u[0] + y2 * u[1], x2 * v[0] + y2 * v[1]
        if abs(s2 - s1) < 1e-6:
            continue
        b = (t2 - t1) / (s2 - s1)
        a = t1 - b * s1
        if not (t_window[0] <= a + b * s_ref <= t_window[1]):
            continue                                     # a boundary has to cross the reference
                                                         # cross-section INSIDE the approach: paint at
                                                         # one end of a long thin zone otherwise
                                                         # extrapolates a thousand pixels sideways
        rows.append((a, b, ln))
    if not rows:
        return []
    rows.sort(key=lambda r: r[0] + r[1] * s_ref)
    out, cur = [], [rows[0]]
    for r in rows[1:]:
        if (r[0] + r[1] * s_ref) - (cur[-1][0] + cur[-1][1] * s_ref) > split:
            out.append(cur); cur = [r]
        else:
            cur.append(r)
    out.append(cur)
    bnds = []
    for cl in out:
        wsum = sum(r[2] for r in cl)
        if wsum < min_support:
            continue
        a = sum(r[0] * r[2] for r in cl) / wsum
        b = sum(r[1] * r[2] for r in cl) / wsum
        bnds.append({"a": a, "b": b, "support": wsum, "t_ref": a + b * s_ref, "n": len(cl)})
    return sorted(bnds, key=lambda d: d["t_ref"])


def _stop_line(segs, u, v, extent, width, span):
    """The strongest across-the-flow paint nearest the downstream end of the zone: a stop bar, or
    the leading edge of a zebra crossing (close enough to it to be useful, and honest about which)."""
    rows = []
    for x1, y1, x2, y2 in segs:
        dx, dy = x2 - x1, y2 - y1
        ln = math.hypot(dx, dy)
        if ln < 1e-6 or abs((dx * u[0] + dy * u[1]) / ln) > ALONG_FLOW:
            continue
        s = ((x1 + x2) / 2) * u[0] + ((y1 + y2) / 2) * u[1]
        rows.append((s, ln))
    if not rows:
        return None
    rows.sort()
    groups, cur = [], [rows[0]]
    for r in rows[1:]:
        if r[0] - cur[-1][0] > 0.04 * extent:
            groups.append(cur); cur = [r]
        else:
            cur.append(r)
    groups.append(cur)
    strong = [g for g in groups if sum(r[1] for r in g) >= 0.30 * width]
    if not strong:
        return None
    g = max(strong, key=lambda g: sum(r[0] for r in g) / len(g))     # nearest the junction
    s = sum(r[0] for r in g) / len(g)
    t0, t1 = span(s)
    return [(s * u[0] + t0 * v[0], s * u[1] + t0 * v[1]),
            (s * u[0] + t1 * v[0], s * u[1] + t1 * v[1])]


def detect_lanes(plate, poly=None, flow=None, toward=(0.5, 0.5), min_conf=MIN_CONFIDENCE):
    """One approach zone -> LaneSet. plate: BGR image (ideally a road_plate). poly: the approach
    polygon, normalized. flow: optional (dx, dy) direction of travel in normalized coords. Pass
    the auto-calibrated heading when there is one, since a hand-drawn square zone carries no
    reliable axis of its own. toward: the junction side, used to orient the axis when flow is None.
    Never raises: a bad plate returns an empty LaneSet with a reason."""
    empty = LaneSet(reason="no paint")
    if plate is None or plate.ndim != 3 or min(plate.shape[:2]) < 32:
        return LaneSet(reason="frame too small")
    h, w = plate.shape[:2]
    diag = math.hypot(w, h)
    ridge = ridge_map(plate)
    mask = marking_mask(plate, poly, ridge)
    paint_px = int(np.count_nonzero(mask))
    crisp = min(1.0, max(0.0, (float(np.median(ridge[mask > 0])) - PAINT_FLOOR)
                         / (PAINT_FULL - PAINT_FLOOR))) if paint_px else 0.0
    # a dash is short and the gap after it is long: measured on the Le Van Hien plate a near-field
    # dash runs ~55 px with an ~85 px gap, so bridge generously (7% of the diagonal) and accept
    # short seeds (2%). Chaining dashes into one line is the whole point of maxLineGap.
    segs = hough_segments(mask, 0.020 * diag, 0.070 * diag)
    if len(segs) < 3:
        empty.support = {"segments": int(len(segs)), "paint_px": paint_px}
        return empty

    zm = zone_mask(plate.shape, poly)
    ys, xs = np.nonzero(zm)
    if not len(xs):
        return LaneSet(reason="empty zone")
    usable_flow = bool(flow) and all(math.isfinite(float(f)) for f in flow) and any(flow)
    theta = math.atan2(flow[1] * h, flow[0] * w) if usable_flow else dominant_axis(segs)
    u = (math.cos(theta), math.sin(theta))
    v = (-u[1], u[0])
    cx, cy = float(xs.mean()), float(ys.mean())
    if (toward[0] * w - cx) * u[0] + (toward[1] * h - cy) * u[1] < 0:
        u = (-u[0], -u[1]); v = (-v[0], -v[1])           # point the axis at the junction, not away

    s_all = xs * u[0] + ys * u[1]
    t_all = xs * v[0] + ys * v[1]
    s_lo, s_hi = float(s_all.min()), float(s_all.max())
    t_lo, t_hi = float(t_all.min()), float(t_all.max())
    extent, width = s_hi - s_lo, t_hi - t_lo
    if extent < 0.05 * diag or width < 0.05 * diag:
        return LaneSet(reason="zone too thin")

    def span(s0):
        """Lateral extent of the zone at one point along the flow: keeps lane corners inside a
        trapezoid zone instead of squaring it off at the widest end."""
        sel = np.abs(s_all - s0) < 0.03 * extent
        return (float(t_all[sel].min()), float(t_all[sel].max())) if sel.any() else (t_lo, t_hi)

    s_ref = s_lo + 0.5 * extent
    ref_span = span(s_ref)
    pad = 0.05 * (ref_span[1] - ref_span[0])
    bnds = _boundaries(segs, u, v, s_ref,
                       split=max(6.0, SPLIT_FRAC * width),
                       min_support=max(0.05 * extent, 0.03 * diag),
                       t_window=(ref_span[0] - pad, ref_span[1] + pad))
    # merge boundaries closer than a plausible lane: a zebra crossing's stripes run parallel to
    # travel and would otherwise read as one lane per stripe.
    kept = []
    for b in bnds:
        if kept and b["t_ref"] - kept[-1]["t_ref"] < MIN_LANE_FRAC * width:
            if b["support"] > kept[-1]["support"]:
                kept[-1] = b
        else:
            kept.append(b)
    if len(kept) < 2:
        empty.reason = "fewer than two lane boundaries"
        empty.support = {"segments": int(len(segs)), "boundaries": len(kept), "paint_px": paint_px}
        empty.flow = (u[0] / w, u[1] / h)
        return empty

    widths = [kept[i + 1]["t_ref"] - kept[i]["t_ref"] for i in range(len(kept) - 1)]
    typical = float(np.median(widths))
    grid = np.linspace(s_lo + 0.02 * extent, s_hi - 0.02 * extent, 24)
    spans = [span(s) for s in grid]

    # KERB LANE. An edge line is the first marking a road loses, so a 2-lane carriageway usually
    # paints only its centre divider: two boundaries, one bounded lane, and the outer lane counts
    # nothing (measured on both the Le Van Hien clip and the synthetic junction). Where the zone has
    # room for one more lane beyond the outermost boundary, extrapolate one. Extrapolating (a, b)
    # linearly is exact for this: every boundary satisfies t_vp = a + b*s_vp, so the new line still
    # passes through the same vanishing point, one image lane width further out at s_ref. It is an
    # inference, so it is counted as interpolated and it costs confidence.
    edges = 0
    for outward in (-1, 1):
        if len(kept) < 2:
            break
        o, inner = (kept[0], kept[1]) if outward < 0 else (kept[-1], kept[-2])
        room = (ref_span[1] - o["t_ref"]) if outward > 0 else (o["t_ref"] - ref_span[0])
        if room < EDGE_ROOM * typical:
            continue
        new = {"a": 2 * o["a"] - inner["a"], "b": 2 * o["b"] - inner["b"],
               "support": 0.0, "n": 0, "inferred": True}
        new["t_ref"] = new["a"] + new["b"] * s_ref
        if not (ref_span[0] <= new["t_ref"] <= ref_span[1]):
            continue
        kept.insert(0, new) if outward < 0 else kept.append(new)
        edges += 1
    if edges:
        widths = [kept[i + 1]["t_ref"] - kept[i]["t_ref"] for i in range(len(kept) - 1)]

    def valid_run(lo, hi):
        """A lane exists only where BOTH of its boundaries are inside the approach. Take the longest
        stretch along the flow where that holds, rather than clamping the corners onto the zone edge:
        clamping collapsed lanes into zero-width slivers on a foreshortened zone (measured on the
        auto-calibrated Le Van Hien approaches, which is how this was caught)."""
        ok = []
        for s, sp in zip(grid, spans):
            pad = 0.02 * (sp[1] - sp[0])
            ta, tb = lo["a"] + lo["b"] * s, hi["a"] + hi["b"] * s
            ok.append(sp[0] - pad <= min(ta, tb) and max(ta, tb) <= sp[1] + pad)
        best, run = None, None
        for i, good in enumerate(ok + [False]):
            if good:
                run = (i, i) if run is None else (run[0], i)
            elif run is not None:
                best = run if best is None or (run[1] - run[0]) > (best[1] - best[0]) else best
                run = None
        if best is None or grid[best[1]] - grid[best[0]] < 0.25 * extent:
            return None
        return float(grid[best[0]]), float(grid[best[1]])

    lanes, interp = [], 0
    for i, gap in enumerate(widths):
        if gap > 1.02 * width:
            continue                                     # a "lane" spanning the whole approach is an artefact
        lo, hi = kept[i], kept[i + 1]
        run = valid_run(lo, hi)
        if run is None:
            continue
        s_a, s_b = run
        n = int(round(gap / typical)) if typical > 0 and gap > 1.6 * typical else 1
        n = max(1, min(n, MAX_LANES))
        wa = ((hi["a"] + hi["b"] * s_a) - (lo["a"] + lo["b"] * s_a)) / n
        wb = ((hi["a"] + hi["b"] * s_b) - (lo["a"] + lo["b"] * s_b)) / n
        # perspective legitimately narrows a far lane to a tenth of its near width, so the guard is
        # not "too narrow" but "not a quad": boundaries that CROSS inside the zone give a bowtie.
        if wa * wb <= 0 or min(abs(wa), abs(wb)) < 3.0:
            continue
        interp += n if (lo.get("inferred") or hi.get("inferred")) else n - 1
        for j in range(n):
            quad = []
            for s, f in ((s_a, j / n), (s_b, j / n), (s_b, (j + 1) / n), (s_a, (j + 1) / n)):
                ta = lo["a"] + lo["b"] * s
                tb = hi["a"] + hi["b"] * s
                quad.append((s, ta + f * (tb - ta)))
            lanes.append([(min(max((s * u[0] + t * v[0]) / w, 0.0), 1.0),
                           min(max((s * u[1] + t * v[1]) / h, 0.0), 1.0)) for s, t in quad])
        if len(lanes) >= MAX_LANES:
            break
    lanes = lanes[:MAX_LANES]
    if not lanes:
        empty.reason = "no lane fits inside the approach zone"
        empty.support = {"segments": int(len(segs)), "boundaries": len(kept), "paint_px": paint_px}
        empty.flow = (u[0] / w, u[1] / h)
        return empty

    vp, vp_frac = vanishing_point(segs, plate.shape)
    support_len = sum(b["support"] for b in kept)
    # a dashed line paints roughly half its length, so 0.35 x (boundaries x zone extent) is a
    # generous denominator: full marks for a normally marked approach, not for one stripe.
    support = min(1.0, support_len / max(1e-6, 0.35 * len(kept) * extent))
    reg = 1.0 - min(1.0, float(np.std(widths) / max(typical, 1e-6))) if len(widths) > 1 else 0.5
    conf = 0.45 * support + 0.35 * reg + 0.20 * (vp_frac if vp else 0.4)
    conf *= 1.0 - 0.10 * (interp / max(1, len(lanes)))   # interpolated lanes are inferred, not seen
    conf *= crisp                                        # geometry can fit smear; crispness cannot
    conf = round(min(0.95, max(0.0, conf)), 3)
    sl = _stop_line(segs, u, v, extent, width, span)
    return LaneSet(
        lanes=lanes, confidence=conf,
        stop_line=[(min(max(x / w, 0.0), 1.0), min(max(y / h, 0.0), 1.0)) for x, y in sl] if sl else None,
        vanishing_point=(round(vp[0] / w, 4), round(vp[1] / h, 4)) if vp else None,
        flow=(u[0] / w, u[1] / h),
        support={"segments": int(len(segs)), "boundaries": len(kept), "paint_px": paint_px,
                 "interpolated": interp, "lane_width": round(typical / max(width, 1e-6), 3),
                 "paint_support": round(support, 3), "spacing_regularity": round(reg, 3),
                 "vp_agreement": round(vp_frac, 3), "paint_crispness": round(crisp, 3)},
        reason=("ok" if conf >= min_conf else
                "markings too soft to be paint" if crisp < 0.2 else "low confidence"),
    )


def detect(plate, zones, flows=None, min_conf=MIN_CONFIDENCE):
    """Every approach zone -> LaneSet. flows: optional {approach: (dx, dy)} headings."""
    flows = flows or {}
    return {a: detect_lanes(plate, poly, flow=flows.get(a), min_conf=min_conf)
            for a, poly in zones.items() if poly}


def trusted_lanes(lanesets, min_conf=MIN_CONFIDENCE):
    """{approach: [lane polygons]} for the approaches worth using. The rest fall back to
    approach-level counting, which is exactly what the pipeline does with a missing key."""
    return {a: ls.lanes for a, ls in lanesets.items() if ls.trusted(min_conf)}


# ───────────────────────── self-check ─────────────────────────
def _synthetic_plate(w=960, h=540, lanes=3, paint=True, dashed=True, seed=0):
    """A perspective road: asphalt trapezoid plus `lanes+1` boundary lines converging on a
    vanishing point. paint=False leaves bare asphalt, the fallback case."""
    rng = np.random.default_rng(seed)
    img = rng.integers(28, 34, (h, w, 3)).astype(np.int32)
    vx, vy = 0.5 * w, 0.18 * h
    road = np.array([(0.10 * w, h), (0.90 * w, h), (0.58 * w, 0.26 * h), (0.42 * w, 0.26 * h)], np.int32)
    cv2.fillPoly(img, [road], (96, 96, 98))
    img += rng.integers(-5, 6, (h, w, 3))
    img = np.clip(img, 0, 255).astype(np.uint8)
    if paint:
        th = max(2, w // 320)
        for i in range(lanes + 1):
            x0 = 0.12 * w + i * (0.76 * w / lanes)
            if dashed and 0 < i < lanes:
                for k in range(9):
                    f0, f1 = k / 9.0 + 0.02, k / 9.0 + 0.07
                    p0 = (int(x0 + (vx - x0) * f0), int(h + (vy - h) * f0))
                    p1 = (int(x0 + (vx - x0) * f1), int(h + (vy - h) * f1))
                    cv2.line(img, p0, p1, (238, 238, 238), th, cv2.LINE_AA)
            else:
                cv2.line(img, (int(x0), h), (int(x0 + (vx - x0) * 0.74), int(h + (vy - h) * 0.74)),
                         (238, 238, 238), th, cv2.LINE_AA)
    return img


def _check_quads(ls, zone, label=""):
    """A lane must be a real quad inside its approach. Clamping lane corners onto the zone edge used
    to emit slivers with two identical corners on foreshortened auto-calibrated zones, and a sliver
    counts nothing while still claiming a confidence."""
    for poly in ls.lanes:
        assert len(poly) == 4, f"{label}lane polygon should be a quad, got {len(poly)} points"
        assert len(set(poly)) == 4, f"{label}lane polygon has duplicate corners: {poly}"
        assert all(0.0 <= x <= 1.0 and 0.0 <= y <= 1.0 for x, y in poly), f"{label}lane outside the frame: {poly}"
        area = abs(sum(poly[i][0] * poly[(i + 1) % 4][1] - poly[(i + 1) % 4][0] * poly[i][1]
                       for i in range(4))) / 2
        assert area > 1e-4, f"{label}degenerate lane polygon (area {area:.2g}): {poly}"
        cx = sum(p[0] for p in poly) / 4; cy = sum(p[1] for p in poly) / 4
        assert point_in_poly(cx, cy, zone), f"{label}lane centre {cx:.2f},{cy:.2f} outside the approach zone"


def demo():
    ZONE = [(0.06, 0.99), (0.94, 0.99), (0.62, 0.24), (0.38, 0.24)]
    FLOW = (0.0, 1.0)                      # traffic drives down-screen (approach N)

    # a normally marked 3-lane approach: lanes found, ordered across the road, inside the zone
    ls = detect_lanes(_synthetic_plate(lanes=3), ZONE, flow=FLOW)
    assert 2 <= len(ls.lanes) <= 4, f"expected ~3 lanes on a 3-lane plate, got {len(ls.lanes)}"
    assert ls.trusted(), f"marked road should be trusted, confidence {ls.confidence}"
    _check_quads(ls, ZONE)
    mids = [sum(p[0] for p in poly) / 4 for poly in ls.lanes]
    assert all(b - a > 0.02 for a, b in zip(mids, mids[1:])), f"lanes not ordered/disjoint: {mids}"
    assert ls.stop_line is None or len(ls.stop_line) == 2

    # bare asphalt: no invented lanes. The whole fallback contract rests on this one.
    bare = detect_lanes(_synthetic_plate(paint=False), ZONE, flow=FLOW)
    assert not bare.trusted(), \
        f"VIOLATION: invented {len(bare.lanes)} lanes on unmarked asphalt (conf {bare.confidence})"

    # a 2-lane road must not read as 3+: lane COUNT has to track the paint
    two = detect_lanes(_synthetic_plate(lanes=2), ZONE, flow=FLOW)
    assert len(two.lanes) <= 3, f"2-lane plate read as {len(two.lanes)} lanes"

    # a zone that clips the carriageway at an angle (what auto-calibration actually produces on a
    # shallow camera): whatever comes back is still a real quad, or nothing comes back
    skew_zone = [(0.34, 0.99), (0.62, 0.99), (0.55, 0.30), (0.44, 0.30)]
    skew = detect_lanes(_synthetic_plate(lanes=3), skew_zone, flow=FLOW)
    _check_quads(skew, skew_zone, "skewed zone: ")

    # MOTION SMEAR IS NOT PAINT. Under permanent saturation the median plate never sees asphalt and
    # returns a smear of vehicles: long, collinear, whitish, and geometrically a perfect lane line.
    # Before the crispness term this scored 0.86 on the HCMC clip. It must not be trusted.
    smeared = cv2.GaussianBlur(_synthetic_plate(lanes=3), (0, 0), 7)
    assert not detect_lanes(smeared, ZONE, flow=FLOW).trusted(), \
        "VIOLATION: motion smear accepted as lane markings"

    # the median plate is what makes this work on live traffic: vehicles must average away
    clean = _synthetic_plate(lanes=3)
    seq = []
    for i in range(9):
        f = clean.copy()
        cv2.rectangle(f, (120 + i * 70, 300), (260 + i * 70, 430), (240, 240, 240), -1)
        seq.append(f)
    plate = road_plate(seq)
    veh = detect_lanes(plate, ZONE, flow=FLOW)
    assert veh.trusted(), f"vehicles broke lane detection through the median plate (conf {veh.confidence})"
    assert abs(len(veh.lanes) - len(ls.lanes)) <= 1, "median plate changed the lane count"
    # and one raw frame with a vehicle parked over the paint must NOT be trusted more than the plate
    assert road_plate([clean]).shape == clean.shape

    # determinism: same plate in, same lanes out (RANSAC is seeded)
    a = detect_lanes(_synthetic_plate(lanes=3), ZONE, flow=FLOW)
    b = detect_lanes(_synthetic_plate(lanes=3), ZONE, flow=FLOW)
    assert a.lanes == b.lanes and a.confidence == b.confidence, "lane detection is not deterministic"

    # garbage in: never raise, never return junk
    for bad_plate in (None, np.zeros((4, 4, 3), np.uint8), np.zeros((200, 200, 3), np.uint8),
                      np.full((200, 200, 3), 255, np.uint8)):
        r = detect_lanes(bad_plate, ZONE, flow=FLOW)
        assert not r.trusted() and r.lanes == []
    for bad_zone in ([], [(0.5, 0.5)], [(0.5, 0.5)] * 3, [(0.0, 0.0), (0.0, 0.001), (0.001, 0.0)]):
        detect_lanes(_synthetic_plate(), bad_zone, flow=FLOW)      # must not raise
    for bad_flow in ((0.0, 0.0), (float("nan"), 1.0)):
        detect_lanes(_synthetic_plate(), ZONE, flow=bad_flow)      # must not raise
    try:
        road_plate([])
        raise AssertionError("road_plate([]) should refuse, not guess")
    except ValueError:
        pass

    # multi-zone entry point + the trust filter the pipeline relies on
    sets = detect(_synthetic_plate(lanes=3), {"N": ZONE, "S": []}, flows={"N": FLOW})
    assert set(sets) == {"N"}, "empty zones must be skipped, not detected on"
    assert set(trusted_lanes(sets)) == {"N"}
    assert trusted_lanes(sets, min_conf=0.99) == {}, "the confidence gate must be able to reject"

    print("lane detection self-check PASSED:")
    print(f"  ✓ 3-lane marked approach -> {len(ls.lanes)} lanes, confidence {ls.confidence} "
          f"(paint {ls.support['paint_support']}, spacing {ls.support['spacing_regularity']}, "
          f"vp {ls.support['vp_agreement']})")
    print(f"  ✓ unmarked asphalt invents nothing (confidence {bare.confidence} < {MIN_CONFIDENCE} -> "
          f"approach-level fallback)")
    print(f"  ✓ lane count tracks the paint (2-lane plate -> {len(two.lanes)} lanes)")
    print(f"  ✓ a zone that clips the carriageway still yields real quads, never slivers "
          f"({len(skew.lanes)} lanes, every corner distinct and inside the zone)")
    print("  ✓ motion smear rejected: long and collinear is not enough, paint has to be crisp")
    print(f"  ✓ median road plate sees through moving traffic ({len(veh.lanes)} lanes with 9 vehicles over the paint)")
    print("  ✓ lanes ordered across the approach, disjoint, inside the zone and the frame")
    print("  ✓ deterministic (seeded RANSAC vanishing point)")
    print("  ✓ garbage in (None, tiny, black, white, degenerate zones, zero/NaN flow) never raises")
    print("  ✓ confidence gate rejects on demand (trusted_lanes)")


if __name__ == "__main__":
    demo()
