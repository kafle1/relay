#!/usr/bin/env python
"""Run stock YOLO on an image, print vehicle detections, save an annotated copy.
Used to measure the synthetic-render domain gap: does the same YOLO see our sim cars?
Usage: .venv/bin/python tools/detect.py <image> [model] [conf]
"""
import os, sys
from ultralytics import YOLO

# vehicle class NAMES (works for both COCO models and our fine-tuned taxonomy)
VEHICLE_NAMES = {"bicycle", "car", "motorcycle", "bus", "truck", "ambulance", "autorickshaw"}

if len(sys.argv) < 2:
    sys.exit("usage: detect.py <image> [model] [conf]")
img = sys.argv[1]
model_name = sys.argv[2] if len(sys.argv) > 2 else "yolo11s.pt"
conf = float(sys.argv[3]) if len(sys.argv) > 3 else 0.25

if not os.path.exists(img):
    sys.exit(f"no such file: {img}")

model = YOLO(model_name)                      # downloads weights on first run
res = model(img, conf=conf, verbose=False)[0]

total = 0 if res.boxes is None else len(res.boxes)
counts, vehicle_total = {}, 0
for b in (res.boxes or []):
    c = int(b.cls)
    counts[c] = counts.get(c, 0) + 1
    if res.names.get(c) in VEHICLE_NAMES:
        vehicle_total += 1

print(f"model={model_name} conf={conf}  image={img}")
print(f"total detections: {total}   (vehicles: {vehicle_total})")
for c, n in sorted(counts.items(), key=lambda kv: -kv[1]):
    tag = " <-- vehicle" if res.names.get(c) in VEHICLE_NAMES else ""
    print(f"  {res.names.get(c, c):<14} x{n}{tag}")

out = img.rsplit(".", 1)[0] + "_yolo.jpg"
res.save(filename=out)
print("annotated saved:", out)
