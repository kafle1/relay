# Overnight worklog — autonomous build loop

Founder asleep; directive: generate → fix → test → judge-as-hackathon-judge → repeat until flawless.

## Loop state (update every cycle)

**Done this session (all committed unless noted):**
- Turning traffic (left/straight/right, arm-aware — no more driving onto missing arms in T), smooth swing arcs
- In-box collision guard (no overlaps; left-turners yield to oncoming)
- Bike riders (SkeletonUtils clone — fixed the "giant man at origin" skinned-mesh bug)
- Per-file bike facing fix (motorcycle vs scooter GLBs ship opposite orientations)
- N/S/E/W floating arm markers
- CCTV ×4 view mode (one pole cam per approach, 2×2 grid) + view switcher
- Camera drag in live mode (zones re-sent to the detector after each drag)
- In-page junction switcher (shape 4/T/2 × lanes 1–3)
- window.RELAY public state (for dashboards)
- make dev: fail-loud startup + foreground log tail
- Requirements register: docs/requirements.md

**In flight:**
- UI/UX overhaul agent (opus): two-panel compare.html (fixed vs R.E.L.A.Y. iframes + live chart),
  explainer card, real-time mode-colored queue graph, polish. Owns sim/main.js UI surface — do not
  co-edit main.js until it reports.
- JS bug-hunt agent (sonnet): full review of sim/*.js — findings pending.
- git push: intermittently slow from this network — commits are local-first, push retried in background.

**Queue (next cycles):**
1. Apply bug-hunt findings when they land.
2. Pedestrians + pedestrian signals (walk sidewalks, cross zebras on red, green/red man) — after UI agent releases main.js. Model: polypizza_pedestrian_man.glb (use SkeletonUtils.clone).
3. Recapture + retrain detector (visuals changed again: riders, markers) — `?capture=300` then tools/train.py 25 640 mps ft_mixed; restart server.
4. Judge loop: screenshot every mode (free-roam, live ON/OFF, compare, CCTV, T, lanes=3) → spawn judge agent to critique → fix → repeat until no high-severity notes.
5. Final battery: src/controller.py, src/microsim.py, pipeline on GENERATED + Hanoi, make -n targets, browser error sweep.
6. README refresh (new screenshots: CCTV mode, two-panel), final commits + push.

**Key invariants to re-verify after every sim edit:**
- No JS console errors in any mode; 60fps-ish in overview (measure via rAF count)
- Capture mode still produces non-empty labels (verify_labels.py on a fresh frame)
- Live loop: detection boxes + toggle + 🚑 preempt still work

## Bug-hunt findings (JS agent, confirmed) — patch staged in scratchpad/apply_bugfixes.py
- HIGH mutual-yield deadlock in box (symmetric vehicleAhead) → yield only to earlier car (id)
- HIGH CCTV view corrupts capture labels + live zones (projections use overview cam) → guard mode in CAP/LIVE
- MED  N/S signal heads on wrong kerb → negate aside on z-branch
- MED  compare.js reset leaks scene (moot if UI agent deletes compare.js — check)
- LOW  blob PlaneGeometry leak on despawn → dispose on remove
- LOW  ?lanes=abc → NaN geometry → finite guard
- LOW  unknown ?topo → roadless ghost arms → normalize to '4'
APPLY AFTER UI agent releases main.js, then re-verify + commit.
