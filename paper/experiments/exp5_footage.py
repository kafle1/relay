#!/usr/bin/env python
"""Experiment 4b: the perception stack on real junction footage.

Runs the repository's own perception stack (``src/perception.py``) frame by frame over real
fixed-camera junction clips, and where the clip shows competing signalised streams also runs
``src/controller.py`` on the resulting counts so that the signal decision shown on the exported
figure is the controller's real output rather than an illustration. The approach polygons are the
ones the repository already defines for these clips in ``tools/analyze_footage.py``.

Outputs, all measured:
  figures/frame_<preset>.png      annotated still frames for the paper
  raw/exp5_<preset>_frames.csv    per-frame inference latency and per-approach counts
  raw/exp5_summary.csv            per-clip latency and count statistics

Usage:  RELAY_REPO=/path/to/relay .venv/bin/python experiments/exp5_footage.py
"""
import csv
import json
import os
import statistics
import sys
import time

REPO = os.environ.get("RELAY_REPO", os.getcwd())
sys.path.insert(0, os.path.join(REPO, "src"))

import cv2                                                       # noqa: E402
import numpy as np                                               # noqa: E402
import torch                                                     # noqa: E402
from ultralytics import YOLO                                     # noqa: E402

from controller import Controller, Timings, junction_from_dirs, PCU   # noqa: E402
from perception import Perception                                     # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.environ.get("RELAY_OUT", os.path.join(HERE, "raw"))
FIGS = os.environ.get("RELAY_FIGS", os.path.abspath(os.path.join(HERE, "..", "figures")))
os.makedirs(OUT, exist_ok=True)
os.makedirs(FIGS, exist_ok=True)

IMGSZ = 960          # src/pipeline.py's default for real footage: small objects need 960+
DUR = 60.0           # seconds of each clip to process

PRESETS = {
    "levanhien": dict(
        src="footage/levanhien_suvanhanh_intersection_cctv.mp4", signalised=True,
        caption="Signalised boulevard: closed-loop control",
        zones={"N": [(0.52, 0.28), (0.98, 0.28), (0.98, 0.99), (0.52, 0.99)],
               "E": [(0.02, 0.14), (0.98, 0.14), (0.98, 0.29), (0.02, 0.29)]},
        label={"N": "boulevard", "E": "cross street"}, start=0.0, fig_at=[22.0, 38.0]),
    "hcmc": dict(
        src="footage/hcmc_intersection_cctv.mp4", signalised=False,
        caption="Dense mixed traffic: detection and PCU demand only",
        zones={"W": [(0.02, 0.30), (0.49, 0.30), (0.49, 0.97), (0.02, 0.97)],
               "E": [(0.51, 0.30), (0.98, 0.30), (0.98, 0.97), (0.51, 0.97)]},
        label={"W": "west half", "E": "east half"}, start=0.0, fig_at=[18.0]),
}
BOX = {"car": (90, 220, 110), "motorcycle": (40, 170, 255), "bus": (250, 200, 90),
       "truck": (80, 90, 255), "ambulance": (250, 250, 250), "bicycle": (210, 170, 250),
       "autorickshaw": (210, 100, 245)}
GREEN, YEL, RED = (90, 220, 110), (40, 210, 250), (70, 70, 240)


def chip(img, x, y, text, scale=0.52, fg=(240, 240, 240), bg=(22, 22, 26)):
    (tw, th), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, scale, 1)
    pad = 6
    ov = img.copy()
    cv2.rectangle(ov, (x, y), (x + tw + 2 * pad, y + th + 2 * pad), bg, -1)
    cv2.addWeighted(ov, 0.82, img, 0.18, 0, img)
    cv2.putText(img, text, (x + pad, y + th + pad - 1), cv2.FONT_HERSHEY_SIMPLEX,
                scale, fg, 1, cv2.LINE_AA)
    return x + tw + 2 * pad + 8


