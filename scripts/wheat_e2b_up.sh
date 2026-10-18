#!/usr/bin/env bash
# Deploy vision server (wheat/poster ONNX + AprilTag) to Factory Computer + port-forward.
# Default model: models/wheat_poster.onnx (320). Override: MODEL=models/wheat_detection.onnx
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# SSH proxy needs Factory auth in this shell.
if [[ -f "$ROOT/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$ROOT/.env"
  set +a
fi
if [[ -z "${FACTORY_API_KEY:-}" ]]; then
  echo "FACTORY_API_KEY missing. source .env or: droid (interactive login)." >&2
  exit 1
fi
NAME="${FACTORY_COMPUTER_NAME:-agridrone-e2b}"
REMOTE_DIR="/home/factory-user/wheat_infer"
MODEL_LOCAL="${MODEL:-$ROOT/models/wheat_poster.onnx}"
MODEL_NAME="$(basename "$MODEL_LOCAL")"
SSH=(ssh -i "$HOME/.factory/.ssh/id_ed25519" -o IdentitiesOnly=yes
  -o StrictHostKeyChecking=accept-new
  -o "ProxyCommand=droid computer ssh --proxy %h"
  factory-user@"$NAME")
SCP=(scp -i "$HOME/.factory/.ssh/id_ed25519" -o IdentitiesOnly=yes
  -o StrictHostKeyChecking=accept-new
  -o "ProxyCommand=droid computer ssh --proxy %h")

if [[ ! -f "$MODEL_LOCAL" ]]; then
  echo "missing model: $MODEL_LOCAL" >&2
  exit 1
fi

echo "== mkdir on $NAME =="
"${SSH[@]}" "mkdir -p $REMOTE_DIR/wheat_detection $REMOTE_DIR/models"

echo "== upload code + model ($MODEL_NAME) =="
"${SCP[@]}" "$ROOT/wheat_detection/detect.py" "$ROOT/wheat_detection/serve.py" \
  "factory-user@${NAME}:$REMOTE_DIR/wheat_detection/"
"${SSH[@]}" "test -f $REMOTE_DIR/wheat_detection/__init__.py || echo '' > $REMOTE_DIR/wheat_detection/__init__.py"
"${SCP[@]}" "$MODEL_LOCAL" "factory-user@${NAME}:$REMOTE_DIR/models/$MODEL_NAME"

echo "== venv + deps on e2b =="
"${SSH[@]}" "cd $REMOTE_DIR && python3 -m venv .venv && .venv/bin/pip install -q --upgrade pip && .venv/bin/pip install -q onnxruntime opencv-python-headless numpy"

echo "== start vision server (ONNX + AprilTag) =="
"${SSH[@]}" "pkill -f 'wheat_detection.serve' 2>/dev/null || true; cd $REMOTE_DIR && nohup .venv/bin/python -m wheat_detection.serve --port 8767 --model models/$MODEL_NAME > /tmp/wheat_serve.log 2>&1 & sleep 2; curl -sf http://127.0.0.1:8767/health; echo; tail -5 /tmp/wheat_serve.log"

echo "== port-forward 8767 (leave this running) =="
echo "Then: export WHEAT_INFER_URL=http://127.0.0.1:8767/detect"
echo "      python3 demo.py ...   # all vision hits Factory /frame"
exec droid computer port-forward "$NAME" 8767:8767
