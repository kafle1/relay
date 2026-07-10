#!/usr/bin/env python
"""R.E.L.A.Y. perception core — frames in, per-approach vehicle counts out.

Source-agnostic: the same class serves real CCTV clips, live streams, and the synthetic feed.
Handles the perception edge cases from docs/edge-cases.md: per-class confidence, centroid-in-zone
assignment, ~1s temporal smoothing, corrupt-frame tolerance, and a staleness flag.
"""
import time

from controller import PCU  # single source of truth for PCU weights (controller.py owns it)

# per-class confidence: lower for two-wheelers (small, easily missed — edge case A2).
# ambulance runs a HIGH gate: it is the one class that moves signals by itself (emergency
# preempt), and a marginal read — a white motorcycle, a van roofline — as "ambulance" hijacks
# the junction for tens of seconds. Real sirens measure ≥0.9 on the fine-tune; 0.68 costs
# nothing there and silences the impostors (at 0.55, ~0.8% of boxes on an ambulance-free scene
# still read "ambulance" — measured live). A below-gate ambulance still counts as traffic
# demand via whatever its second-best read is on the next frame — never as an emergency.
# truck runs 0.5: at the 0.38 default the live cross-check read ~5x the trucks actually present
# (phantoms on cars/buses at conf 0.4-0.5; real trucks read 0.7-0.98) — and every phantom truck
# injects 2.0 PCU of fake demand into the controller.
CONF = {"motorcycle": 0.24, "bicycle": 0.24, "ambulance": 0.68, "truck": 0.5, "default": 0.38}
EMERGENCY = {"ambulance"}


def iou(a, b):
    """Intersection-over-union of two {x,y,w,h} boxes (normalized coords)."""
    ix = max(0.0, min(a["x"] + a["w"], b["x"] + b["w"]) - max(a["x"], b["x"]))
    iy = max(0.0, min(a["y"] + a["h"], b["y"] + b["h"]) - max(a["y"], b["y"]))
    inter = ix * iy
    union = a["w"] * a["h"] + b["w"] * b["h"] - inter
    return inter / union if union > 0 else 0.0


def cross_class_dedupe(dets, thr=0.5):
    """One vehicle, one box: per-class NMS still leaves a stacked car+truck+motorcycle trio on a
    single far-queue vehicle — keep the most confident. Runs AFTER the PCU/confidence filters,
    unlike agnostic NMS inside predict(), where a person box (discarded later anyway) or a
    below-gate detection could suppress the real vehicle box and erase or relabel it."""
    kept = []
    for d in sorted(dets, key=lambda d: -d["conf"]):
        if all(iou(d, k) < thr for k in kept):
            kept.append(d)
    return kept


def point_in_poly(x, y, poly):
    inside, j = False, len(poly) - 1
    for i in range(len(poly)):
        xi, yi = poly[i]; xj, yj = poly[j]
        if ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / ((yj - yi) or 1e-9) + xi):
            inside = not inside
        j = i
    return inside


class Perception:
    """model: an ultralytics YOLO. zones: {approach: [(x,y)...] normalized 0..1 polygons}."""

    def __init__(self, model, zones, smooth_alpha=0.35):
        self.model = model
        # ultralytics returns names as a dict, but hub/exported checkpoints can give a list
        names = getattr(model, "names", {}) or {}
        self.names = names if isinstance(names, dict) else dict(enumerate(names))
        self.zones = zones
        self.alpha = smooth_alpha                      # EMA ≈ 1s at ~3 detections/s
        self.smoothed = {a: {} for a in zones}
        self.last_update = 0.0

    def read(self, frame, device=None, imgsz=640):
        """One frame -> (counts, emergencies, boxes). Never raises on a bad frame (edge case A9)."""
        try:
            res = self.model.predict(frame, conf=0.2, imgsz=imgsz, device=device, verbose=False)[0]
        except Exception:
            return self.counts(), set(), []            # reuse last smoothed counts
        h, w = frame.shape[:2]
        if not h or not w:
            return self.counts(), set(), []            # degenerate frame — keep last counts
        raw = {a: {} for a in self.zones}
        dets, emergencies = [], set()
        for b in (res.boxes or []):
            # only classes with a PCU weight count — anything else (person, dog, ...) is ignored,
            # never relabeled. Stock COCO and our fine-tune both emit PCU names directly.
            name = self.names.get(int(b.cls), "")
            if name not in PCU:
                continue
            conf = float(b.conf)
            if conf < CONF.get(name, CONF["default"]):
                continue
            x1, y1, x2, y2 = (float(v) for v in b.xyxy[0])
            dets.append({"x": x1 / w, "y": y1 / h, "w": (x2 - x1) / w, "h": (y2 - y1) / h,
                         "cls": name, "conf": round(conf, 2)})
        boxes = cross_class_dedupe(dets)
        for b in boxes:
            cx, cy = b["x"] + b["w"] / 2, b["y"] + b["h"] / 2   # centroid-in-zone (edge case B17)
            appr = next((a for a, poly in self.zones.items() if poly and point_in_poly(cx, cy, poly)), None)
            b["appr"] = appr
            if appr:
                raw[appr][b["cls"]] = raw[appr].get(b["cls"], 0) + 1
                if b["cls"] in EMERGENCY:
                    emergencies.add(appr)
        # temporal smoothing (edge case B16): EMA per approach+class
        for a in self.zones:
            keys = set(raw[a]) | set(self.smoothed[a])
            for k in keys:
                self.smoothed[a][k] = (1 - self.alpha) * self.smoothed[a].get(k, 0.0) + self.alpha * raw[a].get(k, 0)
                if self.smoothed[a][k] < 0.05:
                    del self.smoothed[a][k]
        self.last_update = time.monotonic()
        return self.counts(), emergencies, boxes

    def counts(self):
        return {a: dict(c) for a, c in self.smoothed.items()}

    def stale(self, max_age=3.0):
        """True if perception hasn't produced counts recently (controller should fall back — C30)."""
        return (time.monotonic() - self.last_update) > max_age
