#!/usr/bin/env python
"""Set up R.E.L.A.Y. on a NEW junction camera by WATCHING it: no clicking, no training.

Runs the detector over a warmup window of ordinary traffic, follows every vehicle, and proposes the
junction topology from what the traffic did: approach zones from clustered trajectories, stop lines
from the junction geometry, lanes from the road paint. Writes a config file and a preview image for
review: it proposes, you deploy.

Usage:
  .venv/bin/python tools/autocalibrate.py <video-or-camera> [out.json] [--warmup 120]
Then:
  open the preview JPG, fix anything wrong in the JSON, and run it:
  .venv/bin/python src/pipeline.py <video-or-camera> --zones out.json

tools/draw_zones.py remains the manual path: click the zones yourself when the camera never sees a
warmup window worth trusting (night, a closed arm, a construction detour).
"""
import argparse, os, sys, time

import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))
from calibrate import Tracker, propose, save_topology, validate_zones   # noqa: E402
from controller import junction_from_dirs                              # noqa: E402
from lanes import MIN_CONFIDENCE, detect, road_plate                   # noqa: E402
from perception import Perception                                      # noqa: E402

ARM_COL = {"N": (110, 230, 130), "S": (0, 200, 250), "E": (250, 170, 90), "W": (200, 120, 250)}


def chip(img, x, y, text, fg=(240, 240, 240), bg=(28, 28, 30), scale=0.52, pad=7):
    (tw, th), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, scale, 1)
    ov = img.copy()
    cv2.rectangle(ov, (x, y), (x + tw + 2 * pad, y + th + 2 * pad), bg, -1)
    cv2.addWeighted(ov, 0.82, img, 0.18, 0, img)
    cv2.putText(img, text, (x + pad, y + th + pad - 1), cv2.FONT_HERSHEY_SIMPLEX,
                scale, fg, 1, cv2.LINE_AA)
    return x + tw + 2 * pad


