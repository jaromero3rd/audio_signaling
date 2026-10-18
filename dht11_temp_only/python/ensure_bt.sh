#!/bin/bash
# Power the Bluetooth radio on (BlueZ leaves it OFF after cold boot by default).
# See: AutoEnable=true in /etc/bluetooth/main.conf ; rfkill unblock.
LOG=${LOG:-/tmp/host_bt_relay.log}
{
  echo "[bt] ensure_bt $(date -Is)"
  # Soft-unblock if the kernel rfkill-switched BT off.
  if command -v rfkill >/dev/null 2>&1; then
    rfkill unblock bluetooth 2>/dev/null || true
    rfkill unblock all 2>/dev/null || true
  fi
  # Bring hci up the old way if present.
  if command -v hciconfig >/dev/null 2>&1; then
    hciconfig hci0 up 2>/dev/null || true
  fi
  for i in $(seq 1 30); do
    bluetoothctl power on >/dev/null 2>&1 || true
    if bluetoothctl show 2>/dev/null | grep -q "Powered: yes"; then
      echo "[bt] Powered: yes (try $i)"
      exit 0
    fi
    sleep 2
  done
  echo "[bt] WARNING: adapter still not Powered: yes"
  bluetoothctl show 2>&1 | head -15 || true
  exit 0
} >>"$LOG" 2>&1
