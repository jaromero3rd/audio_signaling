#!/usr/bin/env python3
"""Detect every wheat head in an image with the ONNX YOLOv8s model.

Examples:
  python wheat_detection/detect.py field.png
  python wheat_detection/detect.py images/ --save
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
import onnxruntime as ort

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MODEL = REPO_ROOT / "models" / "wheat_detection.onnx"
POSTER_MODEL = REPO_ROOT / "models" / "wheat_poster.onnx"
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".webp"}


@dataclass(frozen=True)
class WheatHead:
    x1: float
    y1: float
    x2: float
    y2: float
    confidence: float

    def as_row(self) -> str:
        return (
            f"{self.x1:.1f} {self.y1:.1f} {self.x2:.1f} {self.y2:.1f} "
            f"{self.confidence:.3f}"
        )


class WheatHeadDetector:
    """YOLOv8s wheat-head detector. Input is a BGR image; boxes are pixels."""

    def __init__(
        self,
        model_path: Path = DEFAULT_MODEL,
        conf: float = 0.25,
        iou: float = 0.7,
    ) -> None:
        if not model_path.is_file():
            raise FileNotFoundError(model_path)
        available = ort.get_available_providers()
        providers = [
            name
            for name in ("CUDAExecutionProvider", "CPUExecutionProvider")
            if name in available
        ]
        self.session = ort.InferenceSession(str(model_path), providers=providers)
        inp = self.session.get_inputs()[0]
        self.input_name = inp.name
        # Fixed export shapes are [1,3,H,W]; fall back if dynamic/unknown.
        shape = list(inp.shape)
        try:
            self.imgsz = int(shape[-1]) if shape and shape[-1] not in (None, "None", -1) else 640
        except (TypeError, ValueError):
            self.imgsz = 640
        if self.imgsz < 32:
            self.imgsz = 640
        self.conf = conf
        self.iou = iou

    def detect(self, image_bgr: np.ndarray) -> list[WheatHead]:
        canvas, scale, pad_x, pad_y = _letterbox(image_bgr, self.imgsz)
        blob = cv2.cvtColor(canvas, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
        blob = np.transpose(blob, (2, 0, 1))[None]
        raw = self.session.run(None, {self.input_name: blob})[0]
        prediction = np.squeeze(raw, axis=0).T  # (anchors, 5) xywh + score
        if prediction.size == 0:
            return []
        scores = prediction[:, 4]
        keep = scores >= self.conf
        prediction = prediction[keep]
        scores = scores[keep]
        if prediction.size == 0:
            return []
        boxes = _xywh_to_xyxy(prediction[:, :4])
        boxes = _scale_boxes(boxes, scale, pad_x, pad_y, image_bgr.shape)
        chosen = _nms(boxes, scores, self.iou)
        return [
            WheatHead(
                x1=float(boxes[i, 0]),
                y1=float(boxes[i, 1]),
                x2=float(boxes[i, 2]),
                y2=float(boxes[i, 3]),
                confidence=float(scores[i]),
            )
            for i in chosen
        ]


def _letterbox(image: np.ndarray, size: int) -> tuple[np.ndarray, float, float, float]:
    height, width = image.shape[:2]
    scale = min(size / height, size / width)
    resized_w = int(round(width * scale))
    resized_h = int(round(height * scale))
    resized = cv2.resize(image, (resized_w, resized_h), interpolation=cv2.INTER_LINEAR)
    pad_x = (size - resized_w) / 2
    pad_y = (size - resized_h) / 2
    top = int(round(pad_y - 0.1))
    bottom = int(round(pad_y + 0.1))
    left = int(round(pad_x - 0.1))
    right = int(round(pad_x + 0.1))
    canvas = cv2.copyMakeBorder(
        resized,
        top,
        bottom,
        left,
        right,
        cv2.BORDER_CONSTANT,
        value=(114, 114, 114),
    )
    return canvas, scale, left, top


def _xywh_to_xyxy(boxes: np.ndarray) -> np.ndarray:
    out = np.empty_like(boxes)
    out[:, 0] = boxes[:, 0] - boxes[:, 2] / 2
    out[:, 1] = boxes[:, 1] - boxes[:, 3] / 2
    out[:, 2] = boxes[:, 0] + boxes[:, 2] / 2
    out[:, 3] = boxes[:, 1] + boxes[:, 3] / 2
    return out


def _scale_boxes(
    boxes: np.ndarray,
    scale: float,
    pad_x: float,
    pad_y: float,
    shape: tuple[int, ...],
) -> np.ndarray:
    height, width = shape[:2]
    boxes = boxes.copy()
    boxes[:, [0, 2]] = (boxes[:, [0, 2]] - pad_x) / scale
    boxes[:, [1, 3]] = (boxes[:, [1, 3]] - pad_y) / scale
    boxes[:, [0, 2]] = np.clip(boxes[:, [0, 2]], 0, width)
    boxes[:, [1, 3]] = np.clip(boxes[:, [1, 3]], 0, height)
    return boxes


def _nms(boxes: np.ndarray, scores: np.ndarray, iou_threshold: float) -> list[int]:
    order = scores.argsort()[::-1]
    keep: list[int] = []
    while order.size:
        index = int(order[0])
        keep.append(index)
        if order.size == 1:
            break
        rest = order[1:]
        xx1 = np.maximum(boxes[index, 0], boxes[rest, 0])
        yy1 = np.maximum(boxes[index, 1], boxes[rest, 1])
        xx2 = np.minimum(boxes[index, 2], boxes[rest, 2])
        yy2 = np.minimum(boxes[index, 3], boxes[rest, 3])
        intersection = np.maximum(0, xx2 - xx1) * np.maximum(0, yy2 - yy1)
        area_i = (boxes[index, 2] - boxes[index, 0]) * (boxes[index, 3] - boxes[index, 1])
        area_rest = (boxes[rest, 2] - boxes[rest, 0]) * (boxes[rest, 3] - boxes[rest, 1])
        iou = intersection / (area_i + area_rest - intersection + 1e-6)
        order = rest[iou <= iou_threshold]
    return keep


def iter_images(sources: list[Path]) -> list[Path]:
    images: list[Path] = []
    for source in sources:
        if source.is_dir():
            images.extend(
                path
                for path in sorted(source.iterdir())
                if path.suffix.lower() in IMAGE_SUFFIXES
            )
        elif source.is_file() and source.suffix.lower() in IMAGE_SUFFIXES:
            images.append(source)
        else:
            raise FileNotFoundError(source)
    return images


def draw(image: np.ndarray, heads: list[WheatHead]) -> np.ndarray:
    canvas = image.copy()
    for head in heads:
        p1 = (int(round(head.x1)), int(round(head.y1)))
        p2 = (int(round(head.x2)), int(round(head.y2)))
        cv2.rectangle(canvas, p1, p2, (40, 220, 60), 2)
    return canvas


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sources", nargs="+", type=Path, help="image files or a directory")
    parser.add_argument("--model", type=Path, default=DEFAULT_MODEL)
    parser.add_argument("--conf", type=float, default=0.25)
    parser.add_argument("--iou", type=float, default=0.7)
    parser.add_argument(
        "--save",
        action="store_true",
        help="write annotated images under runs/wheat_detection/onnx",
    )
    args = parser.parse_args()

    detector = WheatHeadDetector(args.model, conf=args.conf, iou=args.iou)
    out_dir = REPO_ROOT / "runs" / "wheat_detection" / "onnx"
    if args.save:
        out_dir.mkdir(parents=True, exist_ok=True)

    for path in iter_images(args.sources):
        image = cv2.imread(str(path))
        if image is None:
            raise RuntimeError(f"could not read {path}")
        heads = detector.detect(image)
        print(f"{path} {len(heads)} wheat_head")
        for index, head in enumerate(heads):
            print(f"  {index} {head.as_row()}")
        if args.save:
            destination = out_dir / f"{path.stem}_wheat.jpg"
            cv2.imwrite(str(destination), draw(image, heads))
            print(f"  saved {destination}")


if __name__ == "__main__":
    main()
