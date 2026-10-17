"""Bind Tello UDP to a USB Wi-Fi dongle so the laptop's main Wi-Fi stays on the internet."""

from __future__ import annotations

import ipaddress
import re
import socket
import subprocess
import sys
from dataclasses import dataclass

TELLO_NET = ipaddress.ip_network("192.168.10.0/24")
TELLO_HOST = "192.168.10.1"
TELLO_UDP_PORTS = {8889, 8890, 11111}  # command, state, video


@dataclass
class TelloLink:
    iface: str
    local_ip: str


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


def resolve_tello_link(iface: str | None = None) -> TelloLink:
    links = list_tello_links()
    if iface:
        for link in links:
            if link.iface == iface:
                return link
        raise RuntimeError(
            f"Interface {iface!r} has no 192.168.10.x address. "
            "Join the Tello Wi-Fi on the USB dongle, not the laptop's built-in Wi-Fi."
        )
    if len(links) == 1:
        return links[0]
    if len(links) > 1:
        names = ", ".join(f"{l.iface}={l.local_ip}" for l in links)
        raise RuntimeError(f"Several Tello-subnet interfaces ({names}). Pass --tello-iface.")
    raise RuntimeError(
        "No USB dongle on 192.168.10.0/24. Connect the dongle to the Tello SSID "
        "(keep built-in Wi-Fi on the internet), then retry."
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


def prepare_tello_dongle(iface: str | None = None) -> TelloLink:
    link = resolve_tello_link(iface)
    _install_bind_patch(link.local_ip)
    try:
        from djitellopy import Tello

        Tello.VS_UDP_IP = link.local_ip
    except ImportError:
        pass
    _try_host_route(link)
    print(f"Tello via USB dongle {link.iface} ({link.local_ip} → {TELLO_HOST})")
    return link
