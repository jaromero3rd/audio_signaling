"""
UNO Q Linux entry — AgriDrone field station.

Roles:
  - MCU: DHT11 + photoresistor + SD (env telemetry)
  - Linux mic: capture PCM and transfer to the ground computer
  - Wi-Fi down: buffer env rows on SD; buffer mic bytes locally

Farmer voice commands use Grok Voice on the *laptop*
(ground_station/voice_grok.py) — not this board.
"""

from __future__ import annotations

import json
import os
import random
import threading
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, urlunparse

from autonomy import Mode, ReturnToWifiAgent
from mic_transfer import MicTransfer
from wifi_link import WifiSnapshot, post_telemetry, probe_wifi, wifi_strong_enough

HERE = Path(__file__).resolve().parent
CONFIG_PATH = Path(os.environ.get("FIELD_STATION_CONFIG", HERE / "config.json"))
MOCK = os.environ.get("FIELD_STATION_MOCK", "").strip() in ("1", "true", "yes")

_bridge_lock = threading.Lock()
_state_lock = threading.Lock()
_pending_return: dict[str, Any] | None = None
_last_decision_reason = ""
_samples_sent = 0
_samples_buffered = 0
_mic_xfer: MicTransfer | None = None


def load_config() -> dict[str, Any]:
    path = CONFIG_PATH if CONFIG_PATH.is_file() else HERE / "config.example.json"
    with path.open(encoding="utf-8-sig") as f:
        return json.load(f)


def mic_url_from_telemetry(telemetry_url: str) -> str:
    """http://host:8765/telemetry -> http://host:8765/mic"""
    parts = urlparse(telemetry_url)
    return urlunparse((parts.scheme, parts.netloc, "/mic", "", "", ""))


def normalize_light(light: float, invert: bool = False) -> float:
    v = max(0.0, min(1.0, float(light)))
    return 1.0 - v if invert else v


def _bridge_call(name: str, *args: Any) -> Any:
    if MOCK:
        return _mock_bridge_call(name, *args)
    from arduino.app_utils import Bridge  # type: ignore

    with _bridge_lock:
        return Bridge.call(name, *args)


def _bridge_provide(name: str, fn: Any) -> None:
    if MOCK:
        return
    from arduino.app_utils import Bridge  # type: ignore

    Bridge.provide(name, fn)


def _set_mcu_offline(offline: bool) -> None:
    try:
        _bridge_call("set_offline", offline)
    except Exception as exc:  # noqa: BLE001
        print(f"set_offline failed: {exc}")


def _sd_append_line(line: str) -> None:
    try:
        _bridge_call("sd_append", line)
    except Exception as exc:  # noqa: BLE001
        print(f"sd_append failed: {exc}")


def _parse_sd_row(parts: list[str]) -> dict[str, Any] | None:
    try:
        # New: seq,uptime,temp_c,temp_f,humidity,heat_index_c,light,source
        # Old: seq,uptime,temp_c,humidity,light,...
        row: dict[str, Any] = {
            "seq": int(parts[0]),
            "uptime_ms": int(parts[1]),
            "temp_c": float(parts[2]),
        }
        if len(parts) >= 7 and parts[-1] in ("sd", "linux"):
            row["temp_f"] = float(parts[3])
            row["humidity_pct"] = float(parts[4])
            row["heat_index_c"] = float(parts[5])
            row["light"] = float(parts[6])
        elif len(parts) >= 5:
            row["humidity_pct"] = float(parts[3])
            row["light"] = float(parts[4])
        return row
    except (ValueError, IndexError):
        return None


def _flush_sd_to_ground(cfg: dict[str, Any]) -> int:
    try:
        raw = _bridge_call("sd_pending")
    except Exception as exc:  # noqa: BLE001
        print(f"sd_pending failed: {exc}")
        return 0
    if not raw:
        return 0
    text = raw if isinstance(raw, str) else str(raw)
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if not lines:
        return 0
    header = lines[0] if lines[0].startswith("seq,") else None
    data_lines = lines[1:] if header else lines
    flushed = 0
    url = cfg["ground_station_url"]
    timeout = float(cfg.get("sample_relay_timeout_s", 2.0))
    device_id = cfg.get("device_id", "unoq-field-01")
    for ln in data_lines:
        parsed = _parse_sd_row(ln.split(","))
        if not parsed:
            continue
        payload = {"device_id": device_id, "source": "sd_flush", "ts": time.time(), **parsed}
        ok, _ = post_telemetry(url, payload, timeout_s=timeout)
        if ok:
            flushed += 1
        else:
            break
    if flushed and flushed == len(data_lines):
        try:
            _bridge_call("sd_clear")
        except Exception as exc:  # noqa: BLE001
            print(f"sd_clear failed: {exc}")
    return flushed


