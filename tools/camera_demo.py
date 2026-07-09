#!/usr/bin/env python
"""R.E.L.A.Y. on a REAL camera — no extra hardware, the Mac webcam is the junction camera.

Point the laptop at a phone playing junction footage, a printed scene, or an actual street:
live YOLO detections + per-zone counts + the adaptive controller's signal decision, with real
compute telemetry (inference ms / FPS) on screen — visibly real, nothing pre-scripted.

Usage: .venv/bin/python tools/camera_demo.py [source] [model]
  source: 0 = built-in webcam (default), or a video path / RTSP URL
Keys: q quits.
"""
import os, sys, time
import cv2
from ultralytics import YOLO

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "src"))
from controller import Controller, Timings          # noqa: E402
from perception import Perception                   # noqa: E402
from live_zones import junction_from_dirs           # noqa: E402

ZONES = {   # split the camera view: left half = W approach, right half = E approach
    "W": [(0.02, 0.15), (0.49, 0.15), (0.49, 0.97), (0.02, 0.97)],
    "E": [(0.51, 0.15), (0.98, 0.15), (0.98, 0.97), (0.51, 0.97)],
}
SIG = {"green": (80, 220, 80), "yellow": (0, 210, 250), "red": (60, 60, 255)}
COL = {"car": (80, 220, 80), "motorcycle": (10, 160, 255), "bus": (250, 200, 90),
       "truck": (70, 70, 255), "ambulance": (255, 255, 255), "bicycle": (200, 160, 250)}

src = sys.argv[1] if len(sys.argv) > 1 else "0"
src = int(src) if src.isdigit() else src
model_path = sys.argv[2] if len(sys.argv) > 2 else "yolo11s.pt"   # stock: webcam sees real objects

model = YOLO(model_path)
percep = Perception(model, ZONES)
ctrl = Controller(junction_from_dirs(ZONES.keys()),
                  Timings(min_green=4, max_green=20, yellow=2, all_red=1, max_wait=30, w_wait=0.5))

cap = cv2.VideoCapture(src)
if not cap.isOpened():
    sys.exit(f"cannot open camera/source {src!r}")
print("R.E.L.A.Y. camera demo — q to quit")
last = time.monotonic()
while True:
    ok, frame = cap.read()
    if not ok:
        break
    t0 = time.monotonic()
    counts, emergencies, boxes = percep.read(frame, device="mps")
    infer_ms = (time.monotonic() - t0) * 1000
    now = time.monotonic(); dt = min(now - last, 0.5); last = now
    state = ctrl.tick(counts, emergencies, dt)

    h, w = frame.shape[:2]
    for a, poly in ZONES.items():
        pts = [(int(x * w), int(y * h)) for x, y in poly]
        col = SIG[state["signals"].get(a, "red")]
        cv2.polylines(frame, [__import__("numpy").array(pts)], True, col, 3)
        cv2.putText(frame, f"{a}  {sum(counts[a].values()):.0f} veh  [{state['signals'][a].upper()}]",
                    (pts[0][0] + 6, pts[0][1] + 26), cv2.FONT_HERSHEY_SIMPLEX, 0.8, col, 2)
    for b in boxes:
        c = COL.get(b["cls"], (80, 220, 80))
        x1, y1 = int(b["x"] * w), int(b["y"] * h)
        cv2.rectangle(frame, (x1, y1), (x1 + int(b["w"] * w), y1 + int(b["h"] * h)), c, 2)
        cv2.putText(frame, b["cls"], (x1, max(14, y1 - 5)), cv2.FONT_HERSHEY_SIMPLEX, 0.5, c, 2)
    cv2.rectangle(frame, (0, 0), (w, 34), (18, 16, 12), -1)
    cv2.putText(frame, f"R.E.L.A.Y. LIVE CAMERA | phase {state['phase']} {state['stage']} | "
                f"inference {infer_ms:.0f}ms ({1000 / max(infer_ms, 1):.0f} fps) | nothing pre-recorded",
                (10, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (235, 235, 235), 2)
    cv2.imshow("R.E.L.A.Y. — live camera", frame)
    if cv2.waitKey(1) & 0xFF == ord("q"):
        break
cap.release(); cv2.destroyAllWindows()
