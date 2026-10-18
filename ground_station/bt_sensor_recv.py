#!/usr/bin/env python3
"""Receive UNO Q sensor JSON on the ground-station computer.

  A) TCP (recommended — App Lab usually blocks on-board BLE):
       python -m ground_station.bt_sensor_recv --tcp 192.168.x.x:9123

  B) Bluetooth LE (needs host_bt_relay.py on the UNO Q Debian host):
       pip install bleak
       python -m ground_station.bt_sensor_recv
       python -m ground_station.bt_sensor_recv --scan

  C) Classic Bluetooth Serial:
       python -m ground_station.bt_sensor_recv --com COM5
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


def _parse_serial_text(text: str) -> dict | None:
    """MCU Serial Monitor lines: #12  T=22.1C  RH=40%  light_raw=123"""
    import re

    m = re.search(
        r"T=(?P<t>[\d.]+|----)C?\s+RH=(?P<rh>[\d.]+|----)%?\s+light_raw=(?P<raw>-?\d+)",
        text,
    )
    if not m:
        return None
    t, rh = m.group("t"), m.group("rh")
    return {
        "ok": t != "----",
        "source": "serial",
        "sensor": "DHT11",
        "temp_c": None if t == "----" else float(t),
        "humidity_pct": None if rh == "----" else float(rh),
        "light_raw": int(m.group("raw")),
    }


def _handle_line(text: str, log_path: Path | None) -> None:
    text = text.strip()
    if not text:
        return
    try:
        sample = json.loads(text)
    except json.JSONDecodeError:
        sample = _parse_serial_text(text)
        if sample is None:
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


async def run_ble(
    log_path: Path | None,
    timeout: float,
    preferred_address: str | None = None,
) -> int:
    try:
        from bleak import BleakClient, BleakScanner
    except ImportError:
        print("Install bleak: pip install bleak", file=sys.stderr)
        return 1

    # Last known board MAC from earlier successful sessions on this laptop.
    target_addr = (preferred_address or "").strip() or "14:B5:CD:E4:29:79"
    print(f"Ground station BLE — looking for {DEVICE_NAME} (hint {target_addr})")
    print(
        "Remove AgriDrone-UNOQ from Windows Bluetooth if it shows as headphones. "
        "Do not pair.",
        flush=True,
    )
    buf = bytearray()
    last_data = time.monotonic()

    def on_notify(_handle: int, data: bytearray) -> None:
        nonlocal buf, last_data
        last_data = time.monotonic()
        buf.extend(data)
        while b"\n" in buf:
            line, _, buf = buf.partition(b"\n")
            _handle_line(line.decode("utf-8", errors="replace"), log_path)

    attempt = 0
    while True:
        attempt += 1
        disconnected = asyncio.Event()

        def _on_disconnect(_client: object) -> None:
            disconnected.set()

        try:
            print(f"Scanning/connecting (attempt {attempt})...", flush=True)
            # After a board power-cycle, connect by known MAC first (fast).
            # Fall back to a scan if the address is not visible yet.
            live = None
            if target_addr:
                print(f"Trying known address {target_addr}…", flush=True)
                live = await BleakScanner.find_device_by_address(
                    target_addr, timeout=max(6.0, min(timeout, 12.0))
                )
            if live is None:
                found = await _discover(timeout)
                live = _pick_device(found) if found else None
            if live is None and target_addr:
                live = await BleakScanner.find_device_by_address(
                    target_addr, timeout=max(8.0, timeout / 2)
                )
            if live is None:
                raise RuntimeError(
                    f"{DEVICE_NAME} not advertising right now — after a disconnect "
                    "the board must re-advertise (host_bt_relay). Keep nearby; "
                    "dismiss Windows pair popups; App Lab Running"
                )
            target_addr = live.address
            print(f"Found {live.address} ({live.name or DEVICE_NAME})", flush=True)
            async with BleakClient(
                live,
                timeout=45.0,
                disconnected_callback=_on_disconnect,
            ) as client:
                # Cold-boot: GATT may appear before Nordic UART is registered.
                # Windows classic audio bonding also hides TX — fail fast and
                # reconnect (board relay strips those pairs automatically).
                tx_ok = False
                for gatt_try in range(1, 4):
                    await asyncio.sleep(0.6 if gatt_try == 1 else 1.0)
                    try:
                        services = await client.get_services()
                    except Exception:  # noqa: BLE001
                        services = client.services
                    tx_ok = any(
                        str(char.uuid).lower() == TX_CHAR_UUID.lower()
                        for svc in services
                        for char in svc.characteristics
                    )
                    if tx_ok:
                        break
                    print(
                        f"GATT not ready yet (try {gatt_try}/3) — waiting for TX char…",
                        flush=True,
                    )
                if not tx_ok:
                    try:
                        await client.disconnect()
                    except Exception:  # noqa: BLE001
                        pass
                    raise RuntimeError(
                        "TX characteristic missing (often Windows classic/audio pair). "
                        "Dismiss any Bluetooth pair popup; remove AgriDrone-UNOQ / "
                        "Headphones for that device in Settings if present. Do not pair."
                    )
                print("Connected — streaming (Ctrl+C to stop)", flush=True)
                await client.start_notify(TX_CHAR_UUID, on_notify)
                last_data = time.monotonic()
                try:
                    while not disconnected.is_set():
                        if time.monotonic() - last_data > 15.0:
                            print(
                                "[warn] no samples for 15s — is App Lab still Running?",
                                flush=True,
                            )
                            last_data = time.monotonic()
                        try:
                            await asyncio.wait_for(disconnected.wait(), timeout=5.0)
                        except asyncio.TimeoutError:
                            pass
                finally:
                    try:
                        await client.stop_notify(TX_CHAR_UUID)
                    except Exception:  # noqa: BLE001
                        pass
            print("BLE disconnected — reconnecting in 2s...", flush=True)
        except Exception as exc:  # noqa: BLE001
            msg = str(exc).strip() or repr(exc)
            print(f"BLE error: {msg} — retrying in 3s...", flush=True)
            await asyncio.sleep(3.0)
            continue
        await asyncio.sleep(2.0)


