# R.E.L.A.Y. — Pre-Build Research Synthesis & Build Blueprint

*Lead-engineer synthesis of 8 research axes (9 agents, 240 web fetches). Decision-ready. Read before writing code.*
*Generated 2026-07-09.*

---

## 1. Executive Summary

Build R.E.L.A.Y. as a single Python pipeline: **Ultralytics YOLO (fine-tuned) → supervision ByteTrack + per-lane PolygonZone/RegionCounter → simplified Max-Pressure timing engine → drives both a real signal display and a synthetic junction whose camera feed loops back into the *same* YOLO model.** Every layer has a mature, permissively-usable OSS component today, and the closest published precedent (RPI's CARLA+SUMO+YOLO co-sim, arXiv:2412.03925; and SynTraC, ITSC'24) validates the exact architecture — but nobody has shipped the full "one detector on real + synthetic CCTV, closed loop into both" system as open code, so R.E.L.A.Y. fills a genuine gap. The single biggest risk is the **synthetic-render domain gap**: a COCO-pretrained detector *qualitatively* sees rendered cars but will *not* hit the counting precision the timing engine needs without fine-tuning on rendered frames (SynTraC shows count MAE 0.64–1.02 out-of-the-box). Second-order risk: Kathmandu's motorcycle-dominant, lane-less traffic is structurally different from anything COCO/Western datasets represent — fine-tuning on South-Asian data (BMD-45: 33.6% → 83.8% mAP) is mandatory. Good news: fine-tuning fixes both at once (sim segmentation camera gives free auto-labels), the timing algorithm is ~40 lines of theorem-backed Python, and every recommended library is MIT/Apache except the detector (AGPL, a non-issue for a hackathon demo). Prove the domain gap is closeable first, before polishing anything.

---

## 2. The Stack — Concrete Choices

| Layer | PICK | Runner-up | One-line why |
|---|---|---|---|
| **Detection model** | **YOLO11s** (fine-tuned) | YOLO26s / YOLO26n | YOLO11 has the deepest fine-tuning ecosystem for hackathon time-pressure; YOLO26 is faster on CPU + has small-object STAL but is only ~6mo old with thin community coverage. Benchmark both on your footage. |
| **Tracker** | **ByteTrack** (`roboflow/trackers`, Apache-2.0) | BoT-SORT-ReID | MIT, ~30 FPS, motion-only, low-confidence-box association targets occlusion — the dominant Kathmandu failure mode. Skip DeepSORT (GPL-3.0, weakest on occlusion). |
| **Counting method** | **Instantaneous per-lane density** (`PolygonZone` / `RegionCounter`) | LineZone crossing counts (secondary) | A stopped queue produces *zero* line-crossings; cumulative counting fails once congestion occupies the zone — R.E.L.A.Y.'s normal condition. |
| **Timing algorithm** | **Simplified single-junction Max-Pressure** | Queue-proportional split (Webster-adaptive) | `pressure(phase)=Σ queue_count(lane)`, `argmax`, clamped by min/max green. Consumes YOLO counts directly, Varaiya throughput-optimality theorem, tolerates no-lane-discipline. ~40 lines. |
| **Sim engine** | **CARLA 0.9.16** (MIT+CC-BY) | ursina (pure-Python) / SUMO | Only option with near-photoreal render *and* full Python API for signal control + vehicle spawning + free auto-labels (segmentation camera). SynTraC is a near-exact precedent. **[See §NOTE — Mac caveat.]** |
| **Dashboard** | **FastAPI + vanilla JS + Chart.js** (MJPEG endpoint) | Streamlit (`solutions.Inference`) | Full multi-panel layout + concurrency for the "adaptive vs fixed" race chart. Streamlit ships a live app in minutes as fallback. |

