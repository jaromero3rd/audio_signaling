"""Split radios on a Mac: Wi-Fi → Tello, USB phone hotspot → internet + Uno Q.

No extra dongle. The phone's Personal Hotspot (USB to this Mac, Wi-Fi to the
UNO Q) puts the laptop and the board on 172.20.10.0/28. Built-in Wi-Fi joins
the Tello AP (192.168.10.0/24) for command/video only.

  python -m drone_interface.field_links --check
  python -m drone_interface.field_links --apply --tello-ssid TELLO-7
"""

from __future__ import annotations

import argparse
import ipaddress
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from urllib.error import URLError
from urllib.request import urlopen

TELLO_NET = ipaddress.ip_network("192.168.10.0/24")
TELLO_HOST = "192.168.10.1"
APPLE_HOTSPOT_NET = ipaddress.ip_network("172.20.10.0/28")
DEFAULT_TELLO_SSID = os.environ.get("TELLO_SSID", "TELLO-FE2950")
SENSOR_PORT = int(os.environ.get("UNOQ_TELEMETRY_PORT", "8765"))


@dataclass
class HardwarePort:
    name: str
    device: str
    kind: str  # wifi | phone_usb | other


@dataclass
class LinkStatus:
    wifi_iface: str | None
    wifi_service: str | None
    phone_iface: str | None
    phone_service: str | None
    phone_ip: str | None
    tello_iface: str | None
    tello_ip: str | None
    services: list[str]
    notes: list[str]


def _run(cmd: list[str], timeout: float = 20.0) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(cmd, text=True, capture_output=True, timeout=timeout, check=False)
    except FileNotFoundError:
        return subprocess.CompletedProcess(cmd, 127, "", f"missing {cmd[0]}")
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(cmd, 1, "", "timed out")


def parse_hardware_ports(text: str) -> list[HardwarePort]:
    ports: list[HardwarePort] = []
    name = ""
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("Hardware Port:"):
            name = line.split(":", 1)[1].strip()
        elif line.startswith("Device:") and name:
            device = line.split(":", 1)[1].strip()
            lower = name.lower()
            if "wi-fi" in lower or lower == "wifi" or "airport" in lower:
                kind = "wifi"
            elif any(tok in lower for tok in ("iphone", "ipad", "ncm")):
                kind = "phone_usb"
            else:
                kind = "other"
            ports.append(HardwarePort(name=name, device=device, kind=kind))
            name = ""
    return ports


def parse_network_services(text: str) -> tuple[list[str], set[str]]:
    names: list[str] = []
    disabled: set[str] = set()
    for line in text.splitlines():
        raw = line.strip()
        if not raw or raw.startswith("An asterisk"):
            continue
        if raw.startswith("*"):
            name = raw[1:].strip()
            disabled.add(name)
            names.append(name)
        else:
            names.append(raw)
    return names, disabled


def phone_first_order(services: list[str], disabled: set[str], phone_service: str) -> list[str]:
    enabled = [n for n in services if n not in disabled]
    if phone_service not in enabled:
        raise RuntimeError(f"Network service {phone_service!r} is missing or disabled")
    return [phone_service] + [n for n in enabled if n != phone_service]


def ipv4_on_ifaces() -> list[tuple[str, str]]:
    from drone_interface.tello_net import _ifconfig_ipv4

    return _ifconfig_ipv4()


def _ip_in(net: ipaddress.IPv4Network, ip: str) -> bool:
    try:
        return ipaddress.ip_address(ip) in net
    except ValueError:
        return False


def hardware_ports() -> list[HardwarePort]:
    r = _run(["networksetup", "-listallhardwareports"])
    return parse_hardware_ports(r.stdout or "")


def list_services() -> tuple[list[str], set[str]]:
    r = _run(["networksetup", "-listallnetworkservices"])
    return parse_network_services(r.stdout or "")


def wifi_port(ports: list[HardwarePort] | None = None) -> HardwarePort | None:
    ports = ports if ports is not None else hardware_ports()
    return next((p for p in ports if p.kind == "wifi"), None)


