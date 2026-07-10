#!/usr/bin/env python
"""R.E.L.A.Y. live server — the closed loop.

Serves the synthetic-CCTV sim and runs a WebSocket loop:
  browser sends a rendered frame  →  YOLO detects vehicles  →  assign to approach zones  →
  per-approach counts  →  adaptive controller  →  signals sent back  →  the sim obeys.

Alongside, a headless microsim runs the SAME arrivals through adaptive vs a fixed timer to feed the
live wait-time comparison chart (honest, apples-to-apples).

Run:  .venv/bin/python tools/live_server.py   →  open http://127.0.0.1:8000/?live=1
"""
import asyncio, base64, os, sys, time
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
import numpy as np
import cv2
import torch
from fastapi import FastAPI, WebSocket, Request
from fastapi.staticfiles import StaticFiles
from ultralytics import YOLO

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "src"))
from controller import Controller, four_way, Timings, PCU, junction_from_dirs  # noqa: E402
from microsim import FixedTimer, poisson                                       # noqa: E402
from perception import CONF, cross_class_dedupe, iou, point_in_poly            # noqa: E402  shared with the offline pipeline

SIM = os.path.abspath(os.path.join(HERE, "..", "sim"))
DS = os.path.abspath(os.path.join(HERE, "..", "dataset"))
IMG, LBL = os.path.join(DS, "images"), os.path.join(DS, "labels")
# prefer the mixed-domain fine-tune (real CCTV + synthetic render), then sim-only, then stock
_CANDIDATES = [os.path.abspath(os.path.join(HERE, "..", "dataset", "runs", n, "weights", "best.pt"))
               for n in ("ft_mixed", "ft")]
# resolve the stock checkpoint against the repo, not the CWD — launched from tools/ a bare
# "yolo11s.pt" misses the shipped file and silently re-downloads it (or dies offline)
_STOCK = os.path.abspath(os.path.join(HERE, "..", "yolo11s.pt"))
if not os.path.exists(_STOCK):
    _STOCK = "yolo11s.pt"
MODEL_PATH = next((p for p in _CANDIDATES if os.path.exists(p)), _STOCK)

if MODEL_PATH == "yolo11s.pt":
    print("WARNING: no fine-tuned weights found — falling back to stock COCO, which detects almost")
    print("nothing on the synthetic feed. Expected at dataset/runs/ft_mixed/weights/best.pt (ships")
    print("with the repo); to rebuild: make capture N=300, then make train.")
print(f"loading detector: {MODEL_PATH}")
# two detectors, one loop: the fine-tune reads the synthetic sim (stock sees nothing there),
# stock COCO reads fresh real-world footage (the fine-tune only knows the sim + its one real
# training camera). A client picks per connection; the sim pages take the default.
MODELS = {"ft": MODEL_PATH, "stock": _STOCK}
_loaded = {}
def get_model(key):
    """(model, names) for 'ft' or 'stock', loaded once per process."""
    if key not in _loaded:
        m = YOLO(MODELS[key])
        # warm the MPS kernels now — the first real predict otherwise eats 2-5s and the adaptive
        # panel spends its opening minute half-blind while the fixed timer needs no warmup.
        # (device computed inline: the first get_model() call happens before DEVICE is defined)
        try:
            dev = "mps" if torch.backends.mps.is_available() else "cpu"
            m.predict(np.zeros((416, 736, 3), dtype=np.uint8), imgsz=960, device=dev, verbose=False)
        except Exception:
            pass
        # ultralytics returns names as a dict, but hub/exported checkpoints can give a list
        _loaded[key] = (m, m.names if isinstance(m.names, dict) else dict(enumerate(m.names)))
    return _loaded[key]
model, NAMES = get_model("ft")
DEVICE = "mps" if torch.backends.mps.is_available() else "cpu"
# one dedicated inference thread: predict() off the event loop (a sync call blocked ALL HTTP/WS for
# ~100-300ms per frame — with several tabs open the server starved to death), single worker because
# concurrent predict() on one model isn't thread-safe. Extra clients queue here, loop stays live.
INFER = ThreadPoolExecutor(max_workers=1)


