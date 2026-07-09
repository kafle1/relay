# R.E.L.A.Y. Competitive & Technical Landscape Report
### Adaptive Traffic Signal Control — Global Systems, Mixed-Traffic Detection, Coordination, Nepal's Own History, and What It Takes to Beat Them

*Research conducted via parallel web search across academic papers, government/DOT sources, vendor documentation, and Nepali news outlets (English + Nepali). Vendor-reported figures are explicitly flagged as such — treat them as marketing claims, not independent measurement.*

---

## 1. How Other Countries Do Adaptive Traffic Control

### SCATS (Sydney Coordinated Adaptive Traffic System)
- Developed by NSW Dept of Main Roads; piloted on 8 CBD intersections in 1963, commercialized 1975. [Wikipedia](https://en.wikipedia.org/wiki/SCATS) · [SCATS official](https://www.scats.nsw.gov.au/organisation/about-us)
- Scale: 60,000+ intersections across ~200 cities in 30+ countries (figures vary slightly by source/date). [SCATS](https://www.scats.nsw.gov.au/home) · [Transport NSW brochure (PDF)](https://www.transport.nsw.gov.au/system/files/media/documents/2022/SCATS-Core-brochure-Final-web-spreads_0.pdf)
- **Detection**: Inductive loops at/near the stop line primarily; microwave/radar as loop-failure backup; video cameras in newer deployments. [FHWA Traffic Detector Handbook](https://www.fhwa.dot.gov/publications/research/operations/its/06139/appendp.cfm)
- **Adaptation logic**: Every cycle, adjusts cycle length (20–240s), split, and offset using "Degree of Saturation" and link-queue measurements — a **heuristic feedback controller**, not an explicit optimizer. [Wikipedia](https://en.wikipedia.org/wiki/SCATS)
- **Cost**: US cross-vendor ATCS evaluation puts adaptive systems generally at $20,000–$70,000/intersection (median ~$45,000); SCATS annual maintenance ~$17,500/intersection (US context, not Australia-specific). [US DOT ITS cost database](https://www.itskrs.its.dot.gov/its/benecost.nsf/ID/5a53f0d1919aa5ee8525798300819b6e)
- **Results (independent US corridor evaluations)**: Oakland County, MI — travel time improved 6.6%–31.8%; Park City, UT — ~2 sec less stopped delay. [DOT ITS eval database](https://www.itskrs.its.dot.gov/its/benecost.nsf/ID/c1a22dd1c3ba1ed285257cd60062c3bb)

### SCOOT (UK)
- Built by TRL for UK DoT, 1979. [Wikipedia](https://en.wikipedia.org/wiki/Split_Cycle_Offset_Optimisation_Technique)
- Scale: TRL claims 125+ systems/7,000+ intersections over 30 years; vendor Yunex separately claims "250+ cities" — these two figures are inconsistent, treat cumulative claims skeptically. [TRL](https://trlsoftware.com/software/intelligent-signal-control/scoot/) · [Yunex](https://us.yunextraffic.com/portfolio/adaptive-technologies/scoot/)
- **Detection**: Inductive loops placed *upstream* of the stop line (not at it), continuously building a "Cyclic Flow Profile." [Wikipedia](https://en.wikipedia.org/wiki/Split_Cycle_Offset_Optimisation_Technique)
- **Adaptation logic**: Explicit incremental optimization each cycle (small stepwise split/cycle/offset changes to minimize modeled delay) — contrast with SCATS's discrete heuristic.
- **Cost**: Up to $60,000/intersection (US figure, highest of common ATCS platforms); ~CAD $31,700/intersection in a Toronto study. [US DOT ITS](https://www.itskrs.its.dot.gov/its/benecost.nsf/ID/c1a22dd1c3ba1ed285257cd60062c3bb)
- **Results**: ~15% overall improvement vs. fixed-time cited broadly; specific studies show 25% delay reduction, 11% travel-time reduction. [Wikipedia (sourced)](https://en.wikipedia.org/wiki/Split_Cycle_Offset_Optimisation_Technique)

### InSync (Rhythm Engineering, US)
- **Detection**: Pure IP video cameras per approach — no loops at all.
- **Adaptation logic**: "Fully adaptive" — no fixed cycle/split/offset structure; a finite-state machine recomputes live "green tunnel" progression.
- **Cost**: Falls in the general $20k–$80k/intersection US range; USDOT average for ASCT ~$30k/intersection. [NYSERDA report (PDF)](https://www.nyserda.ny.gov/-/media/Project/Nyserda/Files/Publications/Research/Transportation/2016-12-Decision-Tool-Adaptive-Traffic-Control-Systems.pdf)
- **Results — VENDOR-REPORTED, treat skeptically**: Pinellas County, FL — 37% fewer stops, 24% less delay, 12% less travel time. Generic vendor cross-deployment claims run as high as 57% delay reduction, 23% crash reduction. [Rhythm Engineering](https://rhythmtraffic.com/considering-adaptive-signal-control-technology-for-your-city-heres-how-such-an-investment-pays-off/)
- Independent-ish evaluations: Greeley, CO (Atkins) and a 9-intersection NY State DOT deployment. [Greeley study (PDF)](https://rhythmtraffic.com/downloads/resources/InSync/Independent_Studies/2012_Greeley_CO_Atkins.pdf) · [NTL report (PDF)](https://rosap.ntl.bts.gov/view/dot/24966/dot_24966_DS1.pdf)

### Surtrac (Pittsburgh — CMU / Rapid Flow Technologies)
- Origin: CMU Robotics Institute, 9→12-intersection East Liberty pilot from 2012; spun out 2015. [CMU/Mobility21](https://mobility21.cmu.edu/this-ai-traffic-system-in-pittsburgh-has-reduced-travel-time-by-25/)
- **Detection**: Mixed — video cameras, radar, and induction loops. [CMU RI paper (PDF)](https://www.ri.cmu.edu/pub_files/2013/1/13-0315.pdf)
- **Adaptation logic**: Fully **decentralized** — each intersection solves a real-time scheduling problem (~every 1 second) from its own detected approaches, then passes projected outflow downstream for neighbor coordination. **No central computer** — the sharpest architectural contrast to SCATS's centralized model. [CMU RI paper (PDF)](https://www.ri.cmu.edu/pub_files/2013/1/13-0315.pdf)
- **Cost**: ~$20,000/intersection quoted historically (2017); other adaptive-system estimates run $30k–$60k; a full ground-up "smart intersection" build ~$100k. [Emerging Tech Brew](https://www.emergingtechbrew.com/stories/2022/05/20/how-a-smart-city-platform-created-for-pittsburgh-became-a-nationwide-business)
- **Results (CMU/Rapid Flow-reported, widely cited but not independently peer-reviewed)**: 25% travel-time reduction, 40% wait-time reduction, 30% fewer stops, 20% emissions reduction on the East Liberty corridor. [Smart Cities Dive](https://www.smartcitiesdive.com/news/this-ai-traffic-system-in-pittsburgh-has-reduced-travel-time-by-25/447494/)
- Scale: grew from 12 to 50 operational intersections, 150 more planned via FHWA grant (as of ~2022).

### China's City Brain (Alibaba Cloud, Hangzhou)
- Origin: 2016, first piloted in Hangzhou's Xiaoshan District. [Wikipedia](https://en.wikipedia.org/wiki/City_Brain)
- **How it works**: City-wide AI platform ingesting video surveillance, sensors, GPS, and public records; adjusts signals city-wide in real time, plus incident detection and emergency-vehicle preemption. [Alibaba Cloud](https://www.alibabacloud.com/solutions/intelligence-brain/city)
- **Results — mostly VENDOR/GOVERNMENT-reported**: Xiaoshan pilot avg travel speed +15%, avg travel time −3 min; citywide claim of 15.3% avg travel-time reduction (provenance unclear); Hangzhou's national congestion ranking reportedly fell from 5th-worst to 57th; ambulance response time cut ~50%; incident-detection accuracy >92% downtown. [Wikipedia](https://en.wikipedia.org/wiki/City_Brain)
- Scale/status: ~22 Chinese cities + Kuala Lumpur reported by Alibaba as of 2019; "City Brain 3.0" with DeepSeek-R1 integration launched March 2025. [ehangzhou.gov.cn](https://www.ehangzhou.gov.cn/2025-04/01/c_293162.htm)
- **No public per-city or per-intersection cost figure exists anywhere.**

### India's ATCS Deployments
- **CoSiCoSt** (C-DAC, Thiruvananthapuram): field-tested 2004–06, commercial since 2007 (Bhubaneswar, Hubli-Dharwad BRTS, others). [C-DAC](https://www.cdac.in/index.aspx?id=product_details&productId=CoSiCoStEnV(CompositeSignalControlStrategyEnhancedVersion))
- **Bengaluru B-ATCS** (CoSiCoSt-powered, launched March 2024): scaled from 41 junctions (Oct 2024) to 165–169 junctions by mid-2025, targeting 400–500+. Detection via cameras/sensors, not loops. Claimed 20–33% travel-time reduction — **police/government-reported, not independently audited**. [Bengaluru Traffic Police](https://btp.karnataka.gov.in/214/adaptive-traffic-control-system-(atcs)/en) · [Deccan Herald](https://www.deccanherald.com/india/karnataka/bengaluru/smart-signals-ai-takes-over-41-junctionsin-bengaluru-3187050)
- **Delhi**: Runs SCOOT (not a distinct "Delhi ATCS"); no independent 2022–2025 audit of operational health found.
- **Degradation evidence (Pune, concrete numbers)**: 125-junction ATMS cost ₹102–133 crore install + ₹52 crore maintenance; major post-launch (2022) glitches; extra ₹30 crore paid in 2024 for fixes; still degrades in monsoon rain. [Punekar News](https://www.punekarnews.in/punes-rs-102-crore-traffic-management-system-criticized-for-worsening-congestion/) · [Pune Pulse](https://www.mypunepulse.com/pune-police-raise-concerns-over-adaptive-traffic-management-systems-effectiveness/)

---

## 2. Lane Separation & Detection in Mixed/Non-Lane-Discipline Traffic

### How Western systems do it (established practice)
- **Inductive loops**: one loop per lane, ~6ft×6ft, centered with ≥2ft clearance from neighboring lanes to avoid crosstalk. [Diamond Traffic loop guide (PDF)](https://diamondtraffic.com/sites/default/files/Inductive%20Loop%20Guide%202020%20May.pdf) · [FHWA Traffic Detector Handbook](https://www.fhwa.dot.gov/publications/research/operations/its/06108/04.cfm)
- **Camera vendors** (Iteris Vantage, GRIDSMART, Miovision) define up to 24 virtual detection zones per approach so turn pockets get independent detection — the rationale is that protected-turn phases need isolated demand data. [Iteris](https://www.iteris.com/oursolutions/traffic-detection/vantage-next) · [GRIDSMART](https://gridsmart.com/products/the-bell-camera/)
- All of the above assumes **discrete, lane-disciplined traffic** — a structural mismatch with Kathmandu.

### Academic approaches for non-lane-discipline / heterogeneous traffic
- **Area-occupancy back-pressure control** (IEEE): queue-based back-pressure "is appropriate for homogeneous traffic with strict lane discipline" but breaks for non-lane-based heterogeneous traffic; proposes **area occupancy** (approach-width based) instead of per-lane queues — the closest published analogue to R.E.L.A.Y.'s approach-level + max-pressure design. [IEEE Xplore](https://ieeexplore.ieee.org/document/8979186/)
- **Max Pressure + RL on real Indian intersection video** (Ludhiana, Punjab, 2024): Max Pressure phase-selection with PPO-tuned duration, using classified vehicle counts from surveillance video, in SUMO. [ScienceDirect](https://www.sciencedirect.com/science/article/abs/pii/S095741742401282X)
- **Cellular-automata "seepage" model, Delhi (IIT)**: models motorbikes filtering through gaps; finds Indian/US HCM delay formulas *overestimate* delay for this traffic type. [SAGE](https://journals.sagepub.com/doi/10.1177/03611981231211317)
- **Base Saturation Flow Rate, Banda Aceh, Indonesia**: measures traffic **per approach using effective width**, not per lane: S₀ = 622·We (PCU/hr); recalibrated PCU values cut prediction error from 21% to 4–10%. [PMC](https://pmc.ncbi.nlm.nih.gov/articles/PMC11226031/)
- **Dhaka "NHT-1071" dataset**: purpose-built for non-lane-based heterogeneous traffic, deliberately avoids lane detection; YOLO on a Raspberry Pi 4B over RTSP at a 5-road Dhaka intersection. [arXiv:2510.16622](https://arxiv.org/pdf/2510.16622)
- **Hanoi motorcycle counting**: >90% counting accuracy at motorcycle-dominated intersections. [Springer](https://link.springer.com/article/10.1007/s13177-024-00442-z)

### PCU (Passenger Car Unit) standards — hard numbers
| Standard | Car | Motorcycle | Auto-rickshaw | Bus/Truck |
|---|---|---|---|---|
| IRC:106 (India, commonly cited) | 1.0 | 0.5 | 1.2 | 2.2 |
| Older IRC convention | 1.0 | 0.5 | — | 3.0 |
| **Banda Aceh field recalibration** | 1.0 | **0.24** | 0.78 | — |

The **field-measured motorcycle PCE (0.24) is roughly half the textbook IRC value (0.5)** — official tables likely overstate motorbike road-space impact in motorcycle-dominant cities like Kathmandu. Nepal's own road standards (Nepal Road Standard 2070/2013) adopt PCU values from Indian standards. [Banda Aceh PCE study](https://pmc.ncbi.nlm.nih.gov/articles/PMC8412275/)

### Deployed camera-based pilots in mixed-traffic cities
- **Bangkok, Project Green Light** (Google + BMA): 561 intersections, per-direction camera counting → AI timing; reported 10–41% travel-time reduction, 30% fewer unnecessary stops. [Bangkok Post](https://www.bangkokpost.com/thailand/general/3100497/green-light-for-ai-traffic-signals)
- **Hanoi**: 1,837 AI cameras across 195 intersections (live Dec 2025), primarily violation detection + signal adjustment. [VietnamPlus](https://en.vietnamplus.vn/hanoi-officially-launches-smart-traffic-control-centre-post334261.vnp)
- **India govt pilots**: Nagpur AI-adaptive signals (10 junctions, 28–48% travel-time reduction claimed), Kolkata AI queue-based adjustment. [indiaai.gov.in](https://indiaai.gov.in/article/ai-in-indian-traffic-management-transforming-urban-mobility-challenges)

**Bottom line for R.E.L.A.Y.**: approach-level (not lane-level) detection + PCU weighting + max-pressure control is the direction the academic literature on heterogeneous traffic is already heading (Delhi, Dhaka, Banda Aceh, Ludhiana all converge on it). No literature gives it a standardized name — a genuine gap R.E.L.A.Y. could occupy.

---

## 3. Junction Connectivity & Corridor Coordination

### Green wave / offset basics
- Offset = travel time between adjacent signals at a target progression speed; coordination is practical when signals are **≤ ~1.6 km apart**; green-wave effect can persist up to ~3.2 km. [Wikipedia](https://en.wikipedia.org/wiki/Green_wave) · [IIT Bombay notes](https://www.civil.iitb.ac.in/tvm/nptel/575_CoordSignalA/web/web.html)
- Foundational method: Little's maximal-bandwidth offset optimization, *Operations Research*, 1966. [INFORMS](https://pubsonline.informs.org/doi/10.1287/opre.12.6.896)

### SCATS architecture
- Two-tier hierarchy: **Central Manager → Regional Computers (up to 64) → local controllers (up to 250 sites/region, ~10-intersection subsystems)**. [Aldridge](https://www.aldridgetrafficcontrollers.com.au/scats/adaptive-traffic-management/scats-package-options)
- Comms evolved from dedicated phone lines to IP modems and 3G/UMTS wireless. [Traffic Engineer's Blog](https://trafficengineers.wordpress.com/2010/07/22/scats-comms-going-ip-based/)
- **Degrades gracefully**: comms loss doesn't stop signal operation — local controller falls back to last-known plan. Key design lesson for Kathmandu.

### SCOOT architecture
- Fully centralized: the central computer does all optimization. Comms load deliberately tiny — once-per-second polling, 1–5 byte messages, 300-baud channel. Systems >400 signals need a dedicated server. [FHWA](https://www.fhwa.dot.gov/publications/research/operations/its/06108/03.cfm)

### Physical comms media — cost/reliability tradeoffs
- **Fiber**: highest bandwidth, EMI-immune, but requires trenching. [Lantronix](https://www.lantronix.com/resources/case-studies/fiber-connectivity-advanced-traffic-management-case-study/)
- **Cellular 4G/LTE**: no civil works, ~10 Mbps downlink, <50 ms latency — sufficient for adaptive control but has recurring subscription costs. [PUSR](https://www.pusr.com/blog/Application-of-4G-LTE-Modem-in-Traffic-Signal-Control)
- **Dedicated wireless radio (900 MHz / 5.8 GHz)**: lowest total cost of ownership, <5 ms latency — the budget option. [Western Systems](https://www.westernsystems-inc.com/bridge-the-data-and-communication-gap-with-intuicoms-wireless-traffic-solutions/)
- Some portable deployments run triple-redundant 900MHz radio + WiFi + cellular with no fiber. [Alameda CTC (PDF)](https://www.alamedactc.org/wp-content/uploads/2021/02/Appendix-E_Traffic_Signal_Technology_Implementation_Guidance_20200824.pdf)

### Max-pressure control (R.E.L.A.Y.'s controller family)
- Origin: Varaiya, *Transportation Research Part C*, 2013. [ScienceDirect](https://www.sciencedirect.com/science/article/abs/pii/S0968090X13001782)
- Each intersection independently selects the phase maximizing local "pressure" = weighted upstream-minus-downstream queue difference — **no offline timing plan, no cycle length required**.
- **Decentralized by design**: the base algorithm requires **no communication with neighbors at all**. [Berkeley Connected Corridors (PDF)](https://connected-corridors.berkeley.edu/sites/default/files/Variants%20of%20Max%20Pressure%20Control%20for%20Signalized%20Intersections.pdf)
- Theoretical guarantee: **throughput-optimal** — provably stabilizes queues whenever the network is stabilizable by any policy. [Kouvelas/Lioris/Fayazi/Varaiya, TRB 2014](https://journals.sagepub.com/doi/10.3141/2421-15)
- Closest real-world pilot: University of Minnesota/Hennepin County — hardware-in-the-loop with a real Q-Free MaxTime controller (not yet live traffic). Simulated Twin Cities savings estimate ~$966M/year. [CTS Minnesota](https://www.cts.umn.edu/news-pubs/news/2025/march/signals)
- **No confirmed live-traffic max-pressure deployment was found anywhere in the world** — R.E.L.A.Y. would be near the frontier: differentiator AND risk to flag honestly.

---

## 4. Nepal's Own Traffic Light History (Direct Competitive Context)

### Lalitpur Metropolitan City "Intelligent Traffic Light" project
- **Feb 2022 announcement**: intelligent traffic control at 6 crossroads + 1,100 smart street lights, budgeted **Rs 90 million**, because the federal Department of Roads had delayed its own plan. [OnlineKhabar, Feb 3 2022](https://english.onlinekhabar.com/lalitpur-intelligent-traffic-lights.html)
- **Actual launch: Dec 16, 2024** at Pulchowk by Mayor Chiribabu Maharjan — Nepal's first AI-enabled adaptive traffic light system. [OnlineKhabar, Dec 17 2024](https://english.onlinekhabar.com/nepals-first-intelligent-traffic-lights-operational-from-kupondole-to-jawalakhel.html) · [New Business Age](https://newbusinessage.com/news/41688/what-is-the-intelligence-traffic-light-system-installed-in-lalitpur-how-does-it-work/)
- **Builder**: a JV — named "Namuna Power Grade JV" in one source, "Sanmal Power Grade JV" in another (sources conflict).
- **Tech**: vehicle-detector sensors count/track vehicles and speed → central server; license-plate reading; traffic/accident data stored up to 5 years; four modes — fixed-timing, manual/police override, synchronization, and ATCS; signals adjust every 30–40 seconds by live vehicle density.
- **Initial scope**: 5 units Kupondole–Jawalakhel; feasibility flagged 117 citywide locations; 5-year bundled maintenance warranty.
- **Mid-2025**: 8 sites total, reportedly running 24/7 with **no VIP override** — police chief: "even ministers stop at red lights now." [nepalauto.com](https://nepalauto.com/ai-traffic-light-in-lalitpur/)
- **Critical assessment**: transport-engineer analysis flags mixed traffic, jaywalking, lane indiscipline, and lack of local maintenance expertise as unresolved. [LinkedIn](https://www.linkedin.com/pulse/smart-traffic-lights-lalitpur-opportunities-rajkumar-adhikari-rhqec)

### Kathmandu's broader history — a documented, repeated pattern of failure
- **1966**: first signals via JICA (Japanese grant), 20 intersections. [Nepal Police](https://traffic.nepalpolice.gov.np/about-us/histories/) · [RisingNepal](https://risingnepaldaily.com/news/59784)
- **1995–96 → 2002–03**: "Modern Traffic Light System" at Thapathali, expanded to 10 junctions.
- **2016 ADB plan (largely failed)**: 26–35 junctions, **Rs 500 million**, self-adaptive with CCTV + central control room; ITS component reportedly dropped before loan closure (secondary source, unconfirmed). [Kathmandu Post, May 2016](https://kathmandupost.com/miscellaneous/2016/05/24/kathmandu-to-adopt-intelligent-traffic-system)
- **2018 solar "smart" lights — fast, well-documented failure**: 6 solar lights (7 KTM + 3 Lalitpur locations), March 2018, by Yarsha Technology, 5-year maintenance promised. **Two failed within 24 hours**; by article date **none inside the Ring Road worked properly**. DSP Marasini: *"We tried to fix and upgrade the old traffic lights. But we shortly realised it was not a good idea."* Rs 600m needed for 35 sites, no qualifying bidder. [Himalayan Times, Apr 20 2018](https://thehimalayantimes.com/kathmandu/newly-installed-traffic-lights-go-kaput/) · [Setopati, Mar 27 2018](https://en.setopati.com/social/119627)
- **2020**: repairs brought the Valley to 28 working junctions; **over 90% of existing lights had been non-functional for more than a decade** before that. [Kathmandu Post, Aug 20 2020](https://kathmandupost.com/valley/2020/08/20/traffic-lights-installed-and-repaired-at-major-junctions-of-kathmandu-valley)
- **2025 snapshot**: of 69 total lights Valley-wide (115 ever installed), only **36–40 functional**. [RisingNepal, Apr 2025](https://risingnepaldaily.com/news/59784) · [Annapurna Express, May 2025](https://theannapurnaexpress.com/story/54571/)
- **Named causes of failure across sources**:
  1. **No single agency formally responsible** — lights installed by metros never formally handed over to Dept of Roads, which disclaims maintenance.
  2. Software/hardware malfunction, outdated/unsynced tech.
  3. Lack of skilled local technicians.
  4. Procurement Act 2007 delays slow any repair.
  5. **Protest/vandalism damage** (Mar 2025 demonstrations, Tinkune/Jadibuti).
  6. Drivers ignore lights regardless — 4–5 officers per broken-light junction stay necessary.
  7. Police: *"almost impossible to run traffic lights fully automatic 24/7 as every intersection has distinct traffic pressure."*
  - **No source cites power cuts/load-shedding** as a Kathmandu signal-failure cause — notable negative finding.
- ~1,700–1,800 traffic police still deployed Valley-wide daily — the real baseline cost R.E.L.A.Y. competes against. [Kathmandu Post, Jan 31 2025](https://kathmandupost.com/valley/2025/01/31/kathmandu-lalitpur-metropolises-take-charge-of-traffic-management)

### Other current Nepal initiatives
- **CCTV/ANPR enforcement expanding fast**: ~950 CCTV cameras Valley-wide, 297 linked live to a 24/7 control room, 6 ANPR cameras; 40–50% of Valley traffic penalties now issued via CCTV. [Kathmandu Post, Jul 8 2026](https://kathmandupost.com/valley/2026/07/08/kathmandu-valley-steps-up-cctv-based-traffic-enforcement) — **Nepal is already investing in camera infrastructure**, which R.E.L.A.Y. can piggyback on.

---

## 5. What It Takes to Beat Them

### Why camera+AI beats loop-based detection economically
- Loop installation requires pavement sawcutting, road closure; UK loop install ~£6,500/approach; loops degrade pavement (+25% lifecycle cost) and must be re-cut after every resurfacing. [C&T Technology](https://ct-technologyinfo.com/blog/2020/11/09/traffic-detection-systems/) · [FHWA](https://www.fhwa.dot.gov/publications/research/operations/its/06139/chapt6.cfm)
- Wireless/non-intrusive retrofit at 3 junctions: $60,000 total vs. ~$150,000 for loops — 37% savings (vendor-sourced, upper bound).
- Cameras avoid trenching, survive resurfacing, add pedestrian/incident detection loops can't. [ITS International](https://www.itsinternational.com/its4/its8/feature/cost-effective-alternatives-traditional-loops)
- **Kathmandu specifically**: no evidence anyone ever successfully installed loop detection there — cameras on existing poles sidestep road-cutting entirely.

### Documented failure modes to design around
- **Dhaka — the closest cautionary tale**: World Bank-funded automated signals at 70 intersections, **Tk 119 crore over 15 years (2001–2022)**; government evaluation called it a "complete waste" because "traffic volume and speed were not considered"; city fully reverted to hand-signal police. [The Daily Star](https://www.thedailystar.net/news/bangladesh/news/automated-traffic-signal-money-thrown-down-the-drain-2939261)
- **Traffic-police manual-override culture**: "an increasing trend across the developing world." Dar es Salaam: fixed-time signals produced 11.75–18.63 hrs/day delay vs. 10.29–10.41 hrs/day under manual police control — manual beats *fixed-time*, but adaptive control was not in that comparison. [ScienceDirect](https://www.sciencedirect.com/science/article/abs/pii/S1369847814001521)
- **Power/grid failure**: South Africa ~300 load-shedding events in 2022 knocked out signals; private billboards now backfeed 113+ intersections. [invidis](https://invidis.com/news/2026/06/south-africa-battery-powered-dooh-keeps-traffic-lights-running/) — NOT documented for Kathmandu; cheap insurance, not the lead pitch.
- **Post-installation maintenance funding gap**: India's Smart Cities Mission covered only 5 years O&M; >half of cities couldn't self-fund afterward; 400 projects worth ₹22,814 crore missed deadlines. [The Wire](https://m.thewire.in/article/urban/why-smart-cities-project-never-took-off-in-india-and-was-quietly-shelved)
- **Kathmandu's root cause is organizational, not technical**: no agency owns maintenance, Procurement Act delays fixes, no local technician skill. **R.E.L.A.Y.'s pitch should lead with "cheap enough that the city can self-repair with off-the-shelf parts," not just "smarter algorithm."**

### Minimal real-world hardware stack (2025–2026 pricing)
- **Edge compute**: Jetson Nano is discontinued (EOL Oct 2023). Replacement: **Jetson Orin Nano Super Dev Kit, $249** (8GB, 67 TOPS), runs YOLO11 at 30–60+ FPS. [NVIDIA](https://www.nvidia.com/en-us/autonomous-machines/embedded-systems/jetson-orin/nano-super-developer-kit/) · [Ultralytics benchmark](https://www.ultralytics.com/blog/ultralytics-yolo11-on-nvidia-jetson-orin-orin-nano-super-fast-and-efficient)
- **Cheaper path**: Raspberry Pi 5 + Hailo-8L AI Kit (13 TOPS) runs YOLOv8s at ~25–35 FPS @640 — sub-$150 compute. [Seeed benchmark](https://wiki.seeedstudio.com/benchmark_on_rpi5_and_cm4_running_yolov8s_with_rpi_ai_kit/)
- **Cameras**: budget 1080p IP cameras $30–$80; traffic-grade low-light units from ~$650. [IPVM](https://ipvm.com/discussions/what-are-the-best-low-cost-low-light-ip-cameras)
- **Controller interface**: NEMA TS2 cabinets use a Bus Interface Unit → load switches → mandatory flash-transfer relay for fail-safe. For a Nepal retrofit, GPIO → relay board → signal heads is proven in hobbyist builds — **but a real deployment needs galvanic isolation and a fail-safe flash relay that DIY relay boards don't provide**. Genuine safety gap to close before real-world deployment. [NEMA TS2-2021 (PDF)](https://www.nema.org/docs/default-source/standards-document-library/nema-ts-2-2021-contents-and-scopeb71294ae-eb9a-45e2-a501-e5c0672dbd6b.pdf)
- **Power resilience**: DIY solar+battery Pi rigs ~$150 in parts, 9–10 days through cloud. Commercial solar traffic lights often fail because gel batteries degrade in 1–2 years — matches Kathmandu's 2018 failure pattern: **battery degradation, not power availability, is the bigger local risk**. [OPTraffic](https://optraffic.com/blog/why-solar-powered-traffic-lights-fail-premature/)

### Synthesis — what actually beats the incumbents for Kathmandu
1. **Cost structure**: camera+edge-AI ($250–400/intersection compute+camera) vs. $20k–$80k Western ATCS or ₹100+ crore Indian rollouts — 1–2 orders of magnitude cheaper.
2. **No loop-cutting** sidesteps road-works friction never even attempted in Kathmandu.
3. **Approach-level detection + PCU-aware max-pressure** matches where the heterogeneous-traffic literature is heading, instead of forcing a Western per-lane model onto Kathmandu.
4. **Decentralized max-pressure** needs no central server room and degrades gracefully per-intersection — directly answers Kathmandu's "no agency owns the system" failure mode (each intersection self-contained, independently fixable, small blast radius).
5. **The hardest unsolved problem is organizational/maintenance, not algorithmic** — lead the pitch with off-the-shelf locally-serviceable parts, no vendor lock-in, self-fundable repairs without multi-year tenders.
6. **No live-traffic max-pressure deployment exists anywhere globally** — frontier differentiator AND honest risk.

---

## Master List: Claims Flagged as Unverified or Low-Confidence
- SCATS's Australia-specific cost/intersection (only US cross-vendor figures found).
- China City Brain's cost at any scale; independent verification of its headline percentages.
- CoSiCoSt's per-intersection cost (only a generic "~₹20 lakh/junction" estimate found).
- IRC:106's exact numeric PCU table from the primary document (paywalled).
- Nepal/Kathmandu-specific PCU values (Shrestha 2013, NepJOL — PDF fetch failed).
- Whether Bangkok/Hanoi/Dhaka camera pilots detect per-lane or per-approach.
- Any confirmed non-simulated live-traffic max-pressure deployment anywhere.
- Lalitpur's Dec 2024 system total cost; correct legal name of the contracting JV.
- ADB Kathmandu ITS component cancellation (secondary source only).
- Whether load-shedding ever disabled Kathmandu signals (no source either way).
- Current (2026) status of Lalitpur's 8-site expansion (no update past mid-2025).
- Exact wattage of a standard LED signal head / full intersection load (for solar/UPS sizing).
- A documented case tying vendor lock-in specifically to a named traffic-signal project failure.
