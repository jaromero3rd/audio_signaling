"""Sensor outbound links from the App Lab Python container.

IMPORTANT (Arduino UNO Q / App Lab):
  The App container cannot use BlueZ. Do NOT run bless here — it always fails
  with Errno 2 / missing D-Bus. Bluetooth is done by host_bt_relay.py on the
  Debian host (see README). This module only:

    1) TCP server :9123  — laptop connects with --tcp <ip>:9123
    2) TCP client :9124  — forwards JSON to host_bt_relay.py (real BLE)
"""

from __future__ import annotations

import ipaddress
import json
import socket
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

APPLE_HOTSPOT_NET = ipaddress.ip_network("172.20.10.0/28")

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


def _health_ok(url: str, timeout_s: float = 0.6) -> bool:
    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            return 200 <= getattr(resp, "status", 200) < 300
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


def discover_ground_station(port: int = 8765) -> str | None:
    """Find the Mac on iPhone USB/Wi-Fi hotspot LAN (172.20.10.0/28)."""
    for host in APPLE_HOTSPOT_NET.hosts():
        if _health_ok(f"http://{host}:{port}/health"):
            url = f"http://{host}:{port}/telemetry"
            print(f"[cfg] auto ground_station_url={url}", flush=True)
            return url
    print("[cfg] auto-discover failed; set ground_station_url to the Mac USB IP", flush=True)
    return None


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
        if not self.ground_url or self.ground_url.lower() == "auto":
            found = discover_ground_station()
            self.ground_url = found or ""
        self.device_id = str(cfg.get("device_id") or "unoq-dht11")
        self._http_ok = False
        self._http_error: str | None = None
        self._last_http_post = 0.0
        # Sensors only — no microphone. Post ~2 Hz so ground station stays readable.
        self._http_min_interval_s = float(cfg.get("http_min_interval_s") or 0.5)

        self.sent = 0
        self.ips = _local_ips()

    def start(self) -> None:
        threading.Thread(target=self._tcp_server, name="tcp-9123", daemon=True).start()
        # No BLE host client by default — sensors go over HTTP to sensor_recv
        time.sleep(0.3)
        self._print_howto()

    def _print_howto(self) -> None:
        print("=" * 56, flush=True)
        print("Sending DHT11 + light ONLY (no mic) to ground computer.", flush=True)
        print("  Other PC: python -m ground_station.sensor_recv --host 0.0.0.0 --port 8765", flush=True)
        if self.ground_url:
            print(f"  This board POSTs: {self.ground_url}", flush=True)
        else:
            print("  ERROR: set ground_station_url in python/config.json", flush=True)
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
        """Send sensor JSON (T/RH/light) — never microphone."""
        payload = dict(payload)
        payload.pop("audio_b64", None)
        payload.pop("mic", None)
        payload.setdefault("source", "dht11")
        payload.setdefault("device_id", self.device_id)
        ok = self._post_http(payload)
        if ok:
            self.sent += 1
        return ok

    def _post_http(self, payload: dict[str, Any]) -> bool:
        if not self.ground_url:
            return False
        now = time.time()
        if now - self._last_http_post < self._http_min_interval_s:
            return False  # skip this tick; status still shows last HTTP-OK/WAIT
        self._last_http_post = now
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
                if ok:
                    print(
                        f"[http] posted T={payload.get('temp_c')}C "
                        f"RH={payload.get('humidity_pct')}% "
                        f"light={payload.get('light')}/100",
                        flush=True,
                    )
                return ok
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            self._http_ok = False
            self._http_error = str(exc)
            print(f"[http] POST failed: {exc}", flush=True)
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
