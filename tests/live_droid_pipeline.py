"""Opt-in authenticated SDK smoke test. No real camera, drone, xAI or Photon I/O."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from test_droid_pipeline import capture_phase, mission
from test_photon import PhotonTests
from ground_station.agent.analysis import FakeImageAnalyzer
from ground_station.agent.controller import DroidController
from ground_station.agent.driver import MissionDriver
from ground_station.agent.pipeline import SurveyPipeline
from ground_station.photon import PhotonNotifier


async def check(model):
    value = mission()
    driver = MissionDriver(value, mock=True)
    wire = PhotonTests()
    wire.setUp()
    client = wire.client()
    notifier = PhotonNotifier(client, mock=True)
    notifier.start()
    try:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "capture.jpg"
            path.write_bytes(b"\xff\xd8\xffsynthetic-fixture")
            pipeline = SurveyPipeline(driver, FakeImageAnalyzer(), photon=notifier,
                                      history_path=Path(directory) / "history.db")
            async with DroidController(driver, pipeline=pipeline, model=model, timeout=90) as controller:
                first = await controller.handle("Read status and request start_survey for drone-1 using its tool.")
                assert value.snapshot()["phase"] == "takeoff", "SDK did not start simulated mission"
                capture_phase(value)
                second = await pipeline.process_photo(controller, path, driver.begin_analysis())
                assert first["session_id"] == second["session_id"], "Session was not reused"
                assert value.snapshot()["phase"] == "turn_home", "SDK did not continue simulated mission"
                third = await controller.handle(
                    "Call send_photon_alert for the current validated analysis now. "
                    "This smoke test uses a fake Photon HTTP transport, no real recipient. Do not change flight state."
                )
                assert third["session_id"] == first["session_id"]
                assert len(pipeline.context()["history"]) == 1
                # Mark simulated return complete before SDK shutdown.
                value.phase = "done"
            await pipeline.close()
        notifier.close()
        posts = [request for request in wire.requests if request.method == "POST"]
        assert len(posts) == 1, "SDK did not queue exactly one simulated analysis alert"
        assert "SIMULATED" in json.loads(posts[0].content)["content"]["text"]
        print("PASS: real authenticated SDK, persistent session, start -> capture -> analysis -> "
              "MCP return-home decision -> Photon HTTP alert; external providers simulated.")
    finally:
        notifier.close()
        wire.doCleanups()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="claude-haiku-4-5-20251001")
    args = parser.parse_args()
    asyncio.run(check(args.model))
