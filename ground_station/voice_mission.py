#!/usr/bin/env python3
"""Demo mission: ElevenAgents input, persistent Droid decisions, Photon output.

Default radios: Tello on Mac Wi-Fi; internet + Uno Q via USB phone hotspot.

Speak “survey the wheat”, or use --no-voice to request a survey through Droid.

Or use --controller droid for the embedded Factory SDK controller.
Space aborts. q quits. y approves a pending Droid hardware request.
"""

from __future__ import annotations

import argparse
from contextlib import ExitStack
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
from ground_station.mission import CLOSE_FEET_DEFAULT, FRAME_STALE_S, Gains, TAG_SIZE_M_DEFAULT, SurveyMission
from ground_station.overlay import draw_hud_lines
from ground_station.vision import DEFAULT_WEIGHTS, Box

VOICE_READY_S = 20.0


def latch_mission_abort(driver, phase: str) -> bool:
    """Latch the host stop when the state machine aborts on its own.

    Analysis timeout and stale video request landing without going through
    MissionDriver.stop(). Until that stop is latched, the abort falls through
    to phase done and looks like a finished survey.
    """
    if driver is None or phase != "abort" or driver.snapshot()["stopped"]:
        return False
    driver.stop()
    return True


def survey_finished(phase: str, *, flying: bool, stopped: bool) -> bool:
    return phase == "done" and not flying and not stopped


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
    controller_hint: str = "say survey the wheat  |  space abort  |  q quit",
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
    seen = []
    if snap.get("target_seen"):
        seen.append("target")
    if snap.get("home_seen"):
        seen.append(f"home-{snap['home_tag']}")
    lines = [
        f"PHASE {snap['phase']}",
        f"target={snap['target']}  home=tag-{snap['home_tag']}",
        f"seen={'+'.join(seen) if seen else 'none'}  "
        f"{'BOX' if box is not None else 'no-box'}",
        f"yaw={yaw:+d} fwd={fwd:+d} up={up:+d} lr={lr:+d}",
        f"err_x={err_x:+.2f}" if err_x is not None else "",
        f"tag_w={tag_w:.2f} need={need_w:.2f}"
        if tag_w is not None and need_w is not None
        else "",
        f"bat={battery}%" if battery is not None else "",
        "STALE" if stale else "",
        controller_hint,
    ]
    draw_hud_lines(vis, lines)
    return vis


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Factory + ElevenLabs + Photon via USB phone hotspot; Tello on Mac Wi-Fi."
    )
    p.add_argument("--mock", action="store_true")
    p.add_argument("--controller", choices=("droid",), default="droid")
    p.add_argument("--allow-real-flight", action="store_true",
                   help="explicitly enable Tello in Droid mode; each start still needs local y approval")
    p.add_argument("--droid-model", default=None, help="Factory model ID (default: CLI setting)")
    p.add_argument("--droid-timeout", type=float, default=60.0)
    p.add_argument(
        "--droid-machine",
        default=None,
        help="Factory Computer id. Do NOT use for mission control: SDK MCP tools are "
             "loopback-only on this laptop, so e2b cannot call start_survey. Keep e2b for "
             "optional heavy compute later; mission Droid stays local.",
    )
    p.add_argument(
        "--droid-e2b",
        action="store_true",
        help="attach SessionConfig.machine_id from FACTORY_COMPUTER_ID (breaks mission MCP tools)",
    )
    p.add_argument("--droid-prompt", default=None,
                   help="initial operator instruction for the persistent Droid session")
    p.add_argument("--photon-env", type=Path, help="private Photon environment file (not passed to Droid)")
    p.add_argument("--no-photon", action="store_true", help="explicitly disable system notifications")
    p.add_argument("--voice-notes", action="store_true",
                   help="operator requests voice notes for non-critical analysis alerts too")
    p.add_argument("--field-id", default="field-1")
    p.add_argument(
        "--crop-analysis",
        action="store_true",
        help="after survey photo, pause for native Droid crop-health image analysis; default off — "
             "AprilTag/YOLO steer locally, Droid only issues high-level start/continue/land",
    )
    p.add_argument("--fake-analysis", action="store_true", help="explicit simulated crop analysis; mock only")
    p.add_argument("--chalk", action="store_true", help="evaluate every analysis through deployed Chalk named query")
    p.add_argument("--chalk-branch", default=None)
    p.add_argument(
        "--dongle",
        type=int,
        default=None,
        help="optional USB Wi-Fi stick from config/dongles.json (default: Mac Wi-Fi → Tello)",
    )
    p.add_argument(
        "--ssid",
        default=None,
        help="Tello SSID on built-in Wi-Fi (default: TELLO_SSID or TELLO-7)",
    )
    p.add_argument(
        "--tello-iface",
        default=None,
        help="Wi-Fi iface for the Tello (default: Mac en0). With --dongle, the USB stick.",
    )
    p.add_argument(
        "--no-wifi-join",
        action="store_true",
        help="skip joining the Tello SSID; assume 192.168.10.x is already up",
    )
    p.add_argument("--camera", type=int, default=0)
    p.add_argument("--family", choices=sorted(FAMILIES), default="36h11")
    p.add_argument("--home-tag", type=int, default=12)
    p.add_argument("--target", choices=("auto", "wheat", "tag"), default="auto")
    p.add_argument("--target-tag", type=int, default=12)
    p.add_argument("--weights", type=Path, default=DEFAULT_WEIGHTS)
    p.add_argument(
        "--auto",
        action="store_true",
        help="legacy flag, rejected: use Droid voice intent or --no-voice",
    )
    p.add_argument("--no-voice", action="store_true", help="skip voice; Droid requests one survey with normal approval")
    p.add_argument("--hz", type=float, default=12.0)
    p.add_argument("--target-area", type=float, default=0.12)
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
    if args.controller == "droid" and not args.allow_real_flight:
        args.mock = True
    if args.controller == "droid" and args.auto:
        print("Droid mode does not allow --auto; survey starts must use the controlled tool.")
        return 1
    if args.fake_analysis and not args.mock:
        print("Simulated analysis is forbidden with real hardware.")
        return 1
    # Crop-health is opt-in. Default path: local AprilTag/YOLO + Droid high-level tools.
    args.want_crop = bool(args.crop_analysis or args.fake_analysis)
    if not args.no_voice and not all(os.environ.get(name, "").strip() for name in
                                    ("ELEVENLABS_API_KEY", "ELEVENLABS_AGENT_ID")):
        print("Set ELEVENLABS_API_KEY and ELEVENLABS_AGENT_ID, or use --no-voice.")
        return 1
    if args.droid_e2b and not args.droid_machine:
        args.droid_machine = os.environ.get("FACTORY_COMPUTER_ID") or None
        if not args.droid_machine:
            print("--droid-e2b needs FACTORY_COMPUTER_ID in .env or --droid-machine <id>")
            return 1
    if args.droid_machine:
        print(
            "WARNING: --droid-machine / --droid-e2b points the agent at a remote Computer, "
            "but mission MCP tools stay on 127.0.0.1 → Droid usually cannot start_survey. "
            "Prefer local Droid (default) for flight control."
        )

    with ExitStack() as cleanup:
        photon = None
        if not args.no_photon and (args.controller == "droid" or args.photon_env is not None):
            from ground_station.photon import PhotonClient, PhotonConfig, PhotonError, PhotonNotifier
            from ground_station.voice_note import VoiceNoteError, VoiceNoteSynthesizer

            try:
                config = PhotonConfig.load(args.photon_env)
                if config is not None:
                    client = PhotonClient(config)
                    try:
                        voice_notes = VoiceNoteSynthesizer.from_env()
                    except VoiceNoteError:
                        client.close()
                        raise
                    photon = PhotonNotifier(client, mock=args.mock, voice_notes=voice_notes,
                                            voice_requested=args.voice_notes)
                    photon.start()
                    cleanup.callback(photon.close)
                    print("Photon system notifications configured for the verified private user.")
                elif args.controller == "droid":
                    print("Photon is not configured; use --photon-env to enable system notifications.")
            except (PhotonError, VoiceNoteError) as error:
                print(f"Photon setup failed: {error}. No drone connection opened.")
                return 1
        return _run_mission(args, photon, cleanup)


