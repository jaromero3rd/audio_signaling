#!/usr/bin/env python3
"""Minimal HTTP wheat-head inference server (runs on Factory e2b).

POST /detect  body=JPEG/PNG bytes  →  {"boxes":[[x,y,w,h,conf], ...]}
GET  /health  →  {"ok": true}
"""

from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import cv2
import numpy as np

from wheat_detection.detect import DEFAULT_MODEL, WheatHeadDetector


class Handler(BaseHTTPRequestHandler):
    detector: WheatHeadDetector

    def log_message(self, fmt: str, *args) -> None:  # quieter
        print(f"[wheat-serve] {self.address_string()} {fmt % args}")

    def do_GET(self) -> None:  # noqa: N802
        if self.path.rstrip("/") != "/health":
            self.send_error(404)
            return
        body = json.dumps({"ok": True}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:  # noqa: N802
        if self.path.rstrip("/") != "/detect":
            self.send_error(404)
            return
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0 or length > 12 * 1024 * 1024:
            self.send_error(400, "bad Content-Length")
            return
        raw = self.rfile.read(length)
        arr = np.frombuffer(raw, dtype=np.uint8)
        frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if frame is None:
            self.send_error(400, "not an image")
            return
        heads = self.detector.detect(frame)
        boxes = [
            [int(h.x1), int(h.y1), int(h.x2 - h.x1), int(h.y2 - h.y1), float(h.confidence)]
            for h in heads
        ]
        body = json.dumps({"boxes": boxes, "n": len(boxes)}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--port", type=int, default=8767)
    p.add_argument("--model", type=Path, default=DEFAULT_MODEL)
    p.add_argument("--conf", type=float, default=0.25)
    args = p.parse_args()
    Handler.detector = WheatHeadDetector(args.model, conf=args.conf)
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"wheat ONNX serve on http://{args.host}:{args.port}/detect", flush=True)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
