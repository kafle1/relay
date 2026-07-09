#!/usr/bin/env python
"""Run stock YOLO on an image, print vehicle detections, save an annotated copy.
Used to measure the synthetic-render domain gap: does the same YOLO see our sim cars?
Usage: .venv/bin/python tools/detect.py <image> [model] [conf]
"""
import sys
from ultralytics import YOLO

# COCO vehicle classes we care about
VEHICLE = {1: "bicycle", 2: "car", 3: "motorcycle", 5: "bus", 7: "truck"}

img = sys.argv[1]
model_name = sys.argv[2] if len(sys.argv) > 2 else "yolo11s.pt"
conf = float(sys.argv[3]) if len(sys.argv) > 3 else 0.25

model = YOLO(model_name)                      # downloads weights on first run
res = model(img, conf=conf, verbose=False)[0]

total = 0 if res.boxes is None else len(res.boxes)
counts, vehicle_total = {}, 0
for b in (res.boxes or []):
    c = int(b.cls)
    counts[c] = counts.get(c, 0) + 1
    if c in VEHICLE:
        vehicle_total += 1

print(f"model={model_name} conf={conf}  image={img}")
print(f"total detections: {total}   (vehicles: {vehicle_total})")
for c, n in sorted(counts.items(), key=lambda kv: -kv[1]):
    tag = " <-- vehicle" if c in VEHICLE else ""
    print(f"  {res.names.get(c, c):<14} x{n}{tag}")

out = img.rsplit(".", 1)[0] + "_yolo.jpg"
res.save(filename=out)
print("annotated saved:", out)
