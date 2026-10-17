#!/usr/bin/env python3
"""Export the finetuned wheat-head checkpoint to ONNX."""

from __future__ import annotations

import argparse
from pathlib import Path

from ultralytics import YOLO

DEFAULT_WEIGHTS = Path(__file__).resolve().parents[1] / "models" / "wheat_detection.pt"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--weights", type=Path, default=DEFAULT_WEIGHTS)
    parser.add_argument("--imgsz", type=int, default=1024)
    parser.add_argument("--opset", type=int, default=12)
    args = parser.parse_args()
    if not args.weights.is_file():
        raise FileNotFoundError(args.weights)
    exported = YOLO(str(args.weights)).export(
        format="onnx",
        imgsz=args.imgsz,
        opset=args.opset,
        simplify=True,
        dynamic=False,
    )
    print(exported)


if __name__ == "__main__":
    main()
