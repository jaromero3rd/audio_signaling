#!/usr/bin/env python3
"""Demo mission: Grok Voice on this laptop + Tello Wi-Fi + USB tether internet.

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
from ground_station.apriltag import FAMILIES
from ground_station.envutil import load_dotenv
from ground_station.mission import FRAME_STALE_S, Gains, SurveyMission
from ground_station.vision import DEFAULT_WEIGHTS, Box

GROK_READY_S = 20.0


def draw_hud(
    frame: np.ndarray,
    mission: SurveyMission,
    box: Box | None,
    err_x: float | None,
    area: float | None,
    yaw: int,
    fwd: int,
    up: int,
    stale: bool,
    battery: int | None,
) -> np.ndarray:
    vis = frame.copy()
    h, w = vis.shape[:2]
    cv2.line(vis, (w // 2, 0), (w // 2, h), (80, 80, 80), 1)
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
        f"yaw={yaw:+d} fwd={fwd:+d} up={up:+d}",
        f"err_x={err_x:+.2f}" if err_x is not None else "no box",
        f"area={area:.3f}" if area is not None else "",
        f"bat={battery}%" if battery is not None else "",
        "STALE" if stale else "",
        "say survey the wheat  |  space abort  |  q quit",
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
    p = argparse.ArgumentParser(
        description="Grok-on-laptop Tello survey (USB tether + Tello Wi-Fi)."
    )
    p.add_argument("--mock", action="store_true")
    p.add_argument("--camera", type=int, default=0)
    p.add_argument("--family", choices=sorted(FAMILIES), default="36h11")
    p.add_argument("--home-tag", type=int, default=12)
    p.add_argument("--target", choices=("auto", "wheat", "tag"), default="auto")
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
    return p.parse_args()


def main() -> int:
    load_dotenv()
    args = parse_args()
    if args.no_voice and not args.auto:
        print("`--no-voice` needs `--auto` so the survey still starts.")
        return 1

    print("Network: Tello on Wi-Fi, Grok Voice via USB tether on this laptop.")
    gains = Gains(target_area=args.target_area)
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

    drone = MockDrone(args.camera) if args.mock else TelloDrone()
    drone.connect()
    drone.stream_on()
    print("Tello stream up.")

    if not args.no_voice:
        if not os.environ.get("XAI_API_KEY"):
            print("Missing XAI_API_KEY. Put it in .env or export it.")
            return 1
        from ground_station.voice_grok import start_voice_thread

        voice = start_voice_thread(mission)
        if not voice.ready.wait(GROK_READY_S):
            print(f"Grok Voice did not connect in {GROK_READY_S:.0f}s. Check USB tether and API key.")
            if not args.auto:
                drone.close()
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

            if flying:
                if snap["phase"] in ("idle", "done"):
                    drone.hover()
                else:
                    drone.send_rc(0, tick.fwd, tick.up, tick.yaw)

            if t0 - bat_ts > 5:
                bat = drone.battery()
                bat_ts = t0

            vis = (
                draw_hud(
                    frame,
                    mission,
                    tick.box,
                    tick.err_x,
                    tick.area,
                    tick.yaw,
                    tick.fwd,
                    tick.up,
                    tick.stale,
                    bat,
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
        cv2.destroyAllWindows()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
