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

SIM = os.path.abspath(os.path.join(HERE, "..", "sim"))
DS = os.path.abspath(os.path.join(HERE, "..", "dataset"))
IMG, LBL = os.path.join(DS, "images"), os.path.join(DS, "labels")
# prefer the mixed-domain fine-tune (real CCTV + synthetic render), then sim-only, then stock
_CANDIDATES = [os.path.abspath(os.path.join(HERE, "..", "dataset", "runs", n, "weights", "best.pt"))
               for n in ("ft_mixed", "ft")]
MODEL_PATH = next((p for p in _CANDIDATES if os.path.exists(p)), "yolo11s.pt")

print(f"loading detector: {MODEL_PATH}")
model = YOLO(MODEL_PATH)
# ultralytics returns names as a dict, but hub/exported checkpoints can give a list
NAMES = model.names if isinstance(model.names, dict) else dict(enumerate(model.names))
DEVICE = "mps" if torch.backends.mps.is_available() else "cpu"


def point_in_poly(x, y, poly):
    inside = False
    n = len(poly)
    j = n - 1
    for i in range(n):
        xi, yi = poly[i]; xj, yj = poly[j]
        if ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / ((yj - yi) or 1e-9) + xi):
            inside = not inside
        j = i
    return inside


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


@app.get("/metrics")
async def metrics():          # async → runs on the event loop, never races stepper() mid-update
    wf = cmp.wf
    return {"adaptive": round(cmp.wa, 1), "fixed": round(wf, 1),
            "reduction": round((wf - cmp.wa) / wf * 100, 1) if wf > 5 else 0}


@app.websocket("/ws")
async def ws(sock: WebSocket):
    await sock.accept()
    ctrl = Controller(four_way(), Timings(min_green=4, max_green=25, yellow=3, all_red=1.5, max_wait=45, w_wait=0.4))
    zones = None
    last = time.monotonic()
    try:
        while True:
            msg = await sock.receive_json()
            if msg.get("type") == "zones":
                zs = msg.get("zones") or {}
                try:
                    ctrl = Controller(junction_from_dirs(zs.keys()),
                                      Timings(min_green=4, max_green=25, yellow=3, all_red=1.5, max_wait=45, w_wait=0.4))
                    zones = zs
                except ValueError as e:          # garbage zones must not kill the socket — keep the old ones
                    print("bad zones ignored:", e)
                continue
            if msg.get("type") != "frame":
                continue
            # decode frame (skip a corrupt one — edge case: never crash the loop)
            try:
                raw = base64.b64decode(msg["image"].split(",", 1)[1])
                img = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
                if img is None:
                    continue
            except Exception:
                continue
            h, w = img.shape[:2]
            _t0 = time.monotonic()
            res = model.predict(img, conf=0.35, device=DEVICE, verbose=False)[0]
            infer_ms = round((time.monotonic() - _t0) * 1000)

            boxes = []
            counts = {d: {} for d in (zones or {"N": 1, "S": 1, "E": 1, "W": 1})}
            emergencies = set(msg.get("emergencies") or [])   # transponder-style announce (+ YOLO ambulance below)
            for b in (res.boxes or []):
                # PCU classes only — with the stock 80-class model a pedestrian on the zebra must
                # not be counted (let alone as a car) and inflate that approach's demand.
                cls = NAMES.get(int(b.cls), "")
                if cls not in PCU:
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
                        emergencies.add(appr)

            now = time.monotonic()
            dt = min(now - last, 0.5); last = now
            state = ctrl.tick(counts, emergencies, dt)
            wa, wf = round(cmp.wa, 1), round(cmp.wf, 1)
            n_counts = {d: sum(counts[d].values()) for d in counts}
            await sock.send_json({
                "signals": state["signals"], "phase": state["phase"], "stage": state["stage"],
                "counts": n_counts, "boxes": boxes, "emergencies": list(emergencies),
                "telemetry": {"infer_ms": infer_ms, "model": os.path.basename(MODEL_PATH)},
                "metrics": {"adaptive": wa, "fixed": wf,
                            "reduction": round((wf - wa) / wf * 100, 1) if wf > 5 else 0},
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
