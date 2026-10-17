#!/usr/bin/env python3
"""Receive UNO Q sensor JSON on the ground-station computer.

Two ways (pick one):

  A) Bluetooth LE (no pairing COM port needed):
       pip install bleak
       python -m ground_station.bt_sensor_recv
       python -m ground_station.bt_sensor_recv --scan   # list nearby BLE devices

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
from pathlib import Path

SERVICE_UUID = "6e400001-b5a3-f393-e0a9-e50e24dcca9e"
TX_CHAR_UUID = "6e400003-b5a3-f393-e0a9-e50e24dcca9e"
DEVICE_NAME = "AgriDrone-UNOQ"

# Names Windows / BlueZ sometimes show instead of our advertise name
_NAME_HINTS = (
    "agridrone-unoq",
    "agridrone",
    "unoq",
    "uno q",
    "arduino uno q",
    "arduino",
)


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


def _name_matches(name: str) -> bool:
    n = (name or "").lower()
    if not n:
        return False
    return any(h in n for h in _NAME_HINTS)


async def _discover(timeout: float):
    from bleak import BleakScanner

    print(f"Scanning for BLE devices ({timeout:.0f}s)...")
    # return_adv=True so we can match local_name + service UUIDs
    found = await BleakScanner.discover(timeout=timeout, return_adv=True)
    return found  # dict[str, tuple[BLEDevice, AdvertisementData]] on bleak>=0.21


async def run_scan(timeout: float) -> int:
    try:
        from bleak import BleakScanner  # noqa: F401
    except ImportError:
        print("Install bleak: pip install bleak", file=sys.stderr)
        return 1

    found = await _discover(timeout)
    if not found:
        print("No BLE devices seen. Turn Bluetooth ON on this PC and stay nearby.")
        return 2

    print(f"Found {len(found)} BLE advertisement(s):\n")
    for addr, (dev, adv) in found.items():
        name = dev.name or adv.local_name or "(no name)"
        uuids = list(adv.service_uuids or [])
        mark = ""
        if _name_matches(name) or SERVICE_UUID.lower() in [u.lower() for u in uuids]:
            mark = "  <--- likely UNO Q"
        print(f"  {addr}  {name}{mark}")
        if uuids:
            print(f"           services: {', '.join(uuids[:4])}")
    print(
        "\nIf you see AgriDrone-UNOQ / UNO Q above, run without --scan.\n"
        "If nothing looks right: App Lab must be Running, and on the UNO Q "
        "Monitor should show '[bt-ble] advertising as AgriDrone-UNOQ'."
    )
    return 0


def _pick_device(found: dict):
    """Prefer exact name, then service UUID, then loose name hints."""
    # Pass 1: exact / strong name
    for addr, (dev, adv) in found.items():
        name = (dev.name or adv.local_name or "")
        if DEVICE_NAME.lower() in name.lower():
            return dev
    # Pass 2: our Nordic UART service UUID in advertisement
    for addr, (dev, adv) in found.items():
        uuids = [u.lower() for u in (adv.service_uuids or [])]
        if SERVICE_UUID.lower() in uuids:
            return dev
    # Pass 3: loose Arduino / UNO Q name
    for addr, (dev, adv) in found.items():
        name = (dev.name or adv.local_name or "")
        if _name_matches(name):
            return dev
    return None


async def run_ble(log_path: Path | None, timeout: float) -> int:
    try:
        from bleak import BleakClient
    except ImportError:
        print("Install bleak: pip install bleak", file=sys.stderr)
        return 1

    found = await _discover(timeout)
    device = _pick_device(found) if found else None

    if device is None:
        print(
            f"Could not find {DEVICE_NAME}.\n"
            "Checklist:\n"
            "  1) App Lab app is Running on the UNO Q\n"
            "  2) Monitor shows: [bt-ble] advertising as AgriDrone-UNOQ\n"
            "  3) Bluetooth is ON on this laptop\n"
            "  4) Stay within a few meters\n"
            "  5) Debug: python -m ground_station.bt_sensor_recv --scan\n"
            "Or pair the board in Windows Bluetooth settings and use:\n"
            "  python -m ground_station.bt_sensor_recv --com COMx",
            file=sys.stderr,
        )
        if found:
            print("\nNearby BLE names (for debugging):", file=sys.stderr)
            for addr, (dev, adv) in list(found.items())[:20]:
                name = dev.name or adv.local_name or "(no name)"
                print(f"  {addr}  {name}", file=sys.stderr)
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
    p.add_argument(
        "--scan",
        action="store_true",
        help="Only list nearby BLE devices (helps when the PC cannot see the UNO Q)",
    )
    args = p.parse_args(argv)
    log_path = None if args.no_log else (args.log or Path("data/unoq_bt.jsonl"))

    try:
        if args.scan:
            return asyncio.run(run_scan(args.timeout))
        if args.com:
            return run_com(args.com, log_path, args.baud)
        return asyncio.run(run_ble(log_path, args.timeout))
    except KeyboardInterrupt:
        print("\nbye")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
