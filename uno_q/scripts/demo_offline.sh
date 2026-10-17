#!/usr/bin/env bash
# Demo OFFLINE path: SD JSONL + AprilTag seek/hold (mock frames).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
export PYTHONPATH="${ROOT}/uno_q/linux:${ROOT}:${PYTHONPATH:-}"
export UNO_Q_MOCK=1
export UNO_Q_FORCE_OFFLINE=1
export UNO_Q_FORCE_ONLINE=0
export UNO_Q_SD_DIR="${UNO_Q_SD_DIR:-${ROOT}/.uno_q_sd}"
export UNO_Q_CAMERA=mock
cd "$ROOT"
exec python3 -m uno_q_linux --mock --ticks "${1:-5}"
