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
66. 🔴 **Vehicle bodies must never intersect** → hard capsule separation clamp in the sim (body = spine + real half-width per type): an advance that would close below the safety gap is refused (sliding apart stays allowed), so contact is structurally impossible, not just unlikely.
67. 🔴 **Pedestrian on the zebra when green arrives** → vehicles hold at the stop line while anyone is on their lane's slice of the crossing (per-lane hold — a walker who has cleared your lane no longer freezes it), and walkers are hard obstacles to every vehicle path (exit side included) — safety never depends on signal-timing luck.
68. 🟠 **Walk window shorter than crossing time** (1s all-red vs ≥6s crossing on wide roads) → covered by 66/67; the person finishes, traffic waits.
69. 🟠 **Scenario-button spam** (ambulance/surge clicks) → forced spawns bounded at cap+14, never unbounded array growth or self-inflicted gridlock; the buttons themselves never silently no-op (retry ladders + rearmost-yield for sirens).
70. 🟠 **Live signal feed dies mid-green** → stale signals are dropped immediately and the sim falls back to the safe fixed cycle while reconnecting — never steer on dead data.
71. 🟠 **Corner entry speed** → braking envelope (v² = 2·a·d) caps speed into stops *and* arcs; heavy vehicles accelerate slower — no full-speed 90° turns.
72. 🟠 **One dropped detection frame during an ambulance hold** → 2.5 s emergency latch in the controller; preemption survives detector flicker instead of abandoning the ambulance mid-clear.
73. 🔴 **Pedestrian starvation** (busy road never yields, empty cross street never scores) → ped demand is a first-class controller input: waiting people add pressure + aging to the phases that hold their arm red, and `ped_max_wait` force-opens a walk window — below vehicle starvation and ambulances in priority, above everything else.
74. 🔴 **Walk window cut mid-crossing** → while anyone is ON a zebra the serving phase is held against gap-out *and* score-driven switches (bounded by `max_green` — the junction can never freeze on a crossing stream).
75. 🟠 **Garbage pedestrian payload** (wrong shape, unknown arms, NaN/negative/absurd values) → `_clean_peds` clamps or drops it; junk input can distort nothing and crash nothing.
76. 🟡 **Single-phase junction with ped demand** (2-arm road: no conflicting phase exists to give the walk) → controller returns no walk target rather than inventing one; a dedicated ped stage is the documented upgrade path.
77. 🔴 **Long vehicle wedged mid-turn** (a bus's left-turn arc sweeps nearly the whole box; a small vehicle stopping on that swept path makes the turn geometrically impossible → 18s freeze-then-tow jam) → while a bus/truck is actively turning, other approaches hold at their lines (claim self-releases if the turner wedges >3s); and a turn never commits while a stopped in-box vehicle sits on its swept arc. Verified headless: mid-arc freezes 3→0 per 54 injected buses, no left-turn starvation.
78. 🔴 **Extra browser tabs starve the whole server** (YOLO ran synchronously inside the async WebSocket handler, blocking every HTTP request and socket ~100–300 ms per frame — a few open tabs and the demo machine stopped answering at all) → inference moved to a single dedicated worker thread: the event loop never blocks, concurrent clients queue for the detector instead of killing the server. Verified: 40 HTTP requests during an active stream, worst 9 ms (was: full timeout).
79. 🔴 **Zones sent before the CCTV camera ever rendered** (a camera's world matrices only refresh when it renders; the first zones message of every connection projected through stale matrices → every polygon landed wrong → the controller saw near-zero demand on EVERY fresh page load until a lucky reconnect resent them) → `computeZones` updates the camera matrices itself; zone projection can never depend on render order.
80. 🟠 **A busy green throttled by its own through-traffic** (the "don't block the junction" gate counted same-group vehicles: four of your own cars mid-box held the fifth at the line on a full green — read as "it's green and nobody moves") → the gate counts only cross-group occupants; same-group traffic flows with you. Measured after: 0 stalled-on-green vehicles across both compare panels.
81. 🟠 **The fixed-timer panel claiming emergency preemption** (its banner read the server controller's decision, but with the system pinned OFF the lights ignore those signals — the most dramatic click of the demo showed "EMERGENCY PREEMPT" over a blind cycle) → banner is honest per mode: "ambulance on N — blind fixed cycle, no priority".
82. 🟡 **A stray drag inside a compare embed** (orbit controls left the calibrated CCTV pose → detection boxes vanished permanently; the PiP inset and reset chip exist only full-page) → embeds are watched, not driven: orbit controls disabled under `?embed`.
83. 🟡 **Header clicks while an embed is still loading** (the bridge listener registers after models load; a 🚑/surge/dial click during load — or right after Reset — silently did nothing) → header controls stay dark until BOTH panels ping alive within 2.5 s.
84. 🟠 **A fine-tune quietly failing outside its domain** (our mixed fine-tune reads the sim + its own training cameras, but on an unseen real junction it missed the motorcycle swarm and mislabeled sedans as trucks, while stock YOLO — blind on the synthetic feed — read it cleanly) → per-connection detector choice: each surface announces the model that is honest for its domain, and the on-screen telemetry names it.
85. 🟡 **Phantom suppression tied to frame count** (24 "still frames" assumed the full stream rate; under multi-client contention the rate halves and ghost boxes lived twice as long, exactly when latency already hurts) → stillness is measured on the wall clock (3.5 s), frame-rate independent.

---

### The invariants that must hold at ALL times (the 🔴 core)
1. Never two conflicting greens simultaneously.
2. Never green→red without yellow + all-red between.
3. Min-green always honored (even under preemption).
4. No approach and no pedestrian ever starves (max-wait force-serve).
5. Stale/absent perception → safe fixed-time fallback, never a frozen or garbage signal.
6. One bad frame never crashes the loop.
7. Adaptive vs fixed measured on the identical arrival stream.
