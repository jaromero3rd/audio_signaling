#!/usr/bin/env python3
"""Augment labeled poster frames until we hit a target count (default 1000).

Reads YOLO labels from labels_all (+ matching frames), writes train/val under
data/poster_yolo and a zip. Augmentations: hflip, brightness, slight scale-crop.

  python -m wheat_detection.augment_poster --target 1000
"""

from __future__ import annotations

import argparse
import json
import random
import shutil
import zipfile
from pathlib import Path

import cv2
import numpy as np

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "data" / "poster_yolo"
FRAMES = OUT / "frames"
LABELS = OUT / "labels_all"
REF = OUT / "reference"
CLASS_ID = 0
CLASS_NAME = "wheat_poster"


def read_box(lab: Path) -> tuple[float, float, float, float]:
    _, cx, cy, bw, bh = (float(x) for x in lab.read_text().split())
    return cx, cy, bw, bh


def write_box(path: Path, cx: float, cy: float, bw: float, bh: float) -> None:
    cx = min(1.0, max(0.0, cx))
    cy = min(1.0, max(0.0, cy))
    bw = min(1.0, max(1e-4, bw))
    bh = min(1.0, max(1e-4, bh))
    # clip so box stays inside
    x1, x2 = cx - bw / 2, cx + bw / 2
    y1, y2 = cy - bh / 2, cy + bh / 2
    x1, x2 = max(0.0, x1), min(1.0, x2)
    y1, y2 = max(0.0, y1), min(1.0, y2)
    cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
    bw, bh = x2 - x1, y2 - y1
    path.write_text(f"{CLASS_ID} {cx:.6f} {cy:.6f} {bw:.6f} {bh:.6f}\n")


def find_image(stem: str) -> Path | None:
    for cand in (
        FRAMES / f"{stem}.jpg",
        REF / "images" / f"{stem}.jpg",
    ):
        if cand.is_file():
            return cand
    # strip NN_ prefix
    if len(stem) > 3 and stem[2] == "_" and stem[:2].isdigit():
        return find_image(stem[3:])
    hits = list(FRAMES.glob(f"*{stem}.jpg"))
    if hits:
        return hits[0]
    hits = list((REF / "images").glob(f"*{stem}.jpg"))
    return hits[0] if hits else None


def load_pairs() -> list[tuple[Path, Path, tuple[float, float, float, float]]]:
    pairs = []
    seen_img: set[str] = set()
    for lab in sorted(LABELS.glob("*.txt")):
        if not lab.read_text().strip():
            continue
        img = find_image(lab.stem)
        if img is None:
            continue
        key = img.resolve().as_posix()
        if key in seen_img:
            continue
        seen_img.add(key)
        pairs.append((img, lab, read_box(lab)))
    # also include reference hand labels not yet in labels_all
    for lab in sorted((REF / "labels").glob("*.txt")):
        img = REF / "images" / f"{lab.stem}.jpg"
        if not img.is_file():
            continue
        key = img.resolve().as_posix()
        if key in seen_img:
            continue
        seen_img.add(key)
        pairs.append((img, lab, read_box(lab)))
    return pairs


def hflip(img: np.ndarray, box: tuple[float, float, float, float]):
    cx, cy, bw, bh = box
    return cv2.flip(img, 1), (1.0 - cx, cy, bw, bh)


def brightness(img: np.ndarray, box, delta: float):
    out = img.astype(np.float32) * delta
    out = np.clip(out, 0, 255).astype(np.uint8)
    return out, box


def scale_crop(img: np.ndarray, box: tuple[float, float, float, float], scale: float, rng: random.Random):
    """Zoom in slightly, keep box in frame coords of output."""
    h, w = img.shape[:2]
    cx, cy, bw, bh = box
    # crop window centered near box center with jitter
    nw, nh = int(w / scale), int(h / scale)
    bcx, bcy = cx * w, cy * h
    x0 = int(bcx - nw / 2 + rng.uniform(-0.05, 0.05) * w)
    y0 = int(bcy - nh / 2 + rng.uniform(-0.05, 0.05) * h)
    x0 = max(0, min(w - nw, x0))
    y0 = max(0, min(h - nh, y0))
    crop = img[y0 : y0 + nh, x0 : x0 + nw]
    crop = cv2.resize(crop, (w, h), interpolation=cv2.INTER_LINEAR)
    # remap box
    x1 = (cx - bw / 2) * w
    x2 = (cx + bw / 2) * w
    y1 = (cy - bh / 2) * h
    y2 = (cy + bh / 2) * h
    x1 = (x1 - x0) / nw
    x2 = (x2 - x0) / nw
    y1 = (y1 - y0) / nh
    y2 = (y2 - y0) / nh
    # if box mostly outside, abort
    if x2 <= 0 or y2 <= 0 or x1 >= 1 or y1 >= 1:
        return None
    x1, x2 = max(0.0, x1), min(1.0, x2)
    y1, y2 = max(0.0, y1), min(1.0, y2)
    ncx, ncy = (x1 + x2) / 2, (y1 + y2) / 2
    nbw, nbh = x2 - x1, y2 - y1
    if nbw < 0.01 or nbh < 0.01:
        return None
    return crop, (ncx, ncy, nbw, nbh)