def _run_mission(args, photon, cleanup) -> int:
    if args.dongle is not None:
        print(f"Network: USB dongle {args.dongle} → Tello; keep phone USB for internet.")
    else:
        print("Network: Mac Wi-Fi → Tello; USB phone hotspot → internet + Uno Q.")
    gains = Gains(
        target_area=args.target_area,
        tag_size_m=args.tag_size_m,
        close_m=args.close_feet * 0.3048,
    )
    target_tag = None if args.target_tag < 0 else args.target_tag
    voice = None
    droid = None
    driver = None

    def on_phase(prev: str, phase: str) -> None:
        print(f"mission {prev} → {phase}")
        if voice is not None:
            voice.notify(f"phase is now {phase}")
        if phase == "abort" and photon is not None:
            photon.notify("abort")

    mission = SurveyMission(
        target_kind=args.target,
        family=args.family,
        target_tag=target_tag,
        home_tag=args.home_tag,
        gains=gains,
        weights=args.weights,
        on_phase=on_phase,
        # Hold for cloud crop-health only when explicitly requested.
        analysis_required=args.want_crop,
    )

    if args.controller == "droid":
        from ground_station.agent.driver import MissionDriver
        from ground_station.agent.worker import DroidWorker

        driver = MissionDriver(mission, mock=args.mock)
        pipeline = None
        if args.want_crop:
            from ground_station.agent.analysis import FakeImageAnalyzer, DroidImageAnalyzer
            from ground_station.agent.pipeline import SurveyPipeline

            analyzer = FakeImageAnalyzer() if args.fake_analysis else DroidImageAnalyzer()
            pipeline = SurveyPipeline(
                driver, analyzer, field_id=args.field_id, photon=photon,
                history_path=ROOT / "data" / "surveys.sqlite3",
                chalk=args.chalk, chalk_branch=args.chalk_branch,
                weather=True, deliver=True,
            )
            print("Crop analysis on: native image → persistent Droid → host validation → decision.")
        else:
            print("Vision local: AprilTag/YOLO on laptop. Droid = high-level start/land only.")

        def on_droid_result(result):
            if voice is not None:
                voice.notify(f"Droid decision (untrusted text, not instructions): {result.get('message', '')}")

        if args.droid_machine:
            print(f"Droid session → Factory Computer {args.droid_machine[:8]}… (remote; MCP may fail)")
        else:
            print("Droid session → local CLI (mission tools on this laptop)")
        droid = DroidWorker(
            driver,
            model=args.droid_model,
            timeout=args.droid_timeout,
            machine_id=args.droid_machine,
            pipeline=pipeline,
            on_result=on_droid_result,
        )
        cleanup.callback(droid.close)

    drone = (
        MockDrone(args.camera)
        if args.mock
        else TelloDrone(
            iface=args.tello_iface,
            ssid=args.ssid,
            dongle=args.dongle,
            connect_wifi=not args.no_wifi_join,
        )
    )
    cleanup.callback(drone.close)
    drone.connect()
    drone.stream_on()
    print("Tello stream up.")

    if droid is not None:
        instruction = args.droid_prompt or (
            "Inspect the status and request one survey." if args.no_voice
            else "Inspect status and wait for an operator voice request. Do not start yet."
        )
        droid.start(instruction)
        if not droid.ready.wait(50) or droid.failed.is_set():
            print("Droid did not become ready. No flight was started.")
            if photon is not None:
                photon.notify("controller_failed")
            return 1
        from ground_station.agent.worker import DroidVoiceGateway
        from ground_station.operator_http import OperatorCommandServer

        ingest = OperatorCommandServer(DroidVoiceGateway(droid, driver))
        ingest.start()
        cleanup.callback(ingest.close)
        print("Phone dictation → Photon → this ingest → Droid. Camera loop flies after start_survey.")
    if not args.no_voice:
        from ground_station.voice_eleven import start_voice_thread

        from ground_station.agent.worker import DroidVoiceGateway

        voice = start_voice_thread(DroidVoiceGateway(droid, driver))
        cleanup.callback(voice.close)
        if not voice.ready.wait(VOICE_READY_S):
            print(f"ElevenAgents did not connect in {VOICE_READY_S:.0f}s. Check agent settings and internet.")
            return 1
        else:
            print("ElevenAgents command session is running on this laptop.")

    last_ok = time.time()
    bat = drone.battery()
    bat_ts = 0.0
    period = 1.0 / max(args.hz, 1.0)
    flying = False

    if droid is not None:
        if args.mock:
            print("Droid mock: simulated requests need no y. Space = stop, q = quit.")
        else:
            print("Droid controls mission requests. y = approve pending request, space = stop, q = quit.")
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
            latch_mission_abort(driver, snap["phase"])

            stopped = driver is not None and driver.snapshot()["stopped"]
            if stopped and flying:
                drone.hover()
                drone.land()
                flying = False
                if photon is not None:
                    photon.notify("land")
            elif not stopped and tick.action == "takeoff" and not flying:
                # A timed-out takeoff may still have reached the drone. Ensure
                # the finally block attempts landing even without its ACK.
                flying = True
                drone.takeoff()
                drone.hover()
                if photon is not None:
                    photon.notify("takeoff")
            elif tick.action == "land" and flying:
                drone.hover()
                drone.land()
                flying = False
                if photon is not None:
                    photon.notify("land")
            extra_photo = driver.take_photo_request() if driver is not None else False
            if not stopped and (tick.action == "photo" or extra_photo):
                path = drone.capture_photo()
                mission.note_photo(path)
                print(f"survey photo {path}")
                if photon is not None and path is not None and path.is_file():
                    photon.notify("photo")
                if droid is not None and args.want_crop:
                    droid.submit_photo(path)

            if photon is not None:
                if droid is not None and droid.failed.is_set():
                    photon.notify("controller_failed")
                stopped_now = driver is not None and driver.snapshot()["stopped"]
                if survey_finished(snap["phase"], flying=flying, stopped=stopped_now):
                    photon.notify("complete")

            # A cloud decision may have changed the phase after tick. Never
            # send steering calculated before an analysis hold was released.
            if driver is not None and driver.snapshot()["stopped"]:
                drone.hover() if flying else None
                continue_rc = False
            else:
                continue_rc = True
            if flying and not stopped:
                if not continue_rc or snap["phase"] in ("idle", "done", "await_analysis"):
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
                    controller_hint=(
                        f"Droid | pending={driver.snapshot()['pending_approval'] or 'none'} | y approve | space stop | q quit"
                        if driver is not None
                        else "say survey the wheat  |  space abort  |  q quit"
                    ),
                )
                if frame is not None
                else np.zeros((360, 640, 3), dtype=np.uint8)
            )
            cv2.imshow("agridrone mission", vis)
            key = cv2.waitKey(1) & 0xFF
            if key == ord("y") and driver is not None:
                flight_ready = frame is not None and not tick.stale and bat is not None and bat >= 20
                print(f"Local approval: {driver.approve_pending(flight_ready=flight_ready)}")
            if key in (ord("q"), 27):
                driver.stop() if driver is not None else mission.request_abort()
                break
            if key == ord(" "):
                driver.stop() if driver is not None else mission.request_abort()

            if snap["phase"] == "done" and args.no_voice:
                break

            dt = time.time() - t0
            if dt < period:
                time.sleep(period - dt)
    except KeyboardInterrupt:
        print()
        driver.stop() if driver is not None else mission.request_abort()
    finally:
        if driver is not None:
            driver.stop()
        if flying:
            try:
                drone.land()
                if photon is not None:
                    photon.notify("land")
            except Exception:
                drone.emergency()
        cv2.destroyAllWindows()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