def run(preset, cfg, model_path, device):
    model = YOLO(model_path)
    percep = Perception(model, cfg["zones"])
    ctrl = (Controller(junction_from_dirs(cfg["zones"].keys()),
                       Timings(min_green=5, max_green=25, yellow=3, all_red=1,
                               max_wait=40, w_wait=0.5))
            if cfg["signalised"] else None)
    path = os.path.join(REPO, cfg["src"])
    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        raise SystemExit(f"cannot open {path}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    cap.set(cv2.CAP_PROP_POS_MSEC, cfg["start"] * 1000)

    rows, want = [], sorted(cfg["fig_at"])
    n_frames = int(DUR * fps)
    for i in range(n_frames):
        ok, frame = cap.read()
        if not ok:
            break
        t_clip = i / fps
        t0 = time.monotonic()
        counts, emergencies, boxes = percep.read(frame, device=device, imgsz=IMGSZ)
        infer_ms = (time.monotonic() - t0) * 1000.0
        state = ctrl.tick(counts, emergencies, 1.0 / fps) if ctrl else None

        row = {"t_s": round(t_clip, 3), "infer_ms": round(infer_ms, 2),
               "n_boxes": len(boxes), "phase": state["phase"] if state else "",
               "stage": state["stage"] if state else ""}
        for a in cfg["zones"]:
            c = counts.get(a, {})
            row[f"n_{a}"] = round(sum(c.values()), 2)
            row[f"pcu_{a}"] = round(sum(PCU.get(k, 1.0) * v for k, v in c.items()), 2)
        for cls in BOX:
            row[f"det_{cls}"] = sum(1 for b in boxes if b["cls"] == cls)
        rows.append(row)

        if want and t_clip >= want[0]:
            want.pop(0)
            img = frame.copy()
            ov = img.copy()
            for a, poly in cfg["zones"].items():
                pts = np.array([(int(x * w), int(y * h)) for x, y in poly])
                col = RED
                if state:
                    col = {"green": GREEN, "yellow": YEL}.get(state["signals"].get(a, "red"), RED)
                cv2.fillPoly(ov, [pts], col)
                cv2.polylines(img, [pts], True, col, 2, cv2.LINE_AA)
            cv2.addWeighted(ov, 0.13, img, 0.87, 0, img)
            for b in boxes:
                x1, y1 = int(b["x"] * w), int(b["y"] * h)
                x2, y2 = int((b["x"] + b["w"]) * w), int((b["y"] + b["h"]) * h)
                col = BOX.get(b["cls"], (200, 200, 200))
                cv2.rectangle(img, (x1, y1), (x2, y2), col, 2, cv2.LINE_AA)
                cv2.putText(img, b["cls"], (x1, max(12, y1 - 5)), cv2.FONT_HERSHEY_SIMPLEX,
                            0.40, col, 1, cv2.LINE_AA)
            x = chip(img, 12, 12, "R.E.L.A.Y.", fg=(120, 235, 160), scale=0.58)
            x = chip(img, x, 12, cfg["caption"])
            chip(img, x, 12, f"YOLO {infer_ms:.0f} ms, {len(boxes)} vehicles")
            for a, poly in cfg["zones"].items():
                c = counts.get(a, {})
                pcu = sum(PCU.get(k, 1.0) * v for k, v in c.items())
                px = int(min(p[0] for p in poly) * w) + 8
                py = int(min(p[1] for p in poly) * h) + 8
                txt = f"{cfg['label'][a]}: {sum(c.values()):.0f} veh, {pcu:.1f} PCU"
                if state:
                    txt += f"  [{state['signals'][a]}]"
                chip(img, px, py, txt, scale=0.46)
            if state:
                chip(img, 12, h - 34, f"phase {state['phase']} {state['stage']}"
                                      f"  elapsed {state['elapsed']:.1f} s", scale=0.5)
            fp = os.path.join(FIGS, f"frame_{preset}_{int(t_clip)}s.png")
            cv2.imwrite(fp, img, [cv2.IMWRITE_PNG_COMPRESSION, 6])
            print("wrote", fp, flush=True)
        if i % 200 == 0:
            print(f"  {preset} {i}/{n_frames} frames, {infer_ms:.0f} ms", flush=True)
    cap.release()

    with open(os.path.join(OUT, f"exp5_{preset}_frames.csv"), "w", newline="") as fh:
        wtr = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        wtr.writeheader()
        wtr.writerows(rows)
    lat = [r["infer_ms"] for r in rows]
    nb = [r["n_boxes"] for r in rows]
    return {"preset": preset, "clip": os.path.basename(cfg["src"]), "resolution": f"{w}x{h}",
            "fps": round(fps, 2), "frames": len(rows), "seconds": round(len(rows) / fps, 1),
            "signalised": int(cfg["signalised"]),
            "infer_ms_mean": round(statistics.fmean(lat), 2),
            "infer_ms_sd": round(statistics.stdev(lat), 2),
            "infer_ms_p95": round(sorted(lat)[int(0.95 * (len(lat) - 1))], 2),
            "infer_ms_max": round(max(lat), 2),
            "throughput_fps": round(1000.0 / statistics.fmean(lat), 2),
            "boxes_mean": round(statistics.fmean(nb), 2), "boxes_max": max(nb),
            "phase_switches": len({(r["phase"], i) for i, r in enumerate(rows)}) if False else
                              sum(1 for a, b in zip(rows, rows[1:]) if a["phase"] != b["phase"])}


def main():
    device = "mps" if torch.backends.mps.is_available() else "cpu"
    model_path = os.path.join(REPO, "yolo11s.pt")
    print(f"device={device} model={os.path.basename(model_path)} imgsz={IMGSZ}")
    out = []
    for preset, cfg in PRESETS.items():
        out.append(run(preset, cfg, model_path, device))
        print(out[-1], flush=True)
    with open(os.path.join(OUT, "exp5_summary.csv"), "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(out[0].keys()))
        w.writeheader()
        w.writerows(out)
    with open(os.path.join(OUT, "exp5_config.json"), "w") as fh:
        json.dump({"device": device, "model": os.path.basename(model_path), "imgsz": IMGSZ,
                   "duration_s": DUR, "presets": {k: {kk: vv for kk, vv in v.items()}
                                                  for k, v in PRESETS.items()},
                   "torch": torch.__version__}, fh, indent=2)
    print("wrote exp5_summary.csv to", OUT)


if __name__ == "__main__":
    main()
