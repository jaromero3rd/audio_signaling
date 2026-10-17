"""UNO Q: fast DHT11 + LDR Bluetooth stream + Serial/console print."""

from __future__ import annotations

import time

from arduino.app_utils import App, Bridge

from bt_stream import BtSensorStream
from light_cal import INVERT, LightCalibrator

_bt = BtSensorStream()
_bt.start()
_light = LightCalibrator(room_level=50)


def on_sample(ok: bool, temp_c: float, humidity: float, light_raw: float) -> None:
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
    _bt.send_sample(payload)

    # Print every sample to App Lab / serial console
    print(
        f"T={payload['temp_c']}C  RH={payload['humidity_pct']}%  "
        f"light={lr.level}/100 ({lr.band})  raw={lr.raw}",
        flush=True,
    )


def user_loop() -> None:
    time.sleep(5)
    try:
        print(f"[poll] {Bridge.call('get_reading')}", flush=True)
    except Exception as exc:  # noqa: BLE001
        print(f"[poll] {exc}", flush=True)


Bridge.provide("on_sample", on_sample)
print("Serial/console output ON | light 1=dark 50=room 100=bright | invert=", INVERT, flush=True)
print("Open App Lab Monitor / Serial Monitor to watch live readings", flush=True)
App.run(user_loop=user_loop)
