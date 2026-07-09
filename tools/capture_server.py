#!/usr/bin/env python
"""Serve the sim AND accept POST /save to write auto-labeled YOLO training data.
The sim (capture mode) projects each car's 3D box to a 2D label and POSTs frame+label here.
Run: .venv/bin/python tools/capture_server.py [port]   (default 8123, serves sim/)
Writes: dataset/images/<name>.jpg + dataset/labels/<name>.txt
"""
import base64, json, os, sys
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", "sim"))
DS = os.path.abspath(os.path.join(HERE, "..", "dataset"))
IMG, LBL = os.path.join(DS, "images"), os.path.join(DS, "labels")
os.makedirs(IMG, exist_ok=True)
os.makedirs(LBL, exist_ok=True)


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **k):
        super().__init__(*a, directory=ROOT, **k)

    def log_message(self, *a):
        pass

    def do_POST(self):
        if self.path != "/save":
            self.send_error(404); return
        n = int(self.headers.get("Content-Length", 0))
        data = json.loads(self.rfile.read(n))
        os.makedirs(IMG, exist_ok=True)                 # survive a dataset wipe mid-run
        os.makedirs(LBL, exist_ok=True)
        name = os.path.basename(data["name"])           # no path traversal
        img_b64 = data["image"].split(",", 1)[1]        # strip data:image/jpeg;base64,
        with open(os.path.join(IMG, name + ".jpg"), "wb") as f:
            f.write(base64.b64decode(img_b64))
        with open(os.path.join(LBL, name + ".txt"), "w") as f:
            f.write(data.get("label", ""))
        self.send_response(200); self.end_headers(); self.wfile.write(b"ok")


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8123
    print(f"capture server on http://127.0.0.1:{port}  serving {ROOT}")
    print(f"dataset -> {DS}")
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