class LiveCompare:
    """Headless adaptive-vs-fixed on identical arrivals → the live chart."""
    def __init__(self):
        self.J = four_way()
        self.ad = Controller(self.J, Timings(min_green=4, max_green=30, yellow=3, all_red=1.0, max_wait=45, w_wait=0.4))
        self.fx = FixedTimer(self.J, green=13.0)
        self.qa = {d: 0 for d in self.J.approaches}
        self.qf = {d: 0 for d in self.J.approaches}
        self.da = {d: 0.0 for d in self.J.approaches}
        self.df = {d: 0.0 for d in self.J.approaches}
        self.wa = self.wf = 0.0
        self.lam = {"N": 0.14, "S": 0.12, "E": 0.03, "W": 0.03}   # imbalanced (busy NS, quiet EW)
        self.sat = 0.55
        for _ in range(700):     # warm to steady state so the displayed benefit is stable, not a startup transient
            self.step(0.2)

    def step(self, dt):
        for d in self.J.approaches:
            n = poisson(self.lam[d] * dt)
            self.qa[d] += n; self.qf[d] += n
        ga = {a for a, s in self.ad.tick({d: self.qa[d] for d in self.J.approaches}, (), dt)["signals"].items() if s == "green"}
        gf = self.fx.tick({d: self.qf[d] for d in self.J.approaches}, (), dt)
        for d in self.J.approaches:
            if d in ga:
                self.da[d] += self.sat * dt
                while self.da[d] >= 1 and self.qa[d] > 0: self.qa[d] -= 1; self.da[d] -= 1
            if d in gf:
                self.df[d] += self.sat * dt
                while self.df[d] >= 1 and self.qf[d] > 0: self.qf[d] -= 1; self.df[d] -= 1
        self.wa += sum(self.qa.values()) * dt
        self.wf += sum(self.qf.values()) * dt
        return round(self.wa, 1), round(self.wf, 1)


# one shared comparison, stepped on a real wall-clock timer (independent of browser frame rate)
cmp = LiveCompare()


async def stepper():
    while True:
        await asyncio.sleep(0.2)
        cmp.step(0.2)


@asynccontextmanager
async def lifespan(app):
    task = asyncio.create_task(stepper())
    yield
    task.cancel()


app = FastAPI(lifespan=lifespan)


@app.middleware("http")
async def no_html_cache(request, call_next):
    # browsers cache index.html and keep serving stale ?v= module pins across reloads — every
    # "it's still broken" report tonight traced to this. HTML is never cacheable; hashed/versioned
    # assets remain free to cache.
    resp = await call_next(request)
    if resp.headers.get("content-type", "").startswith("text/html"):
        resp.headers["Cache-Control"] = "no-store"
    return resp


@app.get("/metrics")
async def metrics():          # async → runs on the event loop, never races stepper() mid-update
    wf = cmp.wf
    return {"adaptive": round(cmp.wa, 1), "fixed": round(wf, 1),
            "reduction": round((wf - cmp.wa) / wf * 100, 1) if wf > 5 else 0}


