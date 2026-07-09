#!/usr/bin/env python
"""Pseudo-label real CCTV frames with stock YOLO so the fine-tune can mix real + synthetic domains.

Stock YOLO11s is already strong on real daytime CCTV (verified), so its high-confidence detections
become free labels for the real domain — no manual annotation. Merged with the sim's auto-labels,
one detector learns BOTH domains ("same YOLO on real footage and on the synthetic feed").

Usage: .venv/bin/python tools/pseudo_label.py <frames_dir> [conf]
Writes labeled copies into dataset/images + dataset/labels with a real_ prefix.
"""
import glob, os, shutil, sys
from ultralytics import YOLO

HERE = os.path.dirname(os.path.abspath(__file__))
DS = os.path.abspath(os.path.join(HERE, "..", "dataset"))
COCO2OURS = {2: 0, 3: 1, 5: 2, 7: 3}   # car, motorcycle, bus, truck  (bicycle/person skipped)

frames_dir = sys.argv[1]
conf = float(sys.argv[2]) if len(sys.argv) > 2 else 0.45

os.makedirs(os.path.join(DS, "images"), exist_ok=True)
os.makedirs(os.path.join(DS, "labels"), exist_ok=True)

model = YOLO("yolo11s.pt")
frames = sorted(glob.glob(os.path.join(frames_dir, "*.jpg")))
kept = boxes_total = 0
for f in frames:
    res = model(f, conf=conf, verbose=False)[0]
    lines = []
    for b in (res.boxes or []):
        c = int(b.cls)
        if c not in COCO2OURS:
            continue
        x, y, w, h = (float(v) for v in b.xywhn[0])
        lines.append(f"{COCO2OURS[c]} {x:.6f} {y:.6f} {w:.6f} {h:.6f}")
    name = "real_" + os.path.splitext(os.path.basename(f))[0]
    shutil.copy(f, os.path.join(DS, "images", name + ".jpg"))
    open(os.path.join(DS, "labels", name + ".txt"), "w").write("\n".join(lines))
    kept += 1
    boxes_total += len(lines)

print(f"pseudo-labeled {kept} real frames, {boxes_total} boxes -> {DS}")
