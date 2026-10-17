"""Wheat-head detection: trained YOLOv8 checkpoint, HSV only as a fallback."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_WEIGHTS = ROOT / "models" / "wheat_detection.pt"
MISSING_WEIGHTS_MSG = (
    f"No wheat-head weights at {DEFAULT_WEIGHTS}. "
    "Convert the Global Wheat Detection set, then train:\n"
    "  python ground_station/prepare_wheat_dataset.py --src /path/to/global-wheat-detection\n"
    "  python ground_station/train_wheat.py\n"
    "Or pass --weights /path/to/best.pt"
)


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
    """Ultralytics YOLOv8 .pt trained with class 0 = wheat_head."""

    def __init__(self, weights: Path | None = None, conf: float = 0.25) -> None:
        try:
            from ultralytics import YOLO
        except ImportError as exc:
            raise RuntimeError("pip install ultralytics") from exc
        path = Path(weights) if weights is not None else DEFAULT_WEIGHTS
        if not path.is_file():
            raise FileNotFoundError(MISSING_WEIGHTS_MSG)
        self.model = YOLO(str(path))
        self.conf = conf

    def detect_all(self, frame_bgr: np.ndarray) -> list[Box]:
        results = self.model.predict(frame_bgr, conf=self.conf, verbose=False, imgsz=640)
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
                    label="wheat_head",
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
