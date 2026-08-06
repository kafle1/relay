#!/usr/bin/env python
"""Render R.E.L.A.Y. analysis onto real junction footage -> annotated mp4.

Every frame goes through the same perception stack the live demo uses; where the
clip has competing signalized streams (levanhien) the controller runs too and its
live decision is painted on the zones. Unsignalized clips (roundabout, arterial)
get detection + PCU flow counts only — no fake signal decisions.

Usage: python tools/analyze_footage.py <preset> [--start S] [--dur S] [--out path]
Presets: levanhien | hcmc | hanoi | dhaka
"""
import argparse, os, sys, time

import cv2
import numpy as np
import torch
from ultralytics import YOLO

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))
from controller import Controller, Timings, junction_from_dirs  # noqa: E402
from perception import PCU, Perception                          # noqa: E402

PRESETS = {
    # signalized boulevard survey cam: near approach vs far cross street (zones match sim/real.html)
    "levanhien": dict(
        src="footage/levanhien_suvanhanh_intersection_cctv.mp4", model="yolo11s.pt",
        signalized=True, title="Signalized boulevard - closed-loop control",
        zones={"N": [(0.52, 0.28), (0.98, 0.28), (0.98, 0.99), (0.52, 0.99)],
               "E": [(0.02, 0.14), (0.98, 0.14), (0.98, 0.29), (0.02, 0.29)]},
        names={"N": "boulevard", "E": "cross street"}),
    "hcmc": dict(
        src="footage/hcmc_intersection_cctv.mp4", model="yolo11s.pt",
        signalized=False, title="Dense mixed traffic - detection + PCU flow",
        zones={"W": [(0.02, 0.30), (0.49, 0.30), (0.49, 0.97), (0.02, 0.97)],
               "E": [(0.51, 0.30), (0.98, 0.30), (0.98, 0.97), (0.51, 0.97)]},
        names={"W": "west half", "E": "east half"}),
    "hanoi": dict(
        src="footage/hanoi_roundabout_cctv.mp4", model="yolo11s.pt",
        signalized=False, title="Roundabout - flow analysis",
        zones={"W": [(0.02, 0.35), (0.49, 0.35), (0.49, 0.97), (0.02, 0.97)],
               "E": [(0.51, 0.35), (0.98, 0.35), (0.98, 0.97), (0.51, 0.97)]},
        names={"W": "west half", "E": "east half"}),
    "dhaka": dict(
        src="footage/dhaka_intersection_cctv.mp4", model="yolo11s.pt",
        signalized=False, title="Arterial - flow analysis",
        zones={"W": [(0.02, 0.30), (0.49, 0.30), (0.49, 0.97), (0.02, 0.97)],
               "E": [(0.51, 0.30), (0.98, 0.30), (0.98, 0.97), (0.51, 0.97)]},
        names={"W": "west half", "E": "east half"}),
}

BOX = {"car": (89, 199, 52), "motorcycle": (10, 159, 255), "bicycle": (10, 214, 255),
       "bus": (250, 200, 90), "truck": (58, 69, 255), "ambulance": (255, 255, 255),
       "autorickshaw": (242, 90, 191)}
GREEN, RED, YEL = (110, 230, 130), (80, 80, 245), (0, 210, 250)


