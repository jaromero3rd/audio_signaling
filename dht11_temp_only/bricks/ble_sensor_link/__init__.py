"""Arduino Custom Brick: send sensor JSON to the BLE relay container.

Per Arduino Custom Bricks docs, Brick Python runs in the App container and
talks to Docker services over the network (service name or host localhost
when network_mode: host).
"""

from __future__ import annotations

import json
import socket
import threading
import time
from typing import Any

# host network mode → relay listens on the board's localhost
_RELAY_HOSTS = ("127.0.0.1", "ble_relay", "172.17.0.1", "172.10.0.1")
_RELAY_PORT = 9124


class BleSensorLink:
    """TCP client to the ble_relay Docker service (Custom Brick)."""

    def __init__(self) -> None:
        self._sock: socket.socket | None = None
        self._lock = threading.Lock()
        self._addr: str | None = None
        self._stop = False
        threading.Thread(target=self._maintain, name="ble-link", daemon=True).start()

    def _maintain(self) -> None:
        while not self._stop:
            with self._lock:
                ok = self._sock is not None
            if ok:
                time.sleep(2)
                continue
            for host in _RELAY_HOSTS:
                try:
                    s = socket.create_connection((host, _RELAY_PORT), timeout=1.5)
                    s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                    with self._lock:
                        self._sock = s
                        self._addr = f"{host}:{_RELAY_PORT}"
                    print(f"[ble_sensor_link] connected to relay at {self._addr}", flush=True)
                    break
                except OSError:
                    continue
            else:
                time.sleep(2)

    def send(self, payload: dict[str, Any]) -> bool:
        line = (json.dumps(payload, separators=(",", ":")) + "\n").encode("utf-8")
        with self._lock:
            s = self._sock
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
            with self._lock:
                self._sock = None
                self._addr = None
            return False

    @property
    def connected(self) -> bool:
        with self._lock:
            return self._sock is not None

    @property
    def status(self) -> str:
        with self._lock:
            if self._addr:
                return f"BLE-RELAY {self._addr}"
            return "BLE-RELAY waiting (brick container starting?)"
