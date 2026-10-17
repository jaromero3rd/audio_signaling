"""UNO Q App Lab app: read sensors, stream to laptop.

Bluetooth cannot run inside this container (Arduino App Lab limitation).
Use TCP now, or start host_bt_relay.py on the board for real BLE.
"""

from __future__ import annotations

import time

from arduino.app_utils import App, Bridge

from bt_stream import DEVICE_NAME, SensorStream
from light_cal import INVERT, LightCalibrator

_stream = SensorStream()
_stream.start()
_light = LightCalibrator(room_level=50)
_last = 0.0


def on_sample(ok: bool, temp_c: float, humidity: float, light_raw: float) -> None:
    global _last
    lr = _light.update(light_raw)
    payload = {
        "ok": bool(ok),
        "temp_c": None if not ok else round(float(temp_c), 1),
        "humidity_pct": None if not ok else round(float(humidity), 0),
        "light": lr.level,
        "light_raw": lr.raw,
        "light_band": lr.band,
        "ts": time.time(),
        "device": "unoq",
    }
    sent = _stream.send_sample(payload)

    now = time.time()
    if now - _last >= 1.0:
        _last = now
        tag = "SENT" if sent else "WAITING-FOR-LAPTOP"
        print(
            f"[{tag}] {_stream.status_line()} | "
            f"T={payload['temp_c']}C RH={payload['humidity_pct']}% "
            f"light={lr.level}/100 raw={lr.raw} sent={_stream.sent}",
            flush=True,
        )


def user_loop() -> None:
    while True:
        time.sleep(10)
        print(f"[status] {_stream.status_line()} | BLE name={DEVICE_NAME}", flush=True)


Bridge.provide("on_sample", on_sample)
print(f"Light invert={INVERT} | room~50", flush=True)
App.run(user_loop=user_loop)