def preview(plate, prop, lanesets, tracks, path, title):
    """The reviewer's picture: what the traffic did, and what was inferred from it."""
    img = plate.copy()
    h, w = img.shape[:2]

    def px(t):
        return np.array([(int(x * w), int(y * h)) for x, y in t.pts], np.int32)

    # trajectories go on a blended layer so the geometry inferred from them stays readable on top:
    # grey for what the clustering rejected, the arm's colour for what it kept.
    used = {id(t) for m in prop.members.values() for t in m}
    ov = img.copy()
    for t in tracks:
        if id(t) not in used:
            cv2.polylines(ov, [px(t)], False, (165, 165, 165), 1, cv2.LINE_AA)
    for a, members in prop.members.items():
        for t in sorted(members, key=lambda t: -t.displacement())[:60]:
            cv2.polylines(ov, [px(t)], False, ARM_COL.get(a, (200, 200, 200)), 1, cv2.LINE_AA)
    cv2.addWeighted(ov, 0.6, img, 0.4, 0, img)
    for a, poly in prop.zones.items():
        col = ARM_COL.get(a, (200, 200, 200))
        pts = np.array([(int(x * w), int(y * h)) for x, y in poly], np.int32)
        ov = img.copy(); cv2.fillPoly(ov, [pts], col); cv2.addWeighted(ov, 0.14, img, 0.86, 0, img)
        cv2.polylines(img, [pts], True, col, 2, cv2.LINE_AA)
        info = prop.approaches[a]
        sl = [(int(x * w), int(y * h)) for x, y in info["stop_line"]]
        cv2.line(img, sl[0], sl[1], (0, 210, 250), 3, cv2.LINE_AA)
        hd = info["heading"]
        c = np.array(pts).mean(0).astype(int)
        cv2.arrowedLine(img, tuple(c), (int(c[0] + hd[0] * 0.09 * w), int(c[1] + hd[1] * 0.09 * h)),
                        col, 2, cv2.LINE_AA, tipLength=0.3)
        ls = lanesets.get(a)
        for i, lane in enumerate(ls.lanes if ls and ls.trusted() else []):
            lp = np.array([(int(x * w), int(y * h)) for x, y in lane], np.int32)
            cv2.polylines(img, [lp], True, (255, 255, 255), 1, cv2.LINE_AA)
            cv2.putText(img, str(i + 1), tuple(lp.mean(0).astype(int)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 255, 255), 1, cv2.LINE_AA)
        lane_txt = (f"{len(ls.lanes)} lanes @{ls.confidence:.2f}" if ls and ls.trusted()
                    else "approach-level")
        chip(img, min(max(int(min(p[0] for p in poly) * w), 4), w - 260),
             min(max(int(min(p[1] for p in poly) * h) + 4, 4), h - 30),
             f"{a}: {info['trajectories']} tracks - {lane_txt}", fg=col, scale=0.5)
    cx, cy = int(prop.center[0] * w), int(prop.center[1] * h)
    cv2.drawMarker(img, (cx, cy), (60, 60, 245), cv2.MARKER_CROSS, 26, 2, cv2.LINE_AA)
    x = chip(img, 14, 12, "R.E.L.A.Y.", fg=(120, 235, 160), scale=0.62)
    x = chip(img, x + 8, 12, title)
    chip(img, x + 8, 12, "proposed topology - review before deploying")
    cv2.imwrite(path, img)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("source", help="video file, RTSP URL, or camera index")
    ap.add_argument("out", nargs="?", default="topology.json")
    ap.add_argument("--warmup", type=float, default=120.0, help="seconds of traffic to watch")
    ap.add_argument("--sample-fps", type=float, default=5.0, help="detections per second of footage")
    ap.add_argument("--model", default=None)
    ap.add_argument("--device", default="mps")
    ap.add_argument("--imgsz", type=int, default=960)
    ap.add_argument("--preview", default=None, help="preview image (default: <out>.jpg)")
    ap.add_argument("--min-conf", type=float, default=MIN_CONFIDENCE,
                    help="lane-detection confidence needed before lanes go in the config")
    args = ap.parse_args()

    from ultralytics import YOLO
    model = YOLO(args.model or os.path.join(ROOT, "yolo11s.pt"))
    percep = Perception(model, {})                     # no zones yet: we only want the boxes
    src = int(args.source) if args.source.isdigit() else args.source
    cap = cv2.VideoCapture(src)
    if not cap.isOpened():
        sys.exit(f"cannot open {args.source}")
    fps = cap.get(cv2.CAP_PROP_FPS)
    if not 1 <= fps <= 120:
        fps = 25
    step = max(1, int(round(fps / max(args.sample_fps, 0.5))))
    # 0.08 normalized ≈ one lane width between samples: a motorcycle at 30 km/h moves that far in
    # 0.2s, which is exactly the gap this tracker has to bridge before its velocity term takes over.
    tracker = Tracker(max_dist=0.08, max_age=6, min_pts=6)
    live = isinstance(src, int)
    want = int(args.warmup * fps)
    plate_every = max(1, want // 24)

    plate_frames, n, used, fails = [], 0, 0, 0
    t0 = time.monotonic()
    try:
        while (time.monotonic() - t0 < args.warmup) if live else n < want:
            ok, frame = cap.read()
            if not ok or frame is None:
                fails += 1
                if fails > 25:
                    break
                continue
            fails = 0
            if n % plate_every == 0 and len(plate_frames) < 24:
                plate_frames.append(frame.copy())
            if n % step == 0:
                _, _, boxes = percep.read(frame, device=args.device, imgsz=args.imgsz)
                tracker.update(boxes)
                used += 1
                if used % 60 == 0:
                    print(f"  {n / fps:.0f}s watched, {len(tracker.tracks())} trajectories so far", flush=True)
            n += 1
    except KeyboardInterrupt:
        print("\ninterrupted, proposing from what was seen so far")
    finally:
        cap.release()
    if not plate_frames:
        sys.exit(f"no frames read from {args.source}")

    tracks = tracker.tracks()
    prop = propose(tracks)
    print(f"\nwatched {n / fps:.0f}s ({used} detection passes) -> {len(tracks)} trajectories, "
          f"{prop.tracks_used} usable")
    if not prop.zones:
        for note in prop.notes:
            print("  !", note)
        sys.exit("no topology proposed: see the notes above, or click the zones with tools/draw_zones.py")

    plate = road_plate(plate_frames)
    flows = {a: tuple(v["heading"]) for a, v in prop.approaches.items()}
    lanesets = detect(plate, prop.zones, flows=flows, min_conf=args.min_conf)
    prop.lanes = {a: ls.lanes for a, ls in lanesets.items() if ls.trusted(args.min_conf)}

    bad = validate_zones(prop.zones)
    if bad:
        sys.exit(f"internal: proposed an invalid config ({bad}), please report this clip")
    phases = junction_from_dirs(prop.zones.keys()).phases
    meta = {
        "source": str(args.source), "watched_seconds": round(n / fps, 1),
        "detection_passes": used, "trajectories": len(tracks), "trajectories_used": prop.tracks_used,
        "junction_center": list(prop.center), "phases": {k: v for k, v in phases.items()},
        "lane_min_confidence": args.min_conf,
        "approaches": {a: dict(info, lanes=len(prop.lanes.get(a, [])),
                              lane_confidence=round(lanesets[a].confidence, 3) if a in lanesets else 0.0,
                              lane_reason=lanesets[a].reason if a in lanesets else "not attempted")
                       for a, info in prop.approaches.items()},
        "notes": prop.notes + ["auto-calibrated proposal: review the zones, lanes and labels against "
                               "the preview image before running this on a live junction"],
    }
    save_topology(args.out, prop.zones, prop.lanes, meta)
    prev = args.preview or os.path.splitext(args.out)[0] + ".jpg"
    preview(plate, prop, lanesets, tracks, prev, os.path.basename(str(args.source)))

    print(f"junction centre {prop.center} | approaches {sorted(prop.zones)} | "
          f"phases {sorted(phases)}")
    for a, info in prop.approaches.items():
        ls = lanesets.get(a)
        lane_txt = (f"{len(ls.lanes)} lanes (confidence {ls.confidence:.2f})" if ls and ls.trusted(args.min_conf)
                    else f"no lanes: {ls.reason if ls else 'not attempted'} -> approach-level counting")
        print(f"  {a}: {info['trajectories']:>4} trajectories | heading {info['heading']} "
              f"({info['heading_error_deg']}deg off {a}) | turns {info['turns']} | {lane_txt}")
    for note in prop.notes:
        print("  !", note)
    print(f"\nwrote {args.out} and {prev}")
    print(f"review the preview, then: .venv/bin/python src/pipeline.py {args.source} --zones {args.out}")


if __name__ == "__main__":
    main()
