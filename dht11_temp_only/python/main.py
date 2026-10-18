"""UNO Q App Lab: DHT11 + LDR → Bluetooth bridge (host) → laptop."""

from __future__ import annotations

import os
import threading
import time
from typing import Any

from arduino.app_utils import App, Bridge

from bt_stream import DEVICE_NAME, SensorStream, load_config
from light_cal import INVERT, LightCalibrator
from wifi_link import join_phone_hotspot

_CFG = load_config()


def _join_hotspot() -> None:
    wifi = _CFG.get("wifi") or {}
    if not bool(wifi.get("join", False)):
        return
    ssid = os.environ.get("PHONE_HOTSPOT_SSID") or str(wifi.get("ssid") or "")
    password = os.environ.get("PHONE_HOTSPOT_PASSWORD") or str(wifi.get("password") or "")
    if not ssid:
        return
    try:
        join_phone_hotspot(ssid, password)
    except Exception as exc:  # noqa: BLE001
        print(f"[wifi] join failed: {exc}", flush=True)


_join_hotspot()
_stream = SensorStream()
_stream.start()
_light = LightCalibrator(room_level=50)
_last_print = 0.0
_lock = threading.Lock()


def _heat_index_c(temp_c: float, rh: float) -> float:
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


def on_sample(ok, temp_c: float, humidity: float, light_raw: float) -> None:
    """MCU Bridge.notify → BLE host relay. ok is float 1.0/0.0."""
    global _last_print
    ok_b = bool(ok) if not isinstance(ok, (int, float)) else bool(float(ok) >= 0.5)
    with _lock:
        lr = _light.update(float(light_raw))
        if ok_b:
            t = round(float(temp_c), 1)
            rh = round(float(humidity), 0)
            payload: dict[str, Any] = {
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
        if now - _last_print >= 1.0:
            _last_print = now
            tag = "SENT" if sent else "LIVE"
            print(
                f"[{tag}] {_stream.status_line()} | "
                f"T={payload['temp_c']}C RH={payload['humidity_pct']}% "
                f"light={lr.level}/100 raw={lr.raw} sent={_stream.sent}",
                flush=True,
            )


def user_loop() -> None:
    print(
        f"BLE name: {DEVICE_NAME}\n"
        "Board relay: host_bt_relay.py (auto via install_bt_relay.sh)\n"
        "Laptop wireless: python -m ground_station.bt_sensor_recv\n"
        "(Ignore Windows Add Device — do not pair)\n",
        flush=True,
    )
    while True:
        time.sleep(10)
        print(f"[status] {_stream.status_line()} sent={_stream.sent}", flush=True)


# Notify-only path (official UNO Q pattern). Do NOT Bridge.call-poll — it stalls notify.
Bridge.provide("on_sample", on_sample)
print(f"DHT11 + LDR | Bluetooth bridge | invert={INVERT}", flush=True)
App.run(user_loop=user_loop)
