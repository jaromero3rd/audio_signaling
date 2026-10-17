#!/usr/bin/env python3
"""Listen on the microphone for ggwave messages (from send.py) and print them."""

from __future__ import annotations

import argparse
import queue
import sys
import time

import ggwave
import numpy as np
import sounddevice as sd

SAMPLE_RATE = 48000  # must match send.py
CHUNK = 1024


def decode_stream(chunks):
    """Yield (message, peak_level 0-1) for each ggwave message found in float32 chunks."""
    instance = ggwave.init()  # default 48 kHz, matches send.py
    try:
        for chunk in chunks:
            payload = ggwave.decode(instance, chunk)
            if payload is not None:
                level = float(np.abs(np.frombuffer(chunk, dtype=np.float32)).max())
                yield payload.decode("utf-8", errors="replace"), level
    finally:
        ggwave.free(instance)


def mic_chunks(device, show_level: bool):
    q: queue.Queue[bytes] = queue.Queue()

    def callback(indata, frames, time_info, status):
        if status:
            print(f"audio status: {status}", file=sys.stderr, flush=True)
        q.put(bytes(indata))

    with sd.RawInputStream(
        samplerate=SAMPLE_RATE,
        blocksize=CHUNK,
        device=device,
        channels=1,
        dtype="float32",
        callback=callback,
    ):
        last_meter = 0.0
        while True:
            chunk = q.get()
            if show_level and time.time() - last_meter > 0.25:
                peak = float(np.abs(np.frombuffer(chunk, dtype=np.float32)).max())
                print(f"\r  mic level {'#' * min(40, int(peak * 80)):<40}", end="", flush=True)
                last_meter = time.time()
            yield chunk


def main() -> int:
    parser = argparse.ArgumentParser(description="Listen for ggwave messages and print them.")
    parser.add_argument("-l", "--list-devices", action="store_true", help="list audio devices and exit")
    parser.add_argument("-d", "--device", help="input device index or name substring")
    parser.add_argument("--meter", action="store_true", help="show a live mic level bar")
    args = parser.parse_args()

    if args.list_devices:
        print(sd.query_devices())
        return 0

    device = args.device
    if device is not None and device.isdigit():
        device = int(device)

    ggwave.disableLog()
    print("listening… Ctrl-C to quit", flush=True)
    count = 0
    start = time.time()
    try:
        for text, level in decode_stream(mic_chunks(device, args.meter)):
            count += 1
            print(f"\r[{time.time() - start:7.1f}s] #{count} (peak {level:.2f}) {text}{' ' * 20}", flush=True)
    except KeyboardInterrupt:
        print(f"\n{count} message(s) received")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
