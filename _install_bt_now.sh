#!/bin/bash
set -u
cd /home/arduino || exit 1
sed -i 's/\r$//' ensure_bt.sh start_bt_relay.sh install_bt_relay.sh 2>/dev/null || true
chmod +x ensure_bt.sh start_bt_relay.sh install_bt_relay.sh host_bt_relay.py
bash install_bt_relay.sh
echo "=== diagnose ==="
bluetoothctl show 2>&1 | head -25
rfkill list 2>&1 || true
grep -E 'AutoEnable' /etc/bluetooth/main.conf 2>&1 || echo "no AutoEnable access/file"
