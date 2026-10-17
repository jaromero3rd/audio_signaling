#!/usr/bin/env python3
"""Live Tello (or webcam) loop: YOLOv8 wheat heads → yaw to center, creep forward.

Keys (focus the OpenCV window):
  t  takeoff
  g  toggle guidance (RC only while flying + guidance on)
  l  land
  space  emergency stop
  p  save a still to captures/
  q / Esc  hover, land if flying, quit
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from drone_interface.tello_client import MockDrone, TelloDrone
from ground_station.vision import DEFAULT_WEIGHTS, Box, make_detector, union_boxes

FRAME_STALE_S = 0.5


def clip_cmd(value: float, limit: int) -> int:
    return int(np.clip(round(value), -limit, limit))


def yaw_forward(
    box: Box | None,
    frame_shape: tuple[int, ...],
    *,
    kp_yaw: float,
    kp_fwd: float,
    max_yaw: int,
    max_fwd: int,
    deadband: float,
    target_area: float,
) -> tuple[int, int, float | None, float | None]:
    """Turn first, then approach. Returns (yaw, forward, err_x, area_frac)."""
    if box is None:
        return 0, 0, None, None
    fw = frame_shape[1]
    err_x = (box.cx / fw) - 0.5
    area = box.area_frac(frame_shape)
    yaw = clip_cmd(kp_yaw * err_x, max_yaw)
    if abs(err_x) > deadband:
        return yaw, 0, err_x, area
    gap = target_area - area
    if gap <= 0:
        return 0, 0, err_x, area
    fwd = clip_cmd(kp_fwd * gap, max_fwd)
    return 0, max(fwd, 0), err_x, area


def draw_hud(
    frame: np.ndarray,
    box: Box | None,
    heads: list[Box],
    err_x: float | None,
    area: float | None,
    yaw: int,
    fwd: int,
    flying: bool,
    guidance: bool,
    battery: int | None,
    stale: bool,
) -> np.ndarray:
    vis = frame.copy()
    h, w = vis.shape[:2]
    cv2.line(vis, (w // 2, 0), (w // 2, h), (80, 80, 80), 1)
    for head in heads:
        cv2.rectangle(
            vis, (head.x, head.y), (head.x + head.w, head.y + head.h), (0, 200, 0), 1
        )
    if box is not None:
        cv2.rectangle(vis, (box.x, box.y), (box.x + box.w, box.y + box.h), (0, 255, 255), 2)
        cv2.circle(vis, (int(box.cx), int(box.cy)), 5, (0, 255, 255), -1)
        cv2.putText(
            vis,
            f"{len(heads)} heads  {box.conf:.2f}",
            (box.x, max(20, box.y - 8)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.5,
            (0, 255, 255),
            1,
            cv2.LINE_AA,
        )
    mode = "FLY" if flying else "GROUND"
    if flying and guidance:
        mode += "+GUIDE"
    lines = [
        mode,
        f"yaw={yaw:+d}  fwd={fwd:+d}",
        f"err_x={err_x:+.2f}" if err_x is not None else "no target",
        f"area={area:.3f}" if area is not None else "",
        f"bat={battery}%" if battery is not None else "",
        "STALE FRAME" if stale else "",
        "t takeoff  g guide  l land  space e-stop  p photo  q quit",
    ]
    y = 22
    for line in lines:
        if not line:
            continue
        cv2.putText(
            vis, line, (10, y), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 2, cv2.LINE_AA
        )
        cv2.putText(
            vis, line, (10, y), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (20, 20, 20), 1, cv2.LINE_AA
        )
        y += 22
    return vis


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Tello visual servoing toward trained YOLOv8 wheat heads.")
    p.add_argument("--mock", action="store_true", help="use webcam; print RC instead of flying")
    p.add_argument("--camera", type=int, default=0, help="webcam index for --mock")
    p.add_argument("--detector", choices=("yolo", "hsv"), default="yolo")
    p.add_argument(
        "--weights",
        type=Path,
        default=DEFAULT_WEIGHTS,
        help="YOLOv8 .pt from train_wheat.py (class 0 = wheat_head)",
    )
    p.add_argument("--conf", type=float, default=0.25, help="YOLO confidence threshold")
    p.add_argument("--kp-yaw", type=float, default=160.0)
    p.add_argument("--kp-fwd", type=float, default=80.0)
    p.add_argument("--max-yaw", type=int, default=40)
    p.add_argument("--max-fwd", type=int, default=20)
    p.add_argument("--deadband", type=float, default=0.08, help="|err_x| below this counts as centered")
    p.add_argument("--target-area", type=float, default=0.18, help="stop approaching once box fills this fraction")
    p.add_argument("--hz", type=float, default=12.0)
    return p.parse_args()


def main() -> int:
    args = parse_args()
    drone = MockDrone(args.camera) if args.mock else TelloDrone()
    detector = make_detector(args.detector, weights=args.weights, conf=args.conf)
    drone.connect()
    drone.stream_on()

    flying = False
    guidance = False
    last_ok = time.time()
    bat = drone.battery()
    bat_ts = 0.0
    period = 1.0 / max(args.hz, 1.0)

    print("Window focused: t takeoff, g guidance, l land, space e-stop, q quit")
    try:
        while True:
            t0 = time.time()
            frame = drone.get_frame()
            stale = False
            if frame is None:
                stale = True
            else:
                last_ok = t0

            if t0 - last_ok > FRAME_STALE_S:
                stale = True
                if flying:
                    drone.hover()
                    guidance = False

            heads: list[Box] = detector.detect_all(frame) if frame is not None else []
            box = union_boxes(heads)
            yaw, fwd, err_x, area = (0, 0, None, None)
            if frame is not None:
                yaw, fwd, err_x, area = yaw_forward(
                    box,
                    frame.shape,
                    kp_yaw=args.kp_yaw,
                    kp_fwd=args.kp_fwd,
                    max_yaw=args.max_yaw,
                    max_fwd=args.max_fwd,
                    deadband=args.deadband,
                    target_area=args.target_area,
                )

            if flying:
                if stale or not guidance:
                    drone.hover()
                    yaw, fwd = 0, 0
                else:
                    drone.send_rc(0, fwd, 0, yaw)

            if t0 - bat_ts > 5:
                bat = drone.battery()
                bat_ts = t0

            vis = (
                draw_hud(frame, box, heads, err_x, area, yaw, fwd, flying, guidance, bat, stale)
                if frame is not None
                else np.zeros((360, 640, 3), dtype=np.uint8)
            )
            cv2.imshow("agridrone steer", vis)
            key = cv2.waitKey(1) & 0xFF
            if key in (ord("q"), 27):
                break
            if key == ord("t") and not flying:
                drone.takeoff()
                flying = True
                drone.hover()
            elif key == ord("g"):
                guidance = not guidance
                print(f"guidance={'on' if guidance else 'off'}")
                if flying and not guidance:
                    drone.hover()
            elif key == ord("l") and flying:
                guidance = False
                drone.land()
                flying = False
            elif key == ord(" "):
                guidance = False
                drone.emergency()
                flying = False
            elif key == ord("p") and frame is not None:
                path = drone.capture_photo()
                print(f"saved {path}")

            dt = time.time() - t0
            if dt < period:
                time.sleep(period - dt)
    except KeyboardInterrupt:
        print()
    finally:
        if flying:
            try:
                drone.land()
            except Exception:
                drone.emergency()
        drone.close()
        cv2.destroyAllWindows()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
