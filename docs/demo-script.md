# R.E.L.A.Y. — demo runbook (7 beats, ~4 minutes)

*One rule: never claim a number this sheet doesn't back. Every beat below is live, not a video.*

## Pre-demo checklist (do this BEFORE the audience arrives)
- [ ] `make dev` — wait for "live server →" line + junction visible at http://127.0.0.1:8000/?live=1
- [ ] Open a second tab: http://127.0.0.1:8000/compare.html (leave it warming — its charts accumulate)
- [ ] Check http://127.0.0.1:8000/real.html plays with boxes (needs `sim/footage/real.mp4` on this machine — not in the repo)
- [ ] Dismiss the "What am I looking at?" card once so it doesn't pop mid-pitch
- [ ] Laptop plugged in, notifications off, other apps closed (YOLO + Three.js want the GPU)
- [ ] Close stray R.E.L.A.Y. tabs — extra live tabs share the detector and slow everyone's boxes
- [ ] If anything hangs: `make dev` again — it kills and restarts everything. Zero cloud dependency; wifi can die and the demo doesn't.

## The 7 beats

**1. Problem (15s, no screen)**
"Kathmandu junctions run on fixed timers. A lane holds green for 200 seconds for two motorcycles while a packed lane sits on red. Lalitpur launched Nepal's first adaptive lights in Dec 2024 — they count vehicles. Nobody manages who's been *waiting* — not vehicles, not ambulances, not people at the zebra. R.E.L.A.Y. does."

**2. The dumb baseline (30s)** — live tab, click the green banner → **fixed timer mode**
"This is today's Kathmandu: timer cycles blindly, watch the queue counter climb, greens served to empty lanes."

**3. Flip it on (30s)** — click banner → **R.E.L.A.Y. ON**
"Same traffic, same junction. Green follows the queue — never a green on an empty lane. Watch the queue drain and the headline: measured live from this scene, not a canned number." (Typical: 10–35% fewer queued.)

**4. This is a camera, not magic (30s)** — point at detection boxes
"A single YOLO model reads this feed — the same model reads real CCTV. Boxes are live detections, PCU-weighted: a bus counts 2.5, a motorcycle 0.3 — Kathmandu's mix, not the West's. Per-approach counts drive a weighted max-pressure controller with hard safety rules: min/max green, yellow + all-red clearance, no lane starves past a bound."

**4b. Real CCTV, same pipeline (30s)** — open real.html
"Same server, same controller, real junction — Ho Chi Minh City rush hour. Every box is a live detection, the counts split the two approaches, and the signal chips are the decision the controller computes from those counts right now. This page runs stock YOLO because that junction was never in our training data — each deployment site gets its own fine-tune, which is exactly the Kathmandu plan."

**5. Ambulance (30s)** — click 🚑
"Visual detection, no transponder needed — preemption with clearance, held even if detection flickers for a frame, and capped so one ambulance can never freeze the junction. Lalitpur's system has no emergency priority."

**6. Pedestrians (30s)** — point at a walk-man pole + someone waiting
"People are demand too. Waiting pedestrians add pressure; nobody waits past 45 seconds for a walk window; and a walk is never cut while someone is on the zebra. No deployed system in Nepal manages pedestrian waiting time. Ours does — it's asserted by a runnable safety check, not a promise."

**6b. One street, three lights (optional, 30s)** — open network.html
"A junction doesn't live alone. Here's one arterial with three signals: each runs its own controller and subtracts its neighbour's downstream queue, so an upstream light stops feeding a jam it can see building ahead. Hit the ambulance and watch a green wave roll through all three. This is the path from one smart junction to a smart corridor."

**7. Proof + price (45s)** — switch to compare.html tab
"Split screen, fixed vs adaptive. Offline, on *identical* arrivals, the controlled benchmark measures **36% less waiting on average** (10–73% across seeds). Local anchor: published Kathmandu junction studies (Shrestha and Marsani 2014; Nepali et al. 2024) measure saturation flows and delays well off standard capacity guidance, which is exactly the headroom junction-specific timing exploits. And the hardware story: this runs on one camera and an edge box — **~$250–400 per junction** against $20–80k for SCATS-class systems. Kathmandu's lights failed for a decade because nobody maintains them — ours is cheap enough to replace, simple enough to own locally, and falls back to a fixed cycle if the camera dies."

