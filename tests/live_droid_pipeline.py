"""Opt-in real Factory image turn. Photon/ElevenLabs and all hardware stay fake."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import tempfile
from pathlib import Path

import cv2
import httpx
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from test_droid_pipeline import capture_phase, mission
from test_photon import PhotonTests, rid
from ground_station.agent.analysis import DroidImageAnalyzer
from ground_station.agent.controller import DroidController
from ground_station.agent.driver import MissionDriver
from ground_station.agent.pipeline import SurveyPipeline
from ground_station.photon import PhotonClient, PhotonNotifier
from ground_station.voice_note import VoiceNoteSynthesizer


async def check(model):
    value = mission()
    driver = MissionDriver(value, mock=True)
    wire = PhotonTests()
    wire.setUp()
    original_wire = wire.transport
    def photon_wire(request):
        if request.url.path.endswith("/attachments"):
            wire.requests.append(request)
            return httpx.Response(200, json={"id": rid("att")})
        return original_wire(request)
    client = PhotonClient(wire.config, transport=httpx.MockTransport(photon_wire))
    tts_calls = []
    def tts_wire(request):
        tts_calls.append(request)
        return httpx.Response(200, content=b"ID3fake-mp3", headers={"content-type": "audio/mpeg"})
    voice = VoiceNoteSynthesizer("test-only", "fake-voice", transport=httpx.MockTransport(tts_wire))
    notifier = PhotonNotifier(client, mock=True, voice_notes=voice, voice_requested=True)
    notifier.start()
    try:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "capture.png"
            # Actual decodable PNG, generated locally, no user image or device.
            image = np.full((128, 128, 3), (30, 150, 40), dtype=np.uint8)
            cv2.putText(image, "TEST", (10, 70), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 0), 2)
            ok, encoded = cv2.imencode(".png", image)
            assert ok
            path.write_bytes(encoded.tobytes())
            pipeline = SurveyPipeline(driver, DroidImageAnalyzer(), photon=notifier,
                                      history_path=Path(directory) / "history.db")
            async with DroidController(driver, pipeline=pipeline, model=model, timeout=90) as controller:
                first = await controller.handle("Inspect the status and request one survey.")
                assert value.snapshot()["phase"] == "takeoff", "SDK did not start simulated mission"
                capture_phase(value)
                second = await pipeline.process_photo(controller, path, driver.begin_analysis())
                assert first["session_id"] == second["session_id"], "Session was not reused"
                state = driver.snapshot()
                assert (state["stopped"] or state["photo_queued"]
                        or value.snapshot()["phase"] == "turn_home"), "No bounded image decision"
                assert pipeline.latest["vision_source"] == "droid"
                # Exercise fake delivery even if the model chose a safe reimage/land.
                # A model-requested alert has the same receipt and deduplicates.
                notifier.notify_analysis(pipeline.latest)
                assert len(pipeline.context()["history"]) == 1
                # Mark simulated return complete before SDK shutdown.
                value.phase = "done"
            await pipeline.close()
        notifier.close()
        posts = [request for request in wire.requests if request.method == "POST"]
        text_posts = [r for r in posts if r.url.path.endswith("/messages")
                      and json.loads(r.content)["content"]["type"] == "text"]
        audio_posts = [r for r in posts if r.url.path.endswith("/messages")
                       and json.loads(r.content)["content"]["type"] == "attachment"]
        assert len(text_posts) == len(audio_posts) == len(tts_calls) == 1
        assert "MOCK" in json.loads(text_posts[0].content)["content"]["text"]
        print("PASS: real authenticated SDK, persistent session, start -> capture -> analysis -> "
              "same-turn MCP decision; Photon text/audio and ElevenLabs HTTP simulated.")
    finally:
        notifier.close()
        wire.doCleanups()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default=None, help="image-capable Factory model (default: CLI setting)")
    args = parser.parse_args()
    asyncio.run(check(args.model))
