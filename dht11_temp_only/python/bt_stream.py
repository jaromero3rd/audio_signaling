"""Sensor stream: App Lab → host Bluetooth bridge → laptop.

App Lab cannot use BlueZ. Bridge pattern (Arduino-compatible):
  MCU --Bridge--> App Python --TCP:9124--> host_bt_relay.py (Debian) --BLE--> laptop

Also keeps TCP:9123 so a ground PC can pull with --tcp if needed.
"""

from __future__ import annotations

import json
import socket
import threading
import time
from pathlib import Path
from typing import Any

DEVICE_NAME = "AgriDrone-UNOQ"
TCP_PORT = 9123          # laptop may pull from board
HOST_RELAY_PORT = 9124   # App Lab → host_bt_relay.py
_CONFIG_PATH = Path(__file__).with_name("config.json")

_HOST_CANDIDATES = (
    "172.17.0.1",
    "172.18.0.1",
    "172.10.0.1",
    "192.168.0.1",
    "127.0.0.1",
)


def load_config() -> dict[str, Any]:
    try:
        return json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"[cfg] {exc}", flush=True)
        return {}


def is_bad_ground_url(url: str) -> bool:
    u = (url or "").strip()
    if not u or u.lower() == "auto":
        return True
    bad = ("192.0.0.2", "0.0.0.0", "127.0.0.1", "localhost", "OTHER_COMPUTER", "REPLACE_WITH")
    return any(b.lower() in u.lower() for b in bad)


def _default_gateway() -> str | None:
    try:
        with open("/proc/net/route", encoding="utf-8") as f:
            next(f)
            for line in f:
                parts = line.split()
                if len(parts) >= 3 and parts[1] == "00000000":
                    raw = int(parts[2], 16)
                    return socket.inet_ntoa(raw.to_bytes(4, "little"))
    except OSError:
        return None
    return None


class SensorStream:
    def __init__(self, ground_url_override: str | None = None) -> None:
        cfg = load_config()
        self.device_id = str(cfg.get("device_id") or "unoq-dht11")
        self.board_ip: str | None = None
        self.sent = 0

        self._tcp_clients: list[socket.socket] = []
        self._tcp_lock = threading.Lock()

        self._host_sock: socket.socket | None = None
        self._host_addr: str | None = None
        self._host_lock = threading.Lock()

        self._latest: dict[str, Any] | None = None
        self._lock = threading.Lock()

    def start(self) -> None:
        self.board_ip = self._guess_ip()
        threading.Thread(target=self._tcp_server, name="tcp-9123", daemon=True).start()
        threading.Thread(target=self._host_bridge_loop, name="bt-bridge", daemon=True).start()
        time.sleep(0.3)
        print("=" * 56, flush=True)
        print("BLUETOOTH BRIDGE (wireless OK on battery)", flush=True)
        print("  Board: host_bt_relay.py must be running (install_bt_relay.sh once)", flush=True)
        print(f"  Laptop (no USB): python -m ground_station.bt_sensor_recv", flush=True)
        print(f"  Look for BLE name {DEVICE_NAME} — do NOT pair in Windows Settings", flush=True)
        print("Fallback Wi‑Fi pull:", flush=True)
        print(f"  python -m ground_station.bt_sensor_recv --tcp {self.board_ip or 'BOARD_IP'}:{TCP_PORT}", flush=True)
        print("=" * 56, flush=True)

    @staticmethod
    def _guess_ip() -> str | None:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            if ip and not ip.startswith("127."):
                return ip
        except OSError:
            pass
        return None

    def status_line(self) -> str:
        with self._host_lock:
            ble = f"BLE-BRIDGE({self._host_addr})" if self._host_addr else "BLE-BRIDGE waiting (start host_bt_relay.py)"
        with self._tcp_lock:
            n = len(self._tcp_clients)
        tcp = f"TCP:{TCP_PORT} clients={n}"
        return f"{ble} | {tcp}"

    def send_sample(self, payload: dict[str, Any]) -> bool:
        payload = dict(payload)
        payload.pop("audio_b64", None)
        payload.pop("mic", None)
        payload.setdefault("source", "dht11")
        payload.setdefault("device_id", self.device_id)
        with self._lock:
            self._latest = payload
        line = (json.dumps(payload, separators=(",", ":")) + "\n").encode("utf-8")
        ok = False
        ok = self._send_host(line) or ok
        ok = self._fanout_tcp(line) or ok
        if ok:
            self.sent += 1
        return ok

    def _host_bridge_loop(self) -> None:
        """Keep a TCP link to host_bt_relay.py on the Debian side."""
        while True:
            with self._host_lock:
                up = self._host_sock is not None
            if up:
                time.sleep(2)
                continue
            hosts: list[str] = []
            gw = _default_gateway()
            if gw:
                hosts.append(gw)
            hosts.extend(_HOST_CANDIDATES)
            for host in hosts:
                try:
                    s = socket.create_connection((host, HOST_RELAY_PORT), timeout=1.0)
                    s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                    with self._host_lock:
                        self._host_sock = s
                        self._host_addr = f"{host}:{HOST_RELAY_PORT}"
                    print(f"[bt-bridge] connected to host relay {self._host_addr}", flush=True)
                    # flush latest sample immediately
                    with self._lock:
                        latest = self._latest
                    if latest:
                        line = (json.dumps(latest, separators=(",", ":")) + "\n").encode("utf-8")
                        self._send_host(line)
                    break
                except OSError:
                    continue
            else:
                time.sleep(2)

    def _send_host(self, line: bytes) -> bool:
        with self._host_lock:
            s = self._host_sock
        if s is None:
            return False
        try:
            s.sendall(line)
            return True
        except OSError:
            try:
                s.close()
            except OSError:
                pass
            with self._host_lock:
                self._host_sock = None
                self._host_addr = None
            return False

    def _fanout_tcp(self, line: bytes) -> bool:
        with self._tcp_lock:
            clients = list(self._tcp_clients)
        if not clients:
            return False
        alive: list[socket.socket] = []
        any_ok = False
        for c in clients:
            try:
                c.sendall(line)
                alive.append(c)
                any_ok = True
            except OSError:
                try:
                    c.close()
                except OSError:
                    pass
        with self._tcp_lock:
            self._tcp_clients = alive
        return any_ok

    def _tcp_server(self) -> None:
        try:
            srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            srv.bind(("0.0.0.0", TCP_PORT))
            srv.listen(4)
            print(f"[tcp] fallback listen :{TCP_PORT}", flush=True)
            while True:
                client, addr = srv.accept()
                print(f"[tcp] client {addr}", flush=True)
                with self._tcp_lock:
                    self._tcp_clients.append(client)
        except Exception as exc:  # noqa: BLE001
            print(f"[tcp] {exc}", flush=True)


BtSensorStream = SensorStream
