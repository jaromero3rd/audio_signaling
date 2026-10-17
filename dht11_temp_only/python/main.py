"""UNO Q App Lab app: DHT11 + LDR → ground_station.sensor_recv.

Posts live JSON to config.json ground_station_url (HTTP :8765/telemetry).
"""

from __future__ import annotations

import time

from arduino.app_utils import App, Bridge

from bt_stream import SensorStream
from light_cal import INVERT, LightCalibrator

_stream = SensorStream()
_stream.start()
_light = LightCalibrator(room_level=50)
_last = 0.0


def _heat_index_c(temp_c: float, rh: float) -> float:
    """Rothfusz/Steadman heat index, returned in °C."""
    t_f = temp_c * 9.0 / 5.0 + 32.0
    if t_f < 80.0:
        return round(temp_c, 1)
    hi_f = (
        -42.379
        + 2.04901523 * t_f
        + 10.14333127 * rh
        - 0.22475541 * t_f * rh
        - 0.00683783 * t_f * t_f
        - 0.05481717 * rh * rh
        + 0.00122874 * t_f * t_f * rh
        + 0.00085282 * t_f * rh * rh
        - 0.00000199 * t_f * t_f * rh * rh
    )
    return round((hi_f - 32.0) * 5.0 / 9.0, 1)


def on_sample(ok: bool, temp_c: float, humidity: float, light_raw: float) -> None:
    global _last
    lr = _light.update(light_raw)

    if ok:
        t = round(float(temp_c), 1)
        rh = round(float(humidity), 0)
        payload = {
            "ok": True,
            "source": "dht11",
            "sensor": "DHT11",
            "temp_c": t,
            "temp_f": round(t * 9.0 / 5.0 + 32.0, 1),
            "humidity_pct": rh,
            "heat_index_c": _heat_index_c(t, rh),
            "light": lr.level,
            "light_raw": lr.raw,
            "light_band": lr.band,
            "ts": time.time(),
            "device": "unoq",
            "device_id": _stream.device_id,
        }
    else:
        payload = {
            "ok": False,
            "source": "dht11",
            "sensor": "DHT11",
            "temp_c": None,
            "temp_f": None,
            "humidity_pct": None,
            "heat_index_c": None,
            "light": lr.level,
            "light_raw": lr.raw,
            "light_band": lr.band,
            "ts": time.time(),
            "device": "unoq",
            "device_id": _stream.device_id,
        }

    sent = _stream.send_sample(payload)

    now = time.time()
    if now - _last >= 1.0:
        _last = now
        # Show sensor line every second; SENT only when HTTP post succeeded this tick
        tag = "SENT" if sent else ("LIVE" if _stream._http_ok else "WAITING")
        print(
            f"[{tag}] {_stream.status_line()} | "
            f"T={payload['temp_c']}C RH={payload['humidity_pct']}% "
            f"light={lr.level}/100 raw={lr.raw} sent={_stream.sent}",
            flush=True,
        )


def user_loop() -> None:
    print(
        "DHT11 on D2 + LDR on A0 → "
        f"{_stream.ground_url or '(set ground_station_url in config.json)'}",
        flush=True,
    )
    while True:
        time.sleep(10)
        print(f"[status] {_stream.status_line()} sent={_stream.sent}", flush=True)


Bridge.provide("on_sample", on_sample)
print(f"DHT11 + LDR | light invert={INVERT} | room~50", flush=True)
App.run(user_loop=user_loop)
