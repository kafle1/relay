#!/usr/bin/env python
"""Draw YOLO labels onto their image to eyeball auto-label correctness.
Usage: .venv/bin/python tools/verify_labels.py dataset/images/frame_00020.jpg [out.jpg]
"""
import os, sys, cv2

if len(sys.argv) < 2:
    sys.exit("usage: verify_labels.py <image> [out.jpg]")
img_path = sys.argv[1]
lbl_path = img_path.replace("/images/", "/labels/").rsplit(".", 1)[0] + ".txt"
names = ["car", "moto", "bus", "truck", "amb"]
colors = [(0, 200, 0), (0, 150, 255), (255, 60, 60), (60, 60, 255), (255, 255, 255)]

im = cv2.imread(img_path)
if im is None:
    sys.exit(f"could not read image: {img_path}")
h, w = im.shape[:2]
n = 0
if os.path.exists(lbl_path):
    for line in open(lbl_path):
        p = line.split()
        if len(p) != 5:
            continue
        c = int(p[0]); cx, cy, bw, bh = map(float, p[1:])
        x1, y1 = int((cx - bw / 2) * w), int((cy - bh / 2) * h)
        x2, y2 = int((cx + bw / 2) * w), int((cy + bh / 2) * h)
        cv2.rectangle(im, (x1, y1), (x2, y2), colors[c], 2)
        cv2.putText(im, names[c], (x1, max(10, y1 - 4)), cv2.FONT_HERSHEY_SIMPLEX, 0.45, colors[c], 1)
        n += 1
out = sys.argv[2] if len(sys.argv) > 2 else "verify.jpg"
cv2.imwrite(out, im)
print(f"wrote {out} with {n} boxes")
