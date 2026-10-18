#!/bin/bash
# Boot + forever supervisor for host_bt_relay.
# Critical after cold boot: BlueZ leaves the adapter Powered: no until we power it on.
LOG=/tmp/host_bt_relay.log
RELAY=/home/arduino/host_bt_relay.py
ENSURE=/home/arduino/ensure_bt.sh
MARKER=/tmp/agridrone_bt_supervisor.pid

mkdir -p /tmp
echo "[start] supervisor launch $(date -Is)" >>"$LOG"

if [[ -f "$MARKER" ]]; then
  old=$(cat "$MARKER" 2>/dev/null || true)
  if [[ -n "$old" ]] && kill -0 "$old" 2>/dev/null; then
    echo "[start] supervisor already running pid=$old" >>"$LOG"
    exit 0
  fi
fi
echo $$ >"$MARKER"

# Wait for bluetoothd, then force Powered:on (default after reboot is OFF).
for i in $(seq 1 60); do
  if pgrep -x bluetoothd >/dev/null 2>&1 || bluetoothctl show >/dev/null 2>&1; then
    echo "[start] bluetoothd visible (try $i)" >>"$LOG"
    break
  fi
  sleep 2
done

if [[ -x "$ENSURE" ]]; then
  bash "$ENSURE" || true
else
  rfkill unblock bluetooth 2>/dev/null || true
  bluetoothctl power on >/dev/null 2>&1 || true
fi
sleep 2

while true; do
  # Re-assert power every loop — some boards drop Powered after USB unplug.
  bash "$ENSURE" 2>/dev/null || bluetoothctl power on >/dev/null 2>&1 || true

  if pgrep -f "/usr/bin/python3 /home/arduino/host_bt_relay.py" >/dev/null 2>&1; then
    sleep 10
    continue
  fi
  echo "[start] starting host_bt_relay $(date -Is)" >>"$LOG"
  /usr/bin/python3 "$RELAY" >>"$LOG" 2>&1 &
  rp=$!
  echo "[start] relay pid=$rp" >>"$LOG"
  wait "$rp" 2>/dev/null || true
  echo "[start] relay exited — restart in 5s $(date -Is)" >>"$LOG"
  sleep 5
done
