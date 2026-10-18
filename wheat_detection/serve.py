#!/usr/bin/env python3
"""Factory e2b vision server: wheat/poster ONNX + AprilTag.

POST /detect          JPEG → {"boxes":[[x,y,w,h,conf],...]}   (ONNX only)
POST /apriltag?family=36h11&id=12  JPEG → {"boxes":[...], "tags":[...]}
POST /frame?family=36h11&home_id=12&target_id=12&wheat=1
                      JPEG → wheat + tags in one shot (preferred)
GET  /health          → {"ok": true}
"""

from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import cv2
import numpy as np

from wheat_detection.detect import DEFAULT_MODEL, WheatHeadDetector

# AprilTag dicts (same names as ground_station/apriltag.py)
_FAMILIES = {
    "16h5": "DICT_APRILTAG_16h5",
    "25h9": "DICT_APRILTAG_25h9",
    "36h10": "DICT_APRILTAG_36h10",
    "36h11": "DICT_APRILTAG_36h11",
}


def _dictionary(family: str):
    key = _FAMILIES.get(family, family)
    if not hasattr(cv2.aruco, key):
        raise RuntimeError(f"OpenCV missing {key}")
    return cv2.aruco.getPredefinedDictionary(getattr(cv2.aruco, key))


def _params():
    params = cv2.aruco.DetectorParameters()
    if hasattr(params, "cornerRefinementMethod") and hasattr(cv2.aruco, "CORNER_REFINE_APRILTAG"):
        params.cornerRefinementMethod = cv2.aruco.CORNER_REFINE_APRILTAG
    if hasattr(params, "minMarkerPerimeterRate"):
        params.minMarkerPerimeterRate = 0.02
    return params


def detect_apriltags(frame_bgr: np.ndarray, family: str, tag_id: int | None) -> list[dict]:
    gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
    dictionary = _dictionary(family)
    if hasattr(cv2.aruco, "ArucoDetector"):
        corners, ids, _ = cv2.aruco.ArucoDetector(dictionary, _params()).detectMarkers(gray)
    else:
        corners, ids, _ = cv2.aruco.detectMarkers(gray, dictionary, parameters=_params())
    if ids is None or len(ids) == 0:
        return []
    out: list[dict] = []
    for pts, tid in zip(corners, ids.flatten()):
        tid_i = int(tid)
        if tag_id is not None and tid_i != tag_id:
            continue
        quad = pts.reshape(-1, 2).astype(np.int32)
        x, y, w, h = cv2.boundingRect(quad)
        out.append({"id": tid_i, "x": int(x), "y": int(y), "w": int(w), "h": int(h), "conf": 1.0})
    return out


class Handler(BaseHTTPRequestHandler):
    detector: WheatHeadDetector | None = None

    def log_message(self, fmt: str, *args) -> None:
        print(f"[vision-serve] {self.address_string()} {fmt % args}")

    def _read_image(self) -> np.ndarray | None:
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0 or length > 12 * 1024 * 1024:
            return None
        raw = self.rfile.read(length)
        arr = np.frombuffer(raw, dtype=np.uint8)
        return cv2.imdecode(arr, cv2.IMREAD_COLOR)

    def _json(self, code: int, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        if urlparse(self.path).path.rstrip("/") != "/health":
            self.send_error(404)
            return
        self._json(200, {"ok": True, "wheat": self.detector is not None})

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")
        qs = parse_qs(parsed.query)
        frame = self._read_image()
        if frame is None:
            self.send_error(400, "bad image")
            return

        if path == "/detect":
            if self.detector is None:
                self.send_error(503, "no wheat model")
                return
            heads = self.detector.detect(frame)
            boxes = [
                [int(h.x1), int(h.y1), int(h.x2 - h.x1), int(h.y2 - h.y1), float(h.confidence)]
                for h in heads
            ]
            self._json(200, {"boxes": boxes, "n": len(boxes)})
            return

        if path == "/apriltag":
            family = (qs.get("family") or ["36h11"])[0]
            raw_id = (qs.get("id") or [""])[0]
            tag_id = int(raw_id) if raw_id not in ("", "none", "null") else None
            tags = detect_apriltags(frame, family, tag_id)
            boxes = [[t["x"], t["y"], t["w"], t["h"], t["conf"]] for t in tags]
            self._json(200, {"boxes": boxes, "tags": tags, "n": len(tags)})
            return

        if path == "/frame":
            family = (qs.get("family") or ["36h11"])[0]
            want_wheat = (qs.get("wheat") or ["0"])[0] not in ("0", "false", "no")
            home_raw = (qs.get("home_id") or [""])[0]
            target_raw = (qs.get("target_id") or [""])[0]
            home_id = int(home_raw) if home_raw not in ("", "none") else None
            target_id = int(target_raw) if target_raw not in ("", "none") else None

            wheat_boxes: list[list[float]] = []
            if want_wheat:
                if self.detector is None:
                    self.send_error(503, "no wheat model")
                    return
                heads = self.detector.detect(frame)
                wheat_boxes = [
                    [int(h.x1), int(h.y1), int(h.x2 - h.x1), int(h.y2 - h.y1), float(h.confidence)]
                    for h in heads
                ]

            # Detect all tags in family once; filter client-side by id.
            all_tags = detect_apriltags(frame, family, None)
            def pick(tid: int | None) -> dict | None:
                if tid is None:
                    return None
                matches = [t for t in all_tags if t["id"] == tid]
                if not matches:
                    return None
                return max(matches, key=lambda t: t["w"] * t["h"])

            self._json(
                200,
                {
                    "wheat": wheat_boxes,
                    "tags": all_tags,
                    "home": pick(home_id),
                    "target_tag": pick(target_id),
                },
            )
            return

        self.send_error(404)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--port", type=int, default=8767)
    p.add_argument("--model", type=Path, default=DEFAULT_MODEL)
    p.add_argument("--conf", type=float, default=0.25)
    p.add_argument("--no-wheat", action="store_true", help="AprilTag-only (no ONNX)")
    args = p.parse_args()
    if args.no_wheat or not args.model.is_file():
        Handler.detector = None
        print("vision serve: AprilTag only (no wheat model)", flush=True)
    else:
        Handler.detector = WheatHeadDetector(args.model, conf=args.conf)
        print(f"vision serve: wheat={args.model} + AprilTag on :{args.port}", flush=True)
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