def _pick_usb_serial() -> str:
    try:
        from serial.tools import list_ports
    except ImportError as exc:
        raise RuntimeError("pip install pyserial") from exc
    ports = list(list_ports.comports())
    if not ports:
        raise RuntimeError("No serial ports. Plug the Uno Q into this computer over USB.")
    print("Serial ports:")
    for p in ports:
        print(f"  {p.device}  {p.description}")
    for p in ports:
        name = f"{p.device} {p.description}".lower()
        if any(tok in name for tok in ("usbmodem", "usbserial", "acm", "uno", "arduino")):
            return p.device
    return ports[0].device


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


def run_tcp(target: str, log_path: Path | None) -> int:
    """Connect to relay/App Lab TCP stream; auto-reconnects on drop."""
    import socket
    import time as _time

    if ":" in target:
        host, _, port_s = target.rpartition(":")
        port = int(port_s)
    else:
        host, port = target, 9123

    attempt = 0
    while True:
        attempt += 1
        try:
            print(f"Connecting TCP to {host}:{port} (attempt {attempt})...", flush=True)
            with socket.create_connection((host, port), timeout=10) as sock:
                print("Connected — streaming (Ctrl+C to stop)", flush=True)
                sock.settimeout(1.0)
                buf = ""
                while True:
                    try:
                        chunk = sock.recv(4096)
                    except socket.timeout:
                        continue
                    if not chunk:
                        print("Connection closed — reconnecting in 2s...", flush=True)
                        break
                    buf += chunk.decode("utf-8", errors="replace")
                    while "\n" in buf:
                        line, _, buf = buf.partition("\n")
                        _handle_line(line, log_path)
        except OSError as exc:
            print(f"TCP error: {exc} — retrying in 3s...", flush=True)
            _time.sleep(3.0)
            continue
        _time.sleep(2.0)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Receive UNO Q Bluetooth/TCP sensor stream")
    p.add_argument("--timeout", type=float, default=25.0, help="BLE scan timeout")
    p.add_argument(
        "--com",
        help="USB serial: 'auto' or a port like /dev/cu.usbmodem1101 or COM5",
    )
    p.add_argument(
        "--tcp",
        help="TCP to App Lab stream, e.g. 192.168.1.50:9123 (works when BLE is blocked)",
    )
    p.add_argument("--baud", type=int, default=9600)
    p.add_argument("--log", type=Path, default=None)
    p.add_argument("--no-log", action="store_true")
    p.add_argument(
        "--scan",
        action="store_true",
        help="Only list nearby BLE devices (helps when the PC cannot see the UNO Q)",
    )
    p.add_argument(
        "--address",
        default=None,
        help="Preferred BLE MAC, e.g. 14:B5:CD:E4:29:79",
    )
    args = p.parse_args(argv)
    log_path = None if args.no_log else (args.log or Path("data/unoq_bt.jsonl"))

    try:
        if args.scan:
            return asyncio.run(run_scan(args.timeout))
        if args.tcp:
            return run_tcp(args.tcp, log_path)
        if args.com:
            port = args.com
            if port.lower() in ("auto", "list"):
                port = _pick_usb_serial()
                print(f"Using {port}")
            baud = args.baud if args.baud != 9600 else 115200
            return run_com(port, log_path, baud)
        return asyncio.run(run_ble(log_path, args.timeout, args.address))
    except KeyboardInterrupt:
        print("\nbye")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
