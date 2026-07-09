#!/usr/bin/env python
"""Annotate a real CCTV clip with live YOLO detections + the green time R.E.L.A.Y. would allocate.

The Eye-A proof: the same detector reads real junction footage, PCU-weighted demand is computed
live, and the adaptive green allocation is shown against the blind fixed timer.

Usage: .venv/bin/python tools/real_demo.py <clip.mp4> [model.pt] [max_seconds]
Writes <clip>_relay.mp4 next to the input.
"""
import os, sys
import cv2
from ultralytics import YOLO

PCU = {"car": 1.0, "motorcycle": 0.3, "bus": 2.5, "truck": 2.5, "bicycle": 0.2}
COCO = {1: "bicycle", 2: "car", 3: "motorcycle", 5: "bus", 7: "truck"}
COL = {"car": (80, 220, 80), "motorcycle": (10, 160, 255), "bus": (250, 200, 90), "truck": (70, 70, 255), "bicycle": (200, 160, 250)}
MIN_G, MAX_G, BASE_G, K = 5, 45, 8, 2.2   # green = clamp(base + k*pcu)

src = sys.argv[1]
model_path = sys.argv[2] if len(sys.argv) > 2 else "yolo11s.pt"
max_s = float(sys.argv[3]) if len(sys.argv) > 3 else 30.0

model = YOLO(model_path)
cap = cv2.VideoCapture(src)
fps = cap.get(cv2.CAP_PROP_FPS) or 25
w, h = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
out_path = os.path.splitext(src)[0] + "_relay.mp4"
out = cv2.VideoWriter(out_path, cv2.VideoWriter_fourcc(*"mp4v"), fps, (w, h))

ema_pcu, alpha = 0.0, 0.15          # ~1s smoothing on demand
n = 0
while n < int(max_s * fps):
    ok, frame = cap.read()
    if not ok:
        break
    res = model.predict(frame, conf=0.35, verbose=False)[0]
    pcu = 0.0
    per = {}
    for b in (res.boxes or []):
        cls = COCO.get(int(b.cls)) or res.names.get(int(b.cls))
        if cls not in PCU:
            continue
        x1, y1, x2, y2 = (int(v) for v in b.xyxy[0])
        c = COL.get(cls, (80, 220, 80))
        cv2.rectangle(frame, (x1, y1), (x2, y2), c, 2)
        cv2.putText(frame, cls, (x1, max(14, y1 - 5)), cv2.FONT_HERSHEY_SIMPLEX, 0.5, c, 2)
        pcu += PCU[cls]
        per[cls] = per.get(cls, 0) + 1
    ema_pcu = (1 - alpha) * ema_pcu + alpha * pcu
    green = max(MIN_G, min(MAX_G, BASE_G + K * ema_pcu))

    # HUD strip
    cv2.rectangle(frame, (0, 0), (w, 64), (18, 16, 12), -1)
    txt = "  ".join(f"{k}:{v}" for k, v in sorted(per.items())) or "no vehicles"
    cv2.putText(frame, f"R.E.L.A.Y. live read  |  {txt}  |  demand {ema_pcu:.1f} PCU", (12, 25),
                cv2.FONT_HERSHEY_SIMPLEX, 0.62, (235, 235, 235), 2)
    cv2.putText(frame, f"adaptive green: {green:.0f}s   (fixed timer: 30s regardless of traffic)", (12, 52),
                cv2.FONT_HERSHEY_SIMPLEX, 0.62, (140, 235, 160), 2)
    out.write(frame)
    n += 1

cap.release(); out.release()
print(f"wrote {out_path} ({n} frames @ {fps:.0f}fps, model={model_path})")
