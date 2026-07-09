# R.E.L.A.Y. — one-command dev
VENV := .venv
PY := $(VENV)/bin/python
PORT := 8000

.PHONY: dev setup check pipeline compare capture train stop

## make dev — set up (if needed) + start everything: live server, then open the demos
dev: setup stop
	@echo "→ starting R.E.L.A.Y. live server on :$(PORT)"
	@nohup $(PY) tools/live_server.py > /tmp/relay_server.log 2>&1 & echo $$! > /tmp/relay_server.pid
	@for i in $$(seq 1 20); do curl -s -o /dev/null http://127.0.0.1:$(PORT)/ && break; sleep 1; done
	@echo ""
	@echo "  R.E.L.A.Y. is up:"
	@echo "    live closed loop   →  http://127.0.0.1:$(PORT)/?live=1"
	@echo "    T-junction live    →  http://127.0.0.1:$(PORT)/?live=1&topo=T"
	@echo "    fixed-vs-adaptive  →  http://127.0.0.1:$(PORT)/compare.html?ff=120"
	@echo "    free-roam sim      →  http://127.0.0.1:$(PORT)/"
	@echo ""
	@open "http://127.0.0.1:$(PORT)/?live=1" 2>/dev/null || true

setup:
	@test -d $(VENV) || (echo "→ creating venv + installing deps (first run only)" && \
		python3 -m venv $(VENV) && $(VENV)/bin/pip install -q --upgrade pip && \
		$(VENV)/bin/pip install -q -r requirements.txt)

stop:
	@lsof -ti tcp:$(PORT) | xargs kill 2>/dev/null || true

## make check — run every self-check (controller invariants + benchmark)
check: setup
	$(PY) src/controller.py
	$(PY) src/microsim.py

## make pipeline CLIP=footage/x.mp4 — run the core system on any footage
pipeline: setup
	$(PY) src/pipeline.py $(or $(CLIP),footage/GENERATED_sim_junction.mp4)

## make compare — open the split-screen fixed-vs-adaptive demo
compare:
	@open "http://127.0.0.1:$(PORT)/compare.html?ff=120"

## make capture N=300 — dump N auto-labeled training frames from the sim
capture: setup stop
	@nohup $(PY) tools/capture_server.py $(PORT) > /tmp/relay_capture.log 2>&1 &
	@sleep 2 && open "http://127.0.0.1:$(PORT)/?capture=$(or $(N),300)"

## make train — fine-tune the detector on the captured dataset (mixed run name)
train: setup
	$(PY) tools/train.py 25 640 mps ft_mixed
