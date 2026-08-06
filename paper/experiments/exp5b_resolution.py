#!/usr/bin/env python
"""Experiment 4c: resolution sensitivity, the evidence for the undercounting limitation.

A count that keeps rising as inference resolution rises has not converged, which is direct
evidence that vehicles are being missed rather than that the scene has been read correctly. This
script runs the repository's perception stack over the same sampled frames of each clip at
several inference resolutions and reports how the per-approach vehicle count and the PCU demand
move. It also reports the inference cost of each resolution, because the two trade off.

Usage:  RELAY_REPO=/path/to/relay .venv/bin/python experiments/exp5b_resolution.py
"""
import csv
import os
import statistics
import sys
import time

REPO = os.environ.get("RELAY_REPO", os.getcwd())
sys.path.insert(0, os.path.join(REPO, "src"))

import cv2                                                  # noqa: E402
import torch                                                # noqa: E402
from ultralytics import YOLO                                # noqa: E402

from controller import PCU                                  # noqa: E402
from perception import Perception                           # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.environ.get("RELAY_OUT", os.path.join(HERE, "raw"))
os.makedirs(OUT, exist_ok=True)

SIZES = [640, 960, 1280, 1600]
SAMPLE_EVERY = 2.0        # seconds
DUR = 40.0
CLIPS = {
    "levanhien": dict(src="footage/levanhien_suvanhanh_intersection_cctv.mp4",
                      zones={"N": [(0.52, 0.28), (0.98, 0.28), (0.98, 0.99), (0.52, 0.99)],
                             "E": [(0.02, 0.14), (0.98, 0.14), (0.98, 0.29), (0.02, 0.29)]}),
    "hcmc": dict(src="footage/hcmc_intersection_cctv.mp4",
                 zones={"W": [(0.02, 0.30), (0.49, 0.30), (0.49, 0.97), (0.02, 0.97)],
                        "E": [(0.51, 0.30), (0.98, 0.30), (0.98, 0.97), (0.51, 0.97)]}),
}


def frames_of(path, fps_target):
    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        raise SystemExit(f"cannot open {path}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    step = max(1, int(fps * fps_target))
    out, i = [], 0
    while i < int(DUR * fps):
        ok, fr = cap.read()
        if not ok:
            break
        if i % step == 0:
            out.append(fr.copy())
        i += 1
    cap.release()
    return out


def main():
    device = "mps" if torch.backends.mps.is_available() else "cpu"
    model = YOLO(os.path.join(REPO, "yolo11s.pt"))
    rows = []
    for clip, cfg in CLIPS.items():
        frames = frames_of(os.path.join(REPO, cfg["src"]), SAMPLE_EVERY)
        print(f"{clip}: {len(frames)} sampled frames", flush=True)
        for size in SIZES:
            # alpha=1.0 disables temporal smoothing so each frame is measured independently
            percep = Perception(model, cfg["zones"], smooth_alpha=1.0)
            n_tot, pcu_tot, moto, lat = [], [], [], []
            for k, fr in enumerate(frames):
                t0 = time.monotonic()
                counts, _, boxes = percep.read(fr, device=device, imgsz=size)
                ms = (time.monotonic() - t0) * 1000.0
                if k > 0:                      # first call pays a one-off kernel warm-up
                    lat.append(ms)
                n_tot.append(sum(sum(c.values()) for c in counts.values()))
                pcu_tot.append(sum(PCU.get(cl, 1.0) * v for c in counts.values()
                                   for cl, v in c.items()))
                moto.append(sum(c.get("motorcycle", 0) for c in counts.values()))
            rows.append(dict(clip=clip, imgsz=size, n_frames=len(frames),
                             veh_mean=round(statistics.fmean(n_tot), 2),
                             veh_sd=round(statistics.stdev(n_tot), 2),
                             pcu_mean=round(statistics.fmean(pcu_tot), 2),
                             moto_mean=round(statistics.fmean(moto), 2),
                             infer_ms_mean=round(statistics.fmean(lat), 2),
                             infer_ms_median=round(statistics.median(lat), 2)))
            print(rows[-1], flush=True)
    with open(os.path.join(OUT, "exp5b_resolution.csv"), "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    print("wrote exp5b_resolution.csv to", OUT)


if __name__ == "__main__":
    main()
