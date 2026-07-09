# R.E.L.A.Y. — Design Spec

*Adaptive, camera-based traffic-signal control for Kathmandu-style mixed traffic.*
*Status: draft for review · 2026-07-09 · supersedes nothing. Companion docs: [research](../../research/2026-07-09-relay-research.md), [edge-cases](../../edge-cases.md).*

---

## 1. The problem (sharp)

Kathmandu junctions run **fixed-timer** signals. Fixed timers are **blind** — they give green on a clock, not on demand. Result: an approach sits red ~200 seconds holding 2 motorcycles while an **empty** approach gets a full green. Green wasted on an empty road, paid for by everyone else waiting.

**R.E.L.A.Y. is an AI that sees each approach and never wastes a green on an empty lane.**

## 2. Value proposition

> **"Kathmandu's lights give green to empty roads. Ours don't."**

Camera-based adaptive control. One proven lever: a 2023 SIDRA study of two Kathmandu junctions measured **33–49% delay reduction** from smarter timing alone. Commercially validated: NoTraffic (pure-vision adaptive control) raised $90M. We make it cheap — works on the CCTV a city already has.

## 3. Goals / non-goals

**Goals (MVP):**
- Real-footage vehicle detection + per-approach counting (fixed camera).
- Weighted max-pressure adaptive control: **empty-phase skip + gap-out + aging/fairness + emergency preemption**.
- A Three.js digital-twin sim running the **same YOLO** on its rendered frames.
- A live dashboard proving **adaptive vs fixed** wait-time reduction.

**Non-goals (explicitly cut):** RL, multi-junction coordination, physical signal hardware, VIP-*branded* priority (kept as an authorized-override *capability*, never pitched as privilege — politically toxic in Nepal), cloud infra, production-grade hardening. This is a **working prototype + a credible pilot path**, not a released product — we say exactly that to judges.

## 4. Architecture — one brain, two eyes, one screen

```
        EYE A: real footage ─┐                     ┌─ dashboard: annotated video
        (fixed overhead, or   │                    │   per-approach count bars
         4 synced cams)       ▼                     │   signal widget
                         ┌──────────┐   counts  ┌────────────┐   phase   │   adaptive-vs-fixed
                         │ PERCEPTION│──────────▶│ CONTROLLER │──────────▶│   wait-time race chart
                         │  (YOLO)  │  per-      │ (weighted  │  + green  │   degraded/preempt banner
                         └──────────┘  approach  │ max-press) │  durations└────────────
        EYE B: Three.js sim ─┘   ▲               └────────────┘
        (same YOLO on render)    │                     │
                                 └─────────────────────┘
                          closed loop: lights drive sim cars, sim cars feed YOLO
```

- **THE BRAIN** (build once): `perception` (frames → per-approach counts) + `controller` (counts → phase + green duration). Topology-agnostic, source-agnostic.
- **TWO EYES**: real footage and the sim, **both** through the identical YOLO code path.
- **ONE SCREEN**: the dashboard that makes the value obvious.

## 5. Camera topology (first-class — matches real deployment)

Perception accepts **only** the two real-world setups:

| Mode | Input | Mapping |
|---|---|---|
| **A — single top-view** | 1 fixed overhead frame | 4 approach ROIs (polygon zones) in one frame |
| **B — 4 synced cams** | 4 fixed frames, 1 per direction | each camera → one approach; merged by timestamp |

- Controller consumes per-approach counts **either way** — topology is a perception detail.
- **Sync rule:** a control decision uses one coherent snapshot (all approaches at time *t*); multi-cam frames aligned by timestamp; a per-camera **staleness watchdog** drops to fixed-time fallback for any camera that goes stale.
- **No moving cameras.** Drone/hover/dashcam rejected — drift invalidates fixed zones. Fixed-mount CCTV / pole / building only. (Hover clips salvageable via `ffmpeg vid.stab` before use.)
- The **sim natively provides Mode B** (4 virtual cameras, perfectly synced) and Mode A (one virtual overhead) — free.

## 6. Perception

