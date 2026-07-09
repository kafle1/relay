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
from perception import CONF, point_in_poly                                     # noqa: E402  shared with the offline pipeline

SIM = os.path.abspath(os.path.join(HERE, "..", "sim"))
DS = os.path.abspath(os.path.join(HERE, "..", "dataset"))
IMG, LBL = os.path.join(DS, "images"), os.path.join(DS, "labels")
# prefer the mixed-domain fine-tune (real CCTV + synthetic render), then sim-only, then stock
_CANDIDATES = [os.path.abspath(os.path.join(HERE, "..", "dataset", "runs", n, "weights", "best.pt"))
               for n in ("ft_mixed", "ft")]
MODEL_PATH = next((p for p in _CANDIDATES if os.path.exists(p)), "yolo11s.pt")

if MODEL_PATH == "yolo11s.pt":
    print("WARNING: no fine-tuned weights found — falling back to stock COCO, which detects almost")
    print("nothing on the synthetic feed. Expected at dataset/runs/ft_mixed/weights/best.pt (ships")
    print("with the repo); to rebuild: make capture N=300, then make train.")
print(f"loading detector: {MODEL_PATH}")
model = YOLO(MODEL_PATH)
# ultralytics returns names as a dict, but hub/exported checkpoints can give a list
NAMES = model.names if isinstance(model.names, dict) else dict(enumerate(model.names))
DEVICE = "mps" if torch.backends.mps.is_available() else "cpu"
# one dedicated inference thread: predict() off the event loop (a sync call blocked ALL HTTP/WS for
# ~100-300ms per frame — with several tabs open the server starved to death), single worker because
# concurrent predict() on one model isn't thread-safe. Extra clients queue here, loop stays live.
INFER = ThreadPoolExecutor(max_workers=1)


def iou(a, b):
    """Intersection-over-union of two {x,y,w,h} boxes (normalized coords)."""
    ix = max(0.0, min(a["x"] + a["w"], b["x"] + b["w"]) - max(a["x"], b["x"]))
    iy = max(0.0, min(a["y"] + a["h"], b["y"] + b["h"]) - max(a["y"], b["y"]))
    inter = ix * iy
    union = a["w"] * a["h"] + b["w"] * b["h"] - inter
    return inter / union if union > 0 else 0.0


