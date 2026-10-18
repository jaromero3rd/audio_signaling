"""ElevenLabs transcript routing. No microphone and no network.

Run with: PYTHONPATH=. .venv/bin/python test/test_voice_eleven.py
"""
import os
import unittest
from unittest.mock import patch

from pathlib import Path

from ground_station.voice_eleven import (
    ElevenVoiceBridge, _run_voice, pcm_to_wav, route_transcript, transcribe,
)


class Gateway:
    def __init__(self):
        self.started = 0
        self.aborted = 0
        self.submitted = []
        self.worker = self

    def request_start(self):
        self.started += 1
        return {"ok": True}

    def request_abort(self):
        self.aborted += 1
        return {"ok": True}

    def submit(self, text, event):
        self.submitted.append((text, event))
        return True


class VoiceElevenTests(unittest.TestCase):
    def test_transcript_routes_to_droid_text_without_a_socket(self):
        seen = {}

        def poster(wav_bytes, api_key):
            seen["wav"] = wav_bytes
            seen["called"] = True
            self.assertTrue(api_key)
            self.assertNotIn(b"api.x.ai", wav_bytes)
            return {"text": "survey the wheat"}

        gateway = Gateway()
        with patch.dict(os.environ, {"ELEVENLABS_API_KEY": "test-key"}):
            text = transcribe(pcm_to_wav(b"\x00\x01" * 160), poster=poster)
        self.assertEqual(text, "survey the wheat")
        self.assertTrue(seen["wav"].startswith(b"RIFF"))
        self.assertEqual(route_transcript(text, gateway), "start")
        self.assertEqual(gateway.started, 1)
        self.assertEqual(gateway.aborted, 0)

        self.assertEqual(route_transcript("emergency stop the survey", gateway), "abort")
        self.assertEqual(gateway.aborted, 1)
        self.assertEqual(gateway.started, 1)

        self.assertEqual(route_transcript("where is the drone", gateway), "status")
        self.assertEqual(gateway.submitted[0][1]["kind"], "voice_status")
        self.assertIn("where is the drone", gateway.submitted[0][1]["transcript"])

    def test_missing_key_does_not_call_the_poster(self):
        def poster(wav_bytes, api_key):
            raise AssertionError("poster was called")

        with patch.dict(os.environ, {"ELEVENLABS_API_KEY": ""}):
            with self.assertRaisesRegex(RuntimeError, "ELEVENLABS_API_KEY"):
                transcribe(b"RIFF", poster=poster)

    def test_only_affirmative_commands_can_start_a_survey(self):
        for text in ("what is the survey status?", "don't start the survey",
                     "do not take off", "survey the wheat later", "when does the survey start?"):
            with self.subTest(text=text):
                gateway = Gateway()
                self.assertEqual(route_transcript(text, gateway), "status")
                self.assertEqual(gateway.started, 0)
        for text in ("survey the wheat", "Please start a survey.", "take off", "start survey"):
            with self.subTest(text=text):
                gateway = Gateway()
                self.assertEqual(route_transcript(text, gateway), "start")
                self.assertEqual(gateway.started, 1)

    def test_microphone_failure_is_not_reported_as_readiness(self):
        bridge = ElevenVoiceBridge(Gateway())
        with patch.object(bridge, "run_forever", side_effect=RuntimeError("microphone unavailable")):
            _run_voice(bridge)
        self.assertTrue(bridge.ready.is_set())
        self.assertTrue(bridge.failed.is_set())

    def test_mission_modules_do_not_call_xai(self):
        root = Path(__file__).resolve().parents[1]
        for rel in (
            "ground_station/voice_mission.py",
            "ground_station/agent/pipeline.py",
            "ground_station/agent/controller.py",
        ):
            text = (root / rel).read_text()
            self.assertNotIn("api.x.ai", text)
            self.assertNotIn("XAI_API_KEY", text)
            self.assertNotIn("voice_grok", text)


if __name__ == "__main__":
    unittest.main()
