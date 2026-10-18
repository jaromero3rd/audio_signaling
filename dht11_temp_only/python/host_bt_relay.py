#!/usr/bin/env python3
"""Bluetooth bridge on the UNO Q Debian HOST (not inside App Lab).

App Lab Python cannot open BlueZ. This process:
  - listens on TCP 9124 for sensor JSON from the App Lab container
  - advertises BLE peripheral "AgriDrone-UNOQ" (re-advertises after boot /
    disconnect so the laptop can reconnect without Windows pairing)
  - notifies subscribed laptops (bleak / bt_sensor_recv)

Wireless (battery) path:
  MCU → App Lab → TCP:9124 → this process → BLE "AgriDrone-UNOQ" → laptop bleak

One-time board setup (USB once):
  adb push host_bt_relay.py /home/arduino/
  adb push install_bt_relay.sh /home/arduino/
  adb shell 'bash /home/arduino/install_bt_relay.sh'

Then: App Lab Run (can stay on battery). Laptop (leave this running):
  python -m ground_station.bt_sensor_recv
  # Do NOT pair in Windows Bluetooth settings — dismiss Add Device popups.
"""

from __future__ import annotations

import asyncio
import socket
import subprocess
import threading
import time
from typing import Any

from dbus_next.service import ServiceInterface, dbus_property, method
from dbus_next.signature import Variant

SERVICE_UUID = "6e400001-b5a3-f393-e0a9-e50e24dcca9e"
TX_CHAR_UUID = "6e400003-b5a3-f393-e0a9-e50e24dcca9e"
RX_CHAR_UUID = "6e400002-b5a3-f393-e0a9-e50e24dcca9e"
DEVICE_NAME = "AgriDrone-UNOQ"
LISTEN_PORT = 9124
LAPTOP_PORT = 9125
ADV_PATH_PREFIX = "/com/agridrone/adv"
# Watch often: after a central disconnect BlueZ may drop LE advertising, and
# our old 25s unregister/re-register loop made that worse (Invalid Parameters).
ADV_WATCH_S = 3.0
BT_WAIT_S = 90.0
PAIR_STRIP_S = 90.0


class _CleanAdvertisement(ServiceInterface):
    """BLE advertiser without bless's experimental TxPower/interval fields."""

    def __init__(self, on_release: Any | None = None) -> None:
        super().__init__("org.bluez.LEAdvertisement1")
        self._type = "peripheral"
        self._uuids = [SERVICE_UUID]
        self._includes = ["local-name"]
        self._on_release = on_release

    @method()
    def Release(self):  # noqa: N802
        print("[relay] advertisement released — will re-advertise", flush=True)
        if self._on_release is not None:
            try:
                self._on_release()
            except Exception as exc:  # noqa: BLE001
                print(f"[relay] on_release: {exc}", flush=True)

    @dbus_property()
    def Type(self) -> "s":  # type: ignore[name-defined]  # noqa: F821,N802
        return self._type

    @Type.setter
    def Type(self, value: "s"):  # type: ignore[name-defined]  # noqa: F821,N802
        self._type = value

    @dbus_property()
    def ServiceUUIDs(self) -> "as":  # type: ignore[name-defined]  # noqa: F821,N802
        return self._uuids

    @ServiceUUIDs.setter
    def ServiceUUIDs(self, value: "as"):  # type: ignore[name-defined]  # noqa: F821,N802
        self._uuids = list(value)

    @dbus_property()
    def Includes(self) -> "as":  # type: ignore[name-defined]  # noqa: F821,N802
        return self._includes

    @Includes.setter
    def Includes(self, value: "as"):  # type: ignore[name-defined]  # noqa: F821,N802
        self._includes = list(value)


def _bt_power() -> None:
    """Power adapter on; do not touch pairings (safe while bleak is connected)."""
    for cmd in (
        ["rfkill", "unblock", "bluetooth"],
        ["rfkill", "unblock", "all"],
        ["bluetoothctl", "power", "on"],
        ["bluetoothctl", "pairable", "off"],
        ["bluetoothctl", "discoverable", "off"],
        ["hciconfig", "hci0", "class", "0x000000"],
    ):
        try:
            subprocess.run(cmd, capture_output=True, text=True, timeout=8)
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as exc:
            print(f"[relay] {cmd}: {exc}", flush=True)


