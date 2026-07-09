# R.E.L.A.Y. — demo runbook (7 beats, ~4 minutes)

*One rule: never claim a number this sheet doesn't back. Every beat below is live, not a video.*

## Pre-demo checklist (do this BEFORE judges arrive)
- [ ] `make dev` — wait for "live server →" line + junction visible at http://127.0.0.1:8000/?live=1
- [ ] Open a second tab: http://127.0.0.1:8000/compare.html (leave it warming — its charts accumulate)
- [ ] Dismiss the "What am I looking at?" card once so it doesn't pop mid-pitch
- [ ] Laptop plugged in, notifications off, other apps closed (YOLO + Three.js want the GPU)
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

**5. Ambulance (30s)** — click 🚑
"Visual detection, no transponder needed — preemption with clearance, held even if detection flickers for a frame, and capped so one ambulance can never freeze the junction. Lalitpur's system has no emergency priority."

**6. Pedestrians (30s)** — point at a walk-man pole + someone waiting
"People are demand too. Waiting pedestrians add pressure; nobody waits past 45 seconds for a walk window; and a walk is never cut while someone is on the zebra. No deployed system in Nepal manages pedestrian waiting time. Ours does — it's asserted by a runnable safety check, not a promise."

**7. Proof + price (45s)** — switch to compare.html tab
"Split screen, fixed vs adaptive. Offline, on *identical* arrivals, the controlled benchmark measures **36% less waiting on average** (10–73% across seeds). Real-world anchor: a 2023 SIDRA study of two Kathmandu junctions found 33–49% from timing alone. And the hardware story: this runs on one camera and an edge box — **~$250–400 per junction** against $20–80k for SCATS-class systems. Kathmandu's lights failed for a decade because nobody maintains them — ours is cheap enough to replace, simple enough to own locally, and falls back to a fixed cycle if the camera dies."

## Numbers card (only these, exactly these)
| Claim | Number | What it is |
|---|---|---|
| Controlled benchmark | **~36% avg less waiting** (10–73% by seed) | microsim, identical arrivals, both controllers |
| Live demo | 10–35% fewer queued | measured on-screen from the scene you watch |
| Real-world anchor | 33–49% | 2023 SIDRA re-timing study, 2 KTM junctions (Neupane & Jha) |
| Detector (synthetic held-out) | mAP@50 ≈ 0.88, precision 0.96 | synthetic domain |
| Detector (real footage) | cars strong; dense two-wheeler swarms undercount | honest limit; regional fine-tune is the fix |
| Ped wait bound | ≤ 45 s (live config) | controller invariant, self-checked |
| Cost | ~$250–400/junction | camera + edge compute vs $20–80k SCATS/SCOOT |

Never quote 59% — retired number (benchmark bug, fixed).

## Q&A ammo
- **"Lalitpur already did this?"** — They time by vehicle density. No ambulance priority, no pedestrian management, no starvation bound, ~Rs 90M program. We add fairness + priority + safety invariants at commodity cost.
- **"Why did KTM's lights all die?"** — Organizational: no agency owns maintenance. Our answer is cost (replaceable), local ownership, and fixed-time fallback — the system degrades, never bricks the junction.
- **"What if the camera/model fails?"** — Stale feed → controller gets empty counts → aging + fallback to fixed cycle. Per-frame exceptions are isolated; one bad frame never crashes the loop.
- **"Real deployment path?"** — Shadow mode first (we recommend, police approve), then one pilot junction with manual override retained. Same controller already drove 4 real CCTV feeds (Hanoi 4-cam proof).
- **"Why trust the sim?"** — The sim is the *demo surface*; the benchmark runs on identical arrival streams through both controllers, and the real anchor is the SIDRA study.
