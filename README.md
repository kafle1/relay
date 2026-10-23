# R.E.L.A.Y.

**Real-time Evaluation of Lane Activity for Yield-control.** Adaptive traffic signal control from
ordinary junction cameras.

![live closed loop](docs/img/live_loop.jpg)

Kathmandu's junctions run on fixed timers, so a green can stay on for an empty approach while a
full one waits. RELAY counts vehicles per approach with one YOLO model and retimes the lights from
those counts. Empty phases are skipped, no approach waits past a hard limit, and an ambulance gets
its own green.

The paper is under review at IEEE Transactions on Intelligent Transportation Systems. Until it
is published, cite this repository: https://github.com/kafle1/relay

## How it works

- **Perception.** One YOLO model reads a fixed camera and gives per-approach, per-class vehicle
  counts, weighted by PCU (passenger car units, so a bus counts for more than a motorcycle). The
  same model reads real CCTV and a synthetic Three.js junction. The synthetic feed also produces
  labelled training frames, which were mixed with pseudo-labelled real frames to train the detector.
- **Control.** Queue-weighted max-pressure: `score(phase) = PCU-demand × (1 + wait/max_wait)
  + ped-pressure + emergency-boost`. It has empty-phase skip, gap-out, min/max-green, yellow and
  all-red clearance, anti-starvation and emergency preemption. `src/controller.py` checks all of
  these when you run it.
- **Green length.** Arrival and departure rates are measured from how queues grow and shrink, and
  they set each green's share of Webster's cycle. There's no saturation flow table to fill in per
  site. Without this, plain max-pressure ends every green at the 5s floor once the junction is
  saturated, and loses to a fixed timer.
- **Pedestrians.** People waiting to cross add pressure to the phase that gives them a walk. No one
  waits past `ped_max_wait`, and a walk isn't cut while someone is on the crossing.
- **Topology is config.** The same controller runs a 4-way, a T and a 2-arm junction.
  `network.html` runs three signals on one street, where each junction subtracts its neighbour's
  downstream queue from its own pressure.

![fixed vs adaptive](docs/img/compare.jpg)

*Left is a fixed timer, right is RELAY, on the same seeded arrivals.*

## Quickstart

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt

# LIVE closed loop (YOLO drives the signals):
.venv/bin/python tools/live_server.py
# → http://127.0.0.1:8000/?live=1            4-way, live detection + adaptive control
# → http://127.0.0.1:8000/?live=1&topo=T     3-arm T junction
# → http://127.0.0.1:8000/compare.html         fixed-vs-adaptive split screen
# → http://127.0.0.1:8000/network.html         3-signal arterial + ambulance green wave
# → http://127.0.0.1:8000/real.html            real CCTV clip through the same pipeline
#     (drop any static junction video at sim/footage/real.mp4, clips aren't shipped)

# checks
.venv/bin/python src/controller.py    # safety + fairness invariants
.venv/bin/python src/lanes.py         # lane detection: finds lanes on paint, none without it
.venv/bin/python src/calibrate.py     # junction auto-calibration on synthetic traffic
.venv/bin/python src/microsim.py      # adaptive-vs-fixed benchmark on identical arrivals