def on_sensor_sample(
    temp_c: float,
    temp_f: float,
    humidity_pct: float,
    heat_index_c: float,
    light: float,
    seq: int,
    uptime_ms: int,
) -> None:
    """MCU DHT11 + light sample -> ground telemetry (mic is a separate stream)."""
    global _samples_sent, _samples_buffered
    cfg = load_config()
    snap = probe_wifi() if not MOCK else _mock_wifi()
    online = wifi_strong_enough(snap, float(cfg["wifi"]["min_rssi_dbm"]))
    light_n = normalize_light(float(light), bool(cfg.get("audio", {}).get("invert_light", False)))

    payload: dict[str, Any] = {
        "device_id": cfg.get("device_id", "unoq-field-01"),
        "source": "live",
        "sensor": "DHT11",
        "seq": int(seq),
        "uptime_ms": int(uptime_ms),
        "temp_c": float(temp_c),
        "temp_f": float(temp_f),
        "humidity_pct": float(humidity_pct),
        "heat_index_c": float(heat_index_c),
        "light": light_n,
        "wifi": {
            "connected": snap.connected,
            "ssid": snap.ssid,
            "rssi_dbm": snap.rssi_dbm,
            "ip": snap.ip,
        },
        "ts": time.time(),
    }

    with _state_lock:
        if _pending_return is not None:
            payload["autonomy"] = _pending_return

    if online:
        ok, err = post_telemetry(
            cfg["ground_station_url"],
            payload,
            timeout_s=float(cfg.get("sample_relay_timeout_s", 2.0)),
        )
        if ok:
            _samples_sent += 1
            mic_info = ""
            if _mic_xfer is not None:
                mic_info = f" mic_sent={_mic_xfer.sent} drop={_mic_xfer.dropped}"
            print(
                f"[live] seq={seq} DHT11 T={temp_c:.1f}C/{temp_f:.1f}F "
                f"RH={humidity_pct:.0f}% HI={heat_index_c:.1f}C L={light_n:.2f}{mic_info}"
            )
            return
        print(f"[live] POST failed ({err}); buffering")

    line = (
        f"{int(seq)},{int(uptime_ms)},{float(temp_c):.1f},{float(temp_f):.1f},"
        f"{float(humidity_pct):.0f},{float(heat_index_c):.1f},{light_n:.3f},linux"
    )
    _sd_append_line(line)
    _samples_buffered += 1
    print(f"[sd] buffered seq={seq} (offline or POST fail)")


def make_agent(cfg: dict[str, Any]) -> ReturnToWifiAgent:
    st = cfg.get("station", {})
    auto = cfg.get("autonomy", {})
    wifi = cfg.get("wifi", {})
    return ReturnToWifiAgent(
        station_name=st.get("name", "home_wifi"),
        home_apriltag_id=st.get("home_apriltag_id", 12),
        lat=st.get("lat"),
        lon=st.get("lon"),
        min_rssi_dbm=float(wifi.get("min_rssi_dbm", -75)),
        lost_grace_s=float(wifi.get("lost_grace_s", 8.0)),
        return_after_offline_s=float(auto.get("return_after_offline_s", 15.0)),
        enabled=bool(auto.get("enabled", True)),
    )


def autonomy_loop(agent: ReturnToWifiAgent, cfg: dict[str, Any], stop: threading.Event) -> None:
    global _pending_return, _last_decision_reason
    interval = float(cfg["wifi"].get("check_interval_s", 2.0))
    was_offline = False

    while not stop.is_set():
        snap = probe_wifi() if not MOCK else _mock_wifi()
        decision = agent.decide(snap)
        _last_decision_reason = decision.reason
        offline = decision.mode in (Mode.OFFLINE_BUFFER, Mode.RETURN_TO_STATION)

        if offline != was_offline:
            _set_mcu_offline(offline)
            was_offline = offline

        with _state_lock:
            if decision.command and decision.mode == Mode.RETURN_TO_STATION:
                _pending_return = {
                    "mode": decision.mode.value,
                    "reason": decision.reason,
                    "confidence": decision.confidence,
                    "command": decision.command,
                }
                post_telemetry(
                    cfg["ground_station_url"],
                    {
                        "device_id": cfg.get("device_id", "unoq-field-01"),
                        "source": "autonomy",
                        "event": "return_to_wifi_station",
                        "autonomy": _pending_return,
                        "ts": time.time(),
                    },
                    timeout_s=float(cfg.get("sample_relay_timeout_s", 2.0)),
                )
                print(
                    f"[ai] RETURN conf={decision.confidence:.2f} "
                    f"-> {decision.command.get('target', {})}"
                )
            elif decision.mode == Mode.RECOVERED:
                print(f"[ai] recovered: {decision.reason}")
                flushed = _flush_sd_to_ground(cfg)
                if flushed:
                    print(f"[sd] flushed {flushed} rows to ground station")
                _pending_return = None
            elif decision.mode == Mode.SURVEY:
                _pending_return = None

        stop.wait(interval)


