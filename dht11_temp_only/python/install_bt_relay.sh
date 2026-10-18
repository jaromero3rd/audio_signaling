#!/bin/bash
# One-time on UNO Q: BLE relay + Bluetooth radio power-on at every cold boot.
# Root cause of "no advertise after power-cycle": BlueZ leaves adapter Powered: no
# unless AutoEnable=true (or we explicitly bluetoothctl power on / rfkill unblock).
set -u
DEST=/home/arduino/host_bt_relay.py
START=/home/arduino/start_bt_relay.sh
ENSURE=/home/arduino/ensure_bt.sh
USER_UNIT_DIR=/home/arduino/.config/systemd/user
USER_UNIT_SRC=/home/arduino/agridrone-bt-relay.user.service
SYS_UNIT_SRC=/home/arduino/agridrone-bt-relay.service
POWER_UNIT_SRC=/home/arduino/agridrone-bt-power.service

echo "[install] ensuring bless..."
python3 -m pip install --user --break-system-packages 'bless>=0.2.6' >/dev/null 2>&1 || true

for f in "$DEST" "$START" "$ENSURE"; do
  if [[ ! -f "$f" ]]; then
    echo "[install] missing $f — adb push first"
    exit 1
  fi
done
chmod +x "$DEST" "$START" "$ENSURE" 2>/dev/null || true
sed -i 's/\r$//' "$START" "$DEST" "$ENSURE" 2>/dev/null || true

ok=0

# --- Root fixes (Bluetooth AutoEnable + system units) ---
if sudo -n true 2>/dev/null; then
  echo "[install] applying root Bluetooth AutoEnable=true..."
  if [[ -f /etc/bluetooth/main.conf ]]; then
    if grep -q '^AutoEnable=' /etc/bluetooth/main.conf; then
      sudo sed -i 's/^AutoEnable=.*/AutoEnable=true/' /etc/bluetooth/main.conf
    elif grep -q '^\[Policy\]' /etc/bluetooth/main.conf; then
      sudo sed -i '/^\[Policy\]/a AutoEnable=true' /etc/bluetooth/main.conf
    else
      printf '\n[Policy]\nAutoEnable=true\n' | sudo tee -a /etc/bluetooth/main.conf >/dev/null
    fi
  else
    printf '[Policy]\nAutoEnable=true\n' | sudo tee /etc/bluetooth/main.conf >/dev/null
  fi
  if [[ -f "$POWER_UNIT_SRC" ]]; then
    sudo cp "$POWER_UNIT_SRC" /etc/systemd/system/agridrone-bt-power.service
    sudo systemctl daemon-reload
    sudo systemctl enable --now agridrone-bt-power.service
    echo "[install] agridrone-bt-power.service enabled"
  fi
  if [[ -f "$SYS_UNIT_SRC" ]]; then
    sudo cp "$SYS_UNIT_SRC" /etc/systemd/system/agridrone-bt-relay.service
    sudo systemctl daemon-reload
    sudo systemctl enable --now agridrone-bt-relay.service
    echo "[install] system agridrone-bt-relay.service enabled"
  fi
  echo "@reboot arduino sleep 20; /bin/bash $START" | sudo tee /etc/cron.d/agridrone-bt-relay >/dev/null
  sudo chmod 644 /etc/cron.d/agridrone-bt-relay
  ok=1
else
  echo "[install] no passwordless sudo — will still force power-on from user scripts"
  # Try writing AutoEnable without sudo (usually fails; ignore).
  true
fi

# --- systemd user service + linger ---
uid=$(id -u)
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$uid}"
export DBUS_SESSION_BUS_ADDRESS="${DBUS_SESSION_BUS_ADDRESS:-unix:path=$XDG_RUNTIME_DIR/bus}"
mkdir -p "$USER_UNIT_DIR"
if [[ -f "$USER_UNIT_SRC" ]]; then
  cp "$USER_UNIT_SRC" "$USER_UNIT_DIR/agridrone-bt-relay.service"
fi
systemctl --user daemon-reload 2>/dev/null || true
systemctl --user enable agridrone-bt-relay.service 2>/dev/null && ok=1 || true
systemctl --user restart agridrone-bt-relay.service 2>/dev/null && ok=1 || true
loginctl enable-linger "$(id -un)" 2>/dev/null || sudo -n loginctl enable-linger "$(id -un)" 2>/dev/null || true
echo "[install] Linger=$(loginctl show-user "$(id -un)" -p Linger 2>/dev/null || echo '?')"

# --- user crontab ---
(crontab -l 2>/dev/null | grep -v host_bt_relay | grep -v start_bt_relay || true
 echo "@reboot sleep 20; /bin/bash $ENSURE; /bin/bash $START") | crontab -
ok=1
echo "[install] crontab:"
crontab -l | grep -E 'start_bt|ensure_bt' || true

# --- Start now ---
bash "$ENSURE" || true
pkill -f "/usr/bin/python3 $DEST" 2>/dev/null || true
if [[ -f /tmp/agridrone_bt_supervisor.pid ]]; then
  kill "$(cat /tmp/agridrone_bt_supervisor.pid)" 2>/dev/null || true
  rm -f /tmp/agridrone_bt_supervisor.pid
fi
sleep 1
nohup /bin/bash "$START" >/dev/null 2>&1 &
echo "[install] supervisor started (pid $!)"
sleep 6
echo "=== Powered? ==="
bluetoothctl show 2>/dev/null | grep -E 'Powered|Alias' || true
ps aux | grep -E '[h]ost_bt|[s]tart_bt' | head -5
tail -20 /tmp/host_bt_relay.log 2>/dev/null || true

echo "[install] DONE — after battery power-cycle wait ~45s for advertise"
echo "[install] Laptop must keep: python -m ground_station.bt_sensor_recv"
