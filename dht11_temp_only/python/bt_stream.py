"""Send sensor JSON from the UNO Q to another computer over Bluetooth.

Two paths (both start; whichever the laptop uses will work):
  1) BLE peripheral "AgriDrone-UNOQ" (notify) — laptop: bleak
  2) Classic RFCOMM channel 1 — laptop pairs then opens the COM port

Payload is one JSON object per line.
"""

from __future__ import annotations

import asyncio
import json
import socket
import subprocess
import threading
from typing import Any

SERVICE_UUID = "6e400001-b5a3-f393-e0a9-e50e24dcca9e"
TX_CHAR_UUID = "6e400003-b5a3-f393-e0a9-e50e24dcca9e"
RX_CHAR_UUID = "6e400002-b5a3-f393-e0a9-e50e24dcca9e"
DEVICE_NAME = "AgriDrone-UNOQ"
RFCOMM_CHANNEL = 1


def _run_bt_cmd(args: list[str], timeout: float = 8.0) -> str:
    try:
        r = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        out = (r.stdout or "") + (r.stderr or "")
        return out.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as exc:
        return f"(cmd failed: {exc})"


def ensure_bluetooth_ready() -> None:
    """Power on the UNO Q Bluetooth radio and make it discoverable.

    Without this, Windows / bleak often never see AgriDrone-UNOQ.
    """
    print("[bt] ensuring Bluetooth adapter is ON + discoverable...", flush=True)

    for cmd in (
        ["bluetoothctl", "power", "on"],
        ["bluetoothctl", "pairable", "on"],
        ["bluetoothctl", "discoverable", "on"],
        ["bluetoothctl", "agent", "on"],
        ["bluetoothctl", "default-agent"],
    ):
        out = _run_bt_cmd(cmd)
        if out:
            print(f"[bt] {' '.join(cmd)} -> {out}", flush=True)

    hci = _run_bt_cmd(["hciconfig", "hci0", "up"])
    if hci and "failed" not in hci.lower():
        print(f"[bt] hciconfig hci0 up -> {hci or 'ok'}", flush=True)
    _run_bt_cmd(["hciconfig", "hci0", "piscan"])

    show = _run_bt_cmd(["bluetoothctl", "show"])
    if show:
        for line in show.splitlines():
            low = line.lower()
            if any(k in low for k in ("powered", "discoverable", "pairable", "name", "alias")):
                print(f"[bt] {line.strip()}", flush=True)


class BtSensorStream:
    def __init__(self) -> None:
        self._ble_loop: asyncio.AbstractEventLoop | None = None
        self._ble_server: Any = None
        self._ble_error: str | None = None
        self._rfcomm_clients: list[socket.socket] = []
        self._rfcomm_lock = threading.Lock()
        self._ready = threading.Event()
        self.sent = 0
        self.mode = "starting"

    def start(self) -> None:
        ensure_bluetooth_ready()
        threading.Thread(target=self._ble_thread, name="bt-ble", daemon=True).start()
        threading.Thread(target=self._rfcomm_thread, name="bt-rfcomm", daemon=True).start()
        self._ready.wait(timeout=12)

    # --- BLE ---
    def _ble_thread(self) -> None:
        try:
            asyncio.run(self._ble_main())
        except Exception as exc:  # noqa: BLE001
            self._ble_error = str(exc)
            print(f"[bt-ble] failed: {exc}", flush=True)
            self._ready.set()

    async def _ble_main(self) -> None:
        try:
            from bless import BlessServer  # type: ignore
            from bless.backends.characteristic import (  # type: ignore
                GattCharacteristicsFlags as Flags,
            )
        except ImportError as exc:
            self._ble_error = f"bless missing: {exc}"
            print("[bt-ble] pip install bless  (on the UNO Q)", flush=True)
            self._ready.set()
            return

        self._ble_loop = asyncio.get_running_loop()
        server = BlessServer(name=DEVICE_NAME, loop=self._ble_loop)
        self._ble_server = server
        await server.add_new_service(SERVICE_UUID)
        await server.add_new_characteristic(
            SERVICE_UUID, TX_CHAR_UUID, Flags.read | Flags.notify, None, None
        )
        await server.add_new_characteristic(
            SERVICE_UUID,
            RX_CHAR_UUID,
            Flags.write | Flags.write_without_response,
            None,
            None,
        )
        await server.start()
        self.mode = "ble+rfcomm"
        print(f"[bt-ble] advertising as {DEVICE_NAME}", flush=True)
        # Re-assert discoverable after GATT server claims the adapter
        ensure_bluetooth_ready()
        self._ready.set()
        while True:
            await asyncio.sleep(3600)

    # --- Classic RFCOMM ---
    def _rfcomm_thread(self) -> None:
        try:
            srv = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_STREAM, socket.BTPROTO_RFCOMM)
            srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            srv.bind(("", RFCOMM_CHANNEL))
            srv.listen(1)
            print(
                f"[bt-rfcomm] listening on channel {RFCOMM_CHANNEL} "
                f"(pair phone/PC then open Serial/COM)",
                flush=True,
            )
            self._ready.set()
            while True:
                client, addr = srv.accept()
                print(f"[bt-rfcomm] client connected: {addr}", flush=True)
                with self._rfcomm_lock:
                    self._rfcomm_clients.append(client)
        except Exception as exc:  # noqa: BLE001
            print(f"[bt-rfcomm] unavailable: {exc}", flush=True)
            self._ready.set()

    def send_sample(self, payload: dict[str, Any]) -> bool:
        line = (json.dumps(payload, separators=(",", ":")) + "\n").encode("utf-8")
        if len(line) > 180:
            small = {
                "ok": payload.get("ok"),
                "temp_c": payload.get("temp_c"),
                "humidity_pct": payload.get("humidity_pct"),
                "light": payload.get("light"),
                "light_raw": payload.get("light_raw"),
                "ts": payload.get("ts"),
            }
            line = (json.dumps(small, separators=(",", ":")) + "\n").encode("utf-8")

        ok = False
        ok = self._send_ble(line) or ok
        ok = self._send_rfcomm(line) or ok
        if ok:
            self.sent += 1
        return ok

    def _send_ble(self, line: bytes) -> bool:
        if self._ble_error or self._ble_server is None or self._ble_loop is None:
            return False

        async def _push() -> None:
            self._ble_server.get_characteristic(TX_CHAR_UUID).value = line
            self._ble_server.update_value(SERVICE_UUID, TX_CHAR_UUID)

        try:
            fut = asyncio.run_coroutine_threadsafe(_push(), self._ble_loop)
            fut.result(timeout=1.5)
            return True
        except Exception:
            return False

    def _send_rfcomm(self, line: bytes) -> bool:
        with self._rfcomm_lock:
            clients = list(self._rfcomm_clients)
        if not clients:
            return False
        alive: list[socket.socket] = []
        sent_any = False
        for c in clients:
            try:
                c.sendall(line)
                alive.append(c)
                sent_any = True
            except OSError:
                try:
                    c.close()
                except OSError:
                    pass
        with self._rfcomm_lock:
            self._rfcomm_clients = alive
        return sent_any

    @property
    def ok(self) -> bool:
        return self._ble_server is not None or True
