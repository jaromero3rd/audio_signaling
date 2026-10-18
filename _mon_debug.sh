#!/bin/bash
set -e
nohup timeout 6 arduino-app-cli monitor >/tmp/mon_out.txt 2>/tmp/mon_err.txt &
sleep 1
journalctl -u arduino-router --no-pager -n 20 || true
sleep 5
echo "===OUT==="
cat /tmp/mon_out.txt || true
echo "===ERR==="
cat /tmp/mon_err.txt || true
echo "===LIST==="
arduino-app-cli app list --format json 2>/dev/null | head -c 500 || true
