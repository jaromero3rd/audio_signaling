"""Mock-first entrypoint for the SDK-driven camera mission."""

from __future__ import annotations

import sys


async def check_sdk() -> int:
    from ground_station.agent.controller import ControllerError, DroidController
    from ground_station.agent.driver import MissionDriver

    class ReadinessMission:
        def snapshot(self):
            return {"phase": "idle", "flying": False}

        def request_abort(self):
            return {"ok": True}

        def request_start(self):
            raise ControllerError("Readiness checks cannot start a mission")

    try:
        async with DroidController(MissionDriver(ReadinessMission(), mock=True)):
            print("SDK ready: exact mission-only tools verified; no model turn or hardware opened.")
        return 0
    except Exception as error:
        detail = str(error) if isinstance(error, ControllerError) else type(error).__name__
        print(f"SDK readiness failed: {detail}")
        return 1


def main() -> int:
    if sys.argv[1:] == ["--check-sdk"]:
        import asyncio

        return asyncio.run(check_sdk())
    from ground_station.voice_mission import main as mission_main

    args = sys.argv[1:]
    if "--controller" in args or any(arg.startswith("--controller=") for arg in args):
        raise SystemExit("The SDK entrypoint always uses --controller droid")
    if "--allow-real-flight" not in args:
        args = ["--mock", *args]
    sys.argv = [sys.argv[0], "--controller", "droid", *args]
    return mission_main()


if __name__ == "__main__":
    raise SystemExit(main())