- **Model:** Ultralytics **YOLO11s** on Apple MPS. Start **stock COCO** (classes: car, motorcycle, bus, truck, bicycle). Fine-tune later (South-Asian + rendered frames) as the accuracy upgrade — not MVP-blocking.
- **Tracking + counting:** `supervision` ByteTrack + **PolygonZone per-approach density** (instantaneous occupancy, *not* line-crossing — a stopped queue crosses no line). Secondary `LineZone` past the stop-line = throughput/gap-out signal.
- **Per-class confidence** (lower threshold for motorcycles). **Class→taxonomy** map (collapse ambiguous pairs). Per-camera **homography** so perspective doesn't bias near/far density. **~1s temporal smoothing** (median/EMA) before the controller sees counts.
- **Decoupled from control** — runs on its own cadence; the controller never blocks on inference, always uses the latest counts. Emits a **degraded-mode** flag (night/occlusion/dropout).
- **Interface:** `perceive(frames: {cam_id: ndarray}, t) -> {approach_id: {class: count}}`.

## 7. Controller — weighted max-pressure state machine (the heart)

**One score, three weights** — per candidate phase *p* (a conflict-free set of movements):

```
demand(p)   = Σ_{approach a∈p} Σ_{class c} PCU[c] · count[a][c]     # PCU-weighted vehicles present
wait(p)     = max_{a∈p} waiting_time(a)                              # oldest wait among served approaches
priority(p) = priority_boost   if emergency/school-bus present in p  # else 0
score(p)    = demand(p) + w_wait · wait(p) + priority(p)
```

**Decision each tick** (only after `min_green` satisfied on the current phase):
1. **Emergency active** → preempt the phase covering it (*through* mandatory clearance — never teleport the light); hold until it passes or `max_preempt`.
2. Else **force-serve**: if any approach has waited ≥ `max_wait` → serve its phase (hard anti-starvation — the 2-bikes guarantee).
3. Else **candidates = phases with demand > 0** — **EMPTY-PHASE SKIP**: never select a zero-demand phase while any phase has demand. All zero → rest state (all-red flash / hold).
4. Else `next = argmax score(p)`, with a **hysteresis margin** over the incumbent (anti-thrash); deterministic tie-break.

**Green duration (dynamic):**
- Hold ≥ `min_green`.
- **GAP-OUT**: if served approaches clear (count→~0 or flow gaps for `gap_time`) **and** another phase has demand → end green early (after min-green + clearance). *A green never outlives its need — this kills the "empty lane holds green" problem.*
- Hard cap at `max_green` → force switch (anti-hog).

**Transitions (safety invariant):** `GREEN(p) → YELLOW → ALL_RED → GREEN(next)`. No path skips yellow+all-red. **Conflicting greens can never both be active** (phases are predefined conflict-free sets). **Pedestrian phase** periodic/actuated with a minimum crossing time; a preemption can't cut an in-progress WALK.

**Phase definitions are config/data** (T-junction / 4-way / 5-arm / skew — Kathmandu has all). Uncontrolled slip lanes excluded.

**The unifying rule:** *green goes where (demand × wait) is highest; empty ⇒ skip; every real vehicle served within `max_wait`.*

## 8. Baselines & the honest comparison

Two baselines run as **shadow controllers on the identical arrival stream**:
- **FixedNaive** — equal green per phase, round-robin. The "dumb timer" villain (the 200s-empty-lane scenario).
- **Webster** (optional) — smart fixed cycle; the best a fixed plan can do.

**Where the number comes from:** you can't counterfactually re-time *real* cars, so the rigorous **"X% less wait"** is measured in the **sim**, where R.E.L.A.Y., FixedNaive, and Webster all run on the **same spawned arrivals**. Real footage proves *perception + live decisions*; the sim is the **measurement rig** (a scientific control), not eye candy. This is a credibility pillar, not a shortcut.

## 9. Two eyes

