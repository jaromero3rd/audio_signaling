"""Bind Tello UDP to one radio so internet can stay on another path.

macOS default: built-in Wi-Fi joins the Tello; USB phone hotspot is internet.
Linux / ``--dongle``: USB Wi-Fi stick still supported.
"""

from __future__ import annotations

import ipaddress
import json
import re
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

TELLO_NET = ipaddress.ip_network("192.168.10.0/24")
TELLO_HOST = "192.168.10.1"
TELLO_UDP_PORTS = {8889, 8890, 11111}  # command, state, video
DEFAULT_DONGLE = 7
DONGLES_PATH = Path(__file__).resolve().parents[1] / "config" / "dongles.json"


@dataclass
class TelloLink:
    iface: str
    local_ip: str
    ssid: str | None = None
    drone: str | None = None
    number: int | None = None


@dataclass
class DongleSpec:
    number: int
    iface: str
    ssid: str
    drone: str
    mac: str = ""
    video_port: int | None = None


def _nmcli(args: list[str], timeout: float = 25.0) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            ["nmcli", *args],
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as exc:
        raise RuntimeError("nmcli not found; install NetworkManager to join Tello Wi-Fi") from exc
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(args, 1, "", "nmcli timed out")


def load_dongles(path: Path | None = None) -> list[DongleSpec]:
    cfg = path or DONGLES_PATH
    data = json.loads(cfg.read_text())
    rows = data.get("dongles", data) if isinstance(data, dict) else data
    out: list[DongleSpec] = []
    for row in rows:
        if not row.get("iface") or not row.get("ssid"):
            continue
        out.append(
            DongleSpec(
                number=int(row["number"]),
                iface=str(row["iface"]),
                ssid=str(row["ssid"]),
                drone=str(row.get("drone") or row["ssid"]),
                mac=str(row.get("mac") or ""),
                video_port=int(row["video_port"]) if row.get("video_port") is not None else None,
            )
        )
    return out


def dongle_by_number(number: int, path: Path | None = None) -> DongleSpec:
    for spec in load_dongles(path):
        if spec.number == number:
            return spec
    raise RuntimeError(f"Dongle {number} missing from {path or DONGLES_PATH}")


def iface_present(iface: str) -> bool:
    return Path(f"/sys/class/net/{iface}").exists()


def _ifconfig_ipv4() -> list[tuple[str, str]]:
    try:
        out = subprocess.check_output(["ifconfig"], text=True, stderr=subprocess.DEVNULL)
    except (FileNotFoundError, subprocess.CalledProcessError):
        out = ""
    if not out and sys.platform != "darwin":
        try:
            out = subprocess.check_output(
                ["ip", "-4", "-o", "addr"], text=True, stderr=subprocess.DEVNULL
            )
        except (FileNotFoundError, subprocess.CalledProcessError):
            return []
        rows = []
        for line in out.splitlines():
            # 8: enx...    inet 192.168.10.2/24 ...
            m = re.match(r"\d+:\s+(\S+)\s+inet\s+(\d+\.\d+\.\d+\.\d+)", line)
            if m:
                rows.append((m.group(1).split("@")[0], m.group(2)))
        return rows

    rows = []
    iface = None
    for line in out.splitlines():
        head = re.match(r"^([A-Za-z0-9._-]+):", line)
        if head:
            iface = head.group(1)
            continue
        inet = re.search(r"inet (\d+\.\d+\.\d+\.\d+)", line)
        if iface and inet:
            rows.append((iface, inet.group(1)))
    return rows


def list_tello_links() -> list[TelloLink]:
    found: list[TelloLink] = []
    for iface, ip in _ifconfig_ipv4():
        if iface.startswith("lo"):
            continue
        try:
            addr = ipaddress.ip_address(ip)
        except ValueError:
            continue
        if addr in TELLO_NET and ip != TELLO_HOST:
            found.append(TelloLink(iface=iface, local_ip=ip))
    return found


def on_tello_lan(iface: str) -> bool:
    return any(link.iface == iface for link in list_tello_links())


def resolve_tello_link(iface: str | None = None) -> TelloLink:
    links = list_tello_links()
    if iface:
        for link in links:
            if link.iface == iface:
                return link
        raise RuntimeError(
            f"Interface {iface!r} has no 192.168.10.x address. "
            "On a Mac, join the Tello SSID on built-in Wi-Fi "
            "(python -m drone_interface.field_links --apply)."
        )
    if len(links) == 1:
        return links[0]
    if len(links) > 1:
        names = ", ".join(f"{l.iface}={l.local_ip}" for l in links)
        raise RuntimeError(f"Several Tello-subnet interfaces ({names}). Pass --tello-iface.")
    raise RuntimeError(
        "No interface on 192.168.10.0/24. Join the Tello SSID on Mac Wi-Fi "
        "(or pass --dongle for a USB stick), then retry."
    )


def _device_state(iface: str) -> tuple[str, str]:
    """Return (STATE, CONNECTION) for iface from nmcli device status."""
    result = _nmcli(["-t", "-f", "DEVICE,STATE,CONNECTION", "device", "status"], timeout=10.0)
    for line in result.stdout.splitlines():
        parts = line.split(":")
        if len(parts) >= 3 and parts[0] == iface:
            return parts[1], parts[2]
    return "missing", ""


