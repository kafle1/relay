---
name: verify
description: Build/launch/drive recipe for verifying R.E.L.A.Y. sim changes in a real browser
---

# Verify R.E.L.A.Y. changes

## Launch

```bash
.venv/bin/python tools/live_server.py        # serves sim/ + WS + /metrics on 127.0.0.1:8000
```

- Port 8000 is **hardcoded** (`tools/live_server.py`, `uvicorn.run(..., port=8000)`). If 8000 is
  taken (stale server wedges silently — binds but never answers), run a second instance on
  another port without editing the file:
  ```bash
  cd tools && ../.venv/bin/python -c "import live_server, uvicorn; uvicorn.run(live_server.app, host='127.0.0.1', port=8001, log_level='warning')"
  ```
- Detector load takes ~10–15s before HTTP answers; poll `curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:PORT/compare.html` until 200.

## Surfaces

- `/?live=1` — solo adaptive view; `/?embed=1&lockfixed=1` / `/?embed=1&live=1` — the two compare panels.
- `/compare.html` — A/B split screen. Controls in header broadcast postMessage cmds into both iframes.
- `/network.html` — 3-junction arterial.

## Drive / observe

- Embeds are same-origin: from compare.html, `fL.contentWindow.RELAY` / `fR.contentWindow.RELAY`
  exposes live state (`phase, counts, cars, queued, waitPerSec, density, topo, lanes`) — refreshed
  every animation frame. Best hook for asserting behavior without pixel-reading.
- Embed pages take ~5–8s to load models before `RELAY` exists; wait for `#loading` to disappear.
- postMessage commands land async — read `RELAY` a beat after clicking, not synchronously.
- Gotcha: `sim/index.html` pins `main.js?v=N`; a cached tab can hold old main.js. Hard-reload or bump N.
- Gotcha: at high traffic (density ×2) Chrome can recycle an iframe renderer mid-session — a panel
  silently reloads. compare.html resyncs the dial off the embed's stats ping; expect a ~1s blip.
