# R.E.L.A.Y.

**Adaptive traffic-signal control that never wastes a green on an empty lane.**

Kathmandu's junctions run on fixed timers — blind to real traffic. A green stays on for an empty
approach while a packed one waits (up to ~200 seconds for two motorcycles). R.E.L.A.Y. watches each
junction through ordinary cameras, counts vehicles per approach with a single YOLO model, and
retimes the lights on demand: no green for an empty lane, more green where the queue is, nobody
starves, and an ambulance clears its own path.

## How it works — one brain, two eyes, one screen

- **Perception (the brain's input):** one YOLO model reads a fixed junction camera and produces
  per-approach vehicle counts (PCU-weighted; motorcycles counted right).
- **Control (the brain):** a weighted **max-pressure** engine turns counts into signal timing, with
  empty-phase skip, gap-out, waiting-time fairness (aging), min/max-green, yellow + all-red
  clearance, and emergency preemption. Topology-agnostic — 2 / 3 / 4-arm junctions, any lane count.
- **Two eyes, same detector:**
  - **Real CCTV** — fixed pole-camera footage of an intersection.
  - **A synthetic live feed** — a Three.js junction that endlessly generates random, real-time,
    Kathmandu-style traffic (a stand-in for a live CCTV camera). The *same* YOLO runs on its
    rendered frames.
- The synthetic feed doubles as a **free, perfectly-labeled training set**: the sim knows every
  vehicle's exact box, so it auto-generates YOLO labels with zero manual annotation — which is how
  we fine-tune the detector to read the render.

## Repository layout

```
sim/                Three.js live junction simulation (the synthetic CCTV feed)
  index.html
  main.js
  assets/models/    CC0 / CC-BY 3D vehicle models
src/
  controller.py     adaptive max-pressure signal controller (+ self-check)
tools/
  detect.py         run YOLO on an image, report vehicle detections
  capture_server.py serve the sim + save auto-labeled training frames
  train.py          fine-tune YOLO on the captured frames
  verify_labels.py  draw labels on a frame to eyeball them
docs/               research synthesis, design spec, edge-case register
```

## Quickstart

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# run the live synthetic-CCTV junction
.venv/bin/python tools/capture_server.py            # serves sim/ on http://127.0.0.1:8123

# check the controller's safety + fairness invariants
.venv/bin/python src/controller.py

# (re)build the detector: capture labeled frames, then fine-tune
# open http://127.0.0.1:8123/?capture=300  to dump 300 auto-labeled frames, then:
.venv/bin/python tools/train.py 30 640 mps          # device: mps | cuda | cpu
```

## Status

Working: the live synthetic junction, the auto-label + fine-tune pipeline (fine-tuned YOLO reaches
mAP@50 ≈ 0.90 on held-out sim frames and detects the motorcycles stock YOLO misses entirely), and
the controller with its invariants verified. In progress: the real-time closed loop
(perception → controller → live signals) and the dashboard.

## Credits & license

3D vehicle models: Kenney Car Kit (CC0) and three Poly Pizza models (CC-BY 3.0) — see
[ATTRIBUTION.md](ATTRIBUTION.md). Real CCTV clips used only for local development are **not** included
in this repository (third-party / copyrighted).
