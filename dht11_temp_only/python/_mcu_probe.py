from arduino.app_utils import Bridge

print("probe", flush=True)
try:
    r = Bridge.call("get_reading", timeout=4)
    print("OK", repr(r)[:300], flush=True)
except Exception as exc:
    print("ERR", type(exc).__name__, exc, flush=True)