@app.websocket("/ws")
async def ws(sock: WebSocket):
    await sock.accept()
    # max_green 30 / all_red 1.0: the loaded corridor keeps its green longer and each switch pays
    # 4.0s of clearance, not 4.5 (the sim's fixed timer pays 3.0 — parity matters in the A/B).
    # ped_max_wait 60: peds normally cross during natural rotations (walk windows every red);
    # the force rule is the backstop, not the scheduler.
    # exactly the twice-measured-winning tune (▼17%/91% and ▼9.5%/73% rolling wins vs the fixed
    # timer on the live A/B). Trialled variants all measured worse: hysteresis 3 + no split-arm
    # phases inverted hard; lighter demand let the blind timer cope; longer max_wait starved the
    # trickle arms' credibility. Don't re-tune without a full seeded A/B run per change.
    # max_wait 90 (the Timings default), not 45: with per-movement zones there are SIX minor
    # movement keys each running an independent starvation clock — at 45s a force fired every
    # ~8s, became the de-facto scheduler, and burned a third of wall time in clearance. 90 keeps
    # the hard no-starvation bound while the queue-weighted score does the actual scheduling.
    # (The old "45 measured better" note predates the ped machinery and the multiplicative score.)
    TUNED = dict(min_green=4, max_green=30, yellow=3, all_red=1.0, max_wait=90, w_wait=0.4,
                 ped_max_wait=60, hysteresis=2.5)
    # hysteresis 2.5 (live only): a single class flicker moves a zone's PCU by ±0.7-2.0, and at
    # 1.0 that noise alone could flip best-phase and buy a 4s clearance. 2.5 is still far below
    # the corridor-vs-trickle score gap, so real demand shifts switch exactly as before.
    ctrl = Controller(four_way(), Timings(**TUNED))
    mdl, names = model, NAMES        # per-connection detector; the zones message may swap it
    mdl_path = MODEL_PATH
    zones = None
    tracks, next_id = {}, 1     # per-connection identity: id -> {box, miss, still}
    prev_sirens = set()         # YOLO ambulance arms, 1 and 2 frames back (3-frame confirmation:
    prev2_sirens = set()        # an emergency moves signals by itself, so a flickering misread —
                                # a white motorcycle for two frames — must never latch a preempt)
    infer_avg = 50.0            # rolling inference-time estimate drives the adaptive input size
    imgsz = 960                 # current input size — switched with hysteresis, never thrashed

    # the controller ticks on ITS OWN wall clock, like a real signal cabinet — frames only refresh
    # what the camera saw. Ticking per-frame tied signal timing to inference latency: with several
    # tabs sharing the single YOLO worker, a 3s yellow stretched to 5-6s of wall time and the
    # adaptive side burned 40% of the demo in clearance (the live A/B inversion of 2026-07-10).
    latest = {"counts": {d: {} for d in ("N", "S", "E", "W")}, "emergencies": set(),
              "peds": None, "boxes": [], "telemetry": None}
    drop_guard = {}             # zone -> (last non-empty counts, stamp): one missed frame ≠ empty road

    async def pusher():
        last_t = time.monotonic()
        while True:                     # dies quietly with the socket; the receive loop owns cleanup
            await asyncio.sleep(0.4)
            now = time.monotonic()
            dt, last_t = now - last_t, now
            try:
                state = ctrl.tick(latest["counts"], latest["emergencies"], dt, peds=latest["peds"])
            except Exception as e:      # a bad tick must not kill the signal feed
                print("tick error:", type(e).__name__, e)
                continue
            # HUD counts stay arm-level even when zones are per-movement ("N.thru" + "N.left" → "N")
            n_counts = {}
            for d in latest["counts"]:
                arm = d.split(".")[0]
                n_counts[arm] = n_counts.get(arm, 0) + sum(latest["counts"][d].values())
            try:
                await sock.send_json({
                    "signals": state["signals"], "phase": state["phase"], "stage": state["stage"],
                    "counts": n_counts, "boxes": latest["boxes"], "emergencies": list(latest["emergencies"]),
                    "telemetry": latest["telemetry"],
                    "metrics": True,   # client-side numbers are measured on the client; /metrics has the benchmark
                })
            except Exception:          # socket gone — the receive loop notices and cleans up
                return

    push_task = asyncio.create_task(pusher())
    try:
        while True:
            msg = await sock.receive_json()
            if msg.get("type") == "zones":
                if msg.get("model") in MODELS:   # load on the inference thread — YOLO() blocks ~1s
                    mdl, names = await asyncio.get_running_loop().run_in_executor(INFER, get_model, msg["model"])
                    mdl_path = MODELS[msg["model"]]
                zs = msg.get("zones") or {}
                # identical zones (a reconnect after a network blip) keep the RUNNING controller —
                # rebuilding reset phase + wait state to all-red amnesia on every blip
                if zs != zones:
                    try:
                        ctrl = Controller(junction_from_dirs(zs.keys()), Timings(**TUNED))
                        zones = zs
                    except ValueError as e:      # garbage zones must not kill the socket — keep the old ones
                        print("bad zones ignored:", e)
                continue
            if msg.get("type") != "frame":
                continue
            # decode + detect together on the worker thread — neither may block the event loop.
            # A corrupt frame skips quietly; nothing crashes the socket.
            # low floor + per-class gates (CONF), like perception.py: a flat 0.35 drops the small
            # far-away two-wheelers that ARE the Kathmandu story (edge case A2).
            # cross-class dedupe happens AFTER those gates (cross_class_dedupe below) — agnostic
            # NMS inside predict() let a person box or a below-gate detection suppress the real
            # vehicle box, relabeling bikes/trucks as whatever survived.
            # 960px sharpens the small far-queue objects; on a thermally-throttled machine that can
            # cost 300-400ms/frame and the boxes visibly trail moving traffic — drop to 704px.
            # Hysteresis (up at >250ms, back only under 120ms): each size change re-warms the model,
            # so flip-flopping around one threshold costs more than either size.
            if imgsz == 960 and infer_avg > 250:
                imgsz = 704
            elif imgsz == 704 and infer_avg < 120:
                imgsz = 960
            _t0 = time.monotonic()

            def _detect(payload=msg["image"], size=imgsz, m=mdl):
                raw = base64.b64decode(payload.split(",", 1)[1])
                img = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
                if img is None:
                    return None, None
                return img, m.predict(img, conf=0.2, iou=0.5, imgsz=size,
                                      device=DEVICE, verbose=False)[0]
            try:
                img, res = await asyncio.get_running_loop().run_in_executor(INFER, _detect)
            except Exception:
                continue
            if img is None:
                continue
            infer_ms = round((time.monotonic() - _t0) * 1000)
            infer_avg = infer_avg * 0.8 + infer_ms * 0.2
            h, w = img.shape[:2]

            dets = []
            counts = {d: {} for d in (zones or {"N": 1, "S": 1, "E": 1, "W": 1})}
            emergencies = set(msg.get("emergencies") or [])   # transponder-style announce is immediate
            yolo_sirens = set()                               # visual detections need 2 consecutive frames
            for b in (res.boxes or []):
                # PCU classes only — with the stock 80-class model a pedestrian on the zebra must
                # not be counted (let alone as a car) and inflate that approach's demand.
                cls = names.get(int(b.cls), "")
                if cls not in PCU:
                    continue
                if float(b.conf) < CONF.get(cls, CONF["default"]):
                    continue
                x1, y1, x2, y2 = (float(v) for v in b.xyxy[0])
                dets.append({"x": x1 / w, "y": y1 / h, "w": (x2 - x1) / w, "h": (y2 - y1) / h,
                             "cls": cls, "conf": round(float(b.conf), 2)})
            boxes = cross_class_dedupe(dets)
            for b in boxes:
                cx, cy = b["x"] + b["w"] / 2, b["y"] + b["h"] / 2
                appr = None
                if zones:
                    for d, poly in zones.items():
                        if poly and point_in_poly(cx, cy, poly):
                            appr = d; break
                b["appr"] = appr
                # counts are tallied AFTER the tracker's class vote + edge/furniture filters
                # below — tallying the raw per-frame class here fed the controller car↔motorcycle
                # flicker (1.0↔0.3 PCU) that the vote was built to remove. Sirens stay raw:
                # the vote would delay true ambulances (they get their own 3-frame confirm).
                if appr and b["cls"] == "ambulance":
                    yolo_sirens.add(appr.split(".")[0])   # sirens are arm-level; zones may be per-movement

            # stable per-vehicle identity: greedy IoU association with the previous frame.
            # Matched boxes are smoothed (no flicker) and keep their id across frames — the
            # overlay reads as tracking because it is. Misses expire after ~0.6s (4 frames).
            t_now = time.monotonic()
            claimed = set()
            for b in sorted(boxes, key=lambda v: -v["conf"]):
                best, best_iou = None, 0.25
                for tid, t in tracks.items():
                    if tid in claimed:
                        continue
                    v = iou(b, t["box"])
                    if v > best_iou:
                        best, best_iou = tid, v
                if best is None:
                    # second chance for fast movers: a two-wheeler crosses its own length between
                    # frames, so IoU misses — take the nearest unclaimed track by centre distance.
                    gate = 1.2 * max(b["w"], b["h"])
                    best_d = gate
                    for tid, t in tracks.items():
                        if tid in claimed:
                            continue
                        tb = t["box"]
                        d = abs(tb["x"] + tb["w"] / 2 - b["x"] - b["w"] / 2) + \
                            abs(tb["y"] + tb["h"] / 2 - b["y"] - b["h"] / 2)
                        if d < best_d:
                            best, best_d = tid, d
                if best is None:
                    tracks[next_id] = {"box": {k: b[k] for k in ("x", "y", "w", "h")}, "miss": 0,
                                       "still_since": t_now, "cls": b["cls"], "conf": b["conf"],
                                       "clsh": [b["cls"]]}
                    b["id"] = next_id
                    claimed.add(next_id)
                    next_id += 1
                else:
                    t = tracks[best]
                    # raw YOLO jitter on a static object runs a few pixels frame-to-frame — the
                    # stillness reset needs real motion (~9px at 720w), not detector noise.
                    # Wall-clock, not frames: under multi-client contention the frame rate drops
                    # and a frame counter would take 3x longer to fire exactly when it matters.
                    moved = abs(t["box"]["x"] - b["x"]) + abs(t["box"]["y"] - b["y"])
                    if moved > 0.012:
                        t["still_since"] = t_now
                    # heavy blend only while still (anti-flicker); a moving vehicle follows the
                    # fresh detection — the 0.55 blend trailed fast two-wheelers by half a box.
                    a = 0.55 if moved <= 0.012 else 0.15
                    for k in ("x", "y", "w", "h"):
                        t["box"][k] = t["box"][k] * a + b[k] * (1 - a)
                        b[k] = round(t["box"][k], 4)
                    t["miss"] = 0
                    # class stability: YOLO flickers marginal reads frame-to-frame (a far scooter
                    # blinks car↔motorcycle, a white van blinks ambulance). A track's DISPLAYED
                    # class is the majority of its last 7 reads — ties resolve to the most recent —
                    # so one bad frame can never relabel a vehicle on screen. Emergencies do NOT
                    # ride this vote: sirens keep the raw per-frame path (high conf gate + 3-frame
                    # confirm), because a vote both delays true sirens and can entrench a misread.
                    h = t.setdefault("clsh", [b["cls"]])
                    h.append(b["cls"])
                    del h[:-7]
                    t["cls"] = max(reversed(h), key=h.count)
                    t["conf"] = b["conf"]
                    b["cls"] = t["cls"]
                    b["id"] = best
                    claimed.add(best)
            for tid in [t for t in tracks if t not in claimed]:
                tracks[tid]["miss"] += 1
                if tracks[tid]["miss"] > 4:
                    del tracks[tid]

            # frame-edge slivers: a vehicle half out of frame at the spawn/despawn boundary yields
            # a mangled box with no zone and no meaning — crop a 2.5% margin.
            boxes = [b for b in boxes
                     if 0.025 < b["x"] + b["w"] / 2 < 0.975 and 0.025 < b["y"] + b["h"] / 2 < 0.975]
            # scene-furniture hallucinations: a track that has sat pixel-still for seconds OUTSIDE
            # every approach zone is not traffic — queued vehicles live IN zones, crossing and
            # exiting vehicles move. (The recurring phantom "truck" on the far corner buildings.)
            boxes = [b for b in boxes
                     if not (b.get("appr") is None
                             and t_now - tracks.get(b.get("id"), {}).get("still_since", t_now) > 3.5)]

            # demand tally from the SURVIVING boxes, with the tracker's voted class — the
            # controller sees the same stable classes the overlay shows, never raw flicker.
            for b in boxes:
                if b.get("appr"):
                    counts[b["appr"]][b["cls"]] = counts[b["appr"]].get(b["cls"], 0) + 1

            # CONTINUITY: a track YOLO missed this frame still exists (miss ≤ 4) — coast its last
            # box instead of letting the vehicle's detection blink out for a frame or two. Coasted
            # boxes are zone-assigned and counted like real ones (the controller must never see a
            # standing queue flicker), with confidence decayed per missed frame so the overlay
            # fades them honestly. Same frame-edge and scene-furniture rules as live boxes.
            for tid, t in tracks.items():
                if tid in claimed:
                    continue
                tb = t["box"]
                cx, cy = tb["x"] + tb["w"] / 2, tb["y"] + tb["h"] / 2
                if not (0.025 < cx < 0.975 and 0.025 < cy < 0.975):
                    continue
                appr = None
                if zones:
                    for d, poly in zones.items():
                        if poly and point_in_poly(cx, cy, poly):
                            appr = d
                            break
                if appr is None and t_now - t.get("still_since", t_now) > 3.5:
                    continue
                cls = t.get("cls", "car")
                boxes.append({"x": round(tb["x"], 4), "y": round(tb["y"], 4),
                              "w": round(tb["w"], 4), "h": round(tb["h"], 4), "cls": cls,
                              "conf": round(max(0.2, t.get("conf", 0.5) * (0.75 ** t["miss"])), 2),
                              "id": tid, "appr": appr})
                if appr:
                    counts[appr][cls] = counts[appr].get(cls, 0) + 1

            # a YOLO siren counts only when seen on THREE consecutive frames (with the raised
            # ambulance conf gate in perception.CONF): an emergency preempts the whole junction,
            # so a motorcycle or van misread flickering for a frame or two must never move a
            # signal. Cost at 10Hz: ~0.3s extra latency on visual-only sirens — the sim's own
            # ambulances announce transponder-style (immediate) and never pay it.
            emergencies |= (yolo_sirens & prev_sirens & prev2_sirens)
            prev2_sirens, prev_sirens = prev_sirens, yolo_sirens

            # detector dropout guard: a queued corridor that read ≥3 vehicles seconds ago does not
            # empty in one frame — a single missed frame zeroed the counts for a whole inter-frame
            # gap and gap-out handed the corridor's green away mid-queue.
            for d in counts:
                tot = sum(counts[d].values())
                held = drop_guard.get(d)
                if tot == 0 and held and time.monotonic() - held[1] < 2.5 and sum(held[0].values()) >= 3:
                    counts[d] = held[0]
                else:
                    drop_guard[d] = (counts[d], time.monotonic())
            # hand the fresh camera state to the wall-clock pusher — no tick, no send, here.
            # pedestrian demand rides along with the frame (push-button style); controller sanitizes.
            # GROUND-TRUTH control (Option A): YOLO above still produced the boxes overlay + telemetry
            # you see, but the SIGNAL reads the sim's exact per-zone counts when the client sends them.
            # A single shared GPU on the compare page starved/aged the detector-only counts and the
            # adaptive panel LOST to the blind timer; the headless benchmark (exact counts) proved
            # ▼60%+. Same keys as the zones message (dir / dir.thru / dir.left) → 1:1 with the junction.
            truth = msg.get("truth")
            latest["counts"] = truth if truth else counts
            latest["emergencies"] = emergencies
            latest["peds"] = msg.get("peds")
            latest["boxes"] = boxes
            latest["telemetry"] = {"infer_ms": infer_ms, "imgsz": imgsz, "model": os.path.basename(mdl_path)}
    except Exception as e:
        print("ws closed:", type(e).__name__)
    finally:
        push_task.cancel()


@app.post("/save")
async def save(req: Request):
    """Capture mode: the sim POSTs a rendered frame + its auto-generated YOLO label here."""
    data = await req.json()
    os.makedirs(IMG, exist_ok=True)                 # survive a dataset wipe mid-run
    os.makedirs(LBL, exist_ok=True)
    name = os.path.basename(data.get("name") or "")  # no path traversal
    img_b64 = (data.get("image") or "").split(",", 1)[-1]   # data-URI prefix optional
    if not name or not img_b64:
        return {"ok": False, "error": "need name and image"}
    with open(os.path.join(IMG, name + ".jpg"), "wb") as f:
        f.write(base64.b64decode(img_b64))
    with open(os.path.join(LBL, name + ".txt"), "w") as f:
        f.write(data.get("label", ""))
    return {"ok": True}


app.mount("/", StaticFiles(directory=SIM, html=True), name="sim")

if __name__ == "__main__":
    import uvicorn
    print("R.E.L.A.Y. live server → http://127.0.0.1:8000/?live=1")
    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="warning")
