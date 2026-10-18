#!/bin/bash
cid=$(docker ps -q -f name=dht11_temp_only_import-20261018-041521-main)
echo "CID=$cid"
docker inspect "$cid" --format '{{json .Mounts}}' | head -c 2000; echo
docker inspect "$cid" --format '{{range .Config.Env}}{{println .}}{{end}}' | head -40
echo "==== logs ===="
arduino-app-cli app logs user:dht11_temp_only_import-20261018-041521 --tail 25
echo "==== probe ===="
docker cp /tmp/_probe_bridge.py "$cid":/tmp/_probe_bridge.py
docker exec "$cid" python3 /tmp/_probe_bridge.py
