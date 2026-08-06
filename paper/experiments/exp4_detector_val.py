#!/usr/bin/env python
"""Experiment 4a: detector validation on the held-out split of the mixed dataset.

Runs the shipped fine-tuned checkpoint (dataset/runs/ft_mixed/weights/best.pt) and the
general-purpose COCO checkpoints against the repository's own 96-image validation split
and writes the real metrics to CSV. Nothing here is estimated: every number is whatever
ultralytics' validator reports.

Usage:  .venv/bin/python experiments/exp4_detector_val.py  (run from the repo clone root)
"""
import csv
import json
import os
import sys

REPO = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
if not os.path.exists(os.path.join(REPO, "src", "controller.py")):
    REPO = os.environ.get("RELAY_REPO", os.getcwd())
OUT = os.environ.get("RELAY_OUT", os.path.join(os.path.dirname(os.path.abspath(__file__)), "raw"))
os.makedirs(OUT, exist_ok=True)

DATA_ROOT = os.path.join(REPO, "dataset_local")
NAMES = {0: "car", 1: "motorcycle", 2: "bus", 3: "truck", 4: "ambulance"}


def build_local_yaml():
    """Rewrite the dataset split lists so they point at the workspace copy, then emit a yaml."""
    ref_root = "/Users/nirajkafle/Desktop/niraj/dev-projects/trafficmgmt/dataset"
    for split in ("train", "val"):
        src = os.path.join(ref_root, f"{split}.txt")
        with open(src) as fh:
            lines = [ln.strip() for ln in fh if ln.strip()]
        rewritten = [ln.replace(ref_root, DATA_ROOT) for ln in lines]
        with open(os.path.join(DATA_ROOT, f"{split}.txt"), "w") as fh:
            fh.write("\n".join(rewritten) + "\n")
    yml = os.path.join(DATA_ROOT, "relay.yaml")
    with open(yml, "w") as fh:
        fh.write(f"path: {DATA_ROOT}\ntrain: train.txt\nval: val.txt\nnames:\n")
        for i, n in NAMES.items():
            fh.write(f"  {i}: {n}\n")
    return yml


def label_stats():
    """Instance counts per class over the whole labelled set and over the val split."""
    def count(paths):
        per = {n: 0 for n in NAMES.values()}
        for p in paths:
            lp = p.replace("/images/", "/labels/").rsplit(".", 1)[0] + ".txt"
            if not os.path.exists(lp):
                continue
            with open(lp) as fh:
                for ln in fh:
                    parts = ln.split()
                    if parts:
                        per[NAMES[int(parts[0])]] += 1
        return per

    out = {}
    for split in ("train", "val"):
        with open(os.path.join(DATA_ROOT, f"{split}.txt")) as fh:
            paths = [ln.strip() for ln in fh if ln.strip()]
        out[split] = {"images": len(paths), "instances": count(paths)}
    return out


def main():
    from ultralytics import YOLO
    import torch

    yml = build_local_yaml()
    stats = label_stats()
    with open(os.path.join(OUT, "detector_dataset_stats.json"), "w") as fh:
        json.dump(stats, fh, indent=2)
    print("dataset:", json.dumps(stats, indent=2))

    device = "mps" if torch.backends.mps.is_available() else "cpu"
    models = {
        "relay_ft_mixed": os.path.join(REPO, "dataset", "runs", "ft_mixed", "weights", "best.pt"),
        "yolo11n_coco": os.path.join(REPO, "yolo11n.pt"),
        "yolo11s_coco": os.path.join(REPO, "yolo11s.pt"),
    }
    rows = []
    for tag, path in models.items():
        if not os.path.exists(path):
            print(f"skip {tag}: {path} missing")
            continue
        m = YOLO(path)
        n_par = sum(p.numel() for p in m.model.parameters())
        try:
            r = m.val(data=yml, imgsz=640, device=device, verbose=False, plots=False,
                      project=os.path.join(OUT, "val_runs"), name=tag, exist_ok=True)
        except Exception as exc:                     # a COCO checkpoint has a different class map
            print(f"{tag}: val failed ({exc})")
            continue
        b = r.box
        row = {"model": tag, "params_M": round(n_par / 1e6, 2),
               "mAP50": round(float(b.map50), 4), "mAP50_95": round(float(b.map), 4),
               "precision": round(float(b.mp), 4), "recall": round(float(b.mr), 4)}
        for i, name in NAMES.items():
            try:
                idx = list(b.ap_class_index).index(i)
                row[f"mAP50_{name}"] = round(float(b.ap50[idx]), 4)
            except (ValueError, IndexError):
                row[f"mAP50_{name}"] = ""
        rows.append(row)
        print(tag, row)

    if rows:
        keys = list(rows[0].keys())
        with open(os.path.join(OUT, "detector_val.csv"), "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=keys)
            w.writeheader()
            w.writerows(rows)
        print("wrote", os.path.join(OUT, "detector_val.csv"))


if __name__ == "__main__":
    main()