class LiveCompare:
    """Headless adaptive-vs-fixed on identical arrivals → the live chart."""
    def __init__(self):
        self.J = four_way()
        self.ad = Controller(self.J, Timings(min_green=4, max_green=25, yellow=3, all_red=1.5, max_wait=45, w_wait=0.4))
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
    ctrl = Controller(four_way(), Timings(min_green=4, max_green=25, yellow=3, all_red=1.5, max_wait=45, w_wait=0.4,
                                          ped_max_wait=45))
    zones = None
    last = time.monotonic()
    tracks, next_id = {}, 1     # per-connection identity: id -> {box, miss, still}
    prev_sirens = set()         # YOLO ambulance arms from the PREVIOUS frame (2-frame confirmation)
    infer_avg = 50.0            # rolling inference-time estimate drives the adaptive input size
    imgsz = 960                 # current input size — switched with hysteresis, never thrashed
    try:
        while True:
            msg = await sock.receive_json()
            if msg.get("type") == "zones":
                zs = msg.get("zones") or {}
                try:
                    ctrl = Controller(junction_from_dirs(zs.keys()),
                                      Timings(min_green=4, max_green=25, yellow=3, all_red=1.5, max_wait=45, w_wait=0.4,
                                              ped_max_wait=45))
                    zones = zs
                except ValueError as e:          # garbage zones must not kill the socket — keep the old ones
                    print("bad zones ignored:", e)
                continue
            if msg.get("type") != "frame":
                continue
            # decode + detect together on the worker thread — neither may block the event loop.
            # A corrupt frame skips quietly; nothing crashes the socket.
            # low floor + per-class gates (CONF), like perception.py: a flat 0.35 drops the small
            # far-away two-wheelers that ARE the Kathmandu story (edge case A2).
            # agnostic NMS: a distant queue otherwise grows stacked car+truck+moto boxes per vehicle.
            # 960px sharpens the small far-queue objects; on a thermally-throttled machine that can
            # cost 300-400ms/frame and the boxes visibly trail moving traffic — drop to 704px.
            # Hysteresis (up at >250ms, back only under 120ms): each size change re-warms the model,
            # so flip-flopping around one threshold costs more than either size.
            if imgsz == 960 and infer_avg > 250:
                imgsz = 704
            elif imgsz == 704 and infer_avg < 120:
                imgsz = 960
            _t0 = time.monotonic()

            def _detect(payload=msg["image"], size=imgsz):
                raw = base64.b64decode(payload.split(",", 1)[1])
                img = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
                if img is None:
                    return None, None
                return img, model.predict(img, conf=0.2, iou=0.5, imgsz=size, agnostic_nms=True,
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

            boxes = []
            counts = {d: {} for d in (zones or {"N": 1, "S": 1, "E": 1, "W": 1})}
            emergencies = set(msg.get("emergencies") or [])   # transponder-style announce is immediate
            yolo_sirens = set()                               # visual detections need 2 consecutive frames
            for b in (res.boxes or []):
                # PCU classes only — with the stock 80-class model a pedestrian on the zebra must
                # not be counted (let alone as a car) and inflate that approach's demand.
                cls = NAMES.get(int(b.cls), "")
                if cls not in PCU:
                    continue
                if float(b.conf) < CONF.get(cls, CONF["default"]):
                    continue
                x1, y1, x2, y2 = (float(v) for v in b.xyxy[0])
                cx, cy = (x1 + x2) / 2 / w, (y1 + y2) / 2 / h
                appr = None
                if zones:
                    for d, poly in zones.items():
                        if poly and point_in_poly(cx, cy, poly):
                            appr = d; break
                boxes.append({"x": x1 / w, "y": y1 / h, "w": (x2 - x1) / w, "h": (y2 - y1) / h,
                              "cls": cls, "conf": round(float(b.conf), 2), "appr": appr})
                if appr:
                    counts[appr][cls] = counts[appr].get(cls, 0) + 1
                    if cls == "ambulance":
                        yolo_sirens.add(appr)

            # stable per-vehicle identity: greedy IoU association with the previous frame.
            # Matched boxes are smoothed (no flicker) and keep their id across frames — the
            # overlay reads as tracking because it is. Misses expire after ~0.6s (4 frames).
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
                    tracks[next_id] = {"box": {k: b[k] for k in ("x", "y", "w", "h")}, "miss": 0, "still": 0}
                    b["id"] = next_id
                    claimed.add(next_id)
                    next_id += 1
                else:
                    t = tracks[best]
                    # raw YOLO jitter on a static object runs a few pixels frame-to-frame — the
                    # stillness reset needs real motion (~9px at 720w), not detector noise.
                    moved = abs(t["box"]["x"] - b["x"]) + abs(t["box"]["y"] - b["y"])
                    t["still"] = 0 if moved > 0.012 else t.get("still", 0) + 1
                    for k in ("x", "y", "w", "h"):
                        t["box"][k] = t["box"][k] * 0.55 + b[k] * 0.45
                        b[k] = round(t["box"][k], 4)
                    t["miss"] = 0
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
                     if not (b.get("appr") is None and tracks.get(b.get("id"), {}).get("still", 0) > 24)]

            # a YOLO siren counts only when seen on two consecutive frames — one white-van
            # false positive must never flash an emergency banner nobody triggered.
            emergencies |= (yolo_sirens & prev_sirens)
            prev_sirens = yolo_sirens

            now = time.monotonic()
            dt = min(now - last, 0.5); last = now
            # pedestrian demand rides along with the frame (push-button style); controller sanitizes
            state = ctrl.tick(counts, emergencies, dt, peds=msg.get("peds"))
            n_counts = {d: sum(counts[d].values()) for d in counts}
            await sock.send_json({
                "signals": state["signals"], "phase": state["phase"], "stage": state["stage"],
                "counts": n_counts, "boxes": boxes, "emergencies": list(emergencies),
                "telemetry": {"infer_ms": infer_ms, "imgsz": imgsz, "model": os.path.basename(MODEL_PATH)},
                "metrics": True,   # client-side numbers are measured on the client; /metrics has the benchmark
            })
    except Exception as e:
        print("ws closed:", type(e).__name__)


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
