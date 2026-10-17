"""Wi-Fi health + telemetry POST to the ground computer."""

from __future__ import annotations

import json
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any


@dataclass
class WifiSnapshot:
    connected: bool
    ssid: str | None = None
    bssid: str | None = None
    rssi_dbm: float | None = None
    ip: str | None = None
    checked_at: float = field(default_factory=time.time)


def _run(cmd: list[str], timeout: float = 2.0) -> str:
    try:
        out = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        return (out.stdout or "") + (out.stderr or "")
    except (OSError, subprocess.TimeoutExpired):
        return ""


def probe_wifi() -> WifiSnapshot:
    """Best-effort Wi-Fi probe for Debian on UNO Q (nmcli / iw / ip)."""
    snap = WifiSnapshot(connected=False)

    nm = _run(["nmcli", "-t", "-f", "ACTIVE,SSID,SIGNAL,DEVICE", "dev", "wifi"])
    for line in nm.splitlines():
        parts = line.split(":")
        if len(parts) >= 3 and parts[0] in ("yes", "ja", "oui", "sí", "si"):
            snap.connected = True
            snap.ssid = parts[1] or None
            try:
                # nmcli SIGNAL is 0–100; map roughly to dBm for thresholds.
                signal = float(parts[2])
                snap.rssi_dbm = -100.0 + (signal * 0.7)
            except ValueError:
                pass
            break

    if snap.rssi_dbm is None:
        iw = _run(["iw", "dev", "wlan0", "link"])
        for line in iw.splitlines():
            line = line.strip()
            if line.startswith("SSID:"):
                snap.ssid = line.split(":", 1)[1].strip() or snap.ssid
                snap.connected = True
            elif line.startswith("signal:"):
                # e.g. "signal: -62 dBm"
                try:
                    snap.rssi_dbm = float(line.split()[1])
                    snap.connected = True
                except (IndexError, ValueError):
                    pass
            elif line.startswith("Connected to"):
                snap.bssid = line.split()[2] if len(line.split()) > 2 else None

    ip_out = _run(["hostname", "-I"])
    tokens = ip_out.split()
    if tokens:
        snap.ip = tokens[0]
        if snap.ip and not snap.ip.startswith("127."):
            # Have a routable address even if wifi probe was sparse.
            snap.connected = snap.connected or True

    snap.checked_at = time.time()
    return snap


def post_telemetry(
    url: str,
    payload: dict[str, Any],
    timeout_s: float = 2.0,
) -> tuple[bool, str]:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            return 200 <= getattr(resp, "status", 200) < 300, body
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return False, str(exc)


def wifi_strong_enough(snap: WifiSnapshot, min_rssi_dbm: float) -> bool:
    if not snap.connected:
        return False
    if snap.rssi_dbm is None:
        return True
    return snap.rssi_dbm >= min_rssi_dbm
