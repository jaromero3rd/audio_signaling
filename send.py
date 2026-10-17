#!/usr/bin/env python3
"""Type a message; ggwave encodes it and plays through speakers."""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys

import ggwave

SAMPLE_RATE = 48000
# ggwave.encode returns float32 samples (4 bytes each), despite its docstring.

# Protocol IDs (from ggwave):
# 0 = AudibleNormal, 1 = AudibleFast, 2 = AudibleFastest
# 3 = UltrasoundNormal, 4 = UltrasoundFast, 5 = UltrasoundFastest
# 6 = DTNormal, 7 = DTFast, 8 = DTFastest
PROTOCOLS = {
    "normal": 0,
    "fast": 1,
    "fastest": 2,
    "ultrasound": 3,
    "dt": 6,
}


def play_pcm(waveform: bytes) -> None:
    aplay = shutil.which("aplay")
    if aplay:
        proc = subprocess.run(
            [aplay, "-f", "FLOAT_LE", "-r", str(SAMPLE_RATE), "-c", "1", "-q"],
            input=waveform,
            check=False,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"aplay failed with exit code {proc.returncode}")
        return

    ffplay = shutil.which("ffplay")
    if ffplay:
        proc = subprocess.run(
            [
                ffplay,
                "-nodisp",
                "-autoexit",
                "-loglevel",
                "quiet",
                "-f",
                "f32le",
                "-ar",
                str(SAMPLE_RATE),
                "-ac",
                "1",
                "-i",
                "pipe:0",
            ],
            input=waveform,
            check=False,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"ffplay failed with exit code {proc.returncode}")
        return

    raise RuntimeError("Need aplay or ffplay to play audio through speakers")


def send(text: str, protocol_id: int, volume: int) -> None:
    waveform = ggwave.encode(text, protocolId=protocol_id, volume=volume)
    if not waveform:
        raise RuntimeError("ggwave.encode returned empty waveform (payload too long?)")
    duration = len(waveform) / 4 / SAMPLE_RATE
    print(f"→ playing {len(text)} chars ({duration:.2f}s)…", flush=True)
    play_pcm(waveform)
    print("✓ done", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Encode text with ggwave and play it through speakers."
    )
    parser.add_argument(
        "message",
        nargs="?",
        help="Message to send (omit for interactive prompt)",
    )
    parser.add_argument(
        "-p",
        "--protocol",
        choices=sorted(PROTOCOLS),
        default="fast",
        help="ggwave protocol (default: fast)",
    )
    parser.add_argument(
        "-v",
        "--volume",
        type=int,
        default=50,
        metavar="1-100",
        help="Transmit volume 1–100 (default: 50)",
    )
    args = parser.parse_args()

    if not (1 <= args.volume <= 100):
        parser.error("volume must be between 1 and 100")

    protocol_id = PROTOCOLS[args.protocol]
    ggwave.disableLog()

    if args.message is not None:
        send(args.message, protocol_id, args.volume)
        return 0

    print("ggwave sender — type a message, Enter to play, Ctrl-C / empty to quit")
    print(f"protocol={args.protocol} volume={args.volume}")
    try:
        while True:
            try:
                text = input("> ").strip()
            except EOFError:
                print()
                break
            if not text:
                break
            try:
                send(text, protocol_id, args.volume)
            except RuntimeError as exc:
                print(f"error: {exc}", file=sys.stderr)
    except KeyboardInterrupt:
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
