# Experiment artefacts

Every number, table and figure in `main.tex` is generated from the raw result files in `raw/`
by the scripts in this directory. Nothing in the manuscript is typed in by hand: the text uses
macros from `../numbers.tex`, the tables are the files in `../tables/`, and `verify_claims.py`
regenerates both and fails if the committed copies differ.

## Reproducing

```bash
git clone https://github.com/kafle1/relay relay && cd relay
python3 -m venv .venv
.venv/bin/pip install ultralytics opencv-python numpy matplotlib scipy eclipse-sumo traci sumolib
export RELAY_REPO=$PWD
export SUMO_HOME=$PWD/.venv/lib/python3.12/site-packages/sumo   # the eclipse-sumo wheel

# controller and simulation, point queues
.venv/bin/python src/controller.py > paper/experiments/raw/controller_selfcheck.txt
.venv/bin/python src/microsim.py   > paper/experiments/raw/microsim_asshipped.txt
.venv/bin/python paper/experiments/exp1_single_junction.py     # ~5 min
.venv/bin/python paper/experiments/exp1b_switching.py          # ~2 min
.venv/bin/python paper/experiments/exp2_corridor.py            # ~2 min
.venv/bin/python paper/experiments/exp3_preemption.py          # ~1 min
.venv/bin/python paper/experiments/exp6_robustness.py          # ~4 min
NOISE=0.20 .venv/bin/python paper/experiments/exp6_robustness.py
.venv/bin/python paper/experiments/exp6b_biased_counts.py      # ~40 min, ten count models
.venv/bin/python paper/experiments/exp7_policies.py            # ~10 min
.venv/bin/python paper/experiments/exp10_sensitivity.py        # ~12 min

# microsimulation (SUMO through TraCI, parallel)
NSEEDS=10 NPROC=8 .venv/bin/python paper/experiments/exp8_sumo.py            # ~10 min
NSEEDS=10 NPROC=6 .venv/bin/python paper/experiments/exp9_sumo_corridor.py   # ~8 min

# perception (needs the two footage clips and yolo11s.pt in the clone root; see below)
.venv/bin/python paper/experiments/exp4_detector_val.py
.venv/bin/python paper/experiments/exp5_footage.py
.venv/bin/python paper/experiments/exp5b_resolution.py

# manuscript
.venv/bin/python paper/experiments/make_numbers.py
.venv/bin/python paper/experiments/make_tables.py
.venv/bin/python paper/experiments/make_figures.py
cd paper && pdflatex main && bibtex main && pdflatex main && pdflatex main
.venv/bin/python experiments/verify_claims.py
```

Every run is seeded. Re-running the point-queue experiments from a clean clone returns the
committed CSVs byte for byte. SUMO runs are seeded too, but SUMO's own version and platform
can move a result in the second decimal.

The footage clips `levanhien_suvanhanh_intersection_cctv.mp4` and `hcmc_intersection_cctv.mp4`
and the base COCO checkpoint `yolo11s.pt` are not shipped; place them in the clone root before
running `exp5*`. The fine-tuned checkpoint used by `exp4` ships at
`dataset/runs/ft_mixed/weights/best.pt`.

## What each script is

`relay_bench.py` is the shared harness. It imports unchanged from the repository under test:
`controller.Controller`, `controller.Timings`, `microsim.FixedTimer` and `microsim.poisson`. It
adds `QueueSystem`, a point-queue accounting layer with warm-up handling and per-vehicle wait
records, and `WebsterTimer`, a fixed-time plan from Webster's formulae on the true mean demand,
deliberately generous to fixed-time control so the adaptive policy is not compared against a
strawman. `baselines.py` holds the adaptive reference policies: plain max-pressure,
self-organising traffic lights and vehicle-actuated control, on the same tick interface.
`sumo_bench.py` builds the SUMO junction, generates the motorcycle-dominated fleet, counts
vehicles the way a camera would and drives the signal through TraCI.

| Script | Produces | Feeds |
|---|---|---|
| `exp1_single_junction.py` | `exp1_runs.csv`, `exp1_summary.csv` | Table I, Figs 1 and 2 |
| `exp1b_switching.py` | `exp1b_runs.csv`, `exp1b_summary.csv` | Fig 3 |
| `exp2_corridor.py` | `exp2_runs.csv`, `exp2_summary.csv` | Table IV |
| `exp3_preemption.py` | `exp3_preemption.csv`, `exp3_summary.csv` | Table V, Fig 4 |
| `exp4_detector_val.py` | `detector_val.csv`, `detector_dataset_stats.json` | Table VI, Fig 5 |
| `exp5_footage.py`, `exp5b_resolution.py` | `exp5_*`, `exp5b_resolution.csv` | Fig 5, Fig 6 |
| `exp6_robustness.py` | `exp6_robustness*.csv` | Table VII |
| `exp6b_biased_counts.py` | `exp6b_biased_counts.csv` | Table VIII |
| `exp7_policies.py` | `exp7_policies.csv` | Fig 7 |
| `exp8_sumo.py` | `exp8_sumo_runs.csv`, `exp8_sumo_summary.csv` | Table IX, Fig 8 |
| `exp9_sumo_corridor.py` | `exp9_sumo_corridor_*.csv` | Table X |
| `exp10_sensitivity.py` | `exp10_sensitivity.csv` | Fig 9 |
| `make_numbers.py` | `../numbers.tex` | every number in the text |
| `make_tables.py` | `../tables/*.tex` | every result table |
| `make_figures.py` | `../figures/*.pdf` | every plot |
| `verify_claims.py` | | exit 1 if the manuscript is stale |
| `check_bib.py` | | checks every DOI in `refs.bib` against Crossref |

## Caveats kept visible in the raw data

- `detector_val.csv` carries two rows the paper does not report, `yolo11n_coco` and
  `yolo11s_coco`, which score near zero because the COCO class indices do not match this
  dataset's label space. They are an artefact of index mismatch, not a measurement, and are
  left in so the discrepancy is visible rather than hidden.
- `exp3_preemption.csv` has a `green_hold_relay` column the paper does not use; it accumulates
  all green time on the emergency arm in the window rather than the preemption hold alone.
- The controller changed between the first and the final run of these experiments: the cycle
  used by the measured-saturation floor is now clamped at 120 s and the estimator attributes a
  count change to the signal state of the step that produced it. Section VI-F of the paper
  reports what the earlier version did and why it was wrong.
