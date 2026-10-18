#!/usr/bin/env python3
"""Export a YOLO checkpoint to ONNX (heads @1024 or poster @320)."""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

from ultralytics import YOLO

REPO = Path(__file__).resolve().parents[1]
DEFAULT_WEIGHTS = REPO / "models" / "wheat_detection.pt"
POSTER_OUT = REPO / "models" / "wheat_poster.onnx"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--weights", type=Path, default=DEFAULT_WEIGHTS)
    parser.add_argument(
        "--imgsz",
        type=int,
        default=None,
        help="export square size (default: 320 for poster stems, else 1024)",
    )
    parser.add_argument("--opset", type=int, default=12)
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="destination .onnx path (default: next to weights, or models/wheat_poster.onnx)",
    )
    args = parser.parse_args()
    if not args.weights.is_file():
        raise FileNotFoundError(args.weights)
    stem = args.weights.stem.lower()
    imgsz = args.imgsz
    if imgsz is None:
        imgsz = 320 if "poster" in stem else 1024
    exported = Path(
        YOLO(str(args.weights)).export(
            format="onnx",
            imgsz=imgsz,
            opset=args.opset,
            simplify=True,
            dynamic=False,
        )
    )
    out = args.out
    if out is None and "poster" in stem:
        out = POSTER_OUT
    if out is not None:
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(exported, out)
        print(f"copied → {out} (imgsz={imgsz})")
    print(exported)


if __name__ == "__main__":
    main()
