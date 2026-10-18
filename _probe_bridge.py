from arduino.app_utils import Bridge

print("probe start", flush=True)
for label, args in [("noargs", ()), ("emptystr", ("",))]:
    try:
        print("TRY", label, flush=True)
        r = Bridge.call("get_reading", *args, timeout=3)
        print("OK", repr(r)[:300], flush=True)
    except Exception as e:
        print("ERR", type(e).__name__, str(e), flush=True)