def connect_tello_wifi(
    ssid: str,
    iface: str,
    *,
    timeout: float = 75.0,
    profile: str | None = None,
) -> None:
    """Join ``ssid`` on USB ``iface`` via NetworkManager. Leaves built-in Wi-Fi alone."""
    if not iface_present(iface):
        raise RuntimeError(f"Dongle iface {iface!r} not plugged in")
    if on_tello_lan(iface):
        print(f"{ssid} already up on {iface}")
        return

    profile = profile or ssid
    print(f"Joining {ssid} on USB dongle {iface}…")
    deadline = time.time() + timeout
    last_nudge = 0.0
    last_status = ""
    while time.time() < deadline:
        if on_tello_lan(iface):
            print(f"{ssid} connected on {iface}")
            return
        state, connection = _device_state(iface)
        status = f"{state}:{connection}"
        if status != last_status:
            print(f"  {iface} → {status}")
            last_status = status
        now = time.time()
        # Let NetworkManager finish an in-flight association; re-nudging kills it.
        if "connecting" in state:
            time.sleep(1.0)
            continue
        if now - last_nudge < 8.0:
            time.sleep(1.0)
            continue
        last_nudge = now
        _nmcli(["device", "wifi", "rescan", "ifname", iface], timeout=15.0)
        result = _nmcli(["connection", "up", profile, "ifname", iface], timeout=25.0)
        if result.returncode == 0:
            continue
        err = (result.stderr or result.stdout or "").strip()
        if "unknown connection" in err.lower() or "not found" in err.lower():
            # Open AP (Tello has no password): create/activate profile on this stick.
            _nmcli(
                [
                    "device",
                    "wifi",
                    "connect",
                    ssid,
                    "ifname",
                    iface,
                    "name",
                    profile,
                ],
                timeout=30.0,
            )
        elif err and "timed out" not in err.lower():
            print(f"  nmcli: {err.splitlines()[-1]}")
        time.sleep(1.0)

    raise RuntimeError(
        f"Timed out joining {ssid} on {iface}. Power on the Tello, wait for its SSID, retry."
    )


def _install_bind_patch(local_ip: str) -> None:
    """Force djitellopy (and later UDP binds on Tello ports) onto the dongle IP."""
    orig = socket.socket.bind

    def bind(self, address):  # type: ignore[no-untyped-def]
        try:
            host, port = address[0], int(address[1])
        except (TypeError, ValueError, IndexError):
            return orig(self, address)
        if port in TELLO_UDP_PORTS and host in ("", "0.0.0.0", "::"):
            address = (local_ip, port)
        return orig(self, address)

    socket.socket.bind = bind  # type: ignore[method-assign]


def _try_host_route(link: TelloLink) -> None:
    """Best-effort: send 192.168.10.1 out the dongle. May need admin rights."""
    if sys.platform == "darwin":
        cmd = ["route", "-n", "add", "-host", TELLO_HOST, "-interface", link.iface]
    else:
        cmd = ["ip", "route", "replace", f"{TELLO_HOST}/32", "dev", link.iface]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0 and "File exists" not in (r.stderr or "") and "exists" not in (r.stderr or ""):
            print(
                f"Note: could not add Tello host route ({r.stderr.strip() or r.returncode}). "
                "Socket bind to the dongle IP is still in effect."
            )
    except FileNotFoundError:
        pass


def prepare_tello_link(
    iface: str | None = None,
    *,
    ssid: str | None = None,
    dongle: int | None = None,
    connect: bool = True,
) -> TelloLink:
    """Bind Tello UDP. macOS uses built-in Wi-Fi unless ``dongle`` is set."""
    if dongle is None and sys.platform == "darwin":
        from drone_interface.field_links import prepare_tello_on_wifi

        return prepare_tello_on_wifi(ssid=ssid, iface=iface, connect=connect)
    return prepare_tello_dongle(iface, ssid=ssid, dongle=dongle, connect=connect)


def prepare_tello_dongle(
    iface: str | None = None,
    *,
    ssid: str | None = None,
    dongle: int | None = None,
    connect: bool = True,
) -> TelloLink:
    """Resolve USB stick, optionally ``nmcli``-join the Tello SSID, bind UDP to that IP.

    Defaults: dongle **7** → iface ``wlx6c4cbce344fc`` → SSID ``TELLO-7``.
    """
    spec: DongleSpec | None = None
    if dongle is not None:
        spec = dongle_by_number(dongle)
        iface = iface or spec.iface
        ssid = ssid or spec.ssid
    elif iface is None and ssid is None:
        # Demo default: stick 7 / TELLO-7 when that stick is present.
        try:
            spec = dongle_by_number(DEFAULT_DONGLE)
            if iface_present(spec.iface):
                iface = spec.iface
                ssid = ssid or spec.ssid
        except RuntimeError:
            pass

    if connect and ssid and iface:
        connect_tello_wifi(ssid, iface)

    link = resolve_tello_link(iface)
    if spec is not None:
        link.ssid = spec.ssid
        link.drone = spec.drone
        link.number = spec.number
    elif ssid:
        link.ssid = ssid
    _install_bind_patch(link.local_ip)
    try:
        from djitellopy import Tello

        Tello.VS_UDP_IP = link.local_ip
    except ImportError:
        pass
    _try_host_route(link)
    label = link.drone or link.ssid or "Tello"
    print(f"{label} via USB dongle {link.iface} ({link.local_ip} → {TELLO_HOST})")
    return link
