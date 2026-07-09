# Balkhu Corridor — real-geometry spec for the 3D scene

Geometry pulled live from OpenStreetMap (Overpass API, 2026-07-09), origin = Balkhu Chowk police
post (OSM node 766982477), lat 27.68476 lon 85.29807. Local meters: x = east, z = north.

## Junctions

| # | Name | Local (x, z) | Arms |
|---|---|---|---|
| J1 | **Balkhu Chowk** (Ring Rd × Dakshinkali Rd) | 0, 0 | Ring Rd → Kalanki (290°, 2+2), Ring Rd → bridge/Kuleshwor (95°, 2+2), Dakshinkali Rd → Pharping (205°, 1+1 est.) |
| J2 | **Kuleshwor / Vayodha junction** (north bridgehead) | 16, 16 | Ring Rd ← bridge (225°), Ring Rd → Ekantakuna (100°), University Marg / Kuleshwor Rd to Vayodha Hospitals (2°, 1+1 est.) — only ~30 m from J1: in reality one junction COMPLEX spanning the bridge |
| J3 | **Ekantakuna Chowk** (Ring Rd × Tikabhairab Rd) | 809, −1774 | ~1.9 km away — the next major junction toward Sanepa side |

Plus **Bagmati Marg** (river-corridor road) leaving J1/J2 north along the west bank toward
Kuleshwor/Teku, and the **Bagmati River** crossing under **Balkhu Bridge** (2+2 lanes,
`bridge=yes`) right at the chowk.

## Facts that shape the demo

- **Zero `highway=traffic_signals` nodes** exist in OSM across the whole corridor — every junction
  here is police/manually managed today. Traffic police have publicly *proposed* lights at Balkhu.
  → R.E.L.A.Y. simulating this corridor is a proposal for junctions that have NO working signals.
- Congestion: Balkhu is a documented bottleneck (buses stuck 1h+ at peak; 47 veh/km measured
  mid-block Balkhu–Sanepa in a 2019 road-safety survey; "under-capacity bridges and intersections"
  named as a cause). It is the truck/bus entry from the Prithvi Highway/Dakshinkali side.
- Lane truth: the bridge and chowk carriageways are tagged **2 lanes each way (4 total)** in OSM —
  the bridge is the pinch point, even though the 2018 Chinese-grant widening advertises "8 lanes"
  for the Kalanki–Koteshwor section (which arc that covers is not cleanly resolved; model the
  bridge as 2+2).
- Ring Road centerline + river polylines (local meters) live in the research transcript; the scene
  compresses J3's 1.9 km to a visual ~400 m so the corridor fits one camera.

## Unverified / flagged

Dakshinkali arm heading is estimated (no named OSM way found); a second `bridge=yes` cluster 180 m
ESE may be a double-mapping; per-arm lane counts on the minor roads are estimates; "8-lane" press
figure vs OSM `lanes=2` conflict resolved in favor of live OSM tags.

Sources: OSM/Overpass + Nominatim (2026-07-09), Kathmandu Post (Jun 2026 Ring Road status),
Wikipedia Ring Road (Kathmandu), Ratopati (Kalanki–Basundhara grant; Balkhu–Gwarko e-bus pilot),
RisingNepal (manual signals), Kathmandu Ring Road safety-inspection report (2019 density survey).
