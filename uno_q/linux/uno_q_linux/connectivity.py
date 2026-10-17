"""Host reachability + optional Wi‑Fi SSID hint."""

from __future__ import annotations

import socket
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass

from .config import Config


@dataclass
class ConnectivityReport:
    host_reachable: bool
    method: str
    detail: str = ""
    wifi_ssid: str | None = None


def probe_tcp(host: str, port: int, timeout_s: float = 1.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout_s):
            return True
    except OSError:
        return False


def probe_http(url: str, timeout_s: float = 1.5) -> bool:
    # Prefer /health if caller passed /telemetry — try both.
    candidates = [url]
    if url.rstrip("/").endswith("/telemetry"):
        candidates.insert(0, url.rstrip("/").rsplit("/", 1)[0] + "/health")
    for candidate in candidates:
        try:
            req = urllib.request.Request(candidate, method="GET")
            with urllib.request.urlopen(req, timeout=timeout_s) as resp:
                if 200 <= getattr(resp, "status", 200) < 500:
                    return True
        except (urllib.error.URLError, TimeoutError, OSError):
            continue
    return False


def current_wifi_ssid() -> str | None:
    """Best-effort; VERIFY on Uno Q Debian (nmcli / iwgetid)."""
    for cmd in (
        ["nmcli", "-t", "-f", "active,ssid", "dev", "wifi"],
        ["iwgetid", "-r"],
    ):
        try:
            out = subprocess.check_output(cmd, stderr=subprocess.DEVNULL, text=True, timeout=2)
        except (OSError, subprocess.SubprocessError):
            continue
        if cmd[0] == "iwgetid":
            ssid = out.strip()
            return ssid or None
        for line in out.splitlines():
            # nmcli: yes:MySSID
            if line.startswith("yes:"):
                return line.split(":", 1)[1] or None
    return None


def check_connectivity(cfg: Config) -> ConnectivityReport:
    ssid = current_wifi_ssid()
    if cfg.wifi_ssid and ssid and ssid != cfg.wifi_ssid:
        # Informational only — still allow host probe (venue SSIDs vary).
        pass

    if probe_http(cfg.host_url):
        return ConnectivityReport(True, "http", wifi_ssid=ssid)
    if probe_tcp(cfg.host_ip, cfg.host_port):
        return ConnectivityReport(True, "tcp", wifi_ssid=ssid)
    return ConnectivityReport(False, "none", detail="host unreachable", wifi_ssid=ssid)
