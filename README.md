# R.E.L.A.Y.

**Real-time Evaluation of Lane Activity for Yield-control** — adaptive traffic-signal control that
never wastes a green on an empty lane.

![live closed loop](docs/img/live_loop.jpg)

Kathmandu's junctions run on fixed timers — blind to real traffic. A green stays on for an empty
approach while a packed one waits (up to ~200 seconds for two motorcycles). R.E.L.A.Y. watches the
junction through ordinary cameras, counts vehicles per approach with a single YOLO model, and
retimes the lights on demand: no green for empty lanes, more green where the queue is, nobody
starves, and an ambulance clears its own path.

## The proof, live

**Split-screen, identical traffic** — left runs today's fixed timer, right runs R.E.L.A.Y. Same
seeded arrivals, only the controller differs. The fixed side queues up; R.E.L.A.Y. flows:

![fixed vs adaptive](docs/img/compare.jpg)

**Any junction shape** — topology is config, not code. Same brain runs 4-way, 3-arm (T), 2-arm:

![T junction](docs/img/t_junction.jpg)

## How it works — one brain, two eyes, one screen

- **Perception:** one YOLO model reads a fixed junction camera → per-approach, per-class vehicle
  counts (PCU-weighted, so a bus ≠ a motorcycle).
- **Control:** weighted **max-pressure** — `score(phase) = PCU-demand + w_wait · oldest-wait +
  emergency-boost` — with empty-phase skip, gap-out, min/max-green, yellow + all-red clearance,
  hard anti-starvation, and emergency preemption. Every safety invariant is asserted by a runnable
  self-check (`src/controller.py`).
- **Two eyes, one detector:** the *same* model reads **real CCTV** and a **synthetic live feed** —
  a Three.js junction endlessly generating random Kathmandu-style traffic. The sim doubles as a
  free, perfectly-labeled dataset (it knows every vehicle's exact box), and mixing those auto-labels
  with pseudo-labeled real frames trains **one detector for both domains** — it even learns to
  visually recognize ambulances.
- **The loop is closed:** browser streams rendered frames → server runs YOLO → controller picks the
  phase → signals return → the sim's cars obey → repeat, in real time.

## Quickstart

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt

# LIVE closed loop (YOLO drives the signals):
.venv/bin/python tools/live_server.py
# → http://127.0.0.1:8000/?live=1            4-way, live detection + adaptive control
# → http://127.0.0.1:8000/?live=1&topo=T     3-arm T junction
# → http://127.0.0.1:8000/compare.html?ff=120  fixed-vs-adaptive split screen (ff = fast-forward s)

# checks
.venv/bin/python src/controller.py    # safety + fairness invariants
.venv/bin/python src/microsim.py      # adaptive-vs-fixed benchmark on identical arrivals

# rebuild the detector (optional — weights train in ~20 min on Apple Silicon)
# 1) auto-labeled sim frames:   open http://127.0.0.1:8000/?capture=300
# 2) pseudo-label real frames:  .venv/bin/python tools/pseudo_label.py <frames_dir>
# 3) train mixed:               .venv/bin/python tools/train.py 25 640 mps ft_mixed

# annotate a real CCTV clip with live detections + adaptive green allocation
.venv/bin/python tools/real_demo.py <clip.mp4>
```

## Measured results

| What | Result |
|---|---|
| Detector on held-out sim frames | mAP@50 ≈ 0.90, precision 0.97 |
| Mixed-domain model | one model detects real CCTV **and** the synthetic feed (incl. ambulance class) |
| Adaptive vs fixed (identical arrivals, imbalanced demand) | **~46–86% less waiting** (avg ≈ 59%) |
| Controller invariants | all pass: clearance, empty-skip, min-green, no-starvation, preemption |

*(Comparable real-world anchor: a 2023 SIDRA study of two Kathmandu junctions measured 33–49%
delay reduction from smarter timing alone.)*

## Repository layout

```
sim/                the synthetic live junction (Three.js)
  main.js           live feed · ?live=1 closed loop · ?capture=N auto-labels · ?topo=4|T|2
  compare.html/js   split-screen fixed-vs-adaptive on identical seeded arrivals
  assets/models/    CC0 / CC-BY 3D vehicles
src/
  controller.py     weighted max-pressure controller (+ invariant self-check)
  microsim.py       adaptive-vs-fixed benchmark on identical arrivals
tools/
  live_server.py    FastAPI + WebSocket: YOLO on streamed frames → signals back
  capture_server.py serve sim + save auto-labeled training frames
  pseudo_label.py   label real CCTV frames with stock YOLO (free real-domain labels)
  train.py          fine-tune YOLO (sim-only or mixed)
  detect.py         run YOLO on an image, report vehicle detections
  real_demo.py      annotate real CCTV with detections + adaptive green allocation
docs/               research synthesis · design spec · 65-case edge-case register
```

## Honest scope

Working prototype with a credible pilot path — not a deployed product. Signal timing is one proven
lever on Kathmandu congestion, not the whole answer. Real CCTV clips used in development are
third-party and not redistributed here.

## Credits

3D models: Kenney Car Kit (CC0), Poly Pizza bus/motorcycle/scooter/bicycle (CC-BY 3.0), Quaternius
pedestrian (CC0) — see [ATTRIBUTION.md](ATTRIBUTION.md).
