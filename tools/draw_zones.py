#!/usr/bin/env python
"""Set up R.E.L.A.Y. on a NEW junction camera in one minute: click the approach zones.

Opens the first frame of the video (or camera). For each approach you name, click its polygon
corners, then press SPACE to finish that zone. Press S to save, Q to quit without saving.

Usage: .venv/bin/python tools/draw_zones.py <video-or-camera> [out.json]
Then:  .venv/bin/python src/pipeline.py <video> --zones out.json
"""
import json, sys
import cv2
import numpy as np

APPROACHES = ["N", "S", "E", "W"]
COLORS = [(80, 220, 80), (0, 210, 250), (60, 60, 255), (250, 160, 90)]

src = sys.argv[1] if len(sys.argv) > 1 else "0"
out_path = sys.argv[2] if len(sys.argv) > 2 else "zones.json"

cap = cv2.VideoCapture(int(src) if src.isdigit() else src)
ok, frame = cap.read()
cap.release()
if not ok:
    sys.exit(f"cannot read a frame from {src!r}")
h, w = frame.shape[:2]

zones = {}          # approach -> [(x, y) normalized]
current = []        # clicks for the zone being drawn
idx = 0             # which approach we're on


def redraw():
    img = frame.copy()
    for i, (name, poly) in enumerate(zones.items()):
        pts = [(int(x * w), int(y * h)) for x, y in poly]
        cv2.polylines(img, [np.array(pts)], True, COLORS[i % 4], 2)
        cv2.putText(img, name, pts[0], cv2.FONT_HERSHEY_SIMPLEX, 0.9, COLORS[i % 4], 2)
    for x, y in current:
        cv2.circle(img, (int(x * w), int(y * h)), 4, COLORS[idx % 4], -1)
    label = APPROACHES[idx] if idx < len(APPROACHES) else "done"
    cv2.putText(img, f"click corners of approach {label}  |  SPACE = next zone  S = save  U = undo  Q = quit",
                (10, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)
    cv2.imshow("R.E.L.A.Y. zone setup", img)


def on_mouse(event, x, y, *_):
    if event == cv2.EVENT_LBUTTONDOWN and idx < len(APPROACHES):
        current.append((x / w, y / h))
        redraw()


cv2.namedWindow("R.E.L.A.Y. zone setup")
cv2.setMouseCallback("R.E.L.A.Y. zone setup", on_mouse)
redraw()

while True:
    key = cv2.waitKey(30) & 0xFF
    if key == ord(" ") and len(current) >= 3 and idx < len(APPROACHES):
        zones[APPROACHES[idx]] = current[:]
        current, idx = [], idx + 1
        redraw()
    elif key == ord("u") and current:
        current.pop()
        redraw()
    elif key == ord("s"):
        if len(current) >= 3 and idx < len(APPROACHES):     # save the in-progress zone too
            zones[APPROACHES[idx]] = current[:]
        if not zones:
            print("nothing to save")
            continue
        json.dump({k: [list(p) for p in v] for k, v in zones.items()}, open(out_path, "w"), indent=1)
        print(f"saved {len(zones)} zones -> {out_path}")
        break
    elif key == ord("q"):
        print("quit without saving")
        break

cv2.destroyAllWindows()