def pack(out: Path, items: list[tuple[str, np.ndarray, tuple]], val_frac: float, seed: int) -> Path:
    rng = random.Random(seed)
    rng.shuffle(items)
    n_val = max(1, int(round(len(items) * val_frac)))
    for split in ("train", "val"):
        (out / "images" / split).mkdir(parents=True, exist_ok=True)
        (out / "labels" / split).mkdir(parents=True, exist_ok=True)
        for p in (out / "images" / split).glob("*"):
            p.unlink()
        for p in (out / "labels" / split).glob("*"):
            p.unlink()

    for i, (name, img, box) in enumerate(items):
        split = "val" if i < n_val else "train"
        cv2.imwrite(str(out / "images" / split / f"{name}.jpg"), img, [int(cv2.IMWRITE_JPEG_QUALITY), 90])
        write_box(out / "labels" / split / f"{name}.txt", *box)

    (out / "data.yaml").write_text(
        "\n".join(
            [
                f"path: {out}",
                "train: images/train",
                "val: images/val",
                "names:",
                f"  {CLASS_ID}: {CLASS_NAME}",
                "",
            ]
        )
    )
    meta = {
        "n_total": len(items),
        "n_train": len(items) - n_val,
        "n_val": n_val,
        "class": CLASS_NAME,
        "augmented": True,
    }
    (out / "meta.json").write_text(json.dumps(meta, indent=2) + "\n")

    zip_path = out.parent / "poster_yolo_dataset.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in out.rglob("*"):
            if not path.is_file():
                continue
            rel = path.relative_to(out.parent)
            s = str(rel)
            if any(
                x in s
                for x in (
                    "frames/",
                    "preview",
                    "labels_raw",
                    "labels_all",
                    "labels_propagated",
                    "to_label",
                    "to_review",
                )
            ):
                continue
            if path.suffix in {".jpg", ".txt", ".yaml", ".json"} and (
                "images/train" in s
                or "images/val" in s
                or "labels/train" in s
                or "labels/val" in s
                or path.name in {"data.yaml", "meta.json"}
                or "reference/" in s
            ):
                zf.write(path, rel)
    print(f"train={meta['n_train']} val={meta['n_val']} total={meta['n_total']}")
    print(f"zip: {zip_path}")
    return zip_path


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--target", type=int, default=1000)
    ap.add_argument("--val-frac", type=float, default=0.15)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--out", type=Path, default=OUT)
    args = ap.parse_args()
    rng = random.Random(args.seed)

    pairs = load_pairs()
    if not pairs:
        raise SystemExit("no labeled pairs — run propagate / manual label first")
    print(f"base labeled pairs: {len(pairs)}")

    items: list[tuple[str, np.ndarray, tuple]] = []
    for img_path, _lab, box in pairs:
        img = cv2.imread(str(img_path))
        if img is None:
            continue
        stem = img_path.stem
        if stem[:2].isdigit() and len(stem) > 3 and stem[2] == "_":
            stem = stem[3:]
        items.append((f"{stem}_orig", img, box))

    ops = [
        ("hflip", lambda im, b, r: hflip(im, b)),
        ("bri085", lambda im, b, r: brightness(im, b, 0.85)),
        ("bri115", lambda im, b, r: brightness(im, b, 1.15)),
        ("bri070", lambda im, b, r: brightness(im, b, 0.70)),
        ("bri130", lambda im, b, r: brightness(im, b, 1.30)),
        ("zoom11", lambda im, b, r: scale_crop(im, b, 1.15, r)),
        ("zoom12", lambda im, b, r: scale_crop(im, b, 1.25, r)),
        ("zoom13", lambda im, b, r: scale_crop(im, b, 1.35, r)),
    ]

    # cycle augmentations until target
    base = list(items)
    op_i = 0
    while len(items) < args.target:
        img_path_box = base[len(items) % len(base)]
        # reload from original name
        name0, img0, box0 = img_path_box
        # apply cumulative? no — always from base original
        src = next(x for x in base if x[0] == name0) if False else None
        # pick corresponding base entry
        bi = (len(items) - len(base)) % len(base) if len(items) >= len(base) else len(items) % len(base)
        # simpler: iterate base × ops
        b_idx = (len(items) - len(base)) % len(base)
        o_idx = ((len(items) - len(base)) // len(base)) % len(ops)
        name0, img0, box0 = base[b_idx]
        op_name, op_fn = ops[o_idx]
        result = op_fn(img0, box0, rng)
        if result is None:
            # try next op
            items.append((f"{name0}_dup{len(items)}", img0.copy(), box0))  # fallback copy rare
            continue
        img1, box1 = result
        items.append((f"{name0}_{op_name}_{len(items)}", img1, box1))
        if len(items) % 200 == 0:
            print(f"  … {len(items)}/{args.target}")

    items = items[: args.target]
    pack(args.out, items, args.val_frac, args.seed)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
