# Getting true multi-camera junction footage (AI City Challenge dataset)

The only public source of REAL, separately-mounted, time-synchronized cameras covering single
intersections is NVIDIA's AI City Challenge dataset (CityFlow): 40+ synced cameras across 10+
intersections, with calibration. It is request-gated — no anonymous download.

## How to request (≈5 minutes, ~1 day approval)
1. Go to https://www.aicitychallenge.org/ → "Data and Evaluation".
2. Fill the **Dataset Request Form** (name, email — institutional/company mail approves faster,
   affiliation, intended use). Intended use: *"academic research: vision-based adaptive traffic
   signal control (multi-camera intersection perception)."*
3. Accept the license (research-only, no redistribution).
4. The download link arrives by email. Grab **Track 1 (MTMC) City-scale multi-camera vehicle
   tracking** — that's the synced-intersection footage.

## Once you have it
Each intersection has several cameras with sync offsets in the metadata. Feed them to R.E.L.A.Y.:
- Per camera: click zones once — `.venv/bin/python tools/draw_zones.py <cam.mp4> camN.json`
- Then either run per-camera pipelines and merge counts, or tile the synced videos into one
  mosaic (see `footage/hanoi_4cam.mp4` recipe in the worklog) and run a single pipeline with
  quadrant zones.

## Meanwhile (no approval needed)
`footage/hanoi_4cam.mp4` + `zones/hanoi_4cam.json` already give the same demo shape from real
footage: one verified-fixed junction camera cropped into 4 per-approach feeds (perfect sync by
construction), tiled like a CCTV wall, driving the controller live.
