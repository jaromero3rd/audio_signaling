"""UNO Q: gather DHT11 + LDR and send to another computer over Bluetooth."""

from __future__ import annotations

import time

from arduino.app_utils import App, Bridge

from bt_stream import DEVICE_NAME, BtSensorStream
from light_cal import INVERT, LightCalibrator

_bt = BtSensorStream()
_bt.start()
_light = LightCalibrator(room_level=50)
_last_status = 0.0


def on_sample(ok: bool, temp_c: float, humidity: float, light_raw: float) -> None:
    global _last_status
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
    sent = _bt.send_sample(payload)

    now = time.time()
    if now - _last_status >= 0.5:
        _last_status = now
        link = "BT-SENT" if sent else "BT-WAIT(no laptop connected yet)"
        print(
            f"[{link}] T={payload['temp_c']}C RH={payload['humidity_pct']}% "
            f"light={lr.level}/100 raw={lr.raw}  total_sent={_bt.sent}",
            flush=True,
        )


def user_loop() -> None:
    time.sleep(3)
    print(
        f"Bluetooth name: {DEVICE_NAME}\n"
        "On the OTHER computer run:\n"
        "  pip install bleak\n"
        "  python -m ground_station.bt_sensor_recv\n",
        flush=True,
    )


Bridge.provide("on_sample", on_sample)
print("UNO Q -> Bluetooth -> ground-station laptop", flush=True)
print(f"Advertising / discoverable as: {DEVICE_NAME}", flush=True)
print(f"Light invert={INVERT} | 1=dark 50=room 100=bright", flush=True)
App.run(user_loop=user_loop)
