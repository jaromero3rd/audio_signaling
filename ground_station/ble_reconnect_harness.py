#!/usr/bin/env python3
"""Automated BLE reconnect / cold-start harness for AgriDrone-UNOQ.

Runs without manual connect/reconnect. Optional board control via adb.

  python -m ground_station.ble_reconnect_harness
  python -m ground_station.ble_reconnect_harness --cycles 8 --cold-start 2
  python -m ground_station.ble_reconnect_harness --address 14:B5:CD:E4:29:79

Exit 0 only if all requested phases pass.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

SERVICE_UUID = "6e400001-b5a3-f393-e0a9-e50e24dcca9e"
TX_CHAR_UUID = "6e400003-b5a3-f393-e0a9-e50e24dcca9e"
DEVICE_NAME = "AgriDrone-UNOQ"
DEFAULT_ADDR = "14:B5:CD:E4:29:79"

ADB_CANDIDATES = [
    Path.home() / "AppData/Local/AppLab/a15/data/packages/arduino/tools/adb/32.0.0/adb.exe",
    Path.home() / "AppData/Local/Arduino15/packages/arduino/tools/adb/32.0.0/adb.exe",
]


def _log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def _find_adb() -> str | None:
    for p in ADB_CANDIDATES:
        if p.is_file():
            return str(p)
    which = shutil.which("adb")
    return which


def _adb(adb: str, *args: str, timeout: float = 30.0) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [adb, *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def adb_available(adb: str | None) -> bool:
    if not adb:
        return False
    r = _adb(adb, "devices")
    return any(line.endswith("\tdevice") for line in r.stdout.splitlines())


def board_shell(adb: str, cmd: str, timeout: float = 60.0) -> str:
    r = _adb(adb, "shell", cmd, timeout=timeout)
    out = (r.stdout or "") + (r.stderr or "")
    return out


def board_bt_snapshot(adb: str) -> dict:
    out = board_shell(
        adb,
        "bluetoothctl show 2>/dev/null | grep -E 'Powered|Alias|Pairable|Nordic|ActiveInstances'; "
        "echo ---; pgrep -af 'python3 /home/arduino/host_bt_relay' || echo NO_RELAY; "
        "echo ---; bluetoothctl devices Paired; bluetoothctl devices Connected",
    )
    return {
        "powered": "Powered: yes" in out,
        "nordic": "Nordic UART" in out or "6e400001" in out.lower(),
        "active": "ActiveInstances: 0x01" in out or "ActiveInstances: 0x1" in out
        or "(1)" in out and "ActiveInstances" in out,
        "relay": "host_bt_relay.py" in out and "NO_RELAY" not in out.split("---")[1]
        if "---" in out
        else "host_bt_relay.py" in out,
        "raw": out,
    }


def simulate_cold_start(adb: str) -> None:
    """Approximate cold BT boot: power cycle adapter + restart relay."""
    _log("BOARD cold-start sim: BT power cycle + relay restart")
    board_shell(
        adb,
        "bluetoothctl power off; sleep 2; "
        "rfkill unblock bluetooth; bluetoothctl power on; "
        "bluetoothctl pairable off; "
        "pkill -f '/home/arduino/host_bt_relay.py' || true; "
        "sleep 1; "
        "nohup bash /home/arduino/start_bt_relay.sh >/tmp/start_bt_relay.out 2>&1 & "
        "sleep 6",
        timeout=90.0,
    )


def push_relay(adb: str, local: Path) -> None:
    _log(f"BOARD push {local.name}")
    r = _adb(adb, "push", str(local), "/home/arduino/host_bt_relay.py", timeout=60.0)
    if r.returncode != 0:
        raise RuntimeError(f"adb push failed: {r.stderr or r.stdout}")
    board_shell(
        adb,
        "pkill -f '/home/arduino/host_bt_relay.py' || true; sleep 1; "
        "nohup bash /home/arduino/start_bt_relay.sh >/tmp/start_bt_relay.out 2>&1 & "
        "sleep 6",
        timeout=60.0,
    )


async def _wait_visible(address: str, timeout: float) -> object | None:
    from bleak import BleakScanner

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        rem = max(1.0, min(6.0, deadline - time.monotonic()))
        dev = await BleakScanner.find_device_by_address(address, timeout=rem)
        if dev is not None:
            return dev
        await asyncio.sleep(0.4)
    return None


async def connect_and_sample(
    address: str,
    *,
    find_timeout: float,
    sample_timeout: float,
    min_samples: int,
) -> tuple[bool, str]:
    from bleak import BleakClient

    dev = await _wait_visible(address, find_timeout)
    if dev is None:
        return False, "not_advertising"

    got: list[bytes] = []

    def on_notify(_h: int, data: bytearray) -> None:
        got.append(bytes(data))

    try:
        async with BleakClient(dev, timeout=35.0) as client:
            await asyncio.sleep(1.2)
            chars = [
                str(ch.uuid).lower()
                for s in client.services
                for ch in s.characteristics
            ]
            if TX_CHAR_UUID.lower() not in chars:
                return False, f"no_tx_gatt n={len(chars)}"
            await client.start_notify(TX_CHAR_UUID, on_notify)
            deadline = time.monotonic() + sample_timeout
            while time.monotonic() < deadline and len(got) < min_samples:
                await asyncio.sleep(0.15)
            if len(got) < min_samples:
                return False, f"few_samples={len(got)}"
            # Validate JSON line
            text = got[-1].decode("utf-8", errors="replace")
            line = text.strip().splitlines()[-1] if text.strip() else ""
            try:
                sample = json.loads(line)
            except json.JSONDecodeError:
                return False, f"bad_json={line[:60]!r}"
            if sample.get("temp_c") is None and sample.get("ok") is None:
                return False, f"empty_sample={sample!r}"
            return True, f"ok samples={len(got)} T={sample.get('temp_c')}"
    except Exception as exc:  # noqa: BLE001
        return False, f"{type(exc).__name__}:{exc}"


async def phase_reconnect(
    address: str,
    cycles: int,
    *,
    find_timeout: float,
    gap_s: float,
) -> bool:
    _log(f"PHASE reconnect x{cycles}")
    ok_n = 0
    for i in range(1, cycles + 1):
        ok, detail = await connect_and_sample(
            address,
            find_timeout=find_timeout,
            sample_timeout=8.0,
            min_samples=3,
        )
        _log(f"  cycle {i}/{cycles}: {'PASS' if ok else 'FAIL'} ({detail})")
        if not ok:
            # one immediate retry after short wait (board may be re-advertising)
            await asyncio.sleep(2.0)
            ok2, detail2 = await connect_and_sample(
                address,
                find_timeout=find_timeout,
                sample_timeout=8.0,
                min_samples=3,
            )
            _log(f"  cycle {i} retry: {'PASS' if ok2 else 'FAIL'} ({detail2})")
            if not ok2:
                return False
        ok_n += 1
        await asyncio.sleep(gap_s)
    _log(f"PHASE reconnect PASS ({ok_n}/{cycles})")
    return True


async def phase_cold_start(
    address: str,
    adb: str,
    rounds: int,
    *,
    find_timeout: float,
) -> bool:
    _log(f"PHASE cold-start sim x{rounds}")
    for i in range(1, rounds + 1):
        simulate_cold_start(adb)
        # Wait until board reports nordic + preferably ActiveInstances
        deadline = time.monotonic() + 45.0
        while time.monotonic() < deadline:
            snap = board_bt_snapshot(adb)
            if snap["powered"] and snap["nordic"] and snap["relay"]:
                break
            await asyncio.sleep(1.5)
        else:
            _log(f"  cold {i}: FAIL board never ready")
            _log(board_bt_snapshot(adb)["raw"][:500])
            return False
        await asyncio.sleep(2.0)
        ok, detail = await connect_and_sample(
            address,
            find_timeout=find_timeout,
            sample_timeout=10.0,
            min_samples=3,
        )
        _log(f"  cold {i}/{rounds}: {'PASS' if ok else 'FAIL'} ({detail})")
        if not ok:
            snap = board_bt_snapshot(adb)
            _log(f"  board snap: powered={snap['powered']} nordic={snap['nordic']} "
                 f"active={snap['active']} relay={snap['relay']}")
            return False
    _log("PHASE cold-start PASS")
    return True


async def phase_stress_drop(
    address: str,
    cycles: int,
    *,
    find_timeout: float,
) -> bool:
    """Connect, take 1 sample, hard-drop, immediately try again (worst case)."""
    _log(f"PHASE stress-drop x{cycles}")
    for i in range(1, cycles + 1):
        ok, detail = await connect_and_sample(
            address,
            find_timeout=find_timeout,
            sample_timeout=6.0,
            min_samples=1,
        )
        _log(f"  drop {i}/{cycles}: {'PASS' if ok else 'FAIL'} ({detail})")
        if not ok:
            return False
        await asyncio.sleep(0.5)
    _log("PHASE stress-drop PASS")
    return True


async def amain(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--address", default=DEFAULT_ADDR)
    p.add_argument("--cycles", type=int, default=6, help="reconnect cycles")
    p.add_argument("--stress", type=int, default=4, help="fast drop cycles")
    p.add_argument("--cold-start", type=int, default=2, help="adb cold-start sims")
    p.add_argument("--find-timeout", type=float, default=18.0)
    p.add_argument("--gap", type=float, default=2.0)
    p.add_argument("--push-relay", type=Path, default=None,
                   help="push this host_bt_relay.py before tests")
    p.add_argument("--skip-cold", action="store_true")
    args = p.parse_args(argv)

    adb = _find_adb()
    has_adb = adb_available(adb)
    _log(f"target={args.address} adb={'yes' if has_adb else 'no'}")

    if has_adb and args.push_relay is not None:
        push_relay(adb, args.push_relay)  # type: ignore[arg-type]
        await asyncio.sleep(2.0)

    results: dict[str, bool] = {}

    results["reconnect"] = await phase_reconnect(
        args.address, args.cycles, find_timeout=args.find_timeout, gap_s=args.gap
    )
    if results["reconnect"]:
        results["stress"] = await phase_stress_drop(
            args.address, args.stress, find_timeout=args.find_timeout
        )
    else:
        results["stress"] = False

    if args.skip_cold or args.cold_start <= 0:
        results["cold"] = True
        _log("PHASE cold-start SKIPPED")
    elif not has_adb:
        results["cold"] = False
        _log("PHASE cold-start FAIL (no adb — plug USB for cold-start sim)")
    else:
        results["cold"] = await phase_cold_start(
            args.address, adb, args.cold_start, find_timeout=max(args.find_timeout, 25.0)  # type: ignore[arg-type]
        )

    _log("SUMMARY " + " ".join(f"{k}={'PASS' if v else 'FAIL'}" for k, v in results.items()))
    return 0 if all(results.values()) else 2


def main() -> None:
    try:
        raise SystemExit(asyncio.run(amain()))
    except KeyboardInterrupt:
        raise SystemExit(130)


if __name__ == "__main__":
    main()
