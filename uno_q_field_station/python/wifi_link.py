"""Wi-Fi health + telemetry POST to the ground computer.

On the UNO Q, join the phone Personal Hotspot (same LAN the Mac reaches
over USB: typically 172.20.10.0/28) and discover the laptop telemetry port.
"""

from __future__ import annotations

import ipaddress
import json
import os
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any

APPLE_HOTSPOT_NET = ipaddress.ip_network("172.20.10.0/28")


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


def join_phone_hotspot(ssid: str, password: str = "") -> None:
    """Join the phone Wi-Fi hotspot via NetworkManager (UNO Q Linux)."""
    snap = probe_wifi()
    if snap.connected and snap.ssid == ssid:
        print(f"already on hotspot {ssid} ip={snap.ip}")
        return
    cmd = ["nmcli", "device", "wifi", "connect", ssid]
    if password:
        cmd += ["password", password]
    print(f"joining phone hotspot {ssid}…")
    out = _run(cmd, timeout=30.0)
    if "error" in out.lower() and "successfully" not in out.lower():
        raise RuntimeError(f"nmcli failed to join {ssid}: {out.strip()[:200]}")
    snap = probe_wifi()
    print(f"hotspot {snap.ssid or ssid} ip={snap.ip} connected={snap.connected}")


def _health_ok(url: str, timeout_s: float = 0.6) -> bool:
    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            return 200 <= getattr(resp, "status", 200) < 300
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


def discover_ground_station(port: int = 8765) -> str | None:
    """Find the Mac on the phone hotspot LAN (USB client at 172.20.10.x)."""
    extra = os.environ.get("GROUND_STATION_HOST", "").strip()
    hosts: list[str] = []
    if extra:
        hosts.append(extra)
    snap = probe_wifi()
    if snap.ip:
        try:
            addr = ipaddress.ip_address(snap.ip)
            if addr in APPLE_HOTSPOT_NET:
                hosts.extend(str(h) for h in APPLE_HOTSPOT_NET.hosts() if str(h) != snap.ip)
        except ValueError:
            pass
    if not hosts:
        hosts.extend(str(h) for h in APPLE_HOTSPOT_NET.hosts())
    seen: set[str] = set()
    for host in hosts:
        if host in seen:
            continue
        seen.add(host)
        if _health_ok(f"http://{host}:{port}/health"):
            url = f"http://{host}:{port}/telemetry"
            print(f"found ground station {url}")
            return url
    return None


def resolve_ground_url(cfg: dict[str, Any]) -> str:
    env = os.environ.get("GROUND_STATION_URL", "").strip()
    if env:
        return env
    url = str(cfg.get("ground_station_url") or "").strip()
    if url and url.lower() != "auto":
        return url
    found = discover_ground_station()
    if not found:
        raise RuntimeError(
            "No ground station on 172.20.10.0/28:8765. On the Mac run "
            "python -m ground_station.sensor_recv and "
            "python -m drone_interface.field_links --check"
        )
    return found
