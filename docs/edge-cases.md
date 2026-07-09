# R.E.L.A.Y. — Edge-Case Register

*Every case the system must survive. No tests are written for this project, so each case is handled **in the code logic**. This is a hard checklist for the spec + implementation.*
*Legend: 🔴 = safety-critical (a bug here causes a crash or wrong signal) · 🟠 = correctness · 🟡 = demo/robustness.*

---

## A. Perception / detection
1. 🟡 **No detections** (empty junction or total miss) → zero demand for that approach; never index into empty arrays; don't switch green to an empty phase.
2. 🟠 **Low-confidence / missed motorcycles** → per-class confidence thresholds (lower for two-wheelers); don't globally raise threshold and lose bikes.
3. 🟠 **Motorcycle occlusion / merge** (2 bikes → 1 box, or 1 → 2) → tuned NMS; accept residual count noise, absorb via temporal smoothing (B4).
4. 🟠 **Class confusion** (motorcycle↔bicycle, car↔van↔bus↔truck) → map COCO classes → our taxonomy; ambiguous pairs collapse to a super-class; PCU weighting tolerates it.
5. 🟠 **Night / glare / headlights** → detection degrades → auto **degraded mode**: fall back to actuated gap-out timing, flag on dashboard.
6. 🟠 **Rain / fog / dust / dirty lens** → same degraded fallback + heavier smoothing.
7. 🟠 **Pole-cam sway / camera shake** → zones slightly generous; optional stabilization/homography re-lock; don't let a 5px shift break counting.
8. 🟡 **Shadows / sun / reflections / puddles counted as vehicles** → confidence + class filter (shadows aren't a vehicle class); mostly self-solving.
9. 🔴 **Corrupt / dropped / undecodable frame** → skip frame, reuse last counts, never crash the loop.
10. 🟠 **Variable input resolution / aspect** → letterbox-resize before inference; scale zones with the frame.
11. 🟠 **Parked / roadside vehicles in view** → draw approach polygons to exclude parking bays; centroid-in-zone only; a car outside the polygon doesn't count.
12. 🟠 **Bus spanning two zones** → assign by centroid, count once.
13. 🔴 **Detection latency spike** → detection runs on its own fixed cadence, **decoupled** from the control loop; control always uses the latest available counts (never blocks on inference).

## B. Tracking / counting
14. 🟠 **ID switches in dense queue** → control uses *instantaneous density* (occupancy), not cumulative crossings, so ID switches barely matter for timing.
15. 🟠 **Track fragmentation / ghost tracks** → track max-age timeout; evict stale tracks; ring-buffer history.
16. 🟠 **Frame-to-frame count flicker** → temporal smoothing (median or EMA over ~1s) on per-approach counts before the controller sees them.
17. 🟠 **Zone-boundary vehicle (half in/out)** → single consistent rule: centroid-in-polygon.
18. 🟠 **Perspective bias** (far vehicles denser/smaller) → per-camera homography normalization, or per-zone area weighting.
19. 🟠 **Approach the camera can't see** → mark arm **unmonitored** → controller uses fixed-time for it, adaptive for monitored arms (partial observability, never assume 0).
20. 🟠 **Wait-time aging needs per-vehicle age** → derive from track age with tolerance; if a track is lost/reacquired, estimate conservatively (bias toward *more* wait, never less → fairness safe).

## C. Controller / timing — 🔴 SAFETY CRITICAL
21. 🔴 **Never green→red instantly** → every phase switch passes through mandatory **yellow (3–5s) + all-red clearance (1–2s)**. Hard state-machine invariant; no code path skips it.
22. 🔴 **Conflicting greens must NEVER both be on** → phases are predefined conflict-free movement sets; the state machine can only ever hold one valid phase active.
23. 🔴 **Min-green always enforced** (~5s) → prevents flicker and vehicles stranded in the box.
24. 🟠 **Max-green cap** → anti-hog: a continuously-fed lane must yield after `max_green` even if still highest-scoring.
25. 🔴 **Max-wait / force-serve** → any approach waiting ≥ `max_wait` is served regardless of score. Hard anti-starvation guarantee.
26. 🟠 **Score tie** → deterministic tie-break (fixed phase order / round-robin) → no coin-flip oscillation.
27. 🟠 **Phase thrash** (A>B>A>B flip-flop) → min-green + **hysteresis** (challenger must beat incumbent by a margin to switch).
28. 🟠 **All approaches empty** → configurable rest state (all-red flash / hold / default cycle); do NOT rapidly cycle empty phases.
29. 🟠 **Counts drop to 0 mid-green** (dropout, not real) → don't abort green early; ride min-green + smoothed counts + gap-out.
30. 🔴 **Stale counts (perception hung)** → controller **watchdog**: counts older than N seconds → fall back to a safe fixed-time cycle, flag degraded. Control loop never trusts stale data.
31. 🟠 **Monotonic clock** for all control timing (never wall-clock — immune to NTP jumps/DST); wall-clock only for display/logs.
32. 🟠 **Non-4-way junctions** (T, 5-arm, skew — common in KTM) → phase definitions are data/config, never hardcoded to a 4-way.
33. 🟠 **Uncontrolled slip/free-left lanes** → marked uncontrolled, excluded from phase logic.
34. 🔴 **Pedestrian phase** → heavy KTM foot traffic → periodic or actuated ped phase with a **minimum crossing time** (from crossing width); pedestrians can't be starved and can't be stranded mid-crossing (see D-preemption interaction).

## D. Priority / preemption
35. 🟠 **False-positive emergency** (red car misread as ambulance) → require sustained detection (K-of-M frames) + optional flashing-light/color confirmation before preempting.
36. 🔴 **Preemption still obeys clearance** → an approaching ambulance does NOT teleport the light; the current green completes yellow + all-red first, THEN emergency gets green. Safety > speed.
37. 🟠 **Emergency vanishes** (passed / false) → preemption timeout → return to adaptive; never hang.
38. 🟠 **Emergency stuck in gridlock** → hold green but cap with `max_preempt` time + alert; don't freeze the junction forever.
39. 🟠 **Priority vehicle on an already-green arm** → just extend/hold; no switch.
40. 🟠 **Two conflicting emergencies** (ambulance on A *and* B) → higher-priority/first-detected served; the other waits through its clearance; both-green is impossible (C22). Logged.
41. 🟠 **Priority hierarchy** → emergency > school bus > VIP > normal. VIP never beats an ambulance.
42. 🟠 **VIP false trigger** (manual/ANPR) → confirmation + audit log.
43. 🔴 **Preemption during pedestrian WALK** → must finish minimum ped clearance before switching — never strand a pedestrian mid-crossing.
44. 🟠 **Post-preemption fairness** → arms starved during preemption carry their accrued aging weight → served right after clearance.

## E. Synthetic sim (Eye B)
45. 🟠 **Render undetectable by YOLO** (domain gap) → fine-tune on auto-labeled render frames; fallback: drive sim from ground-truth counts (documented in research §4).
46. 🟠 **Sim faster/slower than real-time** → decouple physics tick from detection cadence; timestamp everything.
47. 🟡 **Browser tab throttling** (rAF pauses when backgrounded) → fixed timestep / server-side loop / keep foreground.
48. 🟡 **WebGL context loss** → detect + auto-reload the sim.
49. 🟠 **Sim car overlap / spawn glitch** → spawn spacing + collision handling.
50. 🟡 **Accuracy panel divide-by-zero** (YOLO-count vs ground-truth when 0 vehicles) → guard, show "warming up".

## F. Footage / domain
51. 🟡 **Only Western footage** → still works (cars); the sim carries the motorcycle-dominant Kathmandu story.
52. 🔴 **Wrong angle (dashcam/moving)** → zones invalid → preprocessing rejects non-fixed footage; require a fixed cam.
53. 🟡 **Mismatched resolution/fps** → normalize on ingest.
54. 🟡 **Clip too short for demo** → seamless loop.

## G. Dashboard / demo / ops
55. 🔴 **Apples-to-apples comparison** → the fixed-timer baseline runs as a **shadow controller on the exact same arrival stream** as adaptive → the "X% less wait" claim is fair and defensible. (Do NOT compare against a different clip/run.)
56. 🟡 **Video stream stall** → reconnect / show last frame + "reconnecting".
57. 🟡 **Chart divide-by-zero early** (no vehicles yet) → guard, show "warming up".
58. 🔴 **Demo machine offline** → everything runs **local**; weights downloaded ahead; zero cloud dependency at demo time.
59. 🟠 **Performance on M3 Pro** → detection at reduced fps if needed, YOLO11n fallback, CoreML/ONNX export; keep it smooth.
60. 🟠 **Long-run memory growth** → ring-buffer histories, evict old tracks, cap chart points.
61. 🔴 **Per-frame exception isolation** → the main loop wraps each frame in try/except and continues; one bad frame never crashes the live demo.

## H. Data / config
62. 🟠 **Missing model weights** → download-on-first-run, pinned version + checksum; offline fallback path.
63. 🟠 **Bad/missing zone config** → validate on startup, clear error, refuse to run with garbage zones.
64. 🟠 **Unit confusion** (s vs ms) → one documented unit throughout config.
65. 🟠 **All tunables are config, not magic numbers** → `w_wait`, `min_green`, `max_green`, `max_wait`, `max_preempt`, yellow, all-red, PCU table, per-class confidence, priority weights — every one calibratable without touching code.

---

### The invariants that must hold at ALL times (the 🔴 core)
1. Never two conflicting greens simultaneously.
2. Never green→red without yellow + all-red between.
3. Min-green always honored (even under preemption).
4. No approach and no pedestrian ever starves (max-wait force-serve).
5. Stale/absent perception → safe fixed-time fallback, never a frozen or garbage signal.
6. One bad frame never crashes the loop.
7. Adaptive vs fixed measured on the identical arrival stream.
