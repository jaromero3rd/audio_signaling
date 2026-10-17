#!/usr/bin/env python3
"""Fine-tune YOLOv8s on Global Wheat Head Dataset 2021."""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

from ultralytics import YOLO

REPO_ROOT = Path(__file__).resolve().parents[1]
DATA_ROOT = REPO_ROOT / "data" / "gwhd" / "data"
YAML_PATH = Path(__file__).resolve().parent / "gwhd.yaml"
WEIGHTS_OUT = Path(__file__).resolve().parent / "weights" / "yolov8s-wheat.pt"


def write_data_yaml() -> Path:
    if not (DATA_ROOT / "images" / "train").is_dir():
        raise FileNotFoundError(
            f"dataset not found at {DATA_ROOT}. Run wheat/download_dataset.py first."
        )
    YAML_PATH.write_text(
        "\n".join(
            [
                f"path: {DATA_ROOT}",
                "train: images/train",
                "val: images/val",
                "test: images/test",
                "names:",
                "  0: wheat_head",
                "",
            ]
        )
    )
    return YAML_PATH


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="yolov8s.pt", help="pretrained checkpoint")
    parser.add_argument("--epochs", type=int, default=50)
    parser.add_argument("--imgsz", type=int, default=1024)
    parser.add_argument("--batch", type=int, default=8)
    parser.add_argument("--device", default="0")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--patience", type=int, default=20)
    parser.add_argument("--project", type=Path, default=REPO_ROOT / "runs" / "wheat")
    parser.add_argument("--name", default="yolov8s")
    args = parser.parse_args()

    data_yaml = write_data_yaml()
    model = YOLO(args.model)
    model.train(
        data=str(data_yaml),
        epochs=args.epochs,
        imgsz=args.imgsz,
        batch=args.batch,
        device=args.device,
        workers=args.workers,
        patience=args.patience,
        project=str(args.project),
        name=args.name,
        exist_ok=True,
        pretrained=True,
        plots=True,
        save=True,
        val=True,
    )

    best = args.project / args.name / "weights" / "best.pt"
    if not best.is_file():
        raise FileNotFoundError(f"training finished without {best}")
    WEIGHTS_OUT.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(best, WEIGHTS_OUT)
    print(f"finetuned weights: {WEIGHTS_OUT}")


if __name__ == "__main__":
    main()
