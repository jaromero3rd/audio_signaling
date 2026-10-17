"""Sensor outbound links from the App Lab Python container.

IMPORTANT (Arduino UNO Q / App Lab):
  The App container cannot use BlueZ. Do NOT run bless here — it always fails
  with Errno 2 / missing D-Bus. Bluetooth is done by host_bt_relay.py on the
  Debian host (see README). This module only:

    1) TCP server :9123  — laptop connects with --tcp <ip>:9123
    2) TCP client :9124  — forwards JSON to host_bt_relay.py (real BLE)
"""

from __future__ import annotations

import json
import socket
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

DEVICE_NAME = "AgriDrone-UNOQ"
TCP_PORT = 9123
HOST_RELAY_PORT = 9124
HOST_RELAY_CANDIDATES = (
    "127.0.0.1",
    "172.17.0.1",   # common Docker bridge gateway
    "172.18.0.1",
    "172.10.0.1",   # mentioned on Arduino forums
)
_CONFIG_PATH = Path(__file__).with_name("config.json")


def load_config() -> dict[str, Any]:
    try:
        return json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"[cfg] could not read config.json: {exc}", flush=True)
        return {}


def _local_ips() -> list[str]:
    ips: list[str] = []
    try:
        hostname = socket.gethostname()
        for info in socket.getaddrinfo(hostname, None, socket.AF_INET):
            ip = info[4][0]
            if ip and not ip.startswith("127.") and ip not in ips:
                ips.append(ip)
    except OSError:
        pass
    # Also try UDP trick for the default outbound interface
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        if ip and not ip.startswith("127.") and ip not in ips:
            ips.insert(0, ip)
    except OSError:
        pass
    return ips


class SensorStream:
    """TCP + HTTP POST to ground_station.sensor_recv + optional host BLE relay."""

    def __init__(self) -> None:
        self._tcp_clients: list[socket.socket] = []
        self._tcp_lock = threading.Lock()
        self._tcp_ok = False
        self._tcp_error: str | None = None

        self._host_sock: socket.socket | None = None
        self._host_addr: str | None = None
        self._host_lock = threading.Lock()

        cfg = load_config()
        self.ground_url = str(cfg.get("ground_station_url") or "").strip()
        self.device_id = str(cfg.get("device_id") or "unoq-dht11")
        self._http_ok = False
        self._http_error: str | None = None

        self.sent = 0
        self.ips = _local_ips()

    def start(self) -> None:
        threading.Thread(target=self._tcp_server, name="tcp-9123", daemon=True).start()
        threading.Thread(target=self._host_client, name="host-9124", daemon=True).start()
        time.sleep(0.5)
        self._print_howto()

    def _print_howto(self) -> None:
        print("=" * 56, flush=True)
        print("SENSORS OK — ground laptop must run:", flush=True)
        print("  python -m ground_station.sensor_recv --host 0.0.0.0 --port 8765", flush=True)
        if self.ground_url:
            print(f"UNO Q will POST to: {self.ground_url}", flush=True)
            print("Edit python/config.json if that IP is wrong (not 0.0.0.0).", flush=True)
        else:
            print("Set ground_station_url in python/config.json to enable HTTP.", flush=True)
        print("=" * 56, flush=True)

    def status_line(self) -> str:
        if self.ground_url:
            http = "HTTP-OK" if self._http_ok else f"HTTP-WAIT({self._http_error or self.ground_url})"
        else:
            http = "HTTP-off"
        with self._tcp_lock:
            n = len(self._tcp_clients)
        tcp = f"TCP:{TCP_PORT} clients={n}" if self._tcp_ok else f"TCP-DOWN({self._tcp_error})"
        return f"{http} | {tcp}"

    def _tcp_server(self) -> None:
        try:
            srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            srv.bind(("0.0.0.0", TCP_PORT))
            srv.listen(4)
            self._tcp_ok = True
            print(f"[tcp] listening on 0.0.0.0:{TCP_PORT}", flush=True)
            while True:
                client, addr = srv.accept()
                print(f"[tcp] laptop connected: {addr}", flush=True)
                with self._tcp_lock:
                    self._tcp_clients.append(client)
        except Exception as exc:  # noqa: BLE001
            self._tcp_error = str(exc)
            print(f"[tcp] failed: {exc}", flush=True)

    def _host_client(self) -> None:
        """Keep trying to reach host_bt_relay.py (real Bluetooth)."""
        while True:
            with self._host_lock:
                alive = self._host_sock is not None
            if alive:
                time.sleep(3)
                continue
            for host in HOST_RELAY_CANDIDATES:
                try:
                    s = socket.create_connection((host, HOST_RELAY_PORT), timeout=1.0)
                    s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                    with self._host_lock:
                        self._host_sock = s
                        self._host_addr = f"{host}:{HOST_RELAY_PORT}"
                    print(
                        f"[ble-host] connected — Bluetooth relay live at {self._host_addr}",
                        flush=True,
                    )
                    break
                except OSError:
                    continue
            else:
                time.sleep(2)

    def send_sample(self, payload: dict[str, Any]) -> bool:
        payload = dict(payload)
        payload.setdefault("source", "dht11_temp_only")
        payload.setdefault("device_id", self.device_id)
        line = (json.dumps(payload, separators=(",", ":")) + "\n").encode("utf-8")
        ok = False
        ok = self._post_http(payload) or ok
        ok = self._fanout(self._tcp_clients, self._tcp_lock, line) or ok
        ok = self._send_host(line) or ok
        if ok:
            self.sent += 1
        return ok

    def _post_http(self, payload: dict[str, Any]) -> bool:
        if not self.ground_url:
            return False
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            self.ground_url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=2.0) as resp:
                ok = 200 <= getattr(resp, "status", 200) < 300
                self._http_ok = ok
                self._http_error = None if ok else f"status={getattr(resp, 'status', '?')}"
                return ok
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            self._http_ok = False
            self._http_error = str(exc)
            return False

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

    @staticmethod
    def _fanout(clients: list[socket.socket], lock: threading.Lock, line: bytes) -> bool:
        with lock:
            current = list(clients)
        if not current:
            return False
        alive: list[socket.socket] = []
        sent_any = False
        for c in current:
            try:
                c.sendall(line)
                alive.append(c)
                sent_any = True
            except OSError:
                try:
                    c.close()
                except OSError:
                    pass
        with lock:
            clients[:] = alive
        return sent_any


# Back-compat aliases used by older main.py imports
BtSensorStream = SensorStream