# the fine-tuned detector ships with the repo (dataset/runs/ft_mixed/weights/best.pt, 5MB), so
# live detection works on a fresh clone. To rebuild it (optional, ~20 min on Apple Silicon):
# 1) auto-labeled sim frames:   open http://127.0.0.1:8000/?capture=300
# 2) pseudo-label real frames:  .venv/bin/python tools/pseudo_label.py <frames_dir>
# 3) train mixed:               .venv/bin/python tools/train.py 25 640 mps ft_mixed
```

## Results

Each number comes from a script in `paper/experiments/` and is rebuilt from the raw result files by
`make_numbers.py`. The paper's `verify_claims.py` fails if the text and the data disagree.

| Setting | Result | Note |
|---|---|---|
| vs a Webster fixed plan, 30 seeds/cell (`exp1_single_junction.py`) | 28-44% less delay, every cell | Webster is given the true mean demand, which no real install has |
| vs an equal-split timer (same runs) | 27-96% less delay | an easy baseline |
| Per-vehicle waits (asymmetric, 1278 veh/h) | p95 and worst wait roughly halved against both fixed plans | |
| 72 cells, 9 demand shapes (7 on the 4-way, 2 on the T), 432 to 2462 veh/h (`exp6_robustness.py`) | beats both fixed plans in all 72 cells; the closest is parity (+1.4%) at one approach demanding 82% of all green | an earlier version lost that cell by 27%, from a discontinuity in the measured-cycle floor near capacity, not the fairness bound; the paper reports the fix |
| Adaptive baselines on the same 72 cells (`exp7_policies.py`) | beats plain max-pressure and SOTL in all 72, vehicle-actuated control in 64, never by less than -1.3% | against actuated control the gap is mostly in the tails (p95 wait 20 s vs 24 s) |
| With the detector's actual error (`exp6b_biased_counts.py`) | unaffected by a saturating undercount or a 20-40% flat undercount; collapses only when a lone vehicle rounds to zero | |
| SUMO, motorcycle-heavy fleet, sublane model, turning conflicts (`exp8_sumo.py`) | less delay in every one of 90 paired runs vs Webster (23-56%), max-pressure (22-36%) and SUMO's actuated program (18-57%) | at the highest load the worst wait is longer than the fixed plans', since the fairness bound only covers what the camera sees |
| SUMO corridor, 150 m links (`exp9_sumo_corridor.py`) | coupling term changes delay by at most 3.9%, both ways | the corridor gain comes from local adaptivity, and the coupling term should go |
| Ambulance to green, 900 trials (`exp3_preemption.py`) | 4.1 s mean, 13.99 s worst against a 14.0 s analytical bound | |
| Sensitivity (`exp10_sensitivity.py`) | fairness bound R from 30 s to none: no change in mean delay; motorcycle PCU weight 0.2 to 1.0: under 1 point | |
| Detector, held-out split (`exp4_detector_val.py`) | mAP@50 0.954, precision 0.950 | the split is synthetic plus pseudo-labelled, not human ground truth |
| Detector, real footage | dense two-wheeler swarms: counts still rising at 1600 px inference | known limitation, modelled in `exp6b` |
| Controller invariants | all twelve pass: clearance, empty-skip, min-green, no-starvation, preemption, pedestrian bounds | checked by `src/controller.py` |

The paper is [`paper/main.pdf`](paper/main.pdf). [`paper/experiments/README.md`](paper/experiments/README.md)
says how to re-run everything.

## Setting up a new junction

The stock detector needs no per-site training. It was checked on Thailand and Hanoi junctions it
never saw in development. Watch the traffic, or click the zones yourself, then run the pipeline:

```bash
.venv/bin/python tools/autocalibrate.py <camera-or-clip> myjunction.json --warmup 240
#   -> myjunction.json  zones + lanes + everything it measured
#   -> myjunction.jpg   the same thing drawn on the road, for review
.venv/bin/python tools/draw_zones.py <camera-or-clip> myjunction.json      # or click the zones
.venv/bin/python src/pipeline.py <camera-or-clip> --zones myjunction.json
```

Approaches come from clustering vehicle trajectories (`src/calibrate.py`), and streams that only
move away from the centre are dropped as exits. Lanes come from road paint (`src/lanes.py`), using
a median road image, Hough lines and a vanishing point. Each lane gets a confidence score.

![auto-calibrated approaches, Hanoi](docs/img/autocal_hanoi.jpg)

*Three minutes of a Hanoi clip: 879 trajectories, 677 usable, three approaches. There's no paint
here, so all three count at approach level.*

Limits:

- Lane detection needs visible markings. Without them it falls back to approach-level counts, and
  `lane_coverage()` reports how much traffic landed inside a lane.
- When traffic never clears (the Dhaka and HCMC clips), the median image is a smear of vehicles,
  and those clips also fall back to approach level.
- The warmup needs traffic on every arm. On cameras where the streets run diagonally, the heading
  error can be large, and anything past 35 degrees prints a warning.
- It writes a proposal for you to review. Edit the JSON, or use `tools/draw_zones.py` when
  clicking is faster.

## Layout

```
sim/      synthetic junction (Three.js): main.js, peds.js, compare.html, network.html, real.html
src/      controller.py, perception.py, lanes.py, calibrate.py, pipeline.py, microsim.py
tools/    live_server.py, autocalibrate.py, pseudo_label.py, train.py, detect.py,
          camera_demo.py, draw_zones.py, verify_labels.py
paper/    main.tex/.pdf and experiments/ (scripts + raw CSVs)
docs/     research notes, design spec, 96-case edge-case register
```

## Scope

This is a working prototype, not a deployed product. The real CCTV clips used in development are
third-party and aren't redistributed here.

## Credits and licence

3D models: Kenney Car Kit (CC0), Poly Pizza bus/motorcycle/scooter (CC-BY 3.0), Quaternius
pedestrian (CC0). See [ATTRIBUTION.md](ATTRIBUTION.md).

GNU AGPL-3.0, see [LICENSE](LICENSE). The detector is built on Ultralytics YOLO, which is AGPL-3.0,
so this repo uses the same terms. 3D assets keep their own licences.
