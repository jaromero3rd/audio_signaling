"""Sensor ingestion: Bridge RPC or mock generator (extensible fields)."""

from __future__ import annotations

import json
import math
import time
from dataclasses import asdict, dataclass, field
from typing import Any

from .bridge_rpc import BridgeClient
from .config import Config


@dataclass
class SensorSample:
    """Canonical sample schema — extend with extra={} for more sensors."""

    temperature_c: float
    humidity_pct: float
    pressure_hpa: float | None = None
    timestamp: float = field(default_factory=time.time)
    source: str = "mock"
    ok: bool = True
    mission: dict[str, Any] = field(default_factory=dict)
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        return d

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), separators=(",", ":"))


class SensorSource:
    def read(self, mission: dict[str, Any] | None = None) -> SensorSample:
        raise NotImplementedError


class MockSensorSource(SensorSource):
    """Deterministic-ish sine wave so demos look alive without hardware."""

    def __init__(self) -> None:
        self._t0 = time.time()

    def read(self, mission: dict[str, Any] | None = None) -> SensorSample:
        t = time.time() - self._t0
        temp = 22.0 + 3.0 * math.sin(t / 17.0)
        hum = 55.0 + 8.0 * math.sin(t / 23.0 + 1.2)
        return SensorSample(
            temperature_c=round(temp, 2),
            humidity_pct=round(hum, 2),
            pressure_hpa=round(1013.25 + math.sin(t / 40.0), 2),
            source="mock",
            ok=True,
            mission=dict(mission or {}),
            extra={"note": "simulated; swap backend=bridge on Uno Q"},
        )


class BridgeSensorSource(SensorSource):
    def __init__(self, bridge: BridgeClient) -> None:
        self._bridge = bridge

    def read(self, mission: dict[str, Any] | None = None) -> SensorSample:
        raw = self._bridge.read_sensors()
        data: dict[str, Any]
        if isinstance(raw, str):
            data = json.loads(raw)
        else:
            data = dict(raw)
        return SensorSample(
            temperature_c=float(data.get("temperature_c", float("nan"))),
            humidity_pct=float(data.get("humidity_pct", float("nan"))),
            pressure_hpa=float(data["pressure_hpa"]) if data.get("pressure_hpa") is not None else None,
            timestamp=time.time(),
            source="bridge",
            ok=bool(data.get("ok", True)),
            mission=dict(mission or {}),
            extra={k: v for k, v in data.items() if k not in {
                "temperature_c", "humidity_pct", "pressure_hpa", "ok", "millis"
            }},
        )


def make_sensor_source(cfg: Config, bridge: BridgeClient | None = None) -> SensorSource:
    backend = cfg.sensor_backend.lower()
    if backend == "mock" or cfg.mock:
        return MockSensorSource()
    if bridge is None:
        bridge = BridgeClient(mock=cfg.mock)
    return BridgeSensorSource(bridge)
