#!/usr/bin/env python
"""R.E.L.A.Y. core system — the full stack on ANY fixed-camera junction footage.

  video (real CCTV clip, stream, or 3D-generated footage)
    → Perception (YOLO + zones + lanes + smoothing)
    → Controller (weighted max-pressure: empty-skip, gap-out, aging, preemption)
    → live signal decisions + annotated output video + decision log

Usage:
  .venv/bin/python src/pipeline.py <video> [--model M.pt] [--zones zones.json]
                                   [--max-seconds N] [--out out.mp4] [--device mps]
                                   [--lanes auto|off] [--save-frame frame.jpg]

Zones JSON: {"N": [[x,y],...], "E": [...]} normalized 0..1 polygons, one per visible approach,
or the wrapped file tools/autocalibrate.py writes, which carries lanes and calibration meta too.
No zones file → screen quadrants (N top, S bottom, W left, E right) as a sane default.
The controller only serves the approaches the camera can see (edge case B19).

Lanes are a finer read on the same detections, never a different one: with lanes the run reports
per-lane queues on top of the per-approach demand the controller consumes. An approach whose lane
detection came back below the confidence gate simply keeps counting at approach level.
"""
import argparse, os, sys, time

import cv2
import numpy as np
from ultralytics import YOLO

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from calibrate import load_topology, validate_zones               # noqa: E402
from controller import Controller, Timings, junction_from_dirs    # noqa: E402
from lanes import MIN_CONFIDENCE, detect, road_plate, trusted_lanes   # noqa: E402
from perception import Perception, PCU, EMERGENCY                 # noqa: E402

COL = {"car": (80, 220, 80), "motorcycle": (10, 160, 255), "bus": (250, 200, 90),
       "truck": (70, 70, 255), "ambulance": (255, 255, 255), "bicycle": (200, 160, 250),
       "autorickshaw": (200, 90, 240)}
SIG = {"green": (80, 220, 80), "yellow": (0, 210, 250), "red": (60, 60, 255)}