def phone_usb_port(ports: list[HardwarePort] | None = None) -> HardwarePort | None:
    ports = ports if ports is not None else hardware_ports()
    hit = next((p for p in ports if p.kind == "phone_usb"), None)
    if hit:
        return hit
    for iface, ip in ipv4_on_ifaces():
        if _ip_in(APPLE_HOTSPOT_NET, ip):
            return HardwarePort(name="iPhone USB", device=iface, kind="phone_usb")
    return None


def tether_ipv4() -> str | None:
    for iface, ip in ipv4_on_ifaces():
        if _ip_in(APPLE_HOTSPOT_NET, ip):
            return ip
    port = phone_usb_port()
    if not port:
        return None
    for iface, ip in ipv4_on_ifaces():
        if iface == port.device and not ip.startswith("127."):
            return ip
    return None


def tello_on_wifi(wifi_iface: str | None) -> tuple[str | None, str | None]:
    for iface, ip in ipv4_on_ifaces():
        if wifi_iface and iface != wifi_iface:
            continue
        if _ip_in(TELLO_NET, ip) and ip != TELLO_HOST:
            return iface, ip
    return None, None


def inspect() -> LinkStatus:
    notes: list[str] = []
    ports = hardware_ports()
    wifi = wifi_port(ports)
    phone = phone_usb_port(ports)
    services, _disabled = list_services()
    phone_ip = tether_ipv4()
    tello_iface, tello_ip = tello_on_wifi(wifi.device if wifi else None)
    if not phone:
        notes.append("Plug the phone in over USB, Trust this computer, and turn Personal Hotspot on.")
    elif not phone_ip:
        notes.append(f"USB {phone.device} is present but has no IPv4. Enable Personal Hotspot → USB.")
    if not tello_ip:
        notes.append("Built-in Wi-Fi is not on 192.168.10.x. Power the Tello and --apply to join its SSID.")
    if phone and services and phone.name in services and services[0] != phone.name:
        notes.append(
            f"Put {phone.name!r} above Wi-Fi in network service order so internet is not the Tello."
        )
    return LinkStatus(
        wifi_iface=wifi.device if wifi else None,
        wifi_service=wifi.name if wifi else None,
        phone_iface=phone.device if phone else None,
        phone_service=phone.name if phone else None,
        phone_ip=phone_ip,
        tello_iface=tello_iface,
        tello_ip=tello_ip,
        services=services,
        notes=notes,
    )


def apply_service_order(phone_service: str) -> None:
    services, disabled = list_services()
    order = phone_first_order(services, disabled, phone_service)
    if services and [n for n in services if n not in disabled][:1] == order[:1]:
        print(f"Service order already prefers {phone_service}")
        return
    r = _run(["networksetup", "-ordernetworkservices", *order])
    if r.returncode != 0:
        err = (r.stderr or r.stdout or "").strip()
        print(
            "Could not change network service order automatically "
            f"({err or r.returncode}). In System Settings → Network, drag "
            f"{phone_service} above Wi-Fi, or run:\n"
            f"  networksetup -ordernetworkservices {' '.join(repr(s) for s in order)}"
        )
        return
    print(f"Network service order: {phone_service} first (internet via USB hotspot)")


def join_tello_wifi(ssid: str, iface: str, timeout: float = 75.0) -> None:
    _, ip = tello_on_wifi(iface)
    if ip:
        print(f"{ssid} already on {iface} ({ip})")
        return
    print(f"Joining {ssid} on built-in Wi-Fi {iface}…")
    _run(["networksetup", "-setairportpower", iface, "on"], timeout=10.0)
    deadline = time.time() + timeout
    last = 0.0
    while time.time() < deadline:
        _, ip = tello_on_wifi(iface)
        if ip:
            print(f"{ssid} connected on {iface} ({ip})")
            return
        now = time.time()
        if now - last >= 8.0:
            last = now
            r = _run(["networksetup", "-setairportnetwork", iface, ssid], timeout=30.0)
            err = (r.stderr or r.stdout or "").strip()
            if r.returncode != 0 and err:
                print(f"  {err.splitlines()[-1]}")
        time.sleep(1.0)
    raise RuntimeError(
        f"Timed out joining {ssid} on {iface}. Power the Tello, wait for its SSID, retry."
    )