def _bt_on() -> None:
    """Cold-boot helper: power on + strip classic bonds once."""
    _bt_power()
    _strip_classic_pairings()


def _strip_classic_pairings() -> None:
    """Drop classic BT bonds only. Do not touch unpaired BLE centrals (bleak)."""
    try:
        r = subprocess.run(
            ["bluetoothctl", "devices", "Paired"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return
    for line in (r.stdout or "").splitlines():
        parts = line.split()
        if len(parts) < 2 or parts[0] != "Device":
            continue
        addr = parts[1]
        name = " ".join(parts[2:]) if len(parts) > 2 else addr
        print(
            f"[relay] removing classic pair {addr} ({name}) — do not Windows-pair",
            flush=True,
        )
        for cmd in (
            ["bluetoothctl", "disconnect", addr],
            ["bluetoothctl", "remove", addr],
        ):
            try:
                subprocess.run(cmd, capture_output=True, text=True, timeout=8)
            except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
                pass


def _adv_active_instances() -> int:
    """BlueZ LEAdvertisingManager1 ActiveInstances (0 = not advertising)."""
    try:
        r = subprocess.run(
            ["bluetoothctl", "show"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return -1
    for line in (r.stdout or "").splitlines():
        if "ActiveInstances" not in line:
            continue
        # e.g. ActiveInstances: 0x01 (1)
        if "(" in line and ")" in line:
            try:
                return int(line.rsplit("(", 1)[-1].split(")", 1)[0])
            except ValueError:
                pass
        parts = line.replace(":", " ").split()
        for p in reversed(parts):
            try:
                return int(p, 0)
            except ValueError:
                continue
    return -1


def _any_device_connected() -> bool:
    try:
        r = subprocess.run(
            ["bluetoothctl", "devices", "Connected"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return False
    return any(line.startswith("Device ") for line in (r.stdout or "").splitlines())


def _adapter_power_cycle() -> None:
    """Last-resort fix when BlueZ refuses to re-enable LE advertising."""
    print("[relay] power-cycling BT adapter to recover advertising", flush=True)
    for cmd in (
        ["bluetoothctl", "power", "off"],
        ["bluetoothctl", "power", "on"],
        ["bluetoothctl", "pairable", "off"],
    ):
        try:
            subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as exc:
            print(f"[relay] {cmd}: {exc}", flush=True)
        time.sleep(0.6)


def _hci_leadv_kick() -> None:
    """Legacy workaround: BlueZ sometimes fails to re-enable LE adv after disconnect.

    Cited widely for BlueZ 5.x (hciconfig hci0 leadv 0 after InterfacesRemoved).
    """
    for cmd in (
        ["hciconfig", "hci0", "leadv", "3"],
        ["hciconfig", "hci0", "leadv", "0"],
        ["hciconfig", "hci0", "leadv"],
    ):
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
            if r.returncode == 0:
                print(f"[relay] hci kick ok: {' '.join(cmd[2:])}", flush=True)
                return
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            continue
    # Newer BlueZ may lack hciconfig — try bluetoothctl advertise as fallback.
    for cmd in (
        ["bluetoothctl", "advertise", "off"],
        ["bluetoothctl", "advertise", "on"],
    ):
        try:
            subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            pass


async def _recover_after_central_gone(ensure_cb: Any) -> None:
    """Ladder used when a BLE central disconnects and advertising may be dead.

    Order matches common public workarounds:
      1) hciconfig leadv kick (SO/BlueZ5 classic fix — even if ActiveInstances>0)
      2) brief wait for BlueZ auto re-enable
      3) force Unregister/Register on a new D-Bus path
      4) adapter power-cycle + raise (supervisor full restart)
    """
    print("[relay] disconnect recovery: hci leadv kick", flush=True)
    await asyncio.to_thread(_hci_leadv_kick)
    await asyncio.sleep(0.6)
    if _adv_active_instances() > 0 and _nordic_uart_registered():
        # Instance present is necessary but not sufficient; still force a
        # re-register if scanners still miss us — harness catches that.
        print("[relay] after leadv: ActiveInstances>0", flush=True)
        return

    print("[relay] adv still weak — force re-register advertisement", flush=True)
    try:
        await ensure_cb(force=True)
    except Exception as exc:  # noqa: BLE001
        print(f"[relay] force re-register failed: {exc}", flush=True)
    await asyncio.sleep(0.5)
    if _adv_active_instances() > 0 and _nordic_uart_registered():
        print("[relay] adv recovered via re-register", flush=True)
        return

    print("[relay] adv recovery failed — power-cycle + full BLE restart", flush=True)
    await asyncio.to_thread(_adapter_power_cycle)
    raise RuntimeError("LE advertising dead after disconnect — restarting BLE stack")


def _bluetooth_ready() -> bool:
    """True when BlueZ adapter exists and can be powered."""
    try:
        r = subprocess.run(
            ["bluetoothctl", "show"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        out = (r.stdout or "") + (r.stderr or "")
        if "No default controller" in out or r.returncode != 0:
            return False
        return "Powered: yes" in out or "Powered: no" in out
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return False


def _wait_for_bluetooth(timeout_s: float = BT_WAIT_S) -> None:
    """Block until bluetoothd / adapter is up (critical after cold boot)."""
    deadline = time.monotonic() + timeout_s
    attempt = 0
    while time.monotonic() < deadline:
        attempt += 1
        _bt_on()
        if _bluetooth_ready():
            print(f"[relay] Bluetooth adapter ready (attempt {attempt})", flush=True)
            return
        print(f"[relay] waiting for Bluetooth… ({attempt})", flush=True)
        time.sleep(2.0)
    print("[relay] WARNING: Bluetooth not ready — will keep retrying in BLE loop", flush=True)


class Relay:
    def __init__(self) -> None:
        self.server: Any = None
        self.loop: asyncio.AbstractEventLoop | None = None
        self.count = 0
        self._laptop_lock = threading.Lock()
        self._laptop_clients: list[socket.socket] = []
        self._refresh_adv: Any = None  # async callable set in ble_main

    def add_laptop(self, sock: socket.socket) -> None:
        with self._laptop_lock:
            self._laptop_clients.append(sock)

    def _fanout_laptop(self, line: bytes) -> None:
        dead: list[socket.socket] = []
        with self._laptop_lock:
            clients = list(self._laptop_clients)
        for sock in clients:
            try:
                sock.sendall(line)
            except OSError:
                dead.append(sock)
        if dead:
            with self._laptop_lock:
                self._laptop_clients = [s for s in self._laptop_clients if s not in dead]
            for sock in dead:
                try:
                    sock.close()
                except OSError:
                    pass

    def push(self, line: bytes) -> None:
        if not line.endswith(b"\n"):
            line = line + b"\n"
        self._fanout_laptop(line)
        self.count += 1
        if self.count == 1 or self.count % 20 == 0:
            print(f"[relay] sample #{self.count}: {line[:100]!r}", flush=True)

        if self.server is None or self.loop is None:
            return

        async def _notify() -> None:
            char = self.server.get_characteristic(TX_CHAR_UUID)
            char.value = bytearray(line)
            self.server.update_value(SERVICE_UUID, TX_CHAR_UUID)

        try:
            asyncio.run_coroutine_threadsafe(_notify(), self.loop)
        except Exception as exc:  # noqa: BLE001
            print(f"[relay] BLE notify failed: {exc}", flush=True)


def tcp_accept(relay: Relay) -> None:
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("0.0.0.0", LISTEN_PORT))
    srv.listen(4)
    print(f"[relay] waiting for App Lab on TCP :{LISTEN_PORT}", flush=True)
    while True:
        client, addr = srv.accept()
        print(f"[relay] App Lab connected from {addr}", flush=True)
        threading.Thread(target=_read_client, args=(client, relay), daemon=True).start()


def _read_client(client: socket.socket, relay: Relay) -> None:
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
                    relay.push(line + b"\n")
    except OSError:
        pass
    finally:
        try:
            client.close()
        except OSError:
            pass
        print("[relay] App Lab disconnected", flush=True)


def laptop_accept(relay: Relay) -> None:
    """Stream the same JSON lines to a laptop over TCP (works with adb forward)."""
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("0.0.0.0", LAPTOP_PORT))
    srv.listen(4)
    print(
        f"[relay] laptop TCP :{LAPTOP_PORT} "
        f"(adb forward tcp:{LAPTOP_PORT} tcp:{LAPTOP_PORT})",
        flush=True,
    )
    while True:
        client, addr = srv.accept()
        print(f"[relay] laptop connected from {addr}", flush=True)
        try:
            client.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except OSError:
            pass
        relay.add_laptop(client)


def _nordic_uart_registered() -> bool:
    """True if BlueZ still exposes our GATT Nordic UART service UUID."""
    try:
        r = subprocess.run(
            ["bluetoothctl", "show"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return SERVICE_UUID.lower() in ((r.stdout or "") + (r.stderr or "")).lower()
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return False


async def _ensure_advertising(
    server: Any,
    *,
    first: bool = False,
    force: bool = False,
    on_release: Any | None = None,
) -> None:
    """Keep one LE advertisement registered.

    Do NOT unregister on a timer — that races disconnect and can leave BlueZ
    unable to advertise until an adapter power-cycle (bluez#644 class of bug).
    Only (re)register on startup, Release, or ActiveInstances==0.
    """
    props = server.adapter.get_interface("org.freedesktop.DBus.Properties")
    await props.call_set("org.bluez.Adapter1", "Powered", Variant("b", True))
    await props.call_set("org.bluez.Adapter1", "Connectable", Variant("b", True))
    await props.call_set("org.bluez.Adapter1", "Pairable", Variant("b", False))
    await props.call_set("org.bluez.Adapter1", "Discoverable", Variant("b", False))
    await props.call_set("org.bluez.Adapter1", "Alias", Variant("s", DEVICE_NAME))

    mgr = server.adapter.get_interface("org.bluez.LEAdvertisingManager1")

    if first:
        try:
            await server.app.stop_advertising(server.adapter)
        except Exception as exc:  # noqa: BLE001
            print(f"[relay] stop default advertiser: {exc}", flush=True)
        await props.call_set("org.bluez.Adapter1", "Alias", Variant("s", DEVICE_NAME))
        await props.call_set("org.bluez.Adapter1", "Connectable", Variant("b", True))

    active = _adv_active_instances()
    path = getattr(server, "_agridrone_adv_path", None)
    if path and not force and active > 0:
        # Already advertising — leave the registration alone.
        return

    # Drop previous registration only when we must recreate it.
    if path:
        try:
            await mgr.call_unregister_advertisement(path)
        except Exception:  # noqa: BLE001
            pass

    n = int(getattr(server, "_agridrone_adv_n", 0)) + 1
    server._agridrone_adv_n = n
    path = f"{ADV_PATH_PREFIX}{n}"
    adv = _CleanAdvertisement(on_release=on_release)
    try:
        server.bus.export(path, adv)
    except Exception as exc:  # noqa: BLE001
        print(f"[relay] export adv {path}: {exc}", flush=True)
    server._agridrone_adv = adv
    server._agridrone_adv_path = path

    try:
        await mgr.call_register_advertisement(path, {})
        print(f"[relay] BLE advertising as {DEVICE_NAME} ({path})", flush=True)
    except Exception as exc:  # noqa: BLE001
        msg = str(exc)
        if "AlreadyExists" in msg or "already" in msg.lower():
            print(f"[relay] BLE advertising as {DEVICE_NAME} (already registered)", flush=True)
        else:
            raise

    if not _nordic_uart_registered():
        raise RuntimeError(
            "Nordic UART GATT missing after advertise — BLE stack needs full restart"
        )


async def ble_main(relay: Relay) -> None:
    from bless import (  # type: ignore
        BlessServer,
        GATTAttributePermissions,
        GATTCharacteristicProperties,
    )

    def perm(*names: str):
        for name in names:
            if hasattr(GATTAttributePermissions, name):
                return getattr(GATTAttributePermissions, name)
        return 0

    _wait_for_bluetooth()
    _bt_on()
    loop = asyncio.get_running_loop()
    relay.loop = loop
    server = BlessServer(name=DEVICE_NAME, loop=loop)
    relay.server = server
    adv_lock = asyncio.Lock()
    need_readvertise = asyncio.Event()
    fail_streak = 0
    last_pair_strip = time.monotonic()

    def _request_readvertise() -> None:
        loop.call_soon_threadsafe(need_readvertise.set)

    def _on_read(characteristic: Any, **_kwargs: Any) -> bytearray:
        val = getattr(characteristic, "value", None)
        return bytearray(val) if val else bytearray(b"")

    def _on_write(_characteristic: Any, _value: Any, **_kwargs: Any) -> None:
        return None

    server.read_request_func = _on_read
    server.write_request_func = _on_write

    tx = GATTCharacteristicProperties.read | GATTCharacteristicProperties.notify
    rx = (
        GATTCharacteristicProperties.write
        | GATTCharacteristicProperties.write_without_response
    )
    await server.add_new_service(SERVICE_UUID)
    await server.add_new_characteristic(
        SERVICE_UUID, TX_CHAR_UUID, tx, bytearray(b""), perm("readable", "read") or 0
    )
    await server.add_new_characteristic(
        SERVICE_UUID,
        RX_CHAR_UUID,
        rx,
        bytearray(b""),
        perm("writeable", "writable", "write") or perm("readable", "read"),
    )
    await server.start()

    async def ensure(*, first: bool = False, force: bool = False) -> None:
        async with adv_lock:
            await _ensure_advertising(
                server, first=first, force=force, on_release=_request_readvertise
            )

    relay._refresh_adv = ensure
    await ensure(first=True, force=True)
    was_connected = _any_device_connected()
    print(
        "[relay] Laptop: leave `python -m ground_station.bt_sensor_recv` running\n"
        "[relay] Disconnect recovery: auto → hci leadv → re-register → power-cycle\n"
        f"[relay] USB debug: adb forward tcp:{LAPTOP_PORT} tcp:{LAPTOP_PORT}",
        flush=True,
    )

    while True:
        try:
            await asyncio.wait_for(need_readvertise.wait(), timeout=ADV_WATCH_S)
            need_readvertise.clear()
            release_hit = True
            print("[relay] advertisement Release signal", flush=True)
        except asyncio.TimeoutError:
            release_hit = False

        try:
            _bt_power()
            if not _nordic_uart_registered():
                raise RuntimeError("Nordic UART GATT disappeared — restarting BLE stack")

            connected = _any_device_connected()
            # Edge: central just left → run recovery ladder (common BlueZ bug).
            if was_connected and not connected:
                print("[relay] central disconnected — running adv recovery ladder", flush=True)
                await _recover_after_central_gone(ensure)
                fail_streak = 0
            elif release_hit and not connected:
                await _recover_after_central_gone(ensure)
                fail_streak = 0
            else:
                active = _adv_active_instances()
                # Idle with no advert instance → recover (cold boot / stuck state).
                if active == 0 and not connected:
                    print("[relay] idle + ActiveInstances=0 — recovering", flush=True)
                    await _recover_after_central_gone(ensure)
                    fail_streak = 0
            was_connected = connected

            now = time.monotonic()
            if now - last_pair_strip >= PAIR_STRIP_S and not connected:
                _strip_classic_pairings()
                last_pair_strip = now
        except Exception as exc:  # noqa: BLE001
            fail_streak += 1
            print(
                f"[relay] advertise ensure failed ({fail_streak}): {exc}",
                flush=True,
            )
            if fail_streak >= 2:
                _adapter_power_cycle()
                raise RuntimeError(f"advertise recovery failed: {exc}") from exc
            await asyncio.sleep(1.0)


def main() -> None:
    print("[relay] UNO Q Bluetooth bridge starting…", flush=True)
    relay = Relay()
    threading.Thread(target=tcp_accept, args=(relay,), daemon=True).start()
    threading.Thread(target=laptop_accept, args=(relay,), daemon=True).start()

    # Never exit on BLE failure after power-cycle — keep retrying forever.
    backoff = 2.0
    while True:
        try:
            asyncio.run(ble_main(relay))
        except KeyboardInterrupt:
            raise
        except Exception as exc:  # noqa: BLE001
            print(f"[relay] BLE stack error: {exc} — restarting in {backoff:.0f}s", flush=True)
            time.sleep(backoff)
            backoff = min(backoff * 1.5, 30.0)
            continue
        backoff = 2.0


if __name__ == "__main__":
    main()
