#!/bin/bash
uid=$(id -u)
export XDG_RUNTIME_DIR=/run/user/$uid
export DBUS_SESSION_BUS_ADDRESS=unix:path=$XDG_RUNTIME_DIR/bus
mkdir -p "$HOME/.config/systemd/user"
cp /home/arduino/agridrone-bt-relay.user.service "$HOME/.config/systemd/user/agridrone-bt-relay.service"
systemctl --user daemon-reload
systemctl --user enable --now agridrone-bt-relay.service
echo "enabled=$(systemctl --user is-enabled agridrone-bt-relay 2>&1)"
echo "active=$(systemctl --user is-active agridrone-bt-relay 2>&1)"
loginctl show-user "$(id -un)" -p Linger
bash /home/arduino/ensure_bt.sh
pkill -f "/usr/bin/python3 /home/arduino/host_bt_relay.py" 2>/dev/null || true
rm -f /tmp/agridrone_bt_supervisor.pid
nohup bash /home/arduino/start_bt_relay.sh >/dev/null 2>&1 &
sleep 5
echo "=== status ==="
bluetoothctl show | grep -E "Powered|Alias"
ps aux | grep -E "[h]ost_bt|[s]tart_bt" | head -5
crontab -l
tail -12 /tmp/host_bt_relay.log
