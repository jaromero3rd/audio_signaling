#!/usr/bin/env bash
# Laptop / CI: mock sensors + local SD + mock frames; state follows host reachability.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
export PYTHONPATH="${ROOT}/uno_q/linux:${ROOT}:${PYTHONPATH:-}"
export UNO_Q_MOCK=1
export UNO_Q_SD_DIR="${UNO_Q_SD_DIR:-${ROOT}/.uno_q_sd}"
export UNO_Q_HOST_IP="${UNO_Q_HOST_IP:-127.0.0.1}"
export UNO_Q_HOST_PORT="${UNO_Q_HOST_PORT:-8787}"
export UNO_Q_HOST_URL="${UNO_Q_HOST_URL:-http://127.0.0.1:8787/telemetry}"
export UNO_Q_CAMERA="${UNO_Q_CAMERA:-mock}"
cd "$ROOT"
exec python3 -m uno_q_linux --mock "$@"