**NOTE (added post-synthesis): CARLA needs an NVIDIA GPU (≥8GB VRAM) + ~170GB disk and does not run well on Apple Silicon macOS. On a Mac dev machine, substitute a browser (Three.js) or Python (ursina/pygame) sim and self-generate ground-truth labels from the scene graph — same free-auto-label trick, no CARLA.**

---

## 3. Per-Axis Findings

### Axis 1 — Detection Model
- **YOLO26 is the current Ultralytics release (Jan 14 2026)**. YOLO26n: 40.9 mAP, 38.9ms CPU; YOLO26s: 48.6 mAP, ~11 FPS CPU. **Progressive Loss + STAL (Small-Target Aware Label assignment)** = most on-point feature for small motorcycles [https://docs.ultralytics.com/models/yolo26].
- **YOLO11s (47.0 mAP, 90ms CPU)** = safer, best-documented fallback. Relevant paper: YOLO11 vs YOLO8 on developing-country mixed traffic [https://arxiv.org/pdf/2606.12066].
- **"43% faster CPU" is marketing** — real numbers compute to ~30%.
- **Fine-tuning is mandatory:** BMD-45 — Western-trained detector 33.6% mAP vs 83.8% after in-domain fine-tuning (2.5×) [https://arxiv.org/abs/2604.24419]. COCO lacks auto-rickshaws/tempos and dense motorcycle-swarm CCTV.
- **Two-stage transfer:** COCO → fine-tune on DriveIndia + UVH-26 + BMD-45 → light calibration on real Kathmandu footage.
- **License:** Ultralytics YOLO is **AGPL-3.0** (fine for demo). Apache alternatives if productized: RT-DETRv2-s (48.1 mAP), RF-DETR (56.5 mAP). YOLO-NAS weights have a non-commercial trap.
- *UNVERIFIED:* "YOLO11m 95% mAP" (false, 51.5); YOLOv5 motorcycle 62–70%; exact YOLO26 mAP.

### Axis 2 — Tracking + Counting
- **ByteTrack (MIT, ~30 FPS)**: MOT17 80.3/77.3/63.1. Occlusion handling via low-conf association, motion-only. **BoT-SORT-ReID** slightly better but adds GPU cost; its camera-motion-comp is *useless* for fixed CCTV. **DeepSORT: skip** (GPL-3.0, weakest).
- **Library: `supervision` (MIT) zones + `roboflow/trackers` (Apache-2.0)** for swappable trackers. Or Ultralytics `solutions.RegionCounter` (named polygons → per-lane counts in one call).
- **Counting = instantaneous density, NOT cumulative crossings.** A red-light queue never crosses a line [https://www.sciencedirect.com/science/article/abs/pii/S0968090X09000230]. Give `LineZone` a secondary gap-out/throughput job past the stop line.
- **Calibrate one homography per camera** (`cv2.getPerspectiveTransform`) so perspective doesn't bias near-vs-far density.
- *No controlled tracker study on dense-motorcycle CCTV exists* — ranking is reasoned extrapolation.

### Axis 3 — Signal-Timing Algorithms
- **PICK: simplified Max-Pressure.** `pressure(phase)=Σ queue_count(lanes served)`, `next=argmax`, `green=clamp(base+k·pressure, min, max)`. **Varaiya (2013) throughput theorem** = credibility. Needs only per-lane counts. ~40 lines.
- **Webster's** (`C₀=(1.5L+5)/(1−Y)`) = baseline-to-beat + source for min/max-green bounds. Overestimates cycle >~50% saturation (Kathmandu's regime).
- **Fully-actuated gap-out** (min-green ~5s, max ~35–60s, passage ~2–3s) = safety fallback when perception degrades.
- **SOTL** = lightest code, good "lights self-organize" multi-junction narrative.
- **RL (PressLight/DQN)** = *stretch only.* Free training env in the closed-loop sim. Tooling: `sumo-rl` v1.4.5, `stable-baselines3` v2.9.0, CityFlow.
- **South-Asia precedent:** arXiv:2205.01640 (AIMD adaptive control, Delhi-validated) + India's CoSiCoSt — cheap local control beats RL here.
- *UNVERIFIED:* max-pressure "77–87% delay reduction" (403-blocked); exact PressLight %.

### Axis 4 — Kathmandu Mixed Traffic (see §5 for pitch)
- **Nepal has no indigenous PCU standard** — NRS 2070 imports India's IRC numbers wholesale.
- Field motorcycle PCU (~0.23–0.27) is **far below** the imported 0.5. Shrestha (2010) redefined the reference unit as "motorcycle" not "car". Shrestha (2013): PCU isn't constant across 3 Kathmandu intersections.
- A 2023 SIDRA re-timing of two Kathmandu junctions found **33–49% delay/travel-time reductions with zero AI** — proven headroom.
- ~1M motorcycles in the Valley, ~70–80% of national fleet, >50% of Valley accidents. Regional study: real saturation flow hits **2574 pcu/h/lane, *above* HCM benchmarks** via seepage/filtering.

---

## 4. THE Synthetic-Render Domain-Gap Risk

**Question: can we feed rendered frames to a real-trained YOLO and get counts good enough to drive signal timing?**

**Verdict: Viable, but ONLY with fine-tuning on rendered frames. Zero-shot is not reliable enough for counting precision.** #1 project risk — retire first.

**Evidence (both directions):**
- *Qualitatively works:* community repos run stock COCO YOLO on CARLA with working detections — but none report rigorous mAP (demos, not evals).
- *Measurable gap:* **SynTraC (ITSC'24)** — closest precedent — non-fine-tuned detectors give vehicle-count MAE 0.64 (sunny), 0.95 (fog), 1.02 (night); "drops a lot even with sunny/daytime." Fine-tuning improved cross-domain MAE 1.975 → 1.725 [https://arxiv.org/html/2408.09588v1].
- *General synthetic→real lit:* source-only detectors on SIM10K/GTA→Cityscapes land ~30% mAP until adapted; photorealism + domain randomization together beat either alone; **synthetic validation metrics poorly predict real performance** [https://arxiv.org/abs/2509.15045].

**De-risking plan:**
1. **Week 1, measure it yourself** — run YOLO on your actual rendered frames, compute precision/recall vs ground truth.
2. **Fine-tune, don't zero-shot** — a few hundred–low-thousands of render frames from your specific virtual camera pose.
3. **Free labels** — the sim's segmentation/scene-graph auto-generates perfect boxes, zero manual annotation.
4. **Match camera geometry + randomize the rest** — set virtual camera to a Kathmandu CCTV pole; randomize weather/time. Perspective + background diversity closes the gap more than raw photorealism.
5. **Fix asset mismatch** — weight spawns toward motorcycles, tune for low lane discipline.
6. **Validate on real frames** — hold out real footage to catch overfitting to render artifacts.
7. **Double-win** — the same South-Asian fine-tune that fixes the Kathmandu gap also helps the render gap. One fine-tuning effort mixing real + rendered frames.

**Fallback if it fails:** decouple the two loops — run the detector on *real* CCTV footage for the perception demo, drive the sim's car behavior from *ground-truth* positions (bypass YOLO on the synthetic side). Lose narrative purity, keep a working closed loop + working real-footage perception.

---

## 5. Kathmandu Pitch Ammunition

**PCU table — the standard vs the reality (this IS the pitch):**

| Vehicle | IRC/NRS standard (imported) | Nepal field-measured | Kathmandu "MCU" (Shrestha 2010, motorcycle=1) |
|---|---|---|---|
| Motorcycle (2W) | 0.5 | **0.23–0.27** | **1.0 (reference unit)** |
| Car | 1.0 | — | 4.92–5.29 |
| Auto-rickshaw (3W) | ~0.8–1.2 | 0.76–1.95 | — |
| Bus | 3.0 | 5.01–5.45 | 26.5–27.1 |
| Truck | 4.5 | 3.31–3.71 | — |
| Cycle | 0.4–0.5 | — | 1.08–1.46 |

**The 3 strongest "why this fits Kathmandu" facts:**
1. **Structurally different traffic physics that fixed PCU tables provably cannot represent** — Nepal's own engineers broke the car-centric convention; PCU isn't even constant across three intersections in one city. Only a live per-lane per-class YOLO count tracks a coefficient that changes intersection-to-intersection and hour-to-hour. A static lookup table is *provably wrong* here.
2. **Headroom empirically proven today, zero AI** — 2023 SIDRA study: **33–49% delay reductions** from smarter logic alone on manually-controlled Kathmandu junctions. Nepal's Dept of Roads already runs a "Kathmandu Valley Intelligent Traffic System Project." You answer a govt-recognized problem more cheaply (CCTV + one YOLO) than SIDRA/drone consulting.
3. **Motorcycles ARE the traffic** — ~1M in the Valley, ~70–80% of national fleet, >50% of accidents, saturation flow **2574 pcu/h/lane above HCM benchmarks** via lateral filtering. R.E.L.A.Y. is built around the exact dense, non-lane, motorcycle-dominant pattern a Western-trained model or fixed timer was never designed to see.

**Commercial validation:** NoTraffic (camera+radar, no induction loops, decentralized real-time control) raised $90M + Florida statewide approval — proves pure-vision adaptive control is fundable and deployable today. (Don't overclaim its proprietary algorithm over your published, theorem-backed max-pressure.)

---

## 6. Prior Art to Clone First

**Repos (ranked):**
1. **[LucasAlegre/sumo-rl](https://github.com/LucasAlegre/sumo-rl)** — MIT, active, Gymnasium standard. Best foundation for the timing engine; clean `TrafficSignal`/TraCI abstraction to plug YOLO counts into.
2. **[mihir-m-gandhi/Adaptive-Traffic-Signal-Timer](https://github.com/mihir-m-gandhi/Adaptive-Traffic-Signal-Timer)** — Apache-2.0, closest architectural blueprint (YOLO camera → per-lane counts → timing → Pygame sim). Clone the 3-module *design*, not the dead darkflow/TF1 code.
3. **[docwza/sumolights](https://github.com/docwza/sumolights)** — GPL-3.0 (read-only, reimplement). Best reference for Webster's + max-pressure baselines.
4. **[AndreaVidali/Deep-QLearning-Agent-for-Traffic-Signal-Control](https://github.com/AndreaVidali/Deep-QLearning-Agent-for-Traffic-Signal-Control)** — MIT, clean, active. RL stretch scaffold.
5. **[cityflow-project/CityFlow](https://github.com/cityflow-project/CityFlow)** — Apache-2.0, ~20–25× faster than SUMO if raw multi-junction throughput matters.
- **Architecture validators (no code):** RPI arXiv:2412.03925 (CARLA+SUMO+YOLO co-sim); **[DaRL-LibSignal/SynTraC](https://github.com/DaRL-LibSignal/SynTraC)** (CARLA cameras → detector → signal decisions).
- **Avoid:** flow-project/flow (Py3.7/TF1.15, stale); darkflow; sub-10-star no-license hackathon repos.

**Datasets (download in order):**
1. **BMD-45** (huggingface.co/datasets/iisc-aim/BMD-45) — real fixed-overhead CCTV, 45K img/480K boxes, 14 South-Asian classes, built-in domain-gap benchmark. *License UNVERIFIED — check.*
2. **SkyEye + EyeonTraffic** — only datasets built for lane-less motorcycle-dominant gap-weaving; MOT tracks + congestion labels.
3. **ITD / IIT Roorkee** — INDO-HCM taxonomy for adaptive-signal turning counts; best schema reference for Nepal's vehicle mix.
- **Supplementary:** DriveIndia (CC-BY-4.0, dashcam→pretraining only), UVH-26, vehicles-nepal-dataset (Nepal-native but tiny/2-class).
- **Genuine gap:** no public Nepal-scale annotated junction dataset. Hand-label a small in-house Kathmandu + synthetic validation set early.

---

## 7. Top 5 Technical Risks + De-Risking (ranked)

1. **Synthetic-render domain gap** → week-1 empirical measurement + fine-tune on auto-labeled render frames + validate on real. Fallback: decouple loops. (§4)
2. **Kathmandu domain gap** (motorcycles under-detected) → two-stage fine-tune. BMD-45 proves 33.6%→83.8%. Combine with #1 into one effort.
3. **Real-time collapse in the loop** (RPI: YOLOv8 → >2× wall-clock under heavy sim traffic) → use YOLO n/s on the synthetic side, detect at lower FPS than physics loop, export ONNX/OpenVINO before demo.
4. **Occlusion → ID switches in dense queues** → ByteTrack low-conf association + ~1s temporal smoothing on counts + gap-out safety net.
5. **CARLA install/stability eats time** (~170GB, ≥8GB VRAM, no Mac) → non-issue if you skip CARLA per §NOTE; else pin 0.9.16 stable, provision GPU day 1.

*License note:* everything MIT/Apache except YOLO (AGPL — fine for demo) and GPL fallback repos (reimplement, don't vendor).

---

## 8. Recommended Build Order

**Prove the risky things first. Don't polish the dashboard until perception is proven.**

- **Phase 0 — Setup (day 1):** Python ≥3.10. `pip install ultralytics supervision`. Get stock YOLO running on a clip.
- **Phase 1 — Retire the #1 risk (days 2–4):** Render sim intersection frames from a CCTV pose. Run stock YOLO, measure precision/recall vs ground truth. Auto-label, fine-tune, re-measure. Gate: MAE ~1? If no → invoke §4 fallback now, not on demo day.
- **Phase 2 — Kathmandu fine-tune (days 3–5, parallel):** Download BMD-45 + SkyEye. Fine-tune on merged South-Asian + render frames. Validate on hand-labeled Kathmandu clips.
- **Phase 3 — Counting layer (days 5–6):** ByteTrack + per-lane PolygonZone/RegionCounter. One homography per camera. ~1s temporal smoothing.
- **Phase 4 — Timing engine (days 6–7):** Max-pressure (~40 lines) + min/max-green clamps + gap-out fallback. Import identically into real-signal driver, sim actor, and dashboard shadow-comparison.
- **Phase 5 — Close the loop (days 7–8):** Sim releases cars on the signal state driven by YOLO-of-rendered-frames. Confirm real-time.
- **Phase 6 — Demo polish (days 8+):** FastAPI dashboard (video + per-lane bars + signal widget + adaptive-vs-fixed race chart). RL comparison if time.

---

## 9. Open Questions

1. Exact YOLO-on-render-vs-real mAP delta for counting — measure yourself in Phase 1.
2. BMD-45 dataset license — verify before redistribution.
3. YOLO11s vs YOLO26s on *our* footage — benchmark both.
4. Will the sim's assets + tuned traffic visually approximate Kathmandu motorcycle-swarm density enough for the fine-tune to transfer? Side-by-side sanity check.
5. Tracker ranking is extrapolation — A/B on real footage.
6. Real-signal-display hardware path (GPIO/serial/mock) under-specified — scope early only if a physical signal is required.
7. AGPL boundary for closed deployment — irrelevant for demo, blocking if productized (budget Enterprise license or swap to RT-DETRv2/RF-DETR).
8. **Don't quote to judges:** max-pressure "77–87%," PressLight %, Google Green Light scale, YOLO26 exact mAP — all UNVERIFIED. Cite *mechanisms and theorems* (Varaiya, BMD-45 33.6→83.8), not unverified percentages.
