# Experiment artefacts

Every number in `main.tex` comes from running the code in this directory against a clone of
<https://github.com/kafle1/relay>. Nothing is estimated, extrapolated, or copied from the
repository's own README.

## Reproducing

```bash
git clone https://github.com/kafle1/relay relay && cd relay
python3 -m venv .venv
.venv/bin/pip install ultralytics opencv-python numpy matplotlib scipy

# footage and the base COCO checkpoints are not shipped with the repo; place
#   footage/levanhien_suvanhanh_intersection_cctv.mp4
#   footage/hcmc_intersection_cctv.mp4
#   yolo11s.pt
# in the clone root before running exp5*. The fine-tuned checkpoint used by
# exp4 ships with the repo at dataset/runs/ft_mixed/weights/best.pt.

export RELAY_REPO=$PWD
export RELAY_OUT=<paper>/experiments/raw
export RELAY_FIGS=<paper>/figures

.venv/bin/python <paper>/experiments/exp1_single_junction.py   # ~4 min
.venv/bin/python <paper>/experiments/exp1b_switching.py        # ~2 min
.venv/bin/python <paper>/experiments/exp2_corridor.py          # ~6 min
.venv/bin/python <paper>/experiments/exp3_preemption.py        # ~2 min
.venv/bin/python <paper>/experiments/exp4_detector_val.py      # ~1 min  (needs MPS or CPU)
.venv/bin/python <paper>/experiments/exp5_footage.py           # ~5 min
.venv/bin/python <paper>/experiments/exp5b_resolution.py       # ~2 min
.venv/bin/python <paper>/experiments/exp6_robustness.py        # ~4 min
NOISE=0.20 .venv/bin/python <paper>/experiments/exp6_robustness.py   # ~4 min, noisy-count repeat
.venv/bin/python <paper>/experiments/make_figures.py           # seconds
```

Then build the paper:

```bash
cd <paper> && pdflatex main && bibtex main && pdflatex main && pdflatex main
```

## What each script is, and what is imported versus added

`relay_bench.py` is the shared harness. It imports **unchanged** from the repository under test:
`controller.Controller` (the adaptive policy being evaluated), `controller.Timings`,
`microsim.FixedTimer` (the repository's own equal-split baseline), `microsim.poisson` (the arrival
process) and `microsim.SAT` (the saturation discharge rate). It **adds**, for this paper:

- `QueueSystem`, a point-queue accounting layer with warm-up handling, per-vehicle wait records,
  queue-length sampling and throughput accounting.
- `WebsterTimer`, a fixed-time baseline whose cycle and splits come from Webster's (1958) formulae
  evaluated on the *true* mean demand of the run. This baseline is deliberately favourable to
  fixed-time control: it is given perfect knowledge of the average demand, which no field
  installation has. It exists so the adaptive policy is not compared against a strawman.

| Script | Produces | Feeds |
|---|---|---|
| `exp1_single_junction.py` | `exp1_runs.csv`, `exp1_summary.csv`, `exp1_config.json` | Table 1, Figs 1, 2, 3 |
| `exp1b_switching.py` | `exp1b_runs.csv`, `exp1b_summary.csv` | Fig 4 and the balanced-demand explanation |
| `exp2_corridor.py` | `exp2_runs.csv`, `exp2_summary.csv`, `exp2_config.json` | Table 2, Fig 5 |
| `exp3_preemption.py` | `exp3_preemption.csv`, `exp3_summary.csv`, `exp3_config.json` | Table 3, Fig 6 |
| `exp4_detector_val.py` | `detector_val.csv`, `detector_dataset_stats.json` | Table 4, Fig 7 (left) |
| `exp5_footage.py` | `exp5_*_frames.csv`, `exp5_summary.csv`, annotated PNG frames | Fig 8, latency figures in the text |
| `exp5b_resolution.py` | `exp5b_resolution.csv` | Fig 7 (right), the undercounting limitation |
| `make_figures.py` | every `figures/*.pdf` | all result plots |

`raw/controller_selfcheck.txt` and `raw/microsim_asshipped.txt` are the captured stdout of
`python src/controller.py` and `python src/microsim.py` as shipped, for reference.

## Deliberate omissions and known caveats in the raw data

- **`detector_val.csv` contains two rows that the paper does not report.** The `yolo11n_coco` and
  `yolo11s_coco` rows score near zero because the COCO checkpoints' class indices (car=2,
  motorcycle=3, bus=5, truck=7) do not match this dataset's label space (car=0, motorcycle=1,
  bus=2, truck=3, ambulance=4). Those numbers are an artefact of index mismatch, not a measurement
  of detection quality, and citing them as a comparison would be dishonest. They are left in the
  CSV only so that the discrepancy is visible rather than hidden.
- **`exp3_preemption.csv` has a `green_hold_relay` column that the paper does not use.** It
  accumulates all green time on the emergency arm during the observation window rather than the
  preemption hold specifically, so the name overstates what it measures.
- **The corridor traffic layer in `exp2_corridor.py` is a re-implementation**, because the
  repository's corridor demonstration is a browser application that animates individual vehicles
  and cannot be driven headlessly. The topology, phase sets, routes, timing constants and the
  0.4 downstream coupling coefficient are taken from `sim/network.js`; the control logic is the
  repository's `src/controller.py`. The link travel time (90 m at 12.5 m/s) is a modelling
  parameter chosen here, not read from the demo.
- **Coupling is applied by discounting the demand presented to the controller**, floored at 0.05
  when an approach is physically occupied, so that the coupling reduces pressure magnitude without
  erasing presence. Without the floor the coupling could make the controller skip an approach that
  has vehicles on it.
- **The `single` demand profile at multiplier 1.4 is the one cell where R.E.L.A.Y. loses badly to
  the Webster plan** (`-33.0 +/- 36.4` per cent). This is `max_green = 30 s` capping the adaptive
  green while Webster allocates 51.2 s. It is reported in the paper rather than dropped.
