#!/usr/bin/env python3
"""Run the finetuned wheat-head detector on an image, folder, or video."""

from __future__ import annotations

import argparse
from pathlib import Path

from ultralytics import YOLO

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_WEIGHTS = Path(__file__).resolve().parent / "weights" / "yolov8s-wheat.pt"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", help="image, directory, or video")
    parser.add_argument("--weights", type=Path, default=DEFAULT_WEIGHTS)
    parser.add_argument("--imgsz", type=int, default=1024)
    parser.add_argument("--conf", type=float, default=0.25)
    parser.add_argument("--device", default="0")
    args = parser.parse_args()

    if not args.weights.is_file():
        raise FileNotFoundError(f"weights not found: {args.weights}")

    model = YOLO(str(args.weights))
    model.predict(
        source=args.source,
        imgsz=args.imgsz,
        conf=args.conf,
        device=args.device,
        save=True,
        project=str(REPO_ROOT / "runs" / "wheat"),
        name="predict",
        exist_ok=True,
    )


if __name__ == "__main__":
    main()
