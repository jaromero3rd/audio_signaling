#!/usr/bin/env python3
"""Fine-tune YOLOv8 on wheat heads (class 0) and copy best.pt to models/wheat_heads.pt."""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ground_station.vision import DEFAULT_WEIGHTS

DATA_YAML = Path(__file__).resolve().parent / "wheat.yaml"


def pick_device(explicit: str | None) -> str:
    if explicit:
        return explicit
    try:
        import torch

        if torch.cuda.is_available():
            return "0"
        if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
            return "mps"
    except ImportError:
        pass
    return "cpu"


def main() -> int:
    p = argparse.ArgumentParser(description="Train YOLOv8 wheat-head detector")
    p.add_argument("--data", type=Path, default=DATA_YAML)
    p.add_argument("--base", default="yolov8n.pt", help="COCO-pretrained YOLOv8 start weights")
    p.add_argument("--epochs", type=int, default=50)
    p.add_argument("--imgsz", type=int, default=640)
    p.add_argument("--batch", type=int, default=16)
    p.add_argument("--device", default=None, help="cpu | mps | 0 (CUDA). Default: auto")
    p.add_argument("--name", default="wheat_heads")
    args = p.parse_args()

    try:
        from ultralytics import YOLO
    except ImportError:
        print("pip install ultralytics", file=sys.stderr)
        return 1

    if not args.data.is_file():
        print(f"missing {args.data}", file=sys.stderr)
        return 1
    images = ROOT / "datasets" / "wheat" / "images" / "train"
    if not images.is_dir() or not any(images.iterdir()):
        print(
            "No YOLO images at datasets/wheat/images/train.\n"
            "  python ground_station/prepare_wheat_dataset.py --src /path/to/global-wheat-detection",
            file=sys.stderr,
        )
        return 1

    device = pick_device(args.device)
    print(f"train {args.base} → wheat_head  device={device}  epochs={args.epochs}")
    model = YOLO(args.base)
    model.train(
        data=str(args.data),
        epochs=args.epochs,
        imgsz=args.imgsz,
        batch=args.batch,
        device=device,
        project=str(ROOT / "runs" / "detect"),
        name=args.name,
        exist_ok=True,
        pretrained=True,
        single_cls=True,
    )
    best = Path(model.trainer.best)
    if not best.is_file():
        print(f"training finished but {best} is missing", file=sys.stderr)
        return 1
    DEFAULT_WEIGHTS.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(best, DEFAULT_WEIGHTS)
    print(f"copied {best} → {DEFAULT_WEIGHTS}")
    print("run: python ground_station/steer_wheat.py --mock")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
