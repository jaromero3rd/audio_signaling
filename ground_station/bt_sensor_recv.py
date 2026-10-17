#!/usr/bin/env python3
"""Receive UNO Q sensor JSON on the ground-station computer.

Two ways (pick one):

  A) Bluetooth LE (no pairing COM port needed):
       pip install bleak
       python -m ground_station.bt_sensor_recv

  B) Classic Bluetooth Serial (after pairing AgriDrone-UNOQ / UNO Q):
       pip install pyserial
       python -m ground_station.bt_sensor_recv --com COM5

The Arduino only gathers sensors and sends JSON lines over Bluetooth.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

SERVICE_UUID = "6e400001-b5a3-f393-e0a9-e50e24dcca9e"
TX_CHAR_UUID = "6e400003-b5a3-f393-e0a9-e50e24dcca9e"
DEVICE_NAME = "AgriDrone-UNOQ"


def _print_sample(sample: dict) -> None:
    light = sample.get("light")
    light_s = f"{light}/100" if light is not None else f"raw={sample.get('light_raw')}"
    print(
        f"T={sample.get('temp_c')}C  RH={sample.get('humidity_pct')}%  "
        f"light={light_s} ({sample.get('light_band', '?')})  "
        f"raw={sample.get('light_raw')}  ok={sample.get('ok')}",
        flush=True,
    )


def _handle_line(text: str, log_path: Path | None) -> None:
    text = text.strip()
    if not text:
        return
    try:
        sample = json.loads(text)
    except json.JSONDecodeError:
        print(f"[raw] {text}", flush=True)
        return
    _print_sample(sample)
    if log_path is not None:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(sample) + "\n")


async def run_ble(log_path: Path | None, timeout: float) -> int:
    try:
        from bleak import BleakClient, BleakScanner
    except ImportError:
        print("Install bleak: pip install bleak", file=sys.stderr)
        return 1

    print(f"Scanning for Bluetooth LE device '{DEVICE_NAME}' ({timeout:.0f}s)...")

    def match(d, ad) -> bool:  # noqa: ANN001
        name = (d.name or ad.local_name or "") or ""
        return DEVICE_NAME.lower() in name.lower() or "unoq" in name.lower()

    device = await BleakScanner.find_device_by_filter(match, timeout=timeout)
    if device is None:
        print(
            f"Could not find {DEVICE_NAME}.\n"
            "Checklist:\n"
            "  1) App Lab app is Running on the UNO Q\n"
            "  2) Bluetooth is ON on this ground-station laptop\n"
            "  3) Stay within a few meters\n"
            "Or pair the board and use: python -m ground_station.bt_sensor_recv --com COMx",
            file=sys.stderr,
        )
        return 2

    print(f"Connecting to {device.address} ({device.name})...")
    buf = bytearray()

    def on_notify(_handle: int, data: bytearray) -> None:
        nonlocal buf
        buf.extend(data)
        while b"\n" in buf:
            line, _, buf = buf.partition(b"\n")
            _handle_line(line.decode("utf-8", errors="replace"), log_path)

    async with BleakClient(device) as client:
        print("Connected — streaming (Ctrl+C to stop)", flush=True)
        await client.start_notify(TX_CHAR_UUID, on_notify)
        try:
            while True:
                await asyncio.sleep(1)
        finally:
            await client.stop_notify(TX_CHAR_UUID)
    return 0


def run_com(port: str, log_path: Path | None, baud: int) -> int:
    try:
        import serial
    except ImportError:
        print("Install pyserial: pip install pyserial", file=sys.stderr)
        return 1

    print(f"Opening {port} @ {baud} (classic Bluetooth Serial)...")
    with serial.Serial(port, baudrate=baud, timeout=1) as ser:
        print("Connected — streaming (Ctrl+C to stop)", flush=True)
        buf = ""
        while True:
            chunk = ser.read(256)
            if not chunk:
                continue
            buf += chunk.decode("utf-8", errors="replace")
            while "\n" in buf:
                line, _, buf = buf.partition("\n")
                _handle_line(line, log_path)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Receive UNO Q Bluetooth sensor stream")
    p.add_argument("--timeout", type=float, default=25.0, help="BLE scan timeout")
    p.add_argument("--com", help="Classic BT serial port, e.g. COM5 (Windows)")
    p.add_argument("--baud", type=int, default=9600)
    p.add_argument("--log", type=Path, default=None)
    p.add_argument("--no-log", action="store_true")
    args = p.parse_args(argv)
    log_path = None if args.no_log else (args.log or Path("data/unoq_bt.jsonl"))

    try:
        if args.com:
            return run_com(args.com, log_path, args.baud)
        return asyncio.run(run_ble(log_path, args.timeout))
    except KeyboardInterrupt:
        print("\nbye")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
