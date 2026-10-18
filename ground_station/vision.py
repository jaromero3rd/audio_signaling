"""Wheat-head detection: ONNX YOLOv8 (models/wheat_detection.onnx), HSV fallback."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DEFAULT_ONNX = ROOT / "models" / "wheat_detection.onnx"
POSTER_ONNX = ROOT / "models" / "wheat_poster.onnx"
DEFAULT_PT = ROOT / "models" / "wheat_detection.pt"
DEFAULT_WEIGHTS = DEFAULT_ONNX if DEFAULT_ONNX.is_file() else DEFAULT_PT
WEIGHT_PRESETS = {
    "heads": DEFAULT_ONNX,
    "wheat": DEFAULT_ONNX,
    "poster": POSTER_ONNX,
    "wheat_poster": POSTER_ONNX,
}
MISSING_WEIGHTS_MSG = (
    f"No wheat-head model at {DEFAULT_ONNX} (or {DEFAULT_PT}). "
    "Export with: python wheat_detection/export_onnx.py"
)


def resolve_weights(value: str | Path | None) -> Path:
    """Map preset name (heads|poster) or filesystem path to a weights file."""
    if value is None:
        return DEFAULT_WEIGHTS
    key = str(value).strip()
    preset = WEIGHT_PRESETS.get(key.lower())
    if preset is not None:
        return preset
    return Path(value)


@dataclass
class Box:
    x: int
    y: int
    w: int
    h: int
    conf: float
    label: str = "wheat_head"

    @property
    def cx(self) -> float:
        return self.x + self.w / 2

    @property
    def cy(self) -> float:
        return self.y + self.h / 2

    def area_frac(self, frame_shape: tuple[int, ...]) -> float:
        fh, fw = frame_shape[:2]
        return (self.w * self.h) / max(fw * fh, 1)


def wheat_size_ratio(heads: list[Box], frame_shape: tuple[int, ...]) -> float:
    """How large typical wheat-head boxes are vs the image (0–1).

    Blend of median and largest head so a distant field of tiny boxes stays small,
    and getting close (big individual heads) raises the score.
    """
    if not heads:
        return 0.0
    fracs = sorted(h.area_frac(frame_shape) for h in heads)
    median = fracs[len(fracs) // 2]
    largest = fracs[-1]
    return 0.5 * median + 0.5 * largest


def union_boxes(boxes: list[Box]) -> Box | None:
    if not boxes:
        return None
    x1 = min(b.x for b in boxes)
    y1 = min(b.y for b in boxes)
    x2 = max(b.x + b.w for b in boxes)
    y2 = max(b.y + b.h for b in boxes)
    return Box(
        x=x1,
        y=y1,
        w=x2 - x1,
        h=y2 - y1,
        conf=max(b.conf for b in boxes),
        label="wheat_head",
    )


# Green vegetative wheat + gold/tan heads (ripe grain). Fallback only.
_GREEN_LO = np.array([30, 40, 40])
_GREEN_HI = np.array([90, 255, 255])
_GOLD_LO = np.array([12, 40, 70])
_GOLD_HI = np.array([38, 255, 255])


class HsvWheatDetector:
    def __init__(self, min_area_frac: float = 0.01) -> None:
        self.min_area_frac = min_area_frac
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7))
        self._kernel = kernel

    def detect_all(self, frame_bgr: np.ndarray) -> list[Box]:
        box = self.detect(frame_bgr)
        return [box] if box is not None else []

    def detect(self, frame_bgr: np.ndarray) -> Box | None:
        hsv = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2HSV)
        mask = cv2.inRange(hsv, _GREEN_LO, _GREEN_HI) | cv2.inRange(
            hsv, _GOLD_LO, _GOLD_HI
        )
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, self._kernel)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, self._kernel)
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not contours:
            return None
        c = max(contours, key=cv2.contourArea)
        x, y, w, h = cv2.boundingRect(c)
        fh, fw = frame_bgr.shape[:2]
        if (w * h) / max(fw * fh, 1) < self.min_area_frac:
            return None
        return Box(x, y, w, h, conf=0.5, label="wheat")


class YoloWheatHeadDetector:
    """Wheat boxes from ONNX/PT: heads (1024) or poster (320 preferred)."""

    def __init__(self, weights: Path | None = None, conf: float = 0.25) -> None:
        path = resolve_weights(weights)
        if path.suffix == ".pt" and not path.is_file():
            onnx = path.with_name("wheat_detection.onnx")
            if onnx.is_file():
                path = onnx
        if not path.is_file():
            raise FileNotFoundError(MISSING_WEIGHTS_MSG)
        self.conf = conf
        self.path = path
        self.label = "wheat_poster" if "poster" in path.stem.lower() else "wheat_head"
        self._onnx = None
        self._yolo = None
        self._imgsz = 640
        if path.suffix.lower() == ".onnx":
            from wheat_detection.detect import WheatHeadDetector

            self._onnx = WheatHeadDetector(path, conf=conf)
            self._imgsz = int(getattr(self._onnx, "imgsz", 640))
            print(f"wheat detector: ONNX {path} imgsz={self._imgsz}")
        else:
            try:
                from ultralytics import YOLO
            except ImportError as exc:
                raise RuntimeError("pip install ultralytics") from exc
            self._yolo = YOLO(str(path))
            # Poster runs prefer 320; head checkpoint historically 640/1024.
            self._imgsz = 320 if "poster" in path.stem.lower() else 640
            print(f"wheat detector: YOLO {path} imgsz={self._imgsz}")

    def detect_all(self, frame_bgr: np.ndarray) -> list[Box]:
        if self._onnx is not None:
            heads = self._onnx.detect(frame_bgr)
            return [
                Box(
                    x=int(h.x1),
                    y=int(h.y1),
                    w=int(h.x2 - h.x1),
                    h=int(h.y2 - h.y1),
                    conf=float(h.confidence),
                    label=self.label,
                )
                for h in heads
            ]
        results = self._yolo.predict(
            frame_bgr, conf=self.conf, verbose=False, imgsz=self._imgsz
        )
        if not results:
            return []
        boxes = results[0].boxes
        if boxes is None or len(boxes) == 0:
            return []
        out: list[Box] = []
        xyxy = boxes.xyxy.cpu().numpy()
        confs = boxes.conf.cpu().numpy()
        for (x1, y1, x2, y2), conf in zip(xyxy, confs):
            out.append(
                Box(
                    x=int(x1),
                    y=int(y1),
                    w=int(x2 - x1),
                    h=int(y2 - y1),
                    conf=float(conf),
                    label=self.label,
                )
            )
        return out

    def detect(self, frame_bgr: np.ndarray) -> Box | None:
        return union_boxes(self.detect_all(frame_bgr))


def make_detector(
    kind: str,
    *,
    weights: Path | None = None,
    conf: float = 0.25,
):
    if kind == "yolo":
        return YoloWheatHeadDetector(weights=weights, conf=conf)
    if kind == "hsv":
        return HsvWheatDetector()
    raise ValueError(f"unknown detector {kind!r}; use hsv or yolo")
