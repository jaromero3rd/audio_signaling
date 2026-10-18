#!/usr/bin/env python3
"""Run on the UNO Q Debian HOST (adb shell), not in App Lab.

App Lab cannot publish ports on the board Wi-Fi IP. This process does:

  App Lab container  --TCP:9124-->  this host
  Mac                <--TCP:9123--  this host (WLAN/hotspot address)

From your laptop (board USB or adb already connected):

  adb shell
  python3 host_tcp_relay.py

Leave it running, then Run the App Lab DHT11 app.
On the Mac:  python3 -m ground_station.sensor_recv
"""

from __future__ import annotations

import socket
import threading

APP_LAB_PORT = 9124
MAC_PORT = 9123


class Hub:
    def __init__(self) -> None:
        self._mac: list[socket.socket] = []
        self._lock = threading.Lock()
        self.n = 0

    def add_mac(self, sock: socket.socket) -> None:
        with self._lock:
            self._mac.append(sock)

    def fanout(self, line: bytes) -> None:
        if not line.endswith(b"\n"):
            line += b"\n"
        with self._lock:
            current = list(self._mac)
        alive: list[socket.socket] = []
        for sock in current:
            try:
                sock.sendall(line)
                alive.append(sock)
            except OSError:
                try:
                    sock.close()
                except OSError:
                    pass
        with self._lock:
            self._mac[:] = alive
        self.n += 1
        if self.n % 20 == 1:
            print(f"[relay] → Mac #{self.n} clients={len(alive)} {line[:80]!r}", flush=True)


def listen(port: int, label: str, on_client) -> None:
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("0.0.0.0", port))
    srv.listen(8)
    print(f"[relay] {label} on 0.0.0.0:{port}", flush=True)
    while True:
        client, addr = srv.accept()
        print(f"[relay] {label} client {addr}", flush=True)
        threading.Thread(target=on_client, args=(client,), daemon=True).start()


def read_app_lab(client: socket.socket, hub: Hub) -> None:
    buf = b""
    try:
        while True:
            chunk = client.recv(4096)
            if not chunk:
                break
            buf += chunk
            while b"\n" in buf:
                line, _, buf = buf.partition(b"\n")
                if line:
                    hub.fanout(line + b"\n")
    except OSError:
        pass
    finally:
        try:
            client.close()
        except OSError:
            pass
        print("[relay] App Lab disconnected", flush=True)


def hold_mac(client: socket.socket, hub: Hub) -> None:
    hub.add_mac(client)
    try:
        while client.recv(64):
            pass
    except OSError:
        pass
    finally:
        try:
            client.close()
        except OSError:
            pass
        print("[relay] Mac disconnected", flush=True)


def _wlan_ips() -> list[str]:
    ips: list[str] = []
    try:
        hostname = socket.gethostname()
        for info in socket.getaddrinfo(hostname, None, socket.AF_INET):
            ip = info[4][0]
            if ip and not ip.startswith("127.") and ip not in ips:
                ips.append(ip)
    except OSError:
        pass
    return ips


def main() -> None:
    ips = _wlan_ips()
    print("[relay] Run this on the UNO Q Debian host (stays on the drone).", flush=True)
    print("[relay] App Lab on any PC only deploys the sketch — it is not the radio.", flush=True)
    if ips:
        print(f"[relay] Board addresses: {', '.join(ips)}", flush=True)
        print(
            f"[relay] Ground laptop:\n"
            f"  python3 -m ground_station.sensor_recv --tcp {ips[0]}:{MAC_PORT}",
            flush=True,
        )
    else:
        print("[relay] No WLAN IP yet — join the phone hotspot, then restart this.", flush=True)
    hub = Hub()
    threading.Thread(
        target=listen,
        args=(APP_LAB_PORT, "App Lab (container)", lambda c: read_app_lab(c, hub)),
        daemon=True,
    ).start()
    listen(MAC_PORT, "Mac pull", lambda c: hold_mac(c, hub))


if __name__ == "__main__":
    main()
