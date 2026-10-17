"""Bluetooth LE peripheral: stream sensor JSON to a nearby computer.

Advertises as AgriDrone-UNOQ. Laptop connects with bleak
(ground_station/bt_sensor_recv.py).
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from typing import Any

# Nordic UART-ish custom service (easy for bleak clients)
SERVICE_UUID = "6e400001-b5a3-f393-e0a9-e50e24dcca9e"
TX_CHAR_UUID = "6e400003-b5a3-f393-e0a9-e50e24dcca9e"  # notify -> computer
RX_CHAR_UUID = "6e400002-b5a3-f393-e0a9-e50e24dcca9e"  # write from computer (unused)

DEVICE_NAME = "AgriDrone-UNOQ"


class BtSensorStream:
    def __init__(self) -> None:
        self._loop: asyncio.AbstractEventLoop | None = None
        self._server: Any = None
        self._ready = threading.Event()
        self._error: str | None = None
        self.sent = 0

    def start(self) -> None:
        t = threading.Thread(target=self._thread_main, name="bt-stream", daemon=True)
        t.start()
        # Don't block forever if BLE stack is missing
        self._ready.wait(timeout=8)

    def _thread_main(self) -> None:
        try:
            asyncio.run(self._async_main())
        except Exception as exc:  # noqa: BLE001
            self._error = str(exc)
            print(f"[bt] failed: {exc}")
            self._ready.set()

    async def _async_main(self) -> None:
        try:
            from bless import BlessServer  # type: ignore
            from bless.backends.characteristic import (  # type: ignore
                GattCharacteristicsFlags as Flags,
            )
        except ImportError as exc:
            self._error = f"bless not installed: {exc}"
            print("[bt] install bless on the UNO Q (see requirements.txt)")
            self._ready.set()
            return

        self._loop = asyncio.get_running_loop()
        server = BlessServer(name=DEVICE_NAME, loop=self._loop)
        self._server = server

        await server.add_new_service(SERVICE_UUID)
        await server.add_new_characteristic(
            SERVICE_UUID,
            TX_CHAR_UUID,
            Flags.read | Flags.notify,
            None,
            None,
        )
        await server.add_new_characteristic(
            SERVICE_UUID,
            RX_CHAR_UUID,
            Flags.write | Flags.write_without_response,
            None,
            None,
        )

        await server.start()
        print(f"[bt] advertising as {DEVICE_NAME}")
        self._ready.set()
        while True:
            await asyncio.sleep(3600)

    def send_sample(self, payload: dict[str, Any]) -> bool:
        if self._error:
            return False
        if self._server is None or self._loop is None:
            return False
        line = (json.dumps(payload, separators=(",", ":")) + "\n").encode("utf-8")
        # BLE notify payloads should stay small
        if len(line) > 180:
            line = line[:179] + b"\n"

        async def _push() -> None:
            self._server.get_characteristic(TX_CHAR_UUID).value = line
            self._server.update_value(SERVICE_UUID, TX_CHAR_UUID)

        try:
            fut = asyncio.run_coroutine_threadsafe(_push(), self._loop)
            fut.result(timeout=2)
            self.sent += 1
            return True
        except Exception as exc:  # noqa: BLE001
            print(f"[bt] notify failed: {exc}")
            return False

    @property
    def ok(self) -> bool:
        return self._error is None and self._server is not None
