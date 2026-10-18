"""Laptop client for Factory e2b vision (wheat ONNX + AprilTag). One JPEG / frame."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

import cv2
import numpy as np

from ground_station.vision import Box, union_boxes


def _default_url() -> str:
    return (os.environ.get("WHEAT_INFER_URL") or "http://127.0.0.1:8767/detect").rstrip("/")


def _base(url: str) -> str:
    """http://host:8767/detect → http://host:8767"""
    if url.endswith("/detect"):
        return url[: -len("/detect")]
    return url.rstrip("/")


@dataclass
class FrameVision:
    wheat: list[Box]
    target_tag: Box | None
    home: Box | None


class RemoteVisionClient:
    """POST each camera frame once to e2b; cache result for that numpy object id."""

    def __init__(
        self,
        url: str | None = None,
        *,
        family: str = "36h11",
        home_tag: int = 12,
        target_tag: int | None = None,
        wheat: bool = False,
        wheat_label: str = "wheat_head",
        conf: float = 0.25,
        timeout: float = 3.5,
    ):
        self.base = _base(url or _default_url())
        self.family = family
        self.home_tag = home_tag
        self.target_tag_id = target_tag
        self.wheat = wheat
        self.wheat_label = wheat_label
        self.conf = conf
        self.timeout = timeout
        self._cache_key: int | None = None
        self._cache: FrameVision | None = None
        print(
            f"vision: Factory e2b {self.base}/frame "
            f"(wheat={wheat}, family={family}, home={home_tag}, target_tag={target_tag})"
        )

    def _post_frame(self, frame_bgr: np.ndarray) -> FrameVision:
        ok, buf = cv2.imencode(".jpg", frame_bgr, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
        if not ok:
            return FrameVision([], None, None)
        qs = {
            "family": self.family,
            "home_id": str(self.home_tag),
            "wheat": "1" if self.wheat else "0",
        }
        if self.target_tag_id is not None:
            qs["target_id"] = str(self.target_tag_id)
        url = f"{self.base}/frame?{urllib.parse.urlencode(qs)}"
        req = urllib.request.Request(
            url, data=buf.tobytes(), headers={"Content-Type": "image/jpeg"}, method="POST"
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                payload: dict[str, Any] = json.loads(resp.read().decode())
        except (urllib.error.URLError, TimeoutError, ValueError, OSError) as exc:
            print(f"remote vision miss: {exc}")
            return FrameVision([], None, None)

        wheat: list[Box] = []
        for row in payload.get("wheat") or []:
            if len(row) < 5 or float(row[4]) < self.conf:
                continue
            x, y, w, h, c = row[:5]
            wheat.append(
                Box(x=int(x), y=int(y), w=int(w), h=int(h), conf=float(c), label=self.wheat_label)
            )

        def box_from(obj: object) -> Box | None:
            if not isinstance(obj, dict):
                return None
            try:
                return Box(
                    x=int(obj["x"]),
                    y=int(obj["y"]),
                    w=int(obj["w"]),
                    h=int(obj["h"]),
                    conf=float(obj.get("conf", 1.0)),
                    label=f"tag-{int(obj['id'])}",
                )
            except (KeyError, TypeError, ValueError):
                return None

        return FrameVision(
            wheat=wheat,
            target_tag=box_from(payload.get("target_tag")),
            home=box_from(payload.get("home")),
        )

    def analyze(self, frame_bgr: np.ndarray) -> FrameVision:
        key = id(frame_bgr)
        if self._cache is not None and self._cache_key == key:
            return self._cache
        result = self._post_frame(frame_bgr)
        self._cache_key = key
        self._cache = result
        return result


class RemoteWheatViaVision:
    """Adapter: wheat detect_all/detect over shared RemoteVisionClient."""

    def __init__(self, client: RemoteVisionClient):
        self.client = client

    def detect_all(self, frame_bgr: np.ndarray) -> list[Box]:
        return self.client.analyze(frame_bgr).wheat

    def detect(self, frame_bgr: np.ndarray) -> Box | None:
        return union_boxes(self.detect_all(frame_bgr))


class RemoteAprilTagViaVision:
    """Adapter: AprilTag detect over shared RemoteVisionClient (target or home)."""

    def __init__(self, client: RemoteVisionClient, *, role: str):
        assert role in ("target", "home")
        self.client = client
        self.role = role

    def detect_all(self, frame_bgr: np.ndarray) -> list[Box]:
        box = self.detect(frame_bgr)
        return [box] if box is not None else []

    def detect(self, frame_bgr: np.ndarray) -> Box | None:
        vision = self.client.analyze(frame_bgr)
        return vision.target_tag if self.role == "target" else vision.home
