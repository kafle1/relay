#!/usr/bin/env python
"""Fine-tune YOLO on the auto-labeled sim dataset to close the synthetic-render gap.
Usage: .venv/bin/python tools/train.py [epochs] [imgsz] [device]
"""
import glob, os, random, sys
from ultralytics import YOLO

HERE = os.path.dirname(os.path.abspath(__file__))
DS = os.path.abspath(os.path.join(HERE, "..", "dataset"))
epochs = int(sys.argv[1]) if len(sys.argv) > 1 else 30
imgsz = int(sys.argv[2]) if len(sys.argv) > 2 else 640
device = sys.argv[3] if len(sys.argv) > 3 else "mps"
run_name = sys.argv[4] if len(sys.argv) > 4 else "ft"

imgs = sorted(glob.glob(os.path.join(DS, "images", "*.jpg")))
random.seed(0); random.shuffle(imgs)
k = max(1, int(len(imgs) * 0.15))
val, train = imgs[:k], imgs[k:]
open(os.path.join(DS, "train.txt"), "w").write("\n".join(train))
open(os.path.join(DS, "val.txt"), "w").write("\n".join(val))

yaml_path = os.path.join(DS, "gatichowk.yaml")
open(yaml_path, "w").write(
    f"path: {DS}\ntrain: train.txt\nval: val.txt\n"
    "names:\n  0: car\n  1: motorcycle\n  2: bus\n  3: truck\n  4: ambulance\n"
)

print(f"train={len(train)} val={len(val)} epochs={epochs} imgsz={imgsz} device={device}")
model = YOLO("yolo11n.pt")
model.train(data=yaml_path, epochs=epochs, imgsz=imgsz, batch=8, device=device,
            project=os.path.join(DS, "runs"), name=run_name, exist_ok=True,
            patience=12, cache=True, verbose=False, plots=False)
print("BEST_WEIGHTS:", model.trainer.best)
