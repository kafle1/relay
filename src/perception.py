#!/usr/bin/env python
"""R.E.L.A.Y. perception core — frames in, per-approach vehicle counts out.

Source-agnostic: the same class serves real CCTV clips, live streams, and the synthetic feed.
Handles the perception edge cases from docs/edge-cases.md: per-class confidence, centroid-in-zone
assignment, ~1s temporal smoothing, corrupt-frame tolerance, and a staleness flag.
"""
import time

# vehicle taxonomy: name -> PCU weight (Kathmandu mix — a bus is not a motorcycle)
PCU = {"car": 1.0, "motorcycle": 0.3, "bus": 2.5, "truck": 2.5, "ambulance": 2.0,
       "bicycle": 0.2, "autorickshaw": 0.8}
# COCO ids -> our names (stock models); fine-tuned models already emit our names
COCO = {1: "bicycle", 2: "car", 3: "motorcycle", 5: "bus", 7: "truck"}
# per-class confidence: lower for two-wheelers (small, easily missed — edge case A2)
CONF = {"motorcycle": 0.30, "bicycle": 0.30, "default": 0.40}
EMERGENCY = {"ambulance"}


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
        self.zones = zones
        self.alpha = smooth_alpha                      # EMA ≈ 1s at ~3 detections/s
        self.smoothed = {a: {} for a in zones}
        self.last_update = 0.0

    def class_name(self, cls_id):
        name = self.model.names.get(cls_id, str(cls_id))
        return COCO.get(cls_id, name) if name.isdigit() or name == "person" or cls_id in COCO and name not in PCU else name

    def read(self, frame, device=None):
        """One frame -> (counts, emergencies, boxes). Never raises on a bad frame (edge case A9)."""
        try:
            res = self.model.predict(frame, conf=0.25, device=device, verbose=False)[0]
        except Exception:
            return self.counts(), set(), []            # reuse last smoothed counts
        h, w = frame.shape[:2]
        raw = {a: {} for a in self.zones}
        boxes, emergencies = [], set()
        for b in (res.boxes or []):
            cls_id = int(b.cls)
            name = self.model.names.get(cls_id, "")
            if name not in PCU:
                name = COCO.get(cls_id, "")
            if name not in PCU:
                continue
            conf = float(b.conf)
            if conf < CONF.get(name, CONF["default"]):
                continue
            x1, y1, x2, y2 = (float(v) for v in b.xyxy[0])
            cx, cy = (x1 + x2) / 2 / w, (y1 + y2) / 2 / h   # centroid-in-zone (edge case B17)
            appr = next((a for a, poly in self.zones.items() if poly and point_in_poly(cx, cy, poly)), None)
            boxes.append({"x": x1 / w, "y": y1 / h, "w": (x2 - x1) / w, "h": (y2 - y1) / h,
                          "cls": name, "conf": round(conf, 2), "appr": appr})
            if appr:
                raw[appr][name] = raw[appr].get(name, 0) + 1
                if name in EMERGENCY:
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
