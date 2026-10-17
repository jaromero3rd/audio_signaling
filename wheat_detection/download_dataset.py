#!/usr/bin/env python3
"""Download Global Wheat Head Dataset 2021 in YOLO layout.

The Kaggle release (vbookshelf/global-wheat-head-dataset-2021) is this same
competition set. Kaggle's API requires ~/.kaggle/kaggle.json, so this script
pulls the public YOLO conversion of that release:

    https://huggingface.co/datasets/dronefreak/GWHD

Images are the original 1024x1024 field PNGs. Labels are one wheat_head box
per line: class x_center y_center width height, normalized.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from huggingface_hub import snapshot_download

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DEST = REPO_ROOT / "data" / "gwhd"
HF_REPO = "dronefreak/GWHD"


def download(dest: Path) -> Path:
    dest.mkdir(parents=True, exist_ok=True)
    snapshot_download(
        repo_id=HF_REPO,
        repo_type="dataset",
        local_dir=str(dest),
        allow_patterns=["data/**"],
    )
    root = dest / "data"
    for split in ("train", "val", "test"):
        images = root / "images" / split
        labels = root / "labels" / split
        if not images.is_dir() or not labels.is_dir():
            raise FileNotFoundError(f"missing split {split} under {root}")
        n_images = len(list(images.glob("*.png")))
        n_labels = len(list(labels.glob("*.txt")))
        print(f"{split}: {n_images} images, {n_labels} label files")
    print(f"dataset ready at {root}")
    return root


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dest", type=Path, default=DEFAULT_DEST)
    args = parser.parse_args()
    download(args.dest)


if __name__ == "__main__":
    main()
