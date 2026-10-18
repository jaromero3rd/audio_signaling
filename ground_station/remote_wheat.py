"""Wheat-head detector that calls a remote ONNX server (Factory e2b)."""

from __future__ import annotations

import os
from typing import Any

import cv2
import numpy as np
import urllib.error
import urllib.request

from ground_station.vision import Box, union_boxes


class RemoteWheatHeadDetector:
    """POST JPEG frames to WHEAT_INFER_URL (default http://127.0.0.1:8767/detect)."""

    def __init__(self, url: str | None = None, conf: float = 0.25, timeout: float = 2.5):
        self.url = (url or os.environ.get("WHEAT_INFER_URL") or "http://127.0.0.1:8767/detect").rstrip("/")
        if not self.url.endswith("/detect"):
            self.url = self.url + "/detect"
        self.conf = conf
        self.timeout = timeout
        print(f"wheat detector: REMOTE {self.url}")

    def detect_all(self, frame_bgr: np.ndarray) -> list[Box]:
        ok, buf = cv2.imencode(".jpg", frame_bgr, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
        if not ok:
            return []
        req = urllib.request.Request(
            self.url,
            data=buf.tobytes(),
            headers={"Content-Type": "image/jpeg"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                payload: dict[str, Any] = __import__("json").loads(resp.read().decode())
        except (urllib.error.URLError, TimeoutError, ValueError, OSError) as exc:
            print(f"remote wheat miss: {exc}")
            return []
        out: list[Box] = []
        for row in payload.get("boxes") or []:
            if len(row) < 5 or float(row[4]) < self.conf:
                continue
            x, y, w, h, conf = row[:5]
            out.append(Box(x=int(x), y=int(y), w=int(w), h=int(h), conf=float(conf), label="wheat_head"))
        return out

    def detect(self, frame_bgr: np.ndarray) -> Box | None:
        return union_boxes(self.detect_all(frame_bgr))
