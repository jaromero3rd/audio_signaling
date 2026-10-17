#!/usr/bin/env python3
"""Receive DHT11 + LDR JSON from the UNO Q over Bluetooth LE.

Run this on the **ground-station laptop** (not necessarily the App Lab / dev PC).
The UNO Q advertises as AgriDrone-UNOQ and notifies sensor JSON lines.
No processing is done on the Arduino — this script only prints (or logs).

  pip install bleak
  python -m ground_station.bt_sensor_recv
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

SERVICE_UUID = "6e400001-b5a3-f393-e0a9-e50e24dcca9e"
TX_CHAR_UUID = "6e400003-b5a3-f393-e0a9-e50e24dcca9e"
DEVICE_NAME = "AgriDrone-UNOQ"


async def find_device(timeout: float):
    from bleak import BleakScanner

    print(f"scanning for {DEVICE_NAME} ({timeout:.0f}s)…")
    device = await BleakScanner.find_device_by_filter(
        lambda d, ad: (d.name or ad.local_name or "") == DEVICE_NAME,
        timeout=timeout,
    )
    return device


async def run(log_path: Path | None, timeout: float) -> int:
    try:
        from bleak import BleakClient
    except ImportError:
        print("Install bleak: pip install bleak", file=sys.stderr)
        return 1

    device = await find_device(timeout)
    if device is None:
        print(
            f"Could not find {DEVICE_NAME}. "
            "Is the App Lab app running on the UNO Q? Is Bluetooth on?",
            file=sys.stderr,
        )
        return 2

    print(f"connecting to {device.address} ({device.name})…")
    buf = bytearray()

    def on_notify(_handle: int, data: bytearray) -> None:
        nonlocal buf
        buf.extend(data)
        while b"\n" in buf:
            line, _, buf = buf.partition(b"\n")
            text = line.decode("utf-8", errors="replace").strip()
            if not text:
                continue
            try:
                sample = json.loads(text)
            except json.JSONDecodeError:
                print(f"[raw] {text}")
                continue
            light = sample.get("light")
            light_s = f"{light}/100" if light is not None else f"raw={sample.get('light_raw')}"
            print(
                f"T={sample.get('temp_c')}C  RH={sample.get('humidity_pct')}%  "
                f"light={light_s} ({sample.get('light_band', '?')})  "
                f"raw={sample.get('light_raw')}  ok={sample.get('ok')}"
            )
            if log_path is not None:
                log_path.parent.mkdir(parents=True, exist_ok=True)
                with log_path.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(sample) + "\n")

    async with BleakClient(device) as client:
        print("connected — streaming sensor samples (Ctrl+C to stop)")
        await client.start_notify(TX_CHAR_UUID, on_notify)
        try:
            while True:
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            pass
        finally:
            await client.stop_notify(TX_CHAR_UUID)
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Receive UNO Q sensor BLE stream")
    p.add_argument("--timeout", type=float, default=20.0)
    p.add_argument(
        "--log",
        type=Path,
        default=None,
        help="Optional JSONL log path (default: data/unoq_bt.jsonl)",
    )
    p.add_argument("--no-log", action="store_true")
    args = p.parse_args(argv)
    log_path = None if args.no_log else (args.log or Path("data/unoq_bt.jsonl"))
    try:
        return asyncio.run(run(log_path, args.timeout))
    except KeyboardInterrupt:
        print("\nbye")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
