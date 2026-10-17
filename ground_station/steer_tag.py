#!/usr/bin/env python3
"""Ryze Tello: drive toward a printed AprilTag (stand-in while YOLO trains).

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
from ground_station.apriltag import FAMILIES, AprilTagDetector
from ground_station.control import width_frac_for_distance, yaw_forward
from ground_station.mission import CLOSE_FEET_DEFAULT, TAG_SIZE_M_DEFAULT
from ground_station.vision import Box

FRAME_STALE_S = 0.5


def draw_hud(
    frame: np.ndarray,
    box: Box | None,
    err_x: float | None,
    area: float | None,
    yaw: int,
    fwd: int,
    up: int,
    lr: int,
    flying: bool,
    guidance: bool,
    battery: int | None,
    stale: bool,
) -> np.ndarray:
    vis = frame.copy()
    h, w = vis.shape[:2]
    cv2.line(vis, (w // 2, 0), (w // 2, h), (80, 80, 80), 1)
    cv2.line(vis, (0, h // 2), (w, h // 2), (80, 80, 80), 1)
    if box is not None:
        cv2.rectangle(vis, (box.x, box.y), (box.x + box.w, box.y + box.h), (0, 255, 255), 2)
        cv2.circle(vis, (int(box.cx), int(box.cy)), 5, (0, 255, 255), -1)
        cv2.putText(
            vis,
            box.label,
            (box.x, max(20, box.y - 8)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.6,
            (0, 255, 255),
            2,
            cv2.LINE_AA,
        )
    mode = "FLY" if flying else "GROUND"
    if flying and guidance:
        mode += "+GUIDE"
    lines = [
        mode,
        f"yaw={yaw:+d}  fwd={fwd:+d}  up={up:+d}  lr={lr:+d}",
        f"err_x={err_x:+.2f}" if err_x is not None else "no tag",
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
    p = argparse.ArgumentParser(description="Ryze Tello visual servoing toward an AprilTag.")
    p.add_argument("--mock", action="store_true", help="use webcam; print RC instead of flying")
    p.add_argument("--camera", type=int, default=0, help="webcam index for --mock")
    p.add_argument("--family", choices=sorted(FAMILIES), default="36h11")
    p.add_argument(
        "--tag-id",
        type=int,
        default=12,
        help="lock onto this id (poster is tag 12); -1 = any tag in the family",
    )
    p.add_argument("--kp-yaw", type=float, default=160.0)
    p.add_argument("--kp-fwd", type=float, default=80.0)
    p.add_argument("--kp-up", type=float, default=120.0)
    p.add_argument("--max-yaw", type=int, default=40)
    p.add_argument("--max-fwd", type=int, default=20)
    p.add_argument("--max-up", type=int, default=20)
    p.add_argument("--deadband", type=float, default=0.08)
    p.add_argument(
        "--target-area",
        type=float,
        default=0.12,
        help="unused when using tag width; kept for compatibility",
    )
    p.add_argument("--close-feet", type=float, default=CLOSE_FEET_DEFAULT)
    p.add_argument("--tag-size-m", type=float, default=TAG_SIZE_M_DEFAULT)
    p.add_argument("--hz", type=float, default=12.0)
    p.add_argument(
        "--tello-iface",
        default=None,
        help="USB dongle interface (e.g. en8). Default: auto-detect 192.168.10.x",
    )
    return p.parse_args()


def main() -> int:
    args = parse_args()
    tag_id = None if args.tag_id < 0 else args.tag_id
    detector = AprilTagDetector(family=args.family, tag_id=tag_id)
    drone = MockDrone(args.camera) if args.mock else TelloDrone(iface=args.tello_iface)
    drone.connect()
    drone.stream_on()

    flying = False
    guidance = False
    last_ok = time.time()
    bat = drone.battery()
    bat_ts = 0.0
    period = 1.0 / max(args.hz, 1.0)

    print(
        f"AprilTag family={args.family} id={'any' if tag_id is None else tag_id}"
    )
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

            box = detector.detect(frame) if frame is not None else None
            yaw = fwd = up = lr = 0
            err_x = area = None
            if frame is not None:
                need_w = width_frac_for_distance(
                    args.tag_size_m, args.close_feet * 0.3048, frame.shape
                )
                rc = yaw_forward(
                    box,
                    frame.shape,
                    kp_yaw=args.kp_yaw,
                    kp_fwd=args.kp_fwd,
                    max_yaw=args.max_yaw,
                    max_fwd=args.max_fwd,
                    deadband=args.deadband,
                    target_area=args.target_area,
                    kp_up=args.kp_up,
                    max_up=args.max_up,
                    target_width=need_w,
                )
                yaw, fwd, up, lr, err_x, area = rc

            if flying:
                if stale or not guidance:
                    drone.hover()
                    yaw = fwd = up = lr = 0
                else:
                    drone.send_rc(lr, fwd, up, yaw)

            if t0 - bat_ts > 5:
                bat = drone.battery()
                bat_ts = t0

            vis = (
                draw_hud(
                    frame, box, err_x, area, yaw, fwd, up, lr, flying, guidance, bat, stale
                )
                if frame is not None
                else np.zeros((360, 640, 3), dtype=np.uint8)
            )
            cv2.imshow("agridrone apriltag", vis)
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
