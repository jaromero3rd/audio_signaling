#!/usr/bin/env python3
"""Real Bluetooth for UNO Q — run on the Debian HOST, not in App Lab.

Why: App Lab's Python container cannot access BlueZ (Errno 2).
This script runs on the host OS where Bluetooth works.

Setup (from your PC, board plugged in USB):
  adb shell
  # then inside the board:
  pip3 install --user bless
  # copy/sync this file, or clone the repo on the board, then:
  python3 host_bt_relay.py

Leave it running. Then Run the App Lab sensor app.
Laptop:
  pip install bleak
  python -m ground_station.bt_sensor_recv
"""

from __future__ import annotations

import asyncio
import socket
import subprocess
import threading
from typing import Any

SERVICE_UUID = "6e400001-b5a3-f393-e0a9-e50e24dcca9e"
TX_CHAR_UUID = "6e400003-b5a3-f393-e0a9-e50e24dcca9e"
RX_CHAR_UUID = "6e400002-b5a3-f393-e0a9-e50e24dcca9e"
DEVICE_NAME = "AgriDrone-UNOQ"
LISTEN_PORT = 9124


def _bt_on() -> None:
    for cmd in (
        ["bluetoothctl", "power", "on"],
        ["bluetoothctl", "pairable", "on"],
        ["bluetoothctl", "discoverable", "on"],
    ):
        try:
            subprocess.run(cmd, capture_output=True, text=True, timeout=8)
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as exc:
            print(f"[host] {cmd}: {exc}", flush=True)


class Relay:
    def __init__(self) -> None:
        self.server: Any = None
        self.loop: asyncio.AbstractEventLoop | None = None
        self.count = 0

    def push(self, line: bytes) -> None:
        if not line.endswith(b"\n"):
            line = line + b"\n"
        if self.server is None or self.loop is None:
            return

        async def _notify() -> None:
            char = self.server.get_characteristic(TX_CHAR_UUID)
            char.value = bytearray(line)
            self.server.update_value(SERVICE_UUID, TX_CHAR_UUID)

        try:
            asyncio.run_coroutine_threadsafe(_notify(), self.loop)
            self.count += 1
            if self.count % 20 == 1:
                print(f"[host] BLE notify #{self.count}: {line[:90]!r}", flush=True)
        except Exception as exc:  # noqa: BLE001
            print(f"[host] notify failed: {exc}", flush=True)


def tcp_accept(relay: Relay) -> None:
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("0.0.0.0", LISTEN_PORT))
    srv.listen(4)
    print(f"[host] waiting for App Lab on TCP :{LISTEN_PORT}", flush=True)
    while True:
        client, addr = srv.accept()
        print(f"[host] App Lab connected from {addr}", flush=True)
        threading.Thread(target=_read_client, args=(client, relay), daemon=True).start()


def _read_client(client: socket.socket, relay: Relay) -> None:
    buf = b""
    try:
        while True:
            chunk = client.recv(4096)
            if not chunk:
                break
            buf += chunk
            while b"\n" in buf:
                line, _, buf = buf.partition(b"\n")
                if line:
                    relay.push(line + b"\n")
    except OSError:
        pass
    finally:
        try:
            client.close()
        except OSError:
            pass
        print("[host] App Lab disconnected", flush=True)


async def ble_main(relay: Relay) -> None:
    from bless import (  # type: ignore
        BlessServer,
        GATTAttributePermissions,
        GATTCharacteristicProperties,
    )

    def perm(*names: str):
        for name in names:
            if hasattr(GATTAttributePermissions, name):
                return getattr(GATTAttributePermissions, name)
        return 0

    _bt_on()
    loop = asyncio.get_running_loop()
    relay.loop = loop
    server = BlessServer(name=DEVICE_NAME, loop=loop)
    relay.server = server

    tx = GATTCharacteristicProperties.read | GATTCharacteristicProperties.notify
    rx = (
        GATTCharacteristicProperties.write
        | GATTCharacteristicProperties.write_without_response
    )
    await server.add_new_service(SERVICE_UUID)
    await server.add_new_characteristic(
        SERVICE_UUID, TX_CHAR_UUID, tx, None, perm("readable", "read")
    )
    await server.add_new_characteristic(
        SERVICE_UUID,
        RX_CHAR_UUID,
        rx,
        None,
        perm("writeable", "writable", "write") or perm("readable", "read"),
    )
    await server.start()
    print(f"[host] BLE advertising as {DEVICE_NAME}", flush=True)
    print("[host] On laptop: python -m ground_station.bt_sensor_recv", flush=True)
    while True:
        await asyncio.sleep(3600)


def main() -> None:
    print("[host] UNO Q Bluetooth relay starting...", flush=True)
    relay = Relay()
    threading.Thread(target=tcp_accept, args=(relay,), daemon=True).start()
    asyncio.run(ble_main(relay))


if __name__ == "__main__":
    main()
