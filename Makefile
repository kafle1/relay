# R.E.L.A.Y. — one-command dev
VENV := .venv
PY := $(VENV)/bin/python
PORT := 8000

.PHONY: dev setup check pipeline compare capture train stop webcam

## make dev — start everything, open the demo, stay in the foreground with live logs (Ctrl-C = stop)
dev: setup stop
	@echo "→ starting R.E.L.A.Y. live server on :$(PORT)  (loads the detector, ~10-15s)"
	@nohup $(PY) tools/live_server.py > /tmp/relay_server.log 2>&1 &
	@ok=0; for i in $$(seq 1 60); do curl -s -o /dev/null http://127.0.0.1:$(PORT)/ && { ok=1; break; }; sleep 1; done; \
	if [ $$ok -ne 1 ]; then echo ""; echo "✗ server did not come up in 60s — log:"; tail -20 /tmp/relay_server.log; \
	echo "   (try: make stop && make dev)"; exit 1; fi
	@echo ""
	@echo "  R.E.L.A.Y. is up:"
	@echo "    live closed loop   →  http://127.0.0.1:$(PORT)/?live=1"
	@echo "    T-junction live    →  http://127.0.0.1:$(PORT)/?live=1&topo=T"
	@echo "    fixed-vs-adaptive  →  http://127.0.0.1:$(PORT)/compare.html"
	@echo "    3-junction network →  http://127.0.0.1:$(PORT)/network.html"
	@echo "    free-roam sim      →  http://127.0.0.1:$(PORT)/"
	@echo ""
	@open "http://127.0.0.1:$(PORT)/?live=1" 2>/dev/null || true
	@echo "── live server log (Ctrl-C stops everything) ──"
	@trap '$(MAKE) stop >/dev/null 2>&1; echo; echo "stopped."; exit 0' INT; tail -f /tmp/relay_server.log || true

setup:
	@test -d $(VENV) || (echo "→ creating venv + installing deps (first run only)" && \
		python3 -m venv $(VENV) && $(VENV)/bin/pip install -q --upgrade pip && \
		$(VENV)/bin/pip install -q -r requirements.txt)

stop:
	@pkill -f "[l]ive_server.py" 2>/dev/null || true
	@lsof -ti tcp:$(PORT) | xargs kill 2>/dev/null || true
	@sleep 1

## make check — run every self-check (controller invariants + benchmark)
check: setup
	$(PY) src/controller.py
	$(PY) src/microsim.py

## make pipeline CLIP=path/to/clip.mp4 — run the core system on any footage
pipeline: setup
	@test -n "$(CLIP)" || { echo "usage: make pipeline CLIP=path/to/clip.mp4  (no footage in repo — live sim is the test surface: make dev)"; exit 1; }
	$(PY) src/pipeline.py "$(CLIP)"

## make compare — open the split-screen fixed-vs-adaptive demo
compare:
	@open "http://127.0.0.1:$(PORT)/compare.html"

## make capture N=300 — dump N auto-labeled training frames from the sim (via the live server)
capture: setup stop
	@nohup $(PY) tools/live_server.py > /tmp/relay_server.log 2>&1 &
	@for i in $$(seq 1 20); do curl -s -o /dev/null http://127.0.0.1:$(PORT)/ && break; sleep 1; done
	@open "http://127.0.0.1:$(PORT)/?capture=$(or $(N),300)"

## make train — fine-tune the detector on the captured dataset (mixed run name)
train: setup
	$(PY) tools/train.py 25 640 mps ft_mixed

## make webcam — R.E.L.A.Y. on the Mac camera (no extra hardware): point it at traffic footage on a phone
webcam: setup
	$(PY) tools/camera_demo.py 0
