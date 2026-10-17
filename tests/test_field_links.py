import unittest

from drone_interface.field_links import (
    parse_hardware_ports,
    parse_network_services,
    phone_first_order,
)


PORTS = """
Hardware Port: Wi-Fi
Device: en0
Ethernet Address: aa:bb:cc:dd:ee:ff

Hardware Port: iPhone USB
Device: en13
Ethernet Address: 11:22:33:44:55:66

Hardware Port: Thunderbolt Bridge
Device: bridge0
Ethernet Address: 00:00:00:00:00:00
"""

SERVICES = """
An asterisk (*) denotes that a network service is disabled.
Wi-Fi
iPhone USB
Thunderbolt Bridge
*Bluetooth PAN
"""


class FieldLinkParseTests(unittest.TestCase):
    def test_hardware_ports(self):
        ports = parse_hardware_ports(PORTS)
        kinds = {p.kind: p.device for p in ports}
        self.assertEqual(kinds["wifi"], "en0")
        self.assertEqual(kinds["phone_usb"], "en13")
        self.assertEqual(kinds["other"], "bridge0")

    def test_service_order_puts_phone_first(self):
        names, disabled = parse_network_services(SERVICES)
        self.assertIn("Bluetooth PAN", disabled)
        order = phone_first_order(names, disabled, "iPhone USB")
        self.assertEqual(order[0], "iPhone USB")
        self.assertEqual(order[1], "Wi-Fi")
        self.assertNotIn("Bluetooth PAN", order)


if __name__ == "__main__":
    unittest.main()
