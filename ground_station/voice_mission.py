#!/usr/bin/env python3
"""Demo mission: Grok Voice on laptop Wi-Fi, Tello on a USB Wi-Fi dongle.

One process. No takeoff/land keys. Speak “survey the wheat”, or pass --auto
to start as soon as Grok and the Tello stream are up.

Space aborts. q quits.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from drone_interface.tello_client import MockDrone, TelloDrone
from ground_station.agent import DroidSession
from ground_station.apriltag import FAMILIES
from ground_station.envutil import load_dotenv
from ground_station.grok_image import analyze_photo_with_grok_async
from ground_station.mission import CLOSE_FEET_DEFAULT, FRAME_STALE_S, Gains, TAG_SIZE_M_DEFAULT, SurveyMission
from ground_station.overlay import draw_hud_lines
from ground_station.vision import DEFAULT_WEIGHTS, Box

GROK_READY_S = 20.0


def draw_hud(
    frame: np.ndarray,
    mission: SurveyMission,
    box: Box | None,
    err_x: float | None,
    yaw: int,
    fwd: int,
    up: int,
    lr: int,
    stale: bool,
    battery: int | None,
    tag_w: float | None = None,
    need_w: float | None = None,
) -> np.ndarray:
    vis = frame.copy()
    h, w = vis.shape[:2]
    cv2.line(vis, (w // 2, 170), (w // 2, h), (60, 60, 60), 1)
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
    snap = mission.snapshot()
    lines = [
        f"PHASE {snap['phase']}",
        f"target={snap['target']}  home=tag-{snap['home_tag']}",
        f"yaw={yaw:+d} fwd={fwd:+d} up={up:+d} lr={lr:+d}",
        f"err_x={err_x:+.2f}" if err_x is not None else "no box",
        f"size={tag_w:.3f} need={need_w:.3f}"
        if tag_w is not None and need_w is not None
        else "",
        f"bat={battery}%" if battery is not None else "",
        "STALE" if stale else "",
        "say survey the wheat  |  space abort  |  q quit",
    ]
    draw_hud_lines(vis, lines)
    return vis


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Grok on laptop Wi-Fi; Tello on USB Wi-Fi dongle."
    )
    p.add_argument("--mock", action="store_true")
    p.add_argument(
        "--tello-iface",
        default=None,
        help="USB dongle interface (e.g. en8). Default: auto-detect 192.168.10.x",
    )
    p.add_argument("--camera", type=int, default=0)
    p.add_argument("--family", choices=sorted(FAMILIES), default="36h11")
    p.add_argument("--home-tag", type=int, default=12)
    p.add_argument(
        "--target",
        choices=("auto", "wheat", "tag"),
        default="auto",
        help="wheat = heads (YOLO or HSV). tag = AprilTag. Home is always --home-tag.",
    )
    p.add_argument("--target-tag", type=int, default=12)
    p.add_argument("--weights", type=Path, default=DEFAULT_WEIGHTS)
    p.add_argument(
        "--auto",
        action="store_true",
        help="start takeoff→survey→home→land as soon as Grok + stream are ready",
    )
    p.add_argument("--no-voice", action="store_true", help="skip Grok (requires --auto)")
    p.add_argument("--hz", type=float, default=12.0)
    p.add_argument("--target-area", type=float, default=0.12)
    p.add_argument(
        "--wheat-close-frac",
        type=float,
        default=0.015,
        help="wheat is close enough when typical head box / image area reaches this (default 0.015)",
    )
    p.add_argument(
        "--close-feet",
        type=float,
        default=CLOSE_FEET_DEFAULT,
        help="do not mark the target done until about this close (AprilTag width in frame)",
    )
    p.add_argument(
        "--tag-size-m",
        type=float,
        default=TAG_SIZE_M_DEFAULT,
        help="printed tag width in meters (default 0.20). Bigger print → stop farther away",
    )
    return p.parse_args()


def main() -> int:
    load_dotenv()
    args = parse_args()
    if args.no_voice and not args.auto:
        print("`--no-voice` needs `--auto` so the survey still starts.")
        return 1

    if args.mock:
        print("Computer test: webcam. Target=wheat, home=AprilTag "
              f"{args.home_tag}. Point cam at wheat, then at tag {args.home_tag}.")
    else:
        print("Network: built-in Wi-Fi → internet/Grok; USB dongle → Tello.")
    gains = Gains(
        target_area=args.target_area,
        wheat_close_frac=args.wheat_close_frac,
        tag_size_m=args.tag_size_m,
        close_m=args.close_feet * 0.3048,
    )
    target_tag = None if args.target_tag < 0 else args.target_tag
    voice = None

    def on_phase(prev: str, phase: str) -> None:
        print(f"mission {prev} → {phase}")
        if voice is not None:
            voice.notify(f"phase is now {phase}")

    mission = SurveyMission(
        target_kind=args.target,
        family=args.family,
        target_tag=target_tag,
        home_tag=args.home_tag,
        gains=gains,
        weights=args.weights,
        on_phase=on_phase,
    )

    droid = DroidSession()
    print("Droid session initialized.")

    drone = MockDrone(args.camera) if args.mock else TelloDrone(iface=args.tello_iface)
    drone.connect()
    drone.stream_on()
    print("Tello stream up.")

    if not args.no_voice:
        if not os.environ.get("XAI_API_KEY"):
            print("Missing XAI_API_KEY. Put it in .env or export it.")
            droid.close()
            return 1
        from ground_station.voice_grok import start_voice_thread

        voice = start_voice_thread(mission)
        if not voice.ready.wait(GROK_READY_S):
            print(f"Grok Voice did not connect in {GROK_READY_S:.0f}s. Check laptop Wi-Fi/internet and API key.")
            if not args.auto:
                drone.close()
                droid.close()
                return 1
            print("Continuing without voice because --auto was set.")
            voice = None
        else:
            print("Grok is running on this laptop.")

    if args.auto:
        mission.request_start()
        if voice is not None:
            voice.notify("Survey is starting automatically. Do not call start_survey. Narrate briefly.")

    last_ok = time.time()
    bat = drone.battery()
    bat_ts = 0.0
    period = 1.0 / max(args.hz, 1.0)
    flying = False

    if args.auto:
        print("Survey starting. Space = abort, q = quit.")
    else:
        print("Say “survey the wheat”. Space = abort, q = quit.")
    try:
        while True:
            t0 = time.time()
            frame = drone.get_frame()
            if frame is not None:
                last_ok = t0
            if t0 - last_ok > FRAME_STALE_S:
                frame = None

            tick = mission.tick(frame, t0)
            snap = mission.snapshot()

            if tick.action == "takeoff" and not flying:
                drone.takeoff()
                flying = True
                drone.hover()
            elif tick.action == "land" and flying:
                drone.hover()
                drone.land()
                flying = False
            elif tick.action == "photo":
                path = drone.capture_photo()
                mission.note_photo(path)
                print(f"survey photo {path}")
                if path:
                    def on_grok_complete(analysis):
                        print(f"photo analysis: health={analysis.get('health_score'):.2f} summary={analysis.get('analysis_summary')[:50]}...")
                        mission.last_analysis = analysis
                    analyze_photo_with_grok_async(path, on_grok_complete)

            if flying:
                if snap["phase"] in ("idle", "done"):
                    drone.hover()
                else:
                    drone.send_rc(tick.lr, tick.fwd, tick.up, tick.yaw)

            if t0 - bat_ts > 5:
                bat = drone.battery()
                bat_ts = t0

            vis = (
                draw_hud(
                    frame,
                    mission,
                    tick.box,
                    tick.err_x,
                    tick.yaw,
                    tick.fwd,
                    tick.up,
                    tick.lr,
                    tick.stale,
                    bat,
                    tag_w=tick.width,
                    need_w=tick.need_w,
                )
                if frame is not None
                else np.zeros((360, 640, 3), dtype=np.uint8)
            )
            cv2.imshow("agridrone mission", vis)
            key = cv2.waitKey(1) & 0xFF
            if key in (ord("q"), 27):
                mission.request_abort()
                break
            if key == ord(" "):
                mission.request_abort()

            if snap["phase"] == "done" and args.auto and args.no_voice:
                break

            dt = time.time() - t0
            if dt < period:
                time.sleep(period - dt)
    except KeyboardInterrupt:
        print()
        mission.request_abort()
    finally:
        if flying:
            try:
                drone.land()
            except Exception:
                drone.emergency()
        drone.close()
        droid.close()
        cv2.destroyAllWindows()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
