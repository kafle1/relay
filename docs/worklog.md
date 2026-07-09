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

## Queued behind UI agent (main.js locked):
1. apply_bugfixes.py (deadlock, CCTV guards, kerb, leaks, param guards)
2. REAL TURNING ARCS: replace 2-segment L path with tangent quarter-circle.
   Math (verified by hand, (perp, along) space; o1=laneOff(dir,lane), o2=exitPerp):
   - rRight = max(2.4, 0.55*R); rLeft = R + LANE_W/2 + 0.6
   - uA = o2/a.sign - r  (arc start); arcLen = r*PI/2
   - ex = exit heading sign on exit axis (ARM[exit].out); ez = -a.sign; rotSign = sign(ex*ez)
   - C = (o1 + r*ex, o2 + r*ez); v(th) = (-ex*r*cos th, rotSign*(-ex*r)*sin th); pos = C + v
   - mesh.rotation.y = APPROACH[dir].rotY - rotSign*th; after arc: pos=(o1+ex*r + ex*(u-uEnd), o2), rotY=exit
   - world map: axis 'z' → (perp→x, along→z) else swapped (same as placeCar)
3. SMOOTH ACCEL: per-car c.vel, accel ~7 m/s^2 toward c.speed; brake = clamp to desired delta.
   Replaces instant start/stop (user: "L turn is robotic, must move like real life").
4. Pedestrians + ped signals.
5. Then: recapture+retrain (visuals changed), judge loop, battery, README, push.
6. CCTV polish: re-aim/frame each pole cam so its full approach (queue + stop line + signal) is
   in frame, no dead corners; add small physical CCTV camera props on the signal poles.
7. Exit condition (user asleep, wake-up bar): NOTHING broken — full battery green, judge-agent pass
   with no high-severity findings, all modes screenshot-verified, all commits pushed.
8. END DELIVERABLE — real 4-camera junction control:
   a. Crop Hanoi 1080p into 4 per-approach feeds (use zones/hanoi.json regions),
      ffmpeg xstack 2x2 labeled mosaic -> footage/hanoi_4cam.mp4; quadrant zones json;
      run src/pipeline.py on the mosaic = 4 real synced camera feeds driving our controller.
   b. Write docs/aicity-request.md: how the user requests the AI City multi-camera dataset (doc since removed — dataset never needed)
      (the only public source of true separate synced junction cams).
9. CHAOS + ACCIDENT scenarios: buttons — (a) "accident": stop a vehicle mid-junction/lane for N
   seconds (blocks a lane; system must keep serving others; alert banner); (b) "Kathmandu chaos":
   spawn-rate x3 all approaches + more bikes; system holds. Super-realistic stress demo.


## CLOSED — overnight loop exit summary
All queue items landed: bug-hunt fixes, anti-gridlock (verified), arcs+accel, peds+walk signals,
per-lane gantries, CCTV wall (labels+props), chaos/accident, two-panel compare, judge pass applied
(scene-measured live headline, reconciled compare metrics, honest banner/labels, 960px real-footage
inference, README honesty table, loading state + startup trap), detector retrained on final look
(P .958 / mAP@50 .877), 4-camera real-footage control proven (hanoi_4cam), AI City request doc.
Battery green: controller invariants, microsim 58.9%, make targets, zero console errors all modes.
Known documented limits: rain-poncho two-wheeler swarms undercount (regional fine-tune next);
CCTV wall is a view (live detection runs the overview feed); compare panels are independent traffic
(controlled benchmark labeled as such).

## Jul 9 (evening) — recheck-everything pass + Balkhu corridor
Founder asks: drop remaining footage, audit requirements register, recheck whole codebase, research
how other countries + Lalitpur do it, real physics + never-touch vehicles, bigger junction, Balkhu
corridor in 3D, log every prompt to docs/all-prompts.md (hook now does this automatically).
- footage/ deleted (live sim is the only test surface); make pipeline requires CLIP=.
- requirements.md → all-prompts.md; UserPromptSubmit hook auto-appends every founder message.
- 3-agent review fleet over src/ sim/ tools/: all confirmed findings fixed. Big ones: hardcoded
  label map mislabeled stock-model detections into PCU counts; one dropped detection frame
  abandoned ambulance preemption mid-hold (2.5s latch now); microsim discharge accumulator banked
  impossible green capacity and inflated the benchmark — honest number is now 36% avg (10–73%
  across seeds), README updated, never quote 59% again.
- Sim physics rebuilt: v²=2ad braking toward constraints, corner speed by lateral-g, per-type
  accel; hard 3-disc no-touch separation (cars + pedestrians) with tight box admission and a
  longest-stuck squeeze-through so knots break with zero contact; junction box widened; riders
  were frustum-culled by skinned bind-pose bounds (fixed); pedestrians hold traffic on zebras.
- Research: docs/research/2026-07-09-global-landscape.md (SCATS/SCOOT/Surtrac/City Brain/ATCS
  costs + results, mixed-traffic detection literature, Lalitpur Dec-2024 AI lights post-mortem,
  KTM's 90%-dead signal history, what beats them) + Balkhu OSM geometry notes (Balkhu Chowk +
  Kuleshwor bridgehead + Ekantakuna, 2+2 bridge pinch, zero signal nodes — all police-managed
  today; folded into the corridor build, scene since generalized to network.html).
- Shipped as sim/network.html — the corridor generalized to a 3-signal arterial (T · 4-way · T)
  with per-junction decentralized max-pressure and downstream-queue coordination; the
  Balkhu-specific scene was retired on founder call (generic beats place-specific for judges).