- **Eye A (real):** fixed overhead (Mode A) or 4-synced (Mode B) footage. Stock YOLO. Proves detection on real traffic.
- **Eye B (sim):** a from-scratch Three.js junction (road plane, 4-way, light poles) + **CC0 textured GLTF car pack (Kenney Car Kit — verify license)** so rendered vehicles are realistic enough for YOLO. Fixed virtual cameras (Mode A + B). The **same YOLO** detects the rendered frames.
  - **Domain gap de-risking (#1 risk):** the scene graph gives **free auto-labels** (perfect boxes) → fine-tune the same YOLO on rendered frames (SynTraC method). Measure precision/recall on rendered frames in Phase 2 before building on it. Fallback: drive sim cars from ground-truth positions if detection stays too weak.
  - **Accuracy eval = a feature:** run the same YOLO11s on real (Eye A) vs rendered (Eye B), report the count-MAE/mAP delta. "Our detector is verified on both real and simulated feeds" is a judge-facing strength.
  - **Closed loop:** sim cars obey the YOLO-driven signal; their motion changes the counts; loop.

## 10. Dashboard

FastAPI + vanilla JS + Chart.js, **local-only** (no cloud dependency at demo time; weights pre-downloaded). Panels: per-eye annotated video (MJPEG endpoint), per-approach count bars, signal-state widget, **adaptive-vs-fixed cumulative-wait race chart**, degraded-mode indicator, emergency-preempt banner. Ring-buffered history (no unbounded growth).

## 11. Invariants & edge cases

The **7 hard invariants** (never two conflicting greens; never green→red without yellow+all-red; min-green always; nobody starves; stale perception → safe fixed-time; one bad frame never crashes the loop; adaptive-vs-fixed on the identical arrival stream) + the full **65-case register** live in [edge-cases.md](../../edge-cases.md). Added here: **multi-camera sync** (timestamp alignment, per-camera staleness watchdog, one cam down → that approach on fixed-time).

## 12. Config (every tunable — no magic numbers)

`w_wait`, `min_green`, `max_green`, `max_wait`, `max_preempt`, `yellow`, `all_red`, `gap_time`, `PCU_table`, `per_class_confidence`, `priority_weights`, `phase_definitions`, per-camera `ROIs`/`homography`, `camera_topology (A|B)`. All calibratable without touching code.

## 13. Tech stack (exact)

Python ≥3.10 · `ultralytics` (YOLO11s, MPS) · `supervision` (ByteTrack + PolygonZone) · `opencv-python` · `fastapi` + `uvicorn` · `numpy` · Torch (MPS). Browser: Three.js + Kenney CC0 GLTF cars + Chart.js. Tooling: `yt-dlp` + `ffmpeg` (footage, `vid.stab` for salvage).

## 14. Build order (de-risk first)

1. **Brain on a real overhead clip** — YOLO → per-approach counts → weighted max-pressure (empty-skip + gap-out + aging) → a minimal sim to measure adaptive-vs-fixed → race chart. *This alone is a demo.*
2. **Three.js sim** — realistic cars → same YOLO on renders → measure detection → fine-tune if needed → accuracy-eval panel.
3. **Close the loop** — sim cars obey YOLO-driven lights → **emergency preemption** → edge-case hardening → multi-cam (Mode B) topology.
4. **Dashboard polish**.

## 15. Top risks + de-risking (from research)

1. Synthetic-render domain gap → measure early, fine-tune on free auto-labels, ground-truth fallback.
2. Kathmandu detection gap (motorcycles) → fine-tune on South-Asian data (BMD-45: 33.6%→83.8%).
3. Real-time collapse in the loop → YOLO n/s, detect below physics FPS, CoreML/ONNX export.
4. Occlusion miscounts → ByteTrack + temporal smoothing + gap-out safety.
5. Footage that fails the fixed-camera bar → live fixed traffic cams + stabilization; sim covers the rest.

## 16. Honest framing for the pitch

- Baseline = **fixed timers** (user-confirmed ground truth for Kathmandu), not a strawman.
- Claim = **working prototype + pilot path**, not "production-ready."
- Scope = **one proven lever** (signal timing), not "we solved Kathmandu traffic."
- Priority = **emergency (ambulance/fire) + school bus**; "VIP" reframed as neutral authorized-override, never pitched.
- Quote **mechanisms and theorems** (Varaiya throughput-optimality; SIDRA 33–49%; BMD-45 33.6→83.8), never the UNVERIFIED percentages flagged in research §9.

## 17. Footage assets (current)

On disk in `footage/`: real Kathmandu overhead roundabout (drone-hover → needs stabilization), dense Fort Myers overhead 4-way (drone-hover → stabilize), Silk Board Bengaluru ×2 (night hyperlapse — B-roll only), mixkit overhead (only fully-free license), KTM ring-road B-roll. **Fixed-camera + 4-synced hunt in progress** (live DOT/YouTube fixed cams, AI City multi-cam). Licensing: most are YouTube all-rights-reserved — fine for dev/demo, not redistribution; use `mixkit` or get permission for any public release.

## 18. Open questions

1. Which real fixed-camera clip becomes the primary Eye-A demo (pending scout).
2. YOLO11s stock vs fine-tuned — measure on our actual clips in Phase 1–2.
3. Kenney Car Kit license — verify CC0 before shipping.
4. Does the sim's car density visually approximate Kathmandu enough for the fine-tune to transfer.
5. Whether we present Mode B on real 4-synced footage (AI City) or only in the sim.
