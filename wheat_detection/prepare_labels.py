#!/usr/bin/env python3
"""Turn Global Wheat Head 2021 CSV boxes into YOLO label files.

Images are 1024x1024. BoxesString is "x1 y1 x2 y2;..." or "no_box".
"""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DATA_ROOT = REPO_ROOT / "data" / "gwhd" / "data"
ANN_DIR = REPO_ROOT / "data" / "gwhd" / "annotations" / "gwhd_2021"
SIZE = 1024
SPLITS = {
    "train": "competition_train.csv",
    "val": "competition_val.csv",
    "test": "competition_test.csv",
}


def to_yolo(boxes: str) -> str:
    boxes = (boxes or "").strip()
    if not boxes or boxes == "no_box":
        return ""
    lines: list[str] = []
    for part in boxes.split(";"):
        part = part.strip()
        if not part:
            continue
        x1, y1, x2, y2 = (float(value) for value in part.split())
        width = x2 - x1
        height = y2 - y1
        if width <= 0 or height <= 0:
            continue
        x_center = min(1.0, max(0.0, ((x1 + x2) / 2) / SIZE))
        y_center = min(1.0, max(0.0, ((y1 + y2) / 2) / SIZE))
        lines.append(
            f"0 {x_center:.6f} {y_center:.6f} {min(1.0, width / SIZE):.6f} {min(1.0, height / SIZE):.6f}"
        )
    return ("\n".join(lines) + "\n") if lines else ""


def prepare(data_root: Path, ann_dir: Path) -> None:
    for split, filename in SPLITS.items():
        image_dir = data_root / "images" / split
        label_dir = data_root / "labels" / split
        label_dir.mkdir(parents=True, exist_ok=True)
        written: set[str] = set()
        with (ann_dir / filename).open(newline="") as handle:
            for row in csv.DictReader(handle):
                stem = Path(row["image_name"]).stem
                if not (image_dir / f"{stem}.png").is_file():
                    continue
                (label_dir / f"{stem}.txt").write_text(to_yolo(row["BoxesString"]))
                written.add(stem)
        for image in image_dir.glob("*.png"):
            label = label_dir / f"{image.stem}.txt"
            if image.stem in written or label.exists():
                continue
            base = image.stem.split("__", 1)[0]
            base_label = label_dir / f"{base}.txt"
            if base_label.is_file():
                label.write_text(base_label.read_text())
            else:
                label.write_text("")
        print(f"{split}: {len(list(label_dir.glob('*.txt')))} labels")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, default=DATA_ROOT)
    parser.add_argument("--ann-dir", type=Path, default=ANN_DIR)
    args = parser.parse_args()
    prepare(args.data_root, args.ann_dir)


if __name__ == "__main__":
    main()