_mock_offline = False
_mock_seq = 0


def _mock_bridge_call(name: str, *args: Any) -> Any:
    global _mock_offline
    if name == "set_offline":
        _mock_offline = bool(args[0]) if args else False
        return None
    if name == "sd_append":
        path = HERE.parent / "data" / "mock_telemetry.csv"
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_text(
                "seq,uptime_ms,temp_c,temp_f,humidity_pct,heat_index_c,light,source\n",
                encoding="utf-8",
            )
        with path.open("a", encoding="utf-8") as f:
            f.write(str(args[0]) + "\n")
        return True
    if name == "sd_pending":
        path = HERE.parent / "data" / "mock_telemetry.csv"
        return path.read_text(encoding="utf-8") if path.exists() else ""
    if name == "sd_clear":
        path = HERE.parent / "data" / "mock_telemetry.csv"
        path.write_text(
            "seq,uptime_ms,temp_c,temp_f,humidity_pct,heat_index_c,light,source\n",
            encoding="utf-8",
        )
        return True
    if name == "get_reading":
        return json.dumps(
            {
                "sensor": "DHT11",
                "pin": "D2",
                "temp_c": 22.5,
                "temp_f": 72.5,
                "humidity_pct": 48.0,
                "heat_index_c": 22.5,
                "light": 0.55,
                "seq": _mock_seq,
            }
        )
    return None


def _mock_wifi() -> WifiSnapshot:
    phase = int(time.time() / 25) % 2
    if phase == 0:
        return WifiSnapshot(True, ssid="AgriDroneGround", rssi_dbm=-55.0, ip="10.0.0.2")
    return WifiSnapshot(False, ssid=None, rssi_dbm=-95.0)


def _mock_sample_loop(stop: threading.Event) -> None:
    global _mock_seq
    while not stop.is_set():
        _mock_seq += 1
        t_c = 20.0 + random.random() * 5
        t_f = t_c * 9.0 / 5.0 + 32.0
        rh = 40.0 + random.random() * 20
        on_sensor_sample(
            t_c,
            t_f,
            rh,
            t_c,
            0.3 + random.random() * 0.5,
            _mock_seq,
            int(time.time() * 1000) % 1_000_000_000,
        )
        stop.wait(3.0)


def main() -> None:
    global _mic_xfer
    cfg = load_config()
    agent = make_agent(cfg)
    stop = threading.Event()
    audio_cfg = cfg.get("audio", {})

    mic_url = audio_cfg.get("mic_url") or mic_url_from_telemetry(cfg["ground_station_url"])
    if bool(audio_cfg.get("transfer", True)) and not MOCK:
        _mic_xfer = MicTransfer(
            url=mic_url,
            device_id=cfg.get("device_id", "unoq-field-01"),
            device=audio_cfg.get("device"),
            timeout_s=float(cfg.get("sample_relay_timeout_s", 2.0)),
            offline_path=HERE.parent / "data" / "mic_offline.f32le",
        )
        _mic_xfer.start()
    elif MOCK:
        print("mock mode: env telemetry only (no live mic device)")

    _bridge_provide("on_sensor_sample", on_sensor_sample)

    t = threading.Thread(target=autonomy_loop, args=(agent, cfg, stop), daemon=True)
    t.start()

    print(
        f"Field station up -> {cfg['ground_station_url']} "
        f"(mock={MOCK}, mic_transfer={_mic_xfer is not None})"
    )
    print("Note: Grok Voice / farmer commands run on the laptop, not this board.")

    if MOCK:
        mt = threading.Thread(target=_mock_sample_loop, args=(stop,), daemon=True)
        mt.start()
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            stop.set()
        return

    from arduino.app_utils import App  # type: ignore

    def user_loop() -> None:
        time.sleep(5)
        reading = None
        try:
            reading = _bridge_call("get_reading")
        except Exception:  # noqa: BLE001
            pass
        mic_stat = ""
        if _mic_xfer is not None:
            mic_stat = f" mic_sent={_mic_xfer.sent} drop={_mic_xfer.dropped}"
        print(
            f"[status] sent={_samples_sent} buffered={_samples_buffered} "
            f"mode={agent.mode.value}{mic_stat} reading={reading}"
        )

    try:
        App.run(user_loop=user_loop)
    finally:
        if _mic_xfer is not None:
            _mic_xfer.stop()


if __name__ == "__main__":
    main()
