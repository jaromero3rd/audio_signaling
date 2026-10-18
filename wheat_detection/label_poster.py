#!/usr/bin/env python3
"""Label wheat-poster frames for YOLO training on another machine.

Flow:
  1. Sample frames from data/poster_capture/session_*/poster.mp4
  2. Draw one box per frame (Jev Box API, YOLO-World, or manual)
  3. Write Ultralytics YOLO layout + zip for USB/scp to the training desktop

Examples:
  # extract + YOLO-World auto-box (no API key)
  python -m wheat_detection.label_poster --backend yolo-world --every-sec 1.0

  # Jev 1.14 Box (needs TYPESAFE_API_KEY)
  export TYPESAFE_API_KEY=...
  python -m wheat_detection.label_poster --backend jev --every-sec 1.0

  # click-drag boxes yourself (terminal + OpenCV window)
  python -m wheat_detection.label_poster --backend manual --every-sec 2.0

  # pack existing labels only
  python -m wheat_detection.label_poster --pack-only
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import random
import shutil
import uuid
import zipfile
from pathlib import Path

import cv2
import numpy as np
import requests

REPO = Path(__file__).resolve().parents[1]
CAPTURE = REPO / "data" / "poster_capture"
OUT = REPO / "data" / "poster_yolo"
CLASS_NAME = "wheat_poster"
CLASS_ID = 0


def _load_dotenv() -> None:
    env = REPO / ".env"
    if not env.is_file():
        return
    for line in env.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip("'").strip('"')
        os.environ.setdefault(key, val)


def find_videos(root: Path) -> list[Path]:
    vids = sorted(root.glob("session_*/poster.mp4"))
    vids += sorted(root.glob("session_*/poster.avi"))
    return vids


def extract_frames(videos: list[Path], frames_dir: Path, every_sec: float) -> list[Path]:
    frames_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for video in videos:
        session = video.parent.name
        cap = cv2.VideoCapture(str(video))
        if not cap.isOpened():
            print(f"skip unreadable {video}")
            continue
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 15.0)
        step = max(1, int(round(fps * every_sec)))
        idx = 0
        saved = 0
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            if idx % step == 0:
                name = f"{session}_f{idx:06d}.jpg"
                path = frames_dir / name
                cv2.imwrite(str(path), frame, [int(cv2.IMWRITE_JPEG_QUALITY), 92])
                written.append(path)
                saved += 1
            idx += 1
        cap.release()
        print(f"{session}: {saved} frames @ every {every_sec}s (fps={fps:.1f})")
    return written


def edges_to_yolo(left: float, top: float, right: float, bottom: float) -> str:
    left = min(1.0, max(0.0, left))
    right = min(1.0, max(0.0, right))
    top = min(1.0, max(0.0, top))
    bottom = min(1.0, max(0.0, bottom))
    if right <= left or bottom <= top:
        return ""
    cx = (left + right) / 2.0
    cy = (top + bottom) / 2.0
    w = right - left
    h = bottom - top
    return f"{CLASS_ID} {cx:.6f} {cy:.6f} {w:.6f} {h:.6f}\n"


def xyxy_to_edges(x1: int, y1: int, x2: int, y2: int, width: int, height: int) -> tuple[float, float, float, float]:
    return x1 / width, y1 / height, x2 / width, y2 / height


def draw_preview(img: np.ndarray, left: float, top: float, right: float, bottom: float, conf: float) -> np.ndarray:
    h, w = img.shape[:2]
    x1, y1 = int(left * w), int(top * h)
    x2, y2 = int(right * w), int(bottom * h)
    out = img.copy()
    cv2.rectangle(out, (x1, y1), (x2, y2), (0, 220, 0), 2)
    cv2.putText(out, f"{CLASS_NAME} {conf:.2f}", (x1, max(20, y1 - 8)), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 220, 0), 1)
    return out


# --- Jev (TypeSafe) ---------------------------------------------------------

def jev_box(image_path: Path, api_key: str, model: str, min_presence: float) -> tuple[float, float, float, float, float] | None:
    """Return (left, top, right, bottom, conf) or None if poster not present."""
    raw = image_path.read_bytes()
    # Jev rejects images under 280px on a side; pad if needed.
    arr = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
    if arr is None:
        return None
    h, w = arr.shape[:2]
    if min(h, w) < 280:
        scale = 280 / min(h, w)
        arr = cv2.resize(arr, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_CUBIC)
        ok, buf = cv2.imencode(".jpg", arr, [int(cv2.IMWRITE_JPEG_QUALITY), 90])
        if not ok:
            return None
        raw = buf.tobytes()

    ref = str(uuid.uuid4())
    payload = {
        "model": model,
        "state": {"photo": ref},
        "assets": {
            ref: {
                "type": "image",
                "format": "jpg",
                "data_base64": base64.b64encode(raw).decode("ascii"),
            }
        },
        "questions": {
            "poster_visible": {
                "type": "noul",
                "instructions": (
                    "Is there a large printed wheat-field poster (a photo print of golden wheat "
                    "heads / crop field, typically rectangular paper or board) visible in `photo`?"
                ),
            },
            "poster": {
                "type": "box",
                "instructions": (
                    "the large wheat-field poster (printed photo of wheat crop) in `photo`"
                ),
            },
        },
    }
    res = requests.post(
        "https://api.typesafe.ai/v1/systemone",
        headers={
            "Authorization": f"Bearer {api_key}",
            "TypeSafe-Danger-Zone-Images": "true",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=120,
    )
    res.raise_for_status()
    answers = res.json().get("answers") or {}
    presence = float((answers.get("poster_visible") or {}).get("noul") or 0.0)
    box = answers.get("poster") or {}
    edges = box.get("box") or {}
    conf = float(box.get("confidence") or 0.0)
    if presence < min_presence:
        return None
    try:
        left, top, right, bottom = (
            float(edges["left"]),
            float(edges["top"]),
            float(edges["right"]),
            float(edges["bottom"]),
        )
    except (KeyError, TypeError, ValueError):
        return None
    return left, top, right, bottom, conf


# --- YOLO-World open-vocab ---------------------------------------------------

def yolo_world_box(model, image_path: Path, conf_min: float) -> tuple[float, float, float, float, float] | None:
    results = model.predict(str(image_path), conf=conf_min, verbose=False)
    if not results:
        return None
    r0 = results[0]
    if r0.boxes is None or len(r0.boxes) == 0:
        return None
    # pick highest-confidence detection among the prompt classes
    best_i = int(r0.boxes.conf.argmax().item())
    xyxy = r0.boxes.xyxy[best_i].cpu().numpy()
    conf = float(r0.boxes.conf[best_i].item())
    h, w = r0.orig_shape
    left, top, right, bottom = xyxy_to_edges(int(xyxy[0]), int(xyxy[1]), int(xyxy[2]), int(xyxy[3]), w, h)
    return left, top, right, bottom, conf


# --- Manual click-drag ------------------------------------------------------

class _DragState:
    def __init__(self) -> None:
        self.drawing = False
        self.x0 = self.y0 = self.x1 = self.y1 = 0
        self.done = False

    def on_mouse(self, event, x, y, _flags, _param) -> None:
        if event == cv2.EVENT_LBUTTONDOWN:
            self.drawing = True
            self.done = False
            self.x0 = self.x1 = x
            self.y0 = self.y1 = y
        elif event == cv2.EVENT_MOUSEMOVE and self.drawing:
            self.x1, self.y1 = x, y
        elif event == cv2.EVENT_LBUTTONUP:
            self.drawing = False
            self.x1, self.y1 = x, y
            self.done = True


def manual_box(image_path: Path, prev: tuple[float, float, float, float] | None) -> tuple[float, float, float, float, float] | None:
    img = cv2.imread(str(image_path))
    if img is None:
        return None
    h, w = img.shape[:2]
    state = _DragState()
    win = "label poster — drag box, ENTER=save, c=copy prev, s=skip, q=quit"
    cv2.namedWindow(win, cv2.WINDOW_NORMAL)
    cv2.setMouseCallback(win, state.on_mouse)
    while True:
        vis = img.copy()
        if prev is not None:
            pl, pt, pr, pb = prev
            cv2.rectangle(
                vis,
                (int(pl * w), int(pt * h)),
                (int(pr * w), int(pb * h)),
                (180, 180, 0),
                1,
            )
        if state.drawing or state.done:
            cv2.rectangle(vis, (state.x0, state.y0), (state.x1, state.y1), (0, 255, 0), 2)
        cv2.putText(
            vis,
            "drag box | ENTER save | c copy prev | s skip | q quit",
            (8, 20),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.5,
            (255, 255, 255),
            1,
        )
        cv2.imshow(win, vis)
        key = cv2.waitKey(30) & 0xFF
        if key in (13, 10):  # enter
            if not state.done and prev is None:
                continue
            if state.done:
                x1, x2 = sorted((state.x0, state.x1))
                y1, y2 = sorted((state.y0, state.y1))
                if x2 - x1 < 4 or y2 - y1 < 4:
                    continue
                return (*xyxy_to_edges(x1, y1, x2, y2, w, h), 1.0)
        if key == ord("c") and prev is not None:
            return (*prev, 1.0)
        if key == ord("s"):
            return None
        if key == ord("q"):
            raise KeyboardInterrupt
    return None


def write_data_yaml(out: Path) -> None:
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


def split_and_pack(frames_dir: Path, labels_raw: Path, out: Path, val_frac: float, seed: int) -> Path:
    images = sorted(frames_dir.glob("*.jpg"))
    labeled = [p for p in images if (labels_raw / f"{p.stem}.txt").is_file() and (labels_raw / f"{p.stem}.txt").read_text().strip()]
    if not labeled:
        raise SystemExit("no labeled frames yet — run a labeling backend first")

    rng = random.Random(seed)
    rng.shuffle(labeled)
    n_val = max(1, int(round(len(labeled) * val_frac))) if len(labeled) > 1 else 0
    val_set = set(labeled[:n_val])
    train_set = [p for p in labeled if p not in val_set]

    for split in ("train", "val"):
        (out / "images" / split).mkdir(parents=True, exist_ok=True)
        (out / "labels" / split).mkdir(parents=True, exist_ok=True)

    def copy_split(paths: list[Path], split: str) -> None:
        for src in paths:
            shutil.copy2(src, out / "images" / split / src.name)
            shutil.copy2(labels_raw / f"{src.stem}.txt", out / "labels" / split / f"{src.stem}.txt")

    copy_split(train_set, "train")
    copy_split(list(val_set), "val")
    write_data_yaml(out)

    meta = {
        "class": CLASS_NAME,
        "n_train": len(train_set),
        "n_val": len(val_set),
        "frames_dir": str(frames_dir),
    }
    (out / "meta.json").write_text(json.dumps(meta, indent=2) + "\n")

    zip_path = out.parent / "poster_yolo_dataset.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in out.rglob("*"):
            if path.is_file():
                zf.write(path, path.relative_to(out.parent))
    print(f"train={len(train_set)} val={len(val_set)}")
    print(f"dataset: {out}")
    print(f"zip:     {zip_path}")
    return zip_path


def main() -> int:
    _load_dotenv()
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--capture", type=Path, default=CAPTURE)
    p.add_argument("--out", type=Path, default=OUT)
    p.add_argument("--every-sec", type=float, default=1.0, help="sample one frame every N seconds")
    p.add_argument("--backend", choices=("jev", "yolo-world", "manual", "extract"), default="yolo-world")
    p.add_argument("--model", default="jev-1.14.0", help="TypeSafe model for --backend jev")
    p.add_argument("--min-presence", type=float, default=0.55, help="Jev noul threshold")
    p.add_argument("--conf", type=float, default=0.15, help="YOLO-World conf floor")
    p.add_argument("--prompt", default="wheat field poster, crop poster, agricultural poster")
    p.add_argument("--val-frac", type=float, default=0.2)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--limit", type=int, default=0, help="label at most N frames (0=all)")
    p.add_argument("--pack-only", action="store_true")
    p.add_argument("--skip-extract", action="store_true")
    args = p.parse_args()

    frames_dir = args.out / "frames"
    labels_raw = args.out / "labels_raw"
    preview_dir = args.out / "preview"
    labels_raw.mkdir(parents=True, exist_ok=True)
    preview_dir.mkdir(parents=True, exist_ok=True)

    if args.pack_only:
        split_and_pack(frames_dir, labels_raw, args.out, args.val_frac, args.seed)
        return 0

    if not args.skip_extract:
        videos = find_videos(args.capture)
        if not videos:
            raise SystemExit(f"no poster.mp4 under {args.capture}")
        extract_frames(videos, frames_dir, args.every_sec)

    frames = sorted(frames_dir.glob("*.jpg"))
    if args.limit > 0:
        frames = frames[: args.limit]
    if not frames:
        raise SystemExit(f"no frames in {frames_dir}")

    if args.backend == "extract":
        print(f"extracted {len(frames)} frames → {frames_dir}")
        return 0

    yw_model = None
    if args.backend == "yolo-world":
        from ultralytics import YOLO

        yw_model = YOLO("yolov8s-world.pt")
        yw_model.set_classes([c.strip() for c in args.prompt.split(",") if c.strip()])

    api_key = os.environ.get("TYPESAFE_API_KEY", "").strip()
    if args.backend == "jev" and not api_key:
        raise SystemExit("TYPESAFE_API_KEY missing — put it in .env or export it")

    prev_box: tuple[float, float, float, float] | None = None
    ok_n = skip_n = 0
    try:
        for i, frame in enumerate(frames, 1):
            label_path = labels_raw / f"{frame.stem}.txt"
            if label_path.is_file() and label_path.read_text().strip():
                print(f"[{i}/{len(frames)}] skip existing {frame.name}")
                continue

            box = None
            if args.backend == "jev":
                box = jev_box(frame, api_key, args.model, args.min_presence)
            elif args.backend == "yolo-world":
                box = yolo_world_box(yw_model, frame, args.conf)
            elif args.backend == "manual":
                box = manual_box(frame, prev_box)

            if box is None:
                skip_n += 1
                print(f"[{i}/{len(frames)}] no box {frame.name}")
                continue

            left, top, right, bottom, conf = box
            line = edges_to_yolo(left, top, right, bottom)
            if not line:
                skip_n += 1
                continue
            label_path.write_text(line)
            prev_box = (left, top, right, bottom)
            img = cv2.imread(str(frame))
            if img is not None:
                cv2.imwrite(str(preview_dir / frame.name), draw_preview(img, left, top, right, bottom, conf))
            ok_n += 1
            print(f"[{i}/{len(frames)}] labeled {frame.name} conf={conf:.2f}")
    except KeyboardInterrupt:
        print("stopped early")
    finally:
        if args.backend == "manual":
            cv2.destroyAllWindows()

    print(f"labeled={ok_n} skipped={skip_n}")
    if ok_n:
        split_and_pack(frames_dir, labels_raw, args.out, args.val_frac, args.seed)
        print("On training desktop:")
        print("  unzip poster_yolo_dataset.zip")
        print("  yolo detect train data=data/poster_yolo/data.yaml model=yolov8s.pt epochs=50 imgsz=640")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
