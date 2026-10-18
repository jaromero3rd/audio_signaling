#!/bin/bash
# Runs at boot for ~3 minutes keeping the adapter Powered:on.
# Works without root (no AutoEnable edit). Complements start_bt_relay.
LOG=/tmp/host_bt_relay.log
echo "[bt-watch] start $(date -Is)" >>"$LOG"
for i in $(seq 1 36); do
  rfkill unblock bluetooth 2>/dev/null || true
  rfkill unblock all 2>/dev/null || true
  bluetoothctl power on >/dev/null 2>&1 || true
  if bluetoothctl show 2>/dev/null | grep -q "Powered: yes"; then
    echo "[bt-watch] Powered yes (tick $i)" >>"$LOG"
  else
    echo "[bt-watch] still off (tick $i)" >>"$LOG"
  fi
  sleep 5
done
echo "[bt-watch] done $(date -Is)" >>"$LOG"
