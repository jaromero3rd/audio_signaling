#!/usr/bin/env python3
"""Write a printable AprilTag PNG (default family 36h11, id 0)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ground_station.apriltag import FAMILIES, dictionary_for


def main() -> int:
    p = argparse.ArgumentParser(description="Generate a printable AprilTag PNG")
    p.add_argument("--family", choices=sorted(FAMILIES), default="36h11")
    p.add_argument("--id", type=int, default=0)
    p.add_argument("--size", type=int, default=600, help="pixel size of the marker")
    p.add_argument("--out", type=Path, default=None)
    args = p.parse_args()

    dictionary = dictionary_for(args.family)
    if args.id < 0 or args.id >= dictionary.bytesList.shape[0]:
        print(
            f"id {args.id} out of range for {args.family} "
            f"(0..{dictionary.bytesList.shape[0] - 1})",
            file=sys.stderr,
        )
        return 1

    tag = np.zeros((args.size, args.size), dtype=np.uint8)
    cv2.aruco.generateImageMarker(dictionary, args.id, args.size, tag, 1)
    # White margin so the black border stays detectable after printing.
    pad = args.size // 8
    canvas = np.full((args.size + 2 * pad, args.size + 2 * pad), 255, dtype=np.uint8)
    canvas[pad : pad + args.size, pad : pad + args.size] = tag

    out = args.out or ROOT / "tags" / f"apriltag_{args.family}_{args.id}.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out), canvas)
    print(f"wrote {out}")
    print("Print large (10–20 cm), flat, matte, well lit. Same --family/--id in steer_tag.py.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
