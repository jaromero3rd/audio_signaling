#!/bin/bash
docker exec bca89fb98551 python3 - <<'PY'
from arduino.app_utils import Bridge
import time
for name, args in [("get_reading", ()), ("get_reading", ("",)), ("get_reading", (0,))]:
    try:
        print("TRY", name, args, flush=True)
        r = Bridge.call(name, *args, timeout=3)
        print("OK", repr(r)[:300], flush=True)
        break
    except Exception as e:
        print("ERR", type(e).__name__, e, flush=True)
PY