QUADRANTS = {   # default zones when none provided: screen quadrants around the centre
    "N": [(0.30, 0.02), (0.70, 0.02), (0.62, 0.42), (0.38, 0.42)],
    "S": [(0.30, 0.98), (0.70, 0.98), (0.62, 0.58), (0.38, 0.58)],
    "W": [(0.02, 0.30), (0.02, 0.70), (0.40, 0.62), (0.40, 0.38)],
    "E": [(0.98, 0.30), (0.98, 0.70), (0.60, 0.62), (0.60, 0.38)],
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("video")
    ap.add_argument("--model", default=None)
    ap.add_argument("--zones", default=None)
    ap.add_argument("--max-seconds", type=float, default=45)
    ap.add_argument("--out", default=None)
    ap.add_argument("--device", default="mps")
    ap.add_argument("--imgsz", type=int, default=960, help="inference size; small objects need 960+ on real footage")
    ap.add_argument("--lanes", choices=("auto", "off"), default="auto",
                    help="auto: use the lanes in the zones file, or detect them from a road plate "
                         "built over the first --lane-warmup seconds")
    ap.add_argument("--lane-warmup", type=float, default=8.0)
    ap.add_argument("--save-frame", default=None, help="write the last annotated frame here (docs, review)")
    args = ap.parse_args()

    if args.model is None:
        args.model = "yolo11s.pt"   # general model: works on any real junction, zero per-site training.
                                    # pass --model dataset/runs/ft_mixed/weights/best.pt for the synthetic feed.
    lane_polys, meta = {}, {}
    if args.zones:
        zones, lane_polys, meta = load_topology(args.zones)
    else:
        zones = QUADRANTS
    zones = {k: [tuple(p) for p in v] for k, v in zones.items()}
    # refuse garbage zones up front (edge case 63): [] disables an approach, anything else
    # must be a real polygon in normalized coords — a silent never-matching zone is worse than an error.
    bad = validate_zones(zones)
    if bad:
        sys.exit(bad)
    flows = {a: tuple(v["heading"]) for a, v in (meta.get("approaches") or {}).items()
             if isinstance(v, dict) and v.get("heading")}
    if args.lanes == "off":
        lane_polys = {}

    model = YOLO(args.model)
    percep = Perception(model, zones, lanes=lane_polys)
    if lane_polys:
        print("lanes from config:", {a: len(p) for a, p in lane_polys.items()})
    if not any(name in EMERGENCY for name in percep.names.values()):
        print(f"WARNING: {os.path.basename(args.model)} has no emergency-vehicle class {sorted(EMERGENCY)} "
              f"— ambulance preemption is inactive with this model.")
    ctrl = Controller(junction_from_dirs(zones.keys()),
                      Timings(min_green=5, max_green=30, yellow=3, all_red=1.5, max_wait=60, w_wait=0.4))

    cap = cv2.VideoCapture(args.video)
    if not cap.isOpened():
        sys.exit(f"cannot open {args.video}")
    fps = cap.get(cv2.CAP_PROP_FPS)
    if not 1 <= fps <= 120:                      # 0/NaN/negative/90000 — broken metadata, assume 25
        fps = 25
    out_path = args.out or os.path.splitext(args.video)[0] + "_relay_system.mp4"
    out = None                                   # created from the first real frame — stream headers lie about size

    decisions, switches, n, fails = [], 0, 0, 0
    prev_phase = None
    # lane detection wants traffic-free asphalt, which no single frame of a live junction has: collect
    # spread-out frames over the warmup window and let the median plate erase the vehicles.
    plate_frames, lanesets = [], {}
    warm_frames = int(max(args.lane_warmup, 0) * fps)
    pending = args.lanes == "auto" and not lane_polys
    t0 = time.monotonic()
    try:
        while n < int(args.max_seconds * fps):
            ok, frame = cap.read()
            if not ok or frame is None:
                fails += 1                       # transient drop (RTSP) → skip; persistent → end of input
                if fails > 25:
                    break
                continue
            fails = 0
            if out is None:
                h, w = frame.shape[:2]
                out = cv2.VideoWriter(out_path, cv2.VideoWriter_fourcc(*"mp4v"), fps, (w, h))
                if not out.isOpened():
                    sys.exit(f"cannot write {out_path} (codec or directory problem)")
            if pending:
                if n % max(1, int(fps / 3)) == 0 and len(plate_frames) < 24:
                    plate_frames.append(frame.copy())
                if n >= warm_frames and len(plate_frames) >= 6:
                    lanesets = detect(road_plate(plate_frames), zones, flows=flows)
                    lane_polys = trusted_lanes(lanesets)
                    percep.set_lanes(lane_polys)
                    plate_frames, pending = [], False
                    print("lane detection: " + "  ".join(
                        f"{a}={len(ls.lanes)}@{ls.confidence:.2f}"
                        + ("" if ls.trusted() else f" (below {MIN_CONFIDENCE}, approach-level: {ls.reason})")
                        for a, ls in lanesets.items()))
            counts, emergencies, boxes = percep.read(frame, device=args.device, imgsz=args.imgsz)
            lane_q = percep.lane_queues()
            stale = percep.stale()
            # camera stale (C30): don't trust it — feed the controller empty counts so aging +
            # max-wait round-robins the phases safely instead of freezing on last-known demand.
            state = ctrl.tick({a: {} for a in zones} if stale else counts, emergencies, 1.0 / fps)
            if state["phase"] != prev_phase:
                switches += 1 if prev_phase is not None else 0
                decisions.append({"t": round(n / fps, 1), "phase": state["phase"],
                                  "counts": {a: round(sum(PCU.get(k, 1) * v for k, v in c.items()), 1)
                                             for a, c in counts.items()},
                                  "lanes": {a: q for a, q in lane_q.items() if q}})
                prev_phase = state["phase"]

            # draw zones tinted by their signal
            for a, poly in zones.items():
                if not poly:
                    continue                     # empty polygon in zones.json → never matches, nothing to draw
                pts = [(int(x * w), int(y * h)) for x, y in poly]
                col = SIG.get(state["signals"].get(a, "red"), (120, 120, 120))
                cv2.polylines(frame, [np.array(pts)], True, col, 2)
                cv2.putText(frame, f"{a}: {sum(counts[a].values()):.0f}", (pts[0][0], max(16, pts[0][1] - 6)),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.6, col, 2)
            # lanes inside the zone, each labelled with its own PCU queue
            for a, polys in lane_polys.items():
                col = SIG.get(state["signals"].get(a, "red"), (120, 120, 120))
                dim = tuple(int(c * 0.65 + 40) for c in col)
                for i, poly in enumerate(polys):
                    pts = np.array([(int(x * w), int(y * h)) for x, y in poly])
                    cv2.polylines(frame, [pts], True, dim, 1)
                    q = lane_q.get(a, [0])[i] if i < len(lane_q.get(a, [])) else 0
                    cv2.putText(frame, f"{q:.1f}", tuple(pts.mean(0).astype(int)),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.45, dim, 1)
            for b in boxes:
                c = COL.get(b["cls"], (80, 220, 80))
                x1, y1 = int(b["x"] * w), int(b["y"] * h)
                cv2.rectangle(frame, (x1, y1), (x1 + int(b["w"] * w), y1 + int(b["h"] * h)), c, 2)
            cv2.rectangle(frame, (0, 0), (w, 36), (18, 16, 12), -1)
            cv2.putText(frame, f"R.E.L.A.Y. core  |  phase {state['phase']} {state['stage']}  |  "
                        + "  ".join(f"{a}:{sum(c.values()):.0f}" for a, c in counts.items()),
                        (10, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (235, 235, 235), 2)
            if stale:
                cv2.rectangle(frame, (0, 36), (w, 62), SIG["red"], -1)
                cv2.putText(frame, "camera stale -- fallback cycling", (10, 56),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.6, (245, 245, 245), 2)
            out.write(frame)
            if args.save_frame:
                cv2.imwrite(args.save_frame, frame)
            n += 1
    finally:
        cap.release()                            # always release, even on Ctrl-C or a mid-loop exception
        if out is not None:
            out.release()
    if n == 0:
        sys.exit(f"no frames read from {args.video}")
    wall = time.monotonic() - t0
    print(f"processed {n} frames ({n / fps:.0f}s of video) in {wall:.0f}s wall "
          f"({n / max(wall, 1e-9):.1f} fps) | model={os.path.basename(args.model)}")
    print(f"approaches seen: {list(zones)} | phase switches: {switches}")
    if lane_polys:
        cover = percep.lane_coverage()
        for a, polys in lane_polys.items():
            conf = f" conf {lanesets[a].confidence:.2f}" if a in lanesets else ""
            print(f"  {a}: {len(polys)} lanes{conf} | queue(PCU) per lane {lane_q.get(a)} | "
                  f"{cover.get(a, 1.0):.0%} of this approach's detections fell inside a lane")
    for d in decisions[:12]:
        print(f"  t={d['t']:>6}s -> {d['phase']:<4} demand(PCU)={d['counts']}")
    print("annotated:", out_path)
    if args.save_frame:
        print("frame:", args.save_frame)


if __name__ == "__main__":
    main()
