"""Arduino Bridge RPC helpers for Uno Q — real board vs mock.

On hardware, replace `_try_import_bridge` body with the official
Arduino_RouterBridge / App Lab Python client once confirmed.
"""

from __future__ import annotations

import json
import time
from typing import Any


class BridgeClient:
    def __init__(self, mock: bool = False) -> None:
        self.mock = mock
        self._bridge = None if mock else _try_import_bridge()
        self._led_mode = 0

    def read_sensors(self) -> str | dict[str, Any]:
        if self.mock or self._bridge is None:
            # MCU sketch format; coordinator may also use MockSensorSource.
            t = time.time()
            return {
                "temperature_c": 21.5,
                "humidity_pct": 50.0,
                "pressure_hpa": 1013.0,
                "millis": int(t * 1000) % 1_000_000_000,
                "ok": True,
            }
        # VERIFY: method name must match sensor_bridge.ino registration
        return self._bridge.call("readSensors")

    def sd_write_line(self, line: str) -> bool:
        if self.mock or self._bridge is None:
            return False  # Linux sd_logger writes to filesystem in mock/offline
        return bool(self._bridge.call("sdWriteLine", line))

    def set_led_mode(self, mode: int) -> None:
        self._led_mode = int(mode)
        if self.mock or self._bridge is None:
            return
        self._bridge.call("setLedMode", int(mode))

    @property
    def led_mode(self) -> int:
        return self._led_mode


def _try_import_bridge() -> Any | None:
    """Attempt to load board Bridge; return None if unavailable (laptop)."""
    try:
        # Placeholder module names — adapt when Maxim confirms App Lab package.
        import importlib

        for name in ("arduino_bridge", "RouterBridge", "uno_q_bridge"):
            try:
                mod = importlib.import_module(name)
            except ImportError:
                continue
            if hasattr(mod, "Bridge"):
                return mod.Bridge()
            if hasattr(mod, "connect"):
                return mod.connect()
    except Exception:
        return None
    return None


def encode_line(obj: dict[str, Any]) -> str:
    return json.dumps(obj, separators=(",", ":"))
