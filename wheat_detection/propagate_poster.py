#!/usr/bin/env python3
"""Propagate wheat-poster boxes from hand-labeled references onto all frames.

Uses ORB feature matching: crop each reference box → match into unlabeled frames
→ take best homography / match cluster as the new box.

  python -m wheat_detection.propagate_poster
  python -m wheat_detection.propagate_poster --min-matches 12 --pack
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
REF = REPO / "data" / "poster_yolo" / "reference"
FRAMES = REPO / "data" / "poster_yolo" / "frames"
OUT = REPO / "data" / "poster_yolo"
CLASS_ID = 0
CLASS_NAME = "wheat_poster"


def read_yolo_box(label_path: Path, w: int, h: int) -> tuple[int, int, int, int]:
    parts = label_path.read_text().strip().split()
    _, cx, cy, bw, bh = (float(p) for p in parts)
    x1 = int(round((cx - bw / 2) * w))
    y1 = int(round((cy - bh / 2) * h))
    x2 = int(round((cx + bw / 2) * w))
    y2 = int(round((cy + bh / 2) * h))
    return max(0, x1), max(0, y1), min(w - 1, x2), min(h - 1, y2)


def to_yolo(x1: int, y1: int, x2: int, y2: int, w: int, h: int) -> str:
    cx = ((x1 + x2) / 2) / w
    cy = ((y1 + y2) / 2) / h
    bw = (x2 - x1) / w
    bh = (y2 - y1) / h
    return f"{CLASS_ID} {cx:.6f} {cy:.6f} {bw:.6f} {bh:.6f}\n"


def load_templates(ref: Path) -> list[dict]:
    orb = cv2.ORB_create(nfeatures=2000)
    templates = []
    for img_path in sorted((ref / "images").glob("*.jpg")):
        label_path = ref / "labels" / f"{img_path.stem}.txt"
        if not label_path.is_file():
            continue
        img = cv2.imread(str(img_path))
        if img is None:
            continue
        h, w = img.shape[:2]
        x1, y1, x2, y2 = read_yolo_box(label_path, w, h)
        if x2 - x1 < 16 or y2 - y1 < 16:
            continue
        crop = img[y1:y2, x1:x2]
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        kp, des = orb.detectAndCompute(gray, None)
        if des is None or len(kp) < 8:
            continue
        templates.append(
            {
                "name": img_path.stem,
                "crop": crop,
                "gray": gray,
                "kp": kp,
                "des": des,
                "size": (x2 - x1, y2 - y1),
            }
        )
    return templates


def match_box(
    frame_bgr: np.ndarray,
    templates: list[dict],
    orb: cv2.ORB,
    bf: cv2.BFMatcher,
    min_matches: int,
    ratio: float,
) -> tuple[int, int, int, int, float, str] | None:
    gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
    kp2, des2 = orb.detectAndCompute(gray, None)
    if des2 is None or len(kp2) < 8:
        return None
    h, w = frame_bgr.shape[:2]
    best = None  # (score, x1,y1,x2,y2, tname)

    for t in templates:
        matches = bf.knnMatch(t["des"], des2, k=2)
        good = []
        for pair in matches:
            if len(pair) < 2:
                continue
            m, n = pair
            if m.distance < ratio * n.distance:
                good.append(m)
        if len(good) < min_matches:
            continue

        src = np.float32([t["kp"][m.queryIdx].pt for m in good]).reshape(-1, 1, 2)
        dst = np.float32([kp2[m.trainIdx].pt for m in good]).reshape(-1, 1, 2)
        H, mask = cv2.findHomography(src, dst, cv2.RANSAC, 5.0)
        if H is None or mask is None:
            continue
        inliers = int(mask.sum())
        if inliers < min_matches:
            continue

        tw, th = t["size"]
        corners = np.float32([[0, 0], [tw, 0], [tw, th], [0, th]]).reshape(-1, 1, 2)
        projected = cv2.perspectiveTransform(corners, H).reshape(-1, 2)
        xs, ys = projected[:, 0], projected[:, 1]
        x1, x2 = int(np.clip(xs.min(), 0, w - 1)), int(np.clip(xs.max(), 0, w - 1))
        y1, y2 = int(np.clip(ys.min(), 0, h - 1)), int(np.clip(ys.max(), 0, h - 1))
        if x2 - x1 < 12 or y2 - y1 < 12:
            continue
        # reject absurd boxes (>95% of frame unless template was huge)
        area = (x2 - x1) * (y2 - y1) / (w * h)
        if area > 0.95 and (tw * th) / (w * h) < 0.5:
            continue
        score = inliers / max(len(good), 1)
        cand = (score * inliers, x1, y1, x2, y2, t["name"], score)
        if best is None or cand[0] > best[0]:
            best = cand

    if best is None:
        return None
    _, x1, y1, x2, y2, tname, score = best
    return x1, y1, x2, y2, float(score), tname


def pack_dataset(out: Path, labels_dir: Path, frames_dir: Path, val_frac: float, seed: int) -> Path:
    labeled = []
    for lab in sorted(labels_dir.glob("*.txt")):
        if not lab.read_text().strip():
            continue
        # frame may be named without NN_ prefix
        stem = lab.stem
        # try exact then strip leading index
        candidates = [frames_dir / f"{stem}.jpg"]
        if "_" in stem and stem.split("_", 1)[0].isdigit():
            candidates.append(frames_dir / f"{stem.split('_', 1)[1]}.jpg")
        # also search reference images
        candidates.append(REF / "images" / f"{stem}.jpg")
        img = next((c for c in candidates if c.is_file()), None)
        if img is None:
            # fuzzy: match by session_fNNNNNN
            parts = stem.split("session_", 1)
            if len(parts) == 2:
                key = "session_" + parts[1]
                hit = list(frames_dir.glob(f"*{key}"))
                # key already has .jpg? stem has no jpg
                hit = list(frames_dir.glob(f"*{key}.jpg")) if not key.endswith(".jpg") else hit
                if not hit:
                    # stem like 01_session_... → session_...
                    key2 = stem.split("_", 1)[-1] if stem[0:2].isdigit() else stem
                    hit = list(frames_dir.glob(f"{key2}.jpg"))
                    if not hit:
                        hit = list(frames_dir.glob(f"*{key2}"))
                img = hit[0] if hit else None
        if img is None:
            # last resort: reference folder
            img = REF / "images" / f"{stem}.jpg"
            if not img.is_file():
                continue
        labeled.append((img, lab))

    if not labeled:
        raise SystemExit("no labeled image/label pairs to pack")

    rng = random.Random(seed)
    rng.shuffle(labeled)
    n_val = max(1, int(round(len(labeled) * val_frac))) if len(labeled) > 4 else max(1, len(labeled) // 5)
    val = set(range(n_val))

    for split in ("train", "val"):
        (out / "images" / split).mkdir(parents=True, exist_ok=True)
        (out / "labels" / split).mkdir(parents=True, exist_ok=True)
        # clear old
        for p in (out / "images" / split).glob("*"):
            p.unlink()
        for p in (out / "labels" / split).glob("*"):
            p.unlink()

    for i, (img, lab) in enumerate(labeled):
        split = "val" if i in val else "train"
        dest_name = img.name if not img.name.startswith(("0", "1")) else img.name
        # normalize name: drop 01_ prefix for uniqueness
        if len(img.name) > 3 and img.name[2] == "_" and img.name[:2].isdigit():
            dest_name = img.name[3:]
        shutil.copy2(img, out / "images" / split / dest_name)
        (out / "labels" / split / f"{Path(dest_name).stem}.txt").write_text(lab.read_text())

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
    meta = {"n_total": len(labeled), "n_train": len(labeled) - n_val, "n_val": n_val, "class": CLASS_NAME}
    (out / "meta.json").write_text(json.dumps(meta, indent=2) + "\n")

    zip_path = out.parent / "poster_yolo_dataset.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in out.rglob("*"):
            if path.is_file() and path.suffix in {".jpg", ".txt", ".yaml", ".json"}:
                # skip frames/ bulk and preview noise optionally — include train/val + yaml
                rel = path.relative_to(out.parent)
                if "frames/" in str(rel) or "preview/" in str(rel) or "labels_raw/" in str(rel) or "to_label/" in str(rel):
                    continue
                if "images/train" in str(rel) or "images/val" in str(rel) or "labels/train" in str(rel) or "labels/val" in str(rel) or path.name in {"data.yaml", "meta.json"}:
                    zf.write(path, rel)
                elif "reference/" in str(rel):
                    zf.write(path, rel)
    print(f"train={meta['n_train']} val={meta['n_val']}")
    print(f"zip: {zip_path}")
    return zip_path


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--ref", type=Path, default=REF)
    p.add_argument("--frames", type=Path, default=FRAMES)
    p.add_argument("--out", type=Path, default=OUT)
    p.add_argument("--min-matches", type=int, default=10)
    p.add_argument("--ratio", type=float, default=0.75)
    p.add_argument("--pack", action="store_true", help="also build train/val + zip")
    p.add_argument("--pack-only", action="store_true")
    p.add_argument("--val-frac", type=float, default=0.2)
    p.add_argument("--seed", type=int, default=42)
    args = p.parse_args()

    labels_auto = args.out / "labels_propagated"
    preview = args.out / "preview_propagated"
    labels_auto.mkdir(parents=True, exist_ok=True)
    preview.mkdir(parents=True, exist_ok=True)

    # always include hand labels in a combined labels dir for packing
    labels_all = args.out / "labels_all"
    labels_all.mkdir(parents=True, exist_ok=True)

    if args.pack_only:
        # merge ref + propagated into labels_all first if empty
        for lab in (args.ref / "labels").glob("*.txt"):
            shutil.copy2(lab, labels_all / lab.name)
        for lab in labels_auto.glob("*.txt"):
            shutil.copy2(lab, labels_all / lab.name)
        pack_dataset(args.out, labels_all, args.frames, args.val_frac, args.seed)
        return 0

    templates = load_templates(args.ref)
    if not templates:
        raise SystemExit(f"no usable templates in {args.ref}")
    print(f"templates: {len(templates)}")

    # seed labels_all with hand labels (map to frame names)
    for lab in (args.ref / "labels").glob("*.txt"):
        stem = lab.stem
        frame_stem = stem.split("_", 1)[-1] if stem[:2].isdigit() and stem[2] == "_" else stem
        shutil.copy2(lab, labels_all / f"{frame_stem}.txt")
        # also copy image into frames if missing
        src_img = args.ref / "images" / f"{stem}.jpg"
        if src_img.is_file():
            dest = args.frames / f"{frame_stem}.jpg"
            if not dest.is_file():
                shutil.copy2(src_img, dest)

    orb = cv2.ORB_create(nfeatures=2500)
    bf = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=False)

    frames = sorted(args.frames.glob("*.jpg"))
    # skip frames that already have a hand label
    hand = {p.stem for p in labels_all.glob("*.txt")}
    ok = fail = 0
    for i, frame_path in enumerate(frames, 1):
        if frame_path.stem in hand:
            continue
        frame = cv2.imread(str(frame_path))
        if frame is None:
            continue
        hit = match_box(frame, templates, orb, bf, args.min_matches, args.ratio)
        if hit is None:
            fail += 1
            if i % 20 == 0:
                print(f"[{i}/{len(frames)}] … ok={ok} fail={fail}")
            continue
        x1, y1, x2, y2, score, tname = hit
        h, w = frame.shape[:2]
        (labels_auto / f"{frame_path.stem}.txt").write_text(to_yolo(x1, y1, x2, y2, w, h))
        (labels_all / f"{frame_path.stem}.txt").write_text(to_yolo(x1, y1, x2, y2, w, h))
        vis = frame.copy()
        cv2.rectangle(vis, (x1, y1), (x2, y2), (0, 255, 0), 2)
        cv2.putText(vis, f"{score:.2f}←{tname[:20]}", (x1, max(20, y1 - 6)), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 255, 0), 1)
        cv2.imwrite(str(preview / frame_path.name), vis)
        ok += 1
        if ok <= 5 or i % 20 == 0:
            print(f"[{i}/{len(frames)}] {frame_path.name} score={score:.2f} via {tname}")

    print(f"propagated={ok} failed={fail} hand={len(hand)}")
    if args.pack or ok or hand:
        pack_dataset(args.out, labels_all, args.frames, args.val_frac, args.seed)
    print(f"preview: {preview}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
