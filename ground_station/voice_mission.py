#!/usr/bin/env python3
"""Demo mission: Photon iMessage → Droid; wheat vision on Factory e2b ONNX.

Default radios: Tello on Mac Wi-Fi; internet + Uno Q via USB phone hotspot.

Wheat/poster + AprilTag vision POSTs to Factory e2b (WHEAT_INFER_URL /
scripts/wheat_e2b_up.sh). Opt-in Photon bridge accepts start/abort/land after
host preflight. Space aborts. q quits. Optional --crop-analysis.
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
from ground_station.mission import (
    CLOSE_FEET_DEFAULT,
    CRUISE_HEIGHT_FT,
    FRAME_STALE_S,
    Gains,
    TAG_SIZE_M_DEFAULT,
    SurveyMission,
)
from ground_station.overlay import draw_hud_lines
from ground_station.vision import DEFAULT_WEIGHTS, POSTER_ONNX, Box, resolve_weights


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
    controller_hint: str = "Photon text: survey | abort  |  y approve  |  space stop  |  q quit",
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
        description="Factory Droid + Photon iMessage commands; Tello on Mac Wi-Fi / USB dongle."
    )
    p.add_argument("--mock", action="store_true")
    p.add_argument("--controller", choices=("droid",), default="droid")
    p.add_argument("--allow-real-flight", action="store_true",
                   help="explicitly enable Tello; starts need local y or opt-in phone authorization")
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
    p.add_argument("--no-photon", action="store_true", help="disable Python outbound notifications, not opt-in phone ingress")
    p.add_argument("--phone-commands", action="store_true",
                   help="enable authenticated loopback start/abort/land; phone start replaces local y after preflight")
    p.add_argument("--phone-command-env", type=Path,
                   help="private COMMAND_BRIDGE_TOKEN/PORT file; requires --phone-commands")
    p.add_argument("--voice-notes", action="store_true",
                   help="optional ElevenLabs TTS for non-critical analysis alerts (text always sent)")
    p.add_argument("--field-id", default="field-1")
    p.add_argument(
        "--crop-analysis",
        action="store_true",
        help="after survey photo, send JPEG to Factory Droid for crop-health analysis",
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
    p.add_argument(
        "--wheat-model",
        choices=("heads", "poster"),
        default="heads",
        help="which ONNX the e2b server should be running (heads or poster@320)",
    )
    p.add_argument(
        "--weights",
        type=Path,
        default=None,
        help="local weights path only used with --local-vision",
    )
    p.add_argument(
        "--local-vision",
        action="store_true",
        help="run wheat ONNX + AprilTag on this laptop (default: all vision on Factory e2b)",
    )
    p.add_argument(
        "--wheat-infer-url",
        default=None,
        help="remote detect URL (default: WHEAT_INFER_URL or http://127.0.0.1:8767/detect)",
    )
    p.add_argument(
        "--auto",
        action="store_true",
        help="legacy flag, rejected: use Photon iMessage or local y after Droid queues start",
    )
    p.add_argument(
        "--no-voice",
        action="store_true",
        help=argparse.SUPPRESS,  # legacy no-op; voice command path removed
    )
    p.add_argument("--hz", type=float, default=12.0)
    p.add_argument("--target-area", type=float, default=0.12)
    p.add_argument(
        "--wheat-close",
        type=float,
        default=0.30,
        help="wheat/poster: turn when box width is this fraction of the frame (0.3 = 30%% width)",
    )
    p.add_argument(
        "--close-feet",
        type=float,
        default=CLOSE_FEET_DEFAULT,
        help="AprilTag stop distance in feet (larger = turn/land farther out)",
    )
    p.add_argument(
        "--cruise-feet",
        type=float,
        default=CRUISE_HEIGHT_FT,
        help="climb to this height (feet) right after takeoff before searching",
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
    if args.weights is None:
        args.weights = POSTER_ONNX if args.wheat_model == "poster" else DEFAULT_WEIGHTS
    else:
        args.weights = resolve_weights(args.weights)
    if args.controller == "droid" and not args.allow_real_flight:
        args.mock = True
    if args.controller == "droid" and args.auto and not args.phone_commands:
        print("Droid mode does not allow --auto; survey starts use Photon text or the controlled tool.")
        return 1
    if args.fake_analysis and not args.mock:
        print("Simulated analysis is forbidden with real hardware.")
        return 1
    args.bridge_settings = None
    if args.phone_command_env is not None and not args.phone_commands:
        print("--phone-command-env requires --phone-commands. No drone connection opened.")
        return 1
    if args.phone_commands:
        from ground_station.phone_command import BridgeSettings

        try:
            args.bridge_settings = BridgeSettings.load(args.phone_command_env)
        except (ValueError, OSError):
            print("Phone bridge setup failed. Check COMMAND_BRIDGE_TOKEN (32+ ASCII characters) "
                  "and COMMAND_BRIDGE_PORT (1–65535). No drone connection opened.")
            return 1
    # Crop-health is opt-in. Default path: local AprilTag/YOLO + Droid high-level tools.
    args.want_crop = bool(args.crop_analysis or args.fake_analysis)
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
        photon_client = None
        if not args.no_photon and (args.controller == "droid" or args.photon_env is not None):
            from ground_station.photon import PhotonClient, PhotonConfig, PhotonError, PhotonNotifier
            from ground_station.voice_note import VoiceNoteError, VoiceNoteSynthesizer

            try:
                config = PhotonConfig.load(args.photon_env)
                if config is not None:
                    photon_client = PhotonClient(config)
                    voice_notes = None
                    try:
                        voice_notes = VoiceNoteSynthesizer.from_env()
                    except VoiceNoteError:
                        voice_notes = None
                    if args.voice_notes and voice_notes is None:
                        print("Warning: --voice-notes needs ELEVENLABS_API_KEY + ELEVENLABS_TTS_VOICE_ID; text-only.")
                    photon = PhotonNotifier(
                        photon_client,
                        mock=args.mock,
                        voice_notes=voice_notes,
                        voice_requested=args.voice_notes,
                    )
                    photon.start()
                    cleanup.callback(photon.close)
                    print("Photon outbound notifications configured; phone commands require separate opt-in.")
                elif args.controller == "droid":
                    print("Photon outbound is not configured; use --photon-env or PHOTON_* in .env.")
            except PhotonError as error:
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
        wheat_close_frac=args.wheat_close,
        tag_size_m=args.tag_size_m,
        close_m=args.close_feet * 0.3048,
    )
    target_tag = None if args.target_tag < 0 else args.target_tag
    droid = None
    driver = None

    def on_phase(prev: str, phase: str) -> None:
        print(f"mission {prev} → {phase}")
        if phase == "abort" and photon is not None:
            photon.notify("abort")

    if not args.local_vision:
        print(
            "All vision → Factory e2b "
            f"({args.wheat_infer_url or os.environ.get('WHEAT_INFER_URL') or 'http://127.0.0.1:8767'}). "
            "Run scripts/wheat_e2b_up.sh (poster/heads ONNX + AprilTag)."
        )
    mission = SurveyMission(
        target_kind=args.target,
        family=args.family,
        target_tag=target_tag,
        home_tag=args.home_tag,
        gains=gains,
        weights=args.weights,
        on_phase=on_phase,
        analysis_required=args.want_crop,
        local_vision=args.local_vision,
        wheat_infer_url=args.wheat_infer_url,
    )

    if args.controller == "droid":
        from ground_station.agent.driver import MissionDriver
        from ground_station.agent.worker import DroidWorker

        driver = MissionDriver(mission, mock=args.mock, phone_commands=args.phone_commands)
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
            print("Crop analysis on: survey JPEG → Factory Droid → host validation → decision.")
        else:
            if args.local_vision:
                print("Vision: LOCAL laptop ONNX/AprilTag (override).")
            else:
                print("Vision: Factory e2b (wheat ONNX + AprilTag). Droid = mission tools.")

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
        )
        cleanup.callback(droid.close)

    # Real + Photon: accept iMessage first, join Tello only after start survey.
    # Mock / non-phone: open the camera adapter immediately (no Wi-Fi join delay).
    defer_tello = bool(args.phone_commands and not args.mock)
    drone = None
    if not defer_tello:
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
        print("Tello stream up." if not args.mock else "Mock camera up.")

    ingest = None
    if droid is not None:
        if args.droid_prompt:
            instruction = args.droid_prompt
        elif args.no_photon and not args.phone_commands:
            # Host fallback retains normal approval; phone mode must wait.
            instruction = "Inspect the status and request one survey."
        else:
            instruction = (
                "Inspect status and wait for an operator Photon iMessage request. Do not start yet."
            )
        droid.start(instruction)
        if not droid.ready.wait(50) or droid.failed.is_set():
            print("Droid did not become ready. No flight was started.")
            if photon is not None:
                photon.notify("controller_failed")
            return 1
        if args.phone_commands:
            from ground_station.agent.worker import DroidVoiceGateway
            from ground_station.phone_command import PhoneCommandBridge, PhoneCommandGateway

            ingest = PhoneCommandBridge(
                PhoneCommandGateway(DroidVoiceGateway(droid, driver), driver),
                token=args.bridge_settings.token, port=args.bridge_settings.port,
            )
            cleanup.callback(ingest.close)
            try:
                ingest.start()
            except OSError:
                print("Phone bridge could not bind loopback. No flight was started.")
                return 1
            if not ingest.ready.wait(3) or ingest.failed.is_set():
                print("Phone bridge did not become ready. No flight was started.")
                return 1
            print(f"Phone commands ready on 127.0.0.1:{ingest.port}; "
                  "text start survey → then join Tello → preflight → takeoff (no local y).")

    if defer_tello:
        print(
            "Armed for Photon: send iMessage `start survey` when ready. "
            "Tello Wi-Fi joins only after that message (Ctrl-C aborts)."
        )
        try:
            while driver is not None and not driver.phone_start_armed():
                if driver.snapshot()["stopped"]:
                    print("Stop latched before Tello connect. No flight.")
                    return 1
                if ingest is not None and ingest.failed.is_set():
                    print("Phone bridge failed before Tello connect. No flight.")
                    return 1
                time.sleep(0.1)
        except KeyboardInterrupt:
            if driver is not None:
                driver.stop()
            print("Interrupted before Tello connect. No flight.")
            return 1
        # Wi-Fi join can take longer than the default 60s approval TTL.
        driver.refresh_phone_start(ttl_s=180.0)
        print("Photon start armed — joining Tello now…")
        try:
            drone = TelloDrone(
                iface=args.tello_iface,
                ssid=args.ssid,
                dongle=args.dongle,
                connect_wifi=not args.no_wifi_join,
            )
        except Exception as exc:
            print(f"Tello connect failed after phone start: {exc}. No flight.")
            if driver is not None:
                driver.stop()
            if photon is not None:
                photon.notify("controller_failed")
            return 1
        cleanup.callback(drone.close)
        try:
            drone.connect()
            drone.stream_on()
        except Exception as exc:
            print(f"Tello stream failed after phone start: {exc}. No flight.")
            if driver is not None:
                driver.stop()
            if photon is not None:
                photon.notify("controller_failed")
            return 1
        print("Tello stream up.")

    last_ok = time.time()
    bat = drone.battery()
    bat_ts = 0.0
    period = 1.0 / max(args.hz, 1.0)
    flying = False

    if droid is not None:
        if args.phone_commands:
            print("Photon text: start survey | abort | land  |  space stop  |  q quit "
                  "(no y — phone start replaces local approval after preflight).")
        elif args.mock:
            print("Droid mock: Photon text or tools queue start; space = stop, q = quit.")
        else:
            print("Text Photon agent “survey the wheat”. y = approve, space = stop, q = quit.")
    try:
        while True:
            t0 = time.time()
            frame = drone.get_frame()
            if frame is not None:
                last_ok = t0
            if t0 - last_ok > FRAME_STALE_S:
                frame = None

            if t0 - bat_ts > 5:
                bat = drone.battery()
                bat_ts = t0
            if ingest is not None and ingest.failed.is_set():
                driver.stop()
            tick = mission.tick(frame, t0)
            if args.phone_commands:
                driver.approve_phone_start(
                    flight_ready=frame is not None and not tick.stale and bat is not None and bat >= 20,
                )
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
                if args.phone_commands and (tick.stale or frame is None or bat is None or bat < 20):
                    driver.stop()
                    continue
                # A timed-out takeoff may still have reached the drone. Ensure
                # the finally block attempts landing even without its ACK.
                flying = True
                drone.takeoff()
                try:
                    drone.climb_to_ft(args.cruise_feet)
                except Exception as exc:
                    print(f"cruise climb failed ({exc}); continuing at takeoff height")
                drone.hover()
                if photon is not None:
                    photon.notify("takeoff")
            elif tick.action in ("land", "flip_land") and flying:
                if tick.action == "flip_land":
                    try:
                        print("home arrival — flip then land")
                        drone.flip("b")
                    except Exception as exc:
                        print(f"flip failed ({exc}); landing anyway")
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

            pending = driver.snapshot()["pending_approval"] if driver is not None else None
            hint = (
                f"Photon | pending={pending or 'none'} | phone start (no y) | space stop | q quit"
                if args.phone_commands
                else (
                    f"pending={pending or 'none'} | y approve | space stop | q quit"
                    if driver is not None
                    else "Photon text: survey | abort  |  q quit"
                )
            )
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
                    controller_hint=hint,
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

            if snap["phase"] == "done" and args.no_photon:
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
