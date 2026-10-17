#!/usr/bin/env bash
# Demo ONLINE path: prefer host relay (start ground_station.uno_q_receiver first).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
export PYTHONPATH="${ROOT}/uno_q/linux:${ROOT}:${PYTHONPATH:-}"
export UNO_Q_MOCK=1
export UNO_Q_FORCE_ONLINE=1
export UNO_Q_FORCE_OFFLINE=0
export UNO_Q_SD_DIR="${UNO_Q_SD_DIR:-${ROOT}/.uno_q_sd}"
export UNO_Q_HOST_URL="${UNO_Q_HOST_URL:-http://127.0.0.1:8787/telemetry}"
export UNO_Q_CAMERA=mock
cd "$ROOT"
echo "Tip: python3 -m ground_station.uno_q_receiver --port 8787"
exec python3 -m uno_q_linux --mock --ticks "${1:-5}"
