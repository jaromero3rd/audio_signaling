#!/usr/bin/env bash
# Deploy wheat ONNX server to Factory Computer agridrone-e2b and port-forward.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
NAME="${FACTORY_COMPUTER_NAME:-agridrone-e2b}"
REMOTE_DIR="/home/factory-user/wheat_infer"
SSH=(ssh -i "$HOME/.factory/.ssh/id_ed25519" -o IdentitiesOnly=yes
  -o StrictHostKeyChecking=accept-new
  -o "ProxyCommand=droid computer ssh --proxy %h"
  factory-user@"$NAME")
SCP=(scp -i "$HOME/.factory/.ssh/id_ed25519" -o IdentitiesOnly=yes
  -o StrictHostKeyChecking=accept-new
  -o "ProxyCommand=droid computer ssh --proxy %h")

echo "== mkdir on $NAME =="
"${SSH[@]}" "mkdir -p $REMOTE_DIR/wheat_detection $REMOTE_DIR/models"

echo "== upload code + model =="
"${SCP[@]}" "$ROOT/wheat_detection/detect.py" "$ROOT/wheat_detection/serve.py" \
  "factory-user@${NAME}:$REMOTE_DIR/wheat_detection/"
"${SCP[@]}" "$ROOT/wheat_detection/__init__.py" "factory-user@${NAME}:$REMOTE_DIR/wheat_detection/" 2>/dev/null || true
# empty package init if missing
"${SSH[@]}" "test -f $REMOTE_DIR/wheat_detection/__init__.py || echo '' > $REMOTE_DIR/wheat_detection/__init__.py"
"${SCP[@]}" "$ROOT/models/wheat_detection.onnx" "factory-user@${NAME}:$REMOTE_DIR/models/"

echo "== install deps on e2b =="
"${SSH[@]}" "python3 -m pip install --user -q onnxruntime opencv-python-headless numpy"

echo "== start server =="
"${SSH[@]}" "pkill -f 'wheat_detection.serve' 2>/dev/null || true; cd $REMOTE_DIR && nohup python3 -m wheat_detection.serve --port 8767 --model models/wheat_detection.onnx > /tmp/wheat_serve.log 2>&1 & sleep 1; curl -sf http://127.0.0.1:8767/health"

echo "== port-forward 8767 (leave this running) =="
echo "Then: export WHEAT_INFER_URL=http://127.0.0.1:8767/detect"
echo "      python3 demo.py ... --target wheat ..."
exec droid computer port-forward "$NAME" 8767:8767
