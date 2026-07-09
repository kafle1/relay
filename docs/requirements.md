# R.E.L.A.Y. — Requirements Register (raw, verbatim)

*Every requirement as stated, unedited. Status verified against the codebase — this file is the audit checklist.*

| # | Raw requirement (verbatim) | Where it lives | Status |
|---|---|---|---|
| 1 | "we will do hours of research before we start to code" | docs/research/ (8-axis cited synthesis) | ✅ |
| 2 | "in synthetic 3d also same yolo model must use" | mixed fine-tune reads real CCTV + render; live loop runs it | ✅ |
| 3 | "dont do too much overcomplicate we need mvp, not shitty mvp bro it must get funding … ready to release" | lean single-repo stack; honest-scope README | ✅ |
| 4 | "do whatever to win hackathon" | ON/OFF toggle, split-screen, webcam demo, pitch ammo | ✅ |
| 5 | "priority mapping, abulance school bus vip" | ambulance: visual detect + preempt ✅; school-bus/VIP: same `emergency_boost` mechanism, needs only a class + weight (no free 3D/visual signature — documented, not faked) | ◐ |
| 6 | "lanes are bad in kathmandu consider everything" | approach polygons not lane lines; PCU weights | ✅ |
| 7 | "if there is 2 in other then we send other but … we cant let 2 old ones keep waiting" | aging weight + max-wait force-serve; asserted by self-check | ✅ |
| 8 | "consider every edge cases" (×3) | docs/edge-cases.md (65 cases) + in-code handling | ✅ |
| 9 | "re validate everything that i said" | premise re-validation (baseline, VIP politics, claim honesty) | ✅ |
| 10 | "lane that needs to wait for 200 seconds even if there is 2 bikes and the empty lane is given green" | empty-phase-skip invariant, asserted by self-check | ✅ |
| 11 | "footages must be like top view of junction, or 4 different cameras for 4 different direction all cameras synced" | Mode A/B in spec; sim provides both; AI City noted for real 4-cam | ✅ |
| 12 | "it cant be moving drone frootages" / "only need junction footage" / "none of the footages … good" | fixed-cam bar enforced; Hanoi 1080p60 + Bangkok verified-fixed junction clips | ✅ |
| 13 | "first we make perfect system by generating our own 3d traffic with real 3d treejs made cars" | Three.js sim, 18 GLB vehicles | ✅ |
| 14 | "make it real random like bikes auto vehicle car taxi lilke super real" | weighted random mix (moto 50%, car, taxi, truck, bus, ambulance); auto-rickshaw: no free license-clean GLB exists — documented | ◐ |
| 15 | "different speed different pace" | per-vehicle speed = base × type × random jitter | ✅ |
| 16 | "3d … real like gta types … then we use our system in that footage, it will detect real vehicle" | render → same YOLO detects (fine-tuned, mAP@50 .90) | ✅ |
| 17 | "make 3d for every edge cases … 3 point junction 2 point junction 4 point junction, 2 lane … 6 lane" | `?topo=4|T|2` × `?lanes=1..3` per direction (2/4/6-lane roads); scenario buttons (ambulance, surge) | ✅ |
| 18 | "infinitely auto randomly generating realtime cc camera footage like live realtime" | endless organic per-approach spawner | ✅ |
| 19 | "i should be able to drag to move view" / "vehicles are moving in opposite direction" | OrbitControls; yaw fixed | ✅ |
| 20 | "keep commiting in my gituhb one by one like real human working" / "update gitub names also" | 19+ incremental commits, repo `kafle1/relay` | ✅ |
| 21 | "our project name is R.E.L.A.Y." | rebranded everywhere | ✅ |
| 22 | "use fable as main model … for subagent … sonnet and opus" | user `/model fable`; subagents sonnet | ✅ |
| 23 | "you are over focused on simulating, first make the whole core algorithm and detection system" | src/perception.py + src/controller.py + src/pipeline.py (source-agnostic) | ✅ |
| 24 | "when i run make dev everything must start" | Makefile `dev` target, tested | ✅ |
| 25 | "i should be able to see the different when our system is on and off (normal fixed time traffic light)" | live ON/OFF toggle + per-mode stats; screenshots in docs/img | ✅ |
| 26 | "simulating looks fake … i dont have any hardwayre" | webcam demo (`make webcam`), on-screen inference telemetry, judge-interference controls | ✅ |
| 27 | "optimize it all my system is hanging" / "everything must run smoothly in 60fps live realtime" | this pass: no shadow maps, merged geometry, blob shadows, async downscaled streaming, pixelRatio cap | ✅ |
| 28 | "proper sidewalks proper zebracrossing … regenerate from scratch" | scene rebuilt: curbed sidewalks, real zebra bands, stop lines behind crossings, box-junction hatching | ✅ |
| 29 | "remove all the unnecessary code … no ambiguty, conflicting and duplicate code … no legacy" | cleanup: real_demo.py + capture_server.py + live_zones.py removed, /save + junction helper consolidated, deprecated FastAPI hooks migrated | ✅ |
| 30 | "no runtime and build issues at the end" | final verification battery (all checks + browser modes + make dev) | ✅ |

◐ = mechanism built and honest about the remaining piece (documented above), not silently skipped.