## Numbers card (only these, exactly these)
| Claim | Number | What it is |
|---|---|---|
| Controlled benchmark | **~36% avg less waiting** (10–73% by seed) | microsim, identical arrivals, both controllers |
| Per-vehicle waits | typical **halved** (p50 6s vs 12s) · p95 21s vs 43s · worst 38s vs 58s | same benchmark — the fairness tail |
| Live demo | 10–35% fewer queued | measured on-screen from the scene you watch |
| Local evidence | delays and saturation flows deviate from standard guidance | Kathmandu junction studies: Shrestha and Marsani (2014); Nepali et al. (2024) |
| Detector (synthetic held-out) | mAP@50 ≈ 0.88, precision 0.96 | synthetic domain |
| Detector (real footage) |  one model reads unseen junctions live (real.html), no per-junction setup | accuracy keeps improving as the model trains on more local footage |
| Ped wait bound | ≤ 45 s (live config) | controller invariant, self-checked |
| Cost | ~$250–400/junction | camera + edge compute vs $20–80k SCATS/SCOOT |

Never quote 59% — retired number (benchmark bug, fixed).

## Q&A ammo
- **"Lalitpur already did this?"** — They time by vehicle density. No ambulance priority, no pedestrian management, no starvation bound, ~Rs 90M program. We add fairness + priority + safety invariants at commodity cost.
- **"Why did KTM's lights all die?"** — Organizational: no agency owns maintenance. Our answer is cost (replaceable), local ownership, and fixed-time fallback — the system degrades, never bricks the junction.
- **"What if the camera/model fails?"** — Stale feed → controller gets empty counts → aging + fallback to fixed cycle. Per-frame exceptions are isolated; one bad frame never crashes the loop.
- **"Real deployment path?"** — Shadow mode first (we recommend, police approve), then one pilot junction with manual override retained. Same controller already drove 4 real CCTV feeds (Hanoi 4-cam proof).
- **"Why trust the sim?"** — The sim is the *demo surface*; the benchmark runs on identical arrival streams through both controllers, and the local anchor is published Kathmandu junction measurements (Shrestha and Marsani 2014; Nepali et al. 2024).
- **"Hasn't someone on GitHub done this?"** — Hundreds of YOLO-counts-cars demos; zero close the camera→signal loop with safety invariants, starvation bounds, or pedestrian wait — we surveyed ~140 repos (docs/research/2026-07-09-github-oss-landscape.md). Max-pressure is proven theory (Varaiya 2013); pedestrian-aware max-pressure was published 2024 *without code*. We're the first to run either on a live camera.
- **"Cost — really?"** — Anchor ladder: legacy ATCS ~$65k/junction; Miovision publishes $11.4k + $998/yr; Bengaluru's BATCS took -33% travel time at Hudson Circle with mixed traffic. We're camera + edge box, ~$250–400.
- **"It's just a simulation — this won't work on a real device."** — Four receipts, in order: (1) the cyan pipeline line on screen — `YOLO 27ms · N boxes → per-arm counts → phase` — every number is that frame's truth, and every box carries a persistent track id; (2) real.html — real Ho Chi Minh City CCTV through the SAME server and controller, boxes and signal decisions live; (3) `make webcam` — point the laptop camera at a phone playing any junction footage; (4) the 3D exists because you cannot A/B a real junction — it's the only honest way to run identical traffic through both controllers.
- **"Why do all heads on an arm show the same colour?"** — Approach-based phases, same as every deployed ATCS (including Lalitpur's). The controller takes any conflict-free phase set as config — a protected left arrow is one more `Junction` phase entry plus a lens, not an architecture change. Cameras can't read turn INTENT from a queue, so per-movement demand needs stop-line turn zones — a pilot-phase item, deliberately not faked.
