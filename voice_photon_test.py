#!/usr/bin/env python3
"""Manual test: speak a command, Scribe transcribes it, Photon iMessages the user.

Say e.g. "text the user saying the survey is done". The part after the
command phrase becomes the iMessage body; with no command phrase the whole
transcript is sent. One billable Scribe request and one real iMessage per run.

Usage: .venv/bin/python voice_photon_test.py [--max-seconds 12]
"""

from __future__ import annotations

import argparse
import io
import os
import re
import sys
import threading
import time
import uuid
import wave
from pathlib import Path

import sounddevice as sd

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ground_station.envutil import load_dotenv
from ground_station.photon import PhotonClient, PhotonConfig, PhotonError
from ground_station.voice_scribe import TEXT_USER as COMMAND, transcribe

RATE = 16000


def input_device(name: str | None) -> int | None:
    if name is None:
        for i, d in enumerate(sd.query_devices()):
            if d["max_input_channels"] > 0 and "macbook" in d["name"].lower():
                return i
        return None
    for i, d in enumerate(sd.query_devices()):
        if name.lower() in d["name"].lower() and d["max_input_channels"] > 0:
            return i
    raise RuntimeError(f"No input device matching {name!r}")


def record_command(max_seconds: float, device: int | None) -> bytes:
    """PCM16 mono 16 kHz; stops after ~1.2 s of quiet once speech is heard."""
    buf = bytearray()
    state = {"heard": False, "quiet": 0.0, "done": threading.Event()}

    def cb(indata, frames, time_info, status):
        buf.extend(bytes(indata))
        peak = max(int.from_bytes(indata[i:i + 2], "little", signed=True)
                   for i in range(0, len(indata), 2)) if frames else 0
        if peak > 400:
            state["heard"], state["quiet"] = True, 0.0
        elif state["heard"]:
            state["quiet"] += frames / RATE
            if state["quiet"] >= 1.2:
                state["done"].set()

    with sd.RawInputStream(device=device, samplerate=RATE, blocksize=1600,
                           channels=1, dtype="int16", callback=cb):
        print(f"Speak now (auto-stops on silence, max {max_seconds:.0f}s)...")
        state["done"].wait(timeout=max_seconds)
    if not state["heard"]:
        raise RuntimeError("No speech detected; check the mic and try again.")
    print(f"Recorded {len(buf) / 2 / RATE:.1f}s.")
    out = io.BytesIO()
    with wave.open(out, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(RATE)
        wav.writeframes(bytes(buf))
    return out.getvalue()


def send_imessage(client: PhotonClient, text: str) -> dict:
    return client.send_text(f"AgriDrone voice test: {text[:1000]}",
                            f"voicetest:{uuid.uuid4().hex}")


def main() -> int:
    load_dotenv()
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-seconds", type=float, default=12.0)
    parser.add_argument("--device", default=None,
                        help="input device name substring (default: MacBook mic, else system)")
    args = parser.parse_args()

    if not os.environ.get("ELEVENLABS_API_KEY"):
        print("Set ELEVENLABS_API_KEY for Scribe transcription.")
        return 1
    config = PhotonConfig.load()
    if config is None:
        print("Photon is not configured.")
        return 1
    client = PhotonClient(config)
    try:
        client.check()
        print("Photon route verified.")
        wav = record_command(args.max_seconds, input_device(args.device))
        transcript = transcribe(wav) if wav else ""
        if not transcript:
            print("Scribe returned an empty transcript; nothing sent.")
            return 1
        print(f"Transcript: {transcript}")
        body = COMMAND.search(transcript)
        message = body.group(1).strip() if body and body.group(1).strip() else transcript
        print(f"iMessage body: {message}")
        result = send_imessage(client, message)
        print(f"Photon accepted: {result.get('id')} (status {result.get('status')}); "
              "acceptance is not delivery.")
        for _ in range(10):
            status = client.delivery_status(result["id"])
            if status in ("delivered", "read", "failed"):
                print(f"Final status: {status}")
                return 0 if status != "failed" else 1
            time.sleep(2)
        print("Delivery still pending; no duplicate sent.")
        return 2
    except (PhotonError, RuntimeError, sd.PortAudioError) as error:
        print(str(error))
        return 1
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
