#!/usr/bin/env python3
"""Convert Kaggle Global Wheat Detection (train.csv + train/*.jpg) to YOLO layout.

Download (needs a Kaggle account):
  kaggle competitions download -c global-wheat-detection
  unzip global-wheat-detection.zip -d data/global-wheat-detection

Then:
  python ground_station/prepare_wheat_dataset.py --src data/global-wheat-detection
"""

from __future__ import annotations

import argparse
import ast
import csv
import random
import shutil
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

OUT = ROOT / "datasets" / "wheat"


def yolo_line(width: int, height: int, xywh: list[float]) -> str | None:
    x, y, bw, bh = xywh
    if width <= 0 or height <= 0 or bw <= 0 or bh <= 0:
        return None
    xc = (x + bw / 2) / width
    yc = (y + bh / 2) / height
    nw = bw / width
    nh = bh / height
    if min(xc, yc, nw, nh) < 0 or max(xc, yc, nw, nh) > 1.5:
        return None
    xc = min(max(xc, 0.0), 1.0)
    yc = min(max(yc, 0.0), 1.0)
    nw = min(max(nw, 1e-6), 1.0)
    nh = min(max(nh, 1e-6), 1.0)
    return f"0 {xc:.6f} {yc:.6f} {nw:.6f} {nh:.6f}"


def load_kaggle(src: Path) -> tuple[dict[str, list[str]], Path]:
    csv_path = src / "train.csv"
    img_dir = src / "train"
    if not csv_path.is_file():
        raise FileNotFoundError(
            f"Expected {csv_path} (Kaggle Global Wheat Detection train.csv)"
        )
    if not img_dir.is_dir():
        raise FileNotFoundError(f"Expected image folder {img_dir}")

    labels: dict[str, list[str]] = defaultdict(list)
    with csv_path.open(newline="") as f:
        for row in csv.DictReader(f):
            image_id = row["image_id"]
            width, height = int(float(row["width"])), int(float(row["height"]))
            bbox = ast.literal_eval(row["bbox"])
            line = yolo_line(width, height, bbox)
            if line:
                labels[image_id].append(line)
    return labels, img_dir


def find_image(img_dir: Path, image_id: str) -> Path | None:
    for ext in (".jpg", ".jpeg", ".png", ".JPG"):
        p = img_dir / f"{image_id}{ext}"
        if p.is_file():
            return p
    return None


def write_split(
    image_ids: list[str],
    split: str,
    labels: dict[str, list[str]],
    img_dir: Path,
) -> int:
    img_out = OUT / "images" / split
    lbl_out = OUT / "labels" / split
    img_out.mkdir(parents=True, exist_ok=True)
    lbl_out.mkdir(parents=True, exist_ok=True)
    n = 0
    for image_id in image_ids:
        src_img = find_image(img_dir, image_id)
        if src_img is None:
            continue
        dest = img_out / src_img.name
        if not dest.exists():
            shutil.copy2(src_img, dest)
        (lbl_out / f"{image_id}.txt").write_text(
            "\n".join(labels[image_id]) + "\n", encoding="utf-8"
        )
        n += 1
    return n


def main() -> int:
    p = argparse.ArgumentParser(description="Kaggle wheat-head CSV → YOLO folders")
    p.add_argument(
        "--src",
        type=Path,
        required=True,
        help="Folder with train.csv and train/ images",
    )
    p.add_argument("--val-frac", type=float, default=0.1)
    p.add_argument("--seed", type=int, default=42)
    args = p.parse_args()

    labels, img_dir = load_kaggle(args.src.resolve())
    ids = sorted(labels)
    rng = random.Random(args.seed)
    rng.shuffle(ids)
    n_val = max(1, int(len(ids) * args.val_frac))
    val_ids, train_ids = ids[:n_val], ids[n_val:]
    if OUT.exists():
        shutil.rmtree(OUT)
    n_train = write_split(train_ids, "train", labels, img_dir)
    n_val_w = write_split(val_ids, "val", labels, img_dir)
    print(f"wrote {n_train} train / {n_val_w} val images → {OUT}")
    print("next: python ground_station/train_wheat.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
