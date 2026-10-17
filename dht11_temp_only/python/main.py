"""UNO Q: read DHT11 + LDR, send raw samples over Bluetooth — no analysis."""

from __future__ import annotations

import time

from arduino.app_utils import App, Bridge

from bt_stream import BtSensorStream

_bt = BtSensorStream()
_bt.start()


def on_sample(ok: bool, temp_c: float, humidity: float, light_raw: float) -> None:
    payload = {
        "ok": bool(ok),
        "temp_c": None if not ok else round(float(temp_c), 1),
        "humidity_pct": None if not ok else round(float(humidity), 0),
        "light_raw": int(light_raw),
        "ts": time.time(),
        "device": "unoq",
    }
    sent = _bt.send_sample(payload)
    mark = "bt-ok" if sent else "bt-wait"
    print(
        f"[{mark}] ok={payload['ok']} T={payload['temp_c']} "
        f"RH={payload['humidity_pct']} light_raw={payload['light_raw']} "
        f"sent={_bt.sent}"
    )


def user_loop() -> None:
    time.sleep(5)
    try:
        print(f"[poll] {Bridge.call('get_reading')}")
    except Exception as exc:  # noqa: BLE001
        print(f"[poll] {exc}")
    if not _bt.ok:
        print("[bt] BLE not ready — pair/connect from the laptop (AgriDrone-UNOQ)")


Bridge.provide("on_sample", on_sample)
print("Mode: sensors only -> Bluetooth LE (AgriDrone-UNOQ)")
print("Ground-station laptop (other PC): python -m ground_station.bt_sensor_recv")
App.run(user_loop=user_loop)