def chip(img, x, y, text, fg=(240, 240, 240), bg=(28, 28, 30), scale=0.52, pad=7):
    (tw, th), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, scale, 1)
    ov = img.copy()
    cv2.rectangle(ov, (x, y), (x + tw + 2 * pad, y + th + 2 * pad), bg, -1)
    cv2.addWeighted(ov, 0.82, img, 0.18, 0, img)
    cv2.putText(img, text, (x + pad, y + th + pad - 1), cv2.FONT_HERSHEY_SIMPLEX,
                scale, fg, 1, cv2.LINE_AA)
    return x + tw + 2 * pad


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("preset", choices=PRESETS)
    ap.add_argument("--start", type=float, default=0.0)
    ap.add_argument("--dur", type=float, default=60.0)
    ap.add_argument("--out", default=None)
    ap.add_argument("--model", default=None)
    args = ap.parse_args()
    cfg = PRESETS[args.preset]

    device = "mps" if torch.backends.mps.is_available() else "cpu"
    model = YOLO(os.path.join(ROOT, args.model or cfg["model"]))
    percep = Perception(model, cfg["zones"])
    ctrl = None
    if cfg["signalized"]:
        ctrl = Controller(junction_from_dirs(cfg["zones"].keys()),
                          Timings(min_green=5, max_green=25, yellow=3, all_red=1,
                                  max_wait=40))

    cap = cv2.VideoCapture(os.path.join(ROOT, cfg["src"]))
    if not cap.isOpened():
        sys.exit(f"cannot open {cfg['src']}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    cap.set(cv2.CAP_PROP_POS_MSEC, args.start * 1000)
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)); h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    out_path = args.out or os.path.join(ROOT, "docs", "clips", f"{args.preset}_analyzed.mp4")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    vw = cv2.VideoWriter(out_path, cv2.VideoWriter_fourcc(*"mp4v"), fps, (w, h))
    if not vw.isOpened():
        sys.exit("VideoWriter failed to open")

    frames = int(args.dur * fps)
    t_wall = time.monotonic()
    for i in range(frames):
        ok, frame = cap.read()
        if not ok:
            break
        t0 = time.monotonic()
        counts, emergencies, boxes = percep.read(frame, device=device, imgsz=960)
        infer_ms = (time.monotonic() - t0) * 1000
        state = ctrl.tick(counts, emergencies, 1.0 / fps) if ctrl else None

        # zones under everything else: tinted fill + outline, colored by live signal
        ov = frame.copy()
        for a, poly in cfg["zones"].items():
            pts = np.array([(int(x * w), int(y * h)) for x, y in poly])
            col = RED
            if state:
                col = {"green": GREEN, "yellow": YEL}.get(state["signals"].get(a, "red"), RED)
            cv2.fillPoly(ov, [pts], col)
            cv2.polylines(frame, [pts], True, col, 2, cv2.LINE_AA)
        cv2.addWeighted(ov, 0.12, frame, 0.88, 0, frame)

        for b in boxes:
            x1, y1 = int(b["x"] * w), int(b["y"] * h)
            x2, y2 = int((b["x"] + b["w"]) * w), int((b["y"] + b["h"]) * h)
            col = BOX.get(b["cls"], (200, 200, 200))
            cv2.rectangle(frame, (x1, y1), (x2, y2), col, 2, cv2.LINE_AA)
            cv2.putText(frame, b["cls"], (x1, max(12, y1 - 5)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.42, col, 1, cv2.LINE_AA)

        # header: brand + clip story + honest telemetry
        x = chip(frame, 14, 12, "R.E.L.A.Y.", fg=(120, 235, 160), scale=0.62)
        x = chip(frame, x + 8, 12, cfg["title"])
        chip(frame, x + 8, 12, f"YOLO {infer_ms:.0f} ms - {len(boxes)} vehicles")

        # per-zone count chips pinned to each zone's top-left corner
        for a, poly in cfg["zones"].items():
            zc = counts.get(a, {})
            n = sum(zc.values())
            pcu = sum(PCU.get(k, 0) * v for k, v in zc.items())
            px = int(min(p[0] for p in poly) * w) + 6
            py = int(min(p[1] for p in poly) * h) + 6
            sig = f" - {state['signals'].get(a, 'red').upper()}" if state else ""
            chip(frame, px, py, f"{cfg['names'][a]}: {n:.0f} veh (PCU {pcu:.1f}){sig}", scale=0.5)

        if state:
            served = " + ".join(cfg["names"].get(p, p) for p in str(state.get("phase", "")).split("+"))
            chip(frame, 14, h - 44, f"decision: serving {served}  |  camera > counts > signal, live",
                 fg=(120, 235, 160))
        vw.write(frame)
        if i % 150 == 0:
            el = time.monotonic() - t_wall
            print(f"{i}/{frames} frames, {el:.0f}s elapsed, {infer_ms:.0f}ms/frame", flush=True)

    vw.release(); cap.release()
    print(f"wrote {out_path}")


if __name__ == "__main__":
    main()
