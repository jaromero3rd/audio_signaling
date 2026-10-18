#!/usr/bin/env python3
"""activate CLI: single command that preps arming + waits for Photon iMessages.

 Maps:
   --photon -> --phone-commands + auto-launch Bun agent
   --auto   -> legacy flag accepted (no-op, already waits); not forwarded to voice_mission
 Without --photon, warns and exits (no indefinite wait with no source).
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="activate: arm and wait indefinitely for Photon iMessages → Droid → drone",
        prog="activate",
    )
    p.add_argument("--photon", action="store_true", help="enable Photon bridge (implies --phone-commands + Bun agent)")
    p.add_argument("--auto", action="store_true", help="auto-queue start when Photon says start survey (default with --photon)")
    p.add_argument("--mock", action="store_true")
    p.add_argument("--allow-real-flight", action="store_true")
    p.add_argument("--controller", choices=("droid",), default="droid")
    p.add_argument("--droid-model", default=None)
    p.add_argument("--droid-timeout", type=float, default=60.0)
    p.add_argument("--droid-machine", default=None)
    p.add_argument("--droid-e2b", action="store_true")
    p.add_argument("--droid-prompt", default=None)
    p.add_argument("--photon-env", type=Path, default=None)
    p.add_argument("--no-photon", action="store_true")
    p.add_argument("--phone-commands", action="store_true", help=argparse.SUPPRESS)
    p.add_argument("--phone-command-env", type=Path, default=None)
    p.add_argument("--voice-notes", action="store_true")
    p.add_argument("--field-id", default="field-1")
    p.add_argument("--crop-analysis", action="store_true")
    p.add_argument("--fake-analysis", action="store_true")
    p.add_argument("--chalk", action="store_true")
    p.add_argument("--chalk-branch", default=None)
    p.add_argument("--dongle", type=int, default=None)
    p.add_argument("--ssid", default=None)
    p.add_argument("--tello-iface", default=None)
    p.add_argument("--no-wifi-join", action="store_true")
    p.add_argument("--camera", type=int, default=0)
    p.add_argument("--family", default="36h11")
    p.add_argument("--home-tag", type=int, default=12)
    p.add_argument("--target", choices=("auto", "wheat", "tag"), default="auto")
    p.add_argument("--target-tag", type=int, default=12)
    p.add_argument("--wheat-model", choices=("heads", "poster"), default="heads")
    p.add_argument("--weights", type=Path, default=None)
    p.add_argument("--no-voice", action="store_true", help=argparse.SUPPRESS)
    p.add_argument("--hz", type=float, default=12.0)
    p.add_argument("--target-area", type=float, default=0.12)
    p.add_argument("--close-feet", type=float, default=3.0)
    p.add_argument("--tag-size-m", type=float, default=0.20)
    return p


def validate_before_drone(args: argparse.Namespace) -> int:
    if not args.photon:
        print("activate needs --photon to have an inbound source; otherwise it would wait forever with no source. "
              "Use --photon (or run python demo.py directly).")
        return 2
    if args.photon and args.no_photon:
        print("--photon and --no-photon conflict; activate always enables Photon inbound.")
        return 2
    # Map --photon -> --phone-commands
    args.phone_commands = True
    # Validate bridge token/port before opening drone/video
    from ground_station.phone_command import BridgeSettings
    try:
        args.bridge_settings = BridgeSettings.load(args.phone_command_env)
    except (ValueError, OSError):
        print("Phone bridge setup failed. Check COMMAND_BRIDGE_TOKEN (32+ ASCII characters) "
              "and COMMAND_BRIDGE_PORT (1–65535). No drone connection opened.")
        return 1
    return 0


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    # --auto is accepted as no-op for activate (always waits)
    rc = validate_before_drone(args)
    if rc != 0:
        return rc
    # Delegate to voice_mission with Bun spawn
    # We need to translate args to voice_mission's parse_args namespace.
    # Instead of re-invoking parser, set env and call voice_mission with our args.
    from ground_station.voice_mission import main as vm_main, _run_mission
    from ground_station.envutil import load_dotenv
    from pathlib import Path as P

    # Replicate voice_mission main setup but with Bun management
    load_dotenv()
    if args.weights is None:
        from ground_station.vision import POSTER_ONNX, DEFAULT_WEIGHTS, resolve_weights
        args.weights = POSTER_ONNX if args.wheat_model == "poster" else DEFAULT_WEIGHTS
    else:
        from ground_station.vision import resolve_weights
        args.weights = resolve_weights(args.weights)

    if args.controller == "droid" and not args.allow_real_flight:
        args.mock = True
    # --auto already handled: do not reject
    if args.fake_analysis and not args.mock:
        print("Simulated analysis is forbidden with real hardware.")
        return 1
    # --phone-commands already set; bridge_settings already validated
    args.want_crop = bool(args.crop_analysis or args.fake_analysis)
    if args.droid_e2b and not args.droid_machine:
        args.droid_machine = os.environ.get("FACTORY_COMPUTER_ID") or None
        if not args.droid_machine:
            print("--droid-e2b needs FACTORY_COMPUTER_ID in .env or --droid-machine <id>")
            return 1

    # Start Bun photon agent
    from contextlib import ExitStack
    with ExitStack() as cleanup:
        photon = None
        if not args.no_photon:
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
                    photon = PhotonNotifier(photon_client, mock=args.mock, voice_notes=voice_notes, voice_requested=args.voice_notes)
                    photon.start()
                    cleanup.callback(photon.close)
                else:
                    print("Photon outbound not configured; continuing with inbound only.")
            except PhotonError as e:
                print(f"Photon setup failed: {e}. No drone connection opened.")
                return 1

        # Spawn Bun process
        bun_proc = None
        try:
            bun_cmd = ["bun", "--env-file=.env", "--env-file=../.env.phone-command", "run", "src/index.ts"]
            # Prefer absolute .env locations if present
            env_files = []
            for f in [ROOT / ".env", ROOT / ".env.phone-command", ROOT / "photon-imessage-agent" / ".env"]:
                if f.is_file():
                    env_files.append(f)
            # Use Popen with cwd
            bun_proc = subprocess.Popen(
                bun_cmd,
                cwd=str(ROOT / "photon-imessage-agent"),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
            def tee_output():
                assert bun_proc.stdout is not None
                for line in bun_proc.stdout:
                    sys.stdout.write(f"[photon] {line}")
                    sys.stdout.flush()
            t = threading.Thread(target=tee_output, daemon=True)
            t.start()
            cleanup.callback(lambda: (bun_proc.terminate(), bun_proc.wait(timeout=5)) if bun_proc and bun_proc.poll() is None else None)

            # Wait for both ready signals with timeout, fail-fast if Bun dies
            deadline = time.time() + 60
            # Poll stdout via waiting for process still alive; actual READY comes from Bun logs
            # The bridge ready is checked inside _run_mission; we just ensure Bun didn't exit early
            time.sleep(0.5)
            if bun_proc.poll() is not None:
                print("Photon Bun agent exited early. Check photon-imessage-agent/.env and COMMAND_BRIDGE_TOKEN.")
                return 1
            print("Photon Bun agent launched; waiting for Phone commands ready + Photon READY...")
            # Give voice_mission time to start 8787 bridge; _run_mission will print "Phone commands ready"
            # We don't block indefinitely here; _run_mission handles the indefinite wait via mission loop
        except FileNotFoundError:
            print("bun not found; install Bun to use --photon. No drone connection opened.")
            return 1
        except Exception as e:
            print(f"Failed to launch Photon agent: {e}")
            return 1

        return _run_mission(args, photon, cleanup)


if __name__ == "__main__":
    raise SystemExit(main())
