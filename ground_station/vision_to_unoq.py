#!/usr/bin/env python3
"""Send a compact drone-camera vision summary to the UNO Q.

The Tello camera is processed on this laptop (OpenCV / YOLO / Grok).
The UNO Q should NOT get raw video — only a small JSON hint it can fuse
with DHT11 + photoresistor readings.

Example:
  python -m ground_station.vision_to_unoq --host 10.250.75.148 \\
      --label wilt --confidence 0.81 --wilt
"""

from __future__ import annotations

import argparse
import json
import urllib.request


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="POST vision summary to UNO Q")
    p.add_argument("--host", required=True, help="UNO Q IP on Wi-Fi")
    p.add_argument("--port", type=int, default=8766)
    p.add_argument("--label", default="wheat")
    p.add_argument("--confidence", type=float, default=0.7)
    p.add_argument("--wilt", action="store_true")
    p.add_argument("--yellowing", action="store_true")
    args = p.parse_args(argv)

    payload = {
        "crop_label": args.label,
        "label": args.label,
        "confidence": args.confidence,
        "wilt": args.wilt,
        "yellowing": args.yellowing,
        "source": "ground_station",
    }
    url = f"http://{args.host}:{args.port}/vision"
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}, method="POST"
    )
    with urllib.request.urlopen(req, timeout=3) as resp:
        print(resp.read().decode("utf-8", errors="replace"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
