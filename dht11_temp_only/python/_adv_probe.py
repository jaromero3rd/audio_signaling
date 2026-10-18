#!/usr/bin/env python3
"""Minimal BLE advertiser (no experimental TxPower/interval properties)."""

import asyncio

from dbus_next.aio import MessageBus
from dbus_next.constants import BusType
from dbus_next.service import ServiceInterface, dbus_property, method
from dbus_next.signature import Variant

ADV_PATH = "/com/agridrone/adv0"
NAME = "AgriDrone-UNOQ"
SERVICE = "6e400001-b5a3-f393-e0a9-e50e24dcca9e"


class Adv(ServiceInterface):
    def __init__(self) -> None:
        super().__init__("org.bluez.LEAdvertisement1")
        self._type = "peripheral"
        self._uuids = [SERVICE]
        self._includes = ["local-name"]

    @method()
    def Release(self) -> None:  # noqa: N802
        print("advertisement released", flush=True)

    @dbus_property()
    def Type(self) -> "s":  # type: ignore[name-defined]  # noqa: F821,N802
        return self._type

    @Type.setter
    def Type(self, value: "s") -> None:  # type: ignore[name-defined]  # noqa: F821,N802
        self._type = value

    @dbus_property()
    def ServiceUUIDs(self) -> "as":  # type: ignore[name-defined]  # noqa: F821,N802
        return self._uuids

    @ServiceUUIDs.setter
    def ServiceUUIDs(self, value: "as") -> None:  # type: ignore[name-defined]  # noqa: F821,N802
        self._uuids = value

    @dbus_property()
    def Includes(self) -> "as":  # type: ignore[name-defined]  # noqa: F821,N802
        return self._includes

    @Includes.setter
    def Includes(self, value: "as") -> None:  # type: ignore[name-defined]  # noqa: F821,N802
        self._includes = value


async def main() -> None:
    bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
    adv = Adv()
    bus.export(ADV_PATH, adv)
    intro = await bus.introspect("org.bluez", "/org/bluez/hci0")
    obj = bus.get_proxy_object("org.bluez", "/org/bluez/hci0", intro)
    props = obj.get_interface("org.freedesktop.DBus.Properties")
    await props.call_set("org.bluez.Adapter1", "Powered", Variant("b", True))
    await props.call_set("org.bluez.Adapter1", "Connectable", Variant("b", True))
    await props.call_set("org.bluez.Adapter1", "Alias", Variant("s", NAME))
    mgr = obj.get_interface("org.bluez.LEAdvertisingManager1")
    await mgr.call_register_advertisement(ADV_PATH, {})
    print(f"advertising {NAME}", flush=True)
    while True:
        await asyncio.sleep(3600)


if __name__ == "__main__":
    asyncio.run(main())
