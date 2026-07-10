# OSS + product landscape — who else built this, what's genuinely ours (Jul 9, 2026)

*Survey: ~140 GitHub repos (20+ query angles), academic control repos, commercial products.
Purpose: pitch positioning + prior-art honesty. MVP is frozen — nothing here is a build item
unless explicitly pulled into the plan later.*

## Verdict on our four claimed differentiators — all four unclaimed in open source

| Claim | Prior art found? |
|---|---|
| Pedestrian waiting time as controller input | **No working code anywhere.** Appears only as roadmap bullets, a literal `def detect_pedestrians(): pass` stub, or a YOLO class nobody feeds into control. Academic validation exists: Xu et al. 2023 (rule-based MP + ped force-serve past a wait threshold — structurally our mechanism) and PQ-MP (arXiv:2406.19305, 2024, no code). Cite them as validation, not competition. |
| Max-pressure from real camera counts | **No repo combines them.** Max-pressure exists in sim-land only (docwza/sumolights, MPLight/PressLight — all SUMO/CityFlow ground truth). Camera repos are all "YOLO count → fixed formula green time". The algorithm is Varaiya (2013) prior art — we cite it, we don't claim to have invented pressure control. |
| Closed-loop synthetic sim ("infinite CCTV") for training + demo | **Nothing comparable found.** Most novel of the four. |
| Safety-invariant self-checks (clearance, min/max green, starvation bounds, preempt caps) | **Zero repos implement any.** Research repos treat safety as the simulator's problem. Also unmeasured in academia: standard benchmarks (RESCO/LibSignal) report only *mean* delay — nobody reports max/percentile waits, which is exactly what our starvation bounds control. |

**Nepal-specific:** the two substantial Nepali repos (ANPR checkpoint system for DoTM; a Kathmandu
congestion-monitoring dashboard with ByteTrack + Jetson guide) both stop at detection/monitoring.
**No Nepal project on GitHub closes the loop from camera to signal actuation.**

## The crowd we must not be confused with

Hundreds of near-identical student clones: YOLO count → weighted green-time formula,
maybe ambulance class, no fairness, no safety bounds, no closed loop, mostly dead repos, mostly
unlicensed. Best-of-breed reference: mihir-m-gandhi/Adaptive-Traffic-Signal-Timer (202★, IEEE
ICRAIE 2020) — per-class weighted counts (a PCU table like ours) + Pygame sim, still images only.
One demo differentiator sentence: *"GitHub has a hundred YOLO-counts-cars demos; none of them can
say what happens when the camera dies, when a lane starves, or when a person waits at the zebra.
Ours can, and proves it with a runnable self-check."*

## Commercial anchors (for cost/results slides — all public, citable)

- **Miovision** (absorbed Surtrac team): publishes **~$11,400/intersection hardware + $998/yr** fee;
  claims -25% travel time, -40% wait. US ATCS average ~**$65k/intersection** (ITS-KRS).
- **NoTraffic** ($165M raised): per-intersection annual SaaS; Tucson -46% peak delay.
- **Yunex FUSION** (replacing SCOOT for TfL): "replans every second" framing — we legitimately match
  (our loop runs at frame rate) and can say so.
- **Google Green Light**: no hardware, fleet-data timing advice, 30% fewer stops, live in Kolkata/
  Bengaluru/Hyderabad. Trust-ladder lesson: *advisory first, control after trust* — our pilot path
  answer ("shadow mode first") in one phrase.
- **Bengaluru BATCS** (165 junctions, 2024–25): -20% travel time, **-33% at Hudson Circle** — the
  best mixed-traffic result to cite next to SIDRA-KTM 33–49%.
- **CoSiCoSt (C-DAC, India)**: markets "engineered for poor lane discipline and vehicle
  heterogeneity" — the *"designed for chaos, not despite it"* framing is directly ours to use.

## Pitch lines adopted (zero code)

1. "Designed for chaos, not despite it" — mixed traffic as a feature, not a caveat.
2. Cost anchor ladder: legacy ATCS ~$65k → Miovision ~$11.4k+$998/yr → **us ~$250–400** camera+edge.
3. "Replans every frame, not every 5 minutes."
4. Pilot-path answer: shadow/advisory mode first (Green Light precedent), control after trust.
5. Prior-art honesty: "max-pressure is proven theory (Varaiya 2013); pedestrian-aware max-pressure
   was published in 2024 without code; we're the first to run either on a live camera."

## Deferred — later phase only (documented so we don't forget; NOT build items now)

- **Downstream-pressure term**: textbook max-pressure subtracts downstream queue (spillback
  prevention): weight = upstream − turn-ratio-weighted downstream (Varaiya 2013; sumo-rl
  `get_pressure()`). Our controller is upstream-only — fine for a single junction (no downstream
  camera exists), becomes real at multi-junction pilots.
- Percentile-wait (p90/p99/max) evaluation report — the literature only reports means; our bounded
  starvation is quantifiable ammo.
- FishEye8K benchmark (8k fisheye images, Jetson-class eval) — closer to deployment shape than
  AI City main tracks.
- Camera-based pedestrian detection (stock YOLO person class) to replace push-button-style input.
- Emissions/CO2 conversion of saved vehicle-seconds; auto-generated before/after report.
- License note: most research repos (colight/presslight/MPLight/LibSignal) ship **no license** —
  ideas are fine, code reuse is not.