def _host_route_tello(iface: str) -> None:
    r = _run(["route", "-n", "add", "-host", TELLO_HOST, "-interface", iface])
    err = (r.stderr or "").lower()
    if r.returncode != 0 and "exists" not in err and "file exists" not in err:
        print(
            f"Note: host route to {TELLO_HOST} via {iface} not added "
            f"({(r.stderr or r.stdout or r.returncode)!s}). UDP bind still pins Tello."
        )


def prepare_tello_on_wifi(
    *,
    ssid: str | None = None,
    iface: str | None = None,
    connect: bool = True,
):
    """Join Tello on Mac Wi-Fi, bind Tello UDP there, prefer USB phone for default route."""
    from drone_interface.tello_net import TelloLink, _install_bind_patch, resolve_tello_link

    ssid = ssid or DEFAULT_TELLO_SSID
    status = inspect()
    wifi_iface = iface or status.wifi_iface
    if not wifi_iface:
        raise RuntimeError("No Wi-Fi hardware port (en0). This path is for the Mac built-in radio.")

    if status.phone_service:
        apply_service_order(status.phone_service)
    else:
        print(
            "No iPhone/iPad USB yet. Plug the phone in, enable Personal Hotspot, "
            "then re-run so internet does not go through the Tello."
        )

    if connect:
        join_tello_wifi(ssid, wifi_iface)
        if status.phone_service:
            apply_service_order(status.phone_service)

    link = resolve_tello_link(wifi_iface)
    link.ssid = ssid
    link.drone = ssid
    _install_bind_patch(link.local_ip)
    try:
        from djitellopy import Tello

        Tello.VS_UDP_IP = link.local_ip
    except ImportError:
        pass
    _host_route_tello(link.iface)
    phone_ip = tether_ipv4()
    print(f"{ssid} via built-in Wi-Fi {link.iface} ({link.local_ip} → {TELLO_HOST})")
    if phone_ip:
        print(
            f"Internet + Uno Q LAN via USB hotspot ({phone_ip}). "
            f"Board should POST http://{phone_ip}:{SENSOR_PORT}/telemetry"
        )
    else:
        print("USB phone hotspot not up — Factory/Grok will fail until it is.")
    return link


def ground_station_url(port: int = SENSOR_PORT) -> str | None:
    ip = tether_ipv4()
    if not ip:
        return None
    return f"http://{ip}:{port}/telemetry"


def print_status(status: LinkStatus) -> None:
    print(f"Wi-Fi         {status.wifi_service or '?'}  {status.wifi_iface or 'missing'}")
    print(f"Phone USB     {status.phone_service or '?'}  {status.phone_iface or 'missing'}  {status.phone_ip or 'no ipv4'}")
    print(f"Tello         {status.tello_iface or 'not joined'}  {status.tello_ip or ''}")
    if status.services:
        print("Service order " + " → ".join(status.services[:6]))
    url = ground_station_url()
    if url:
        print(f"Uno Q target  {url}")
        print(f"Health check  {url.rsplit('/', 1)[0]}/health")
    for note in status.notes:
        print(f"Note: {note}")


def probe_health(url: str, timeout: float = 1.5) -> bool:
    try:
        with urlopen(url, timeout=timeout) as resp:
            return 200 <= getattr(resp, "status", 200) < 300
    except (URLError, TimeoutError, OSError):
        return False


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="Mac radios: Wi-Fi=Tello, USB phone=internet and Uno Q LAN."
    )
    p.add_argument("--check", action="store_true", help="print current links (default)")
    p.add_argument("--apply", action="store_true", help="service order + join Tello SSID")
    p.add_argument("--tello-ssid", default=DEFAULT_TELLO_SSID)
    p.add_argument("--tello-iface", default=None, help="Wi-Fi device, default from hardware ports")
    p.add_argument("--no-join", action="store_true", help="reorder only; do not join Tello")
    args = p.parse_args(argv)
    if sys.platform != "darwin":
        print("field_links is for macOS. UNO Q uses wifi_link.join_phone_hotspot.")
        return 1
    if args.apply:
        prepare_tello_on_wifi(
            ssid=args.tello_ssid,
            iface=args.tello_iface,
            connect=not args.no_join,
        )
        print_status(inspect())
        return 0
    print_status(inspect())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
