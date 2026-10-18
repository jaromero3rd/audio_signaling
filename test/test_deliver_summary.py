"""Phone delivery stays off the network when credentials are missing.

Run with: PYTHONPATH=. .venv/bin/python test/test_deliver_summary.py
"""
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.append(str(Path(__file__).resolve().parents[1] / "chalk"))
import deliver_summary


class DeliverTests(unittest.TestCase):
    def test_missing_key_skips_the_network(self):
        def poster(text, voice_id, api_key):
            raise AssertionError("tts was called")

        env = {"ELEVENLABS_API_KEY": "", "ELEVENLABS_VOICE_ID": "", "PHOTON_API_KEY": ""}
        with patch.dict(os.environ, env):
            error = deliver_summary.deliver("Canopy looks stressed.", poster=poster)
        self.assertIn("ELEVENLABS_API_KEY", error)

    def test_poster_audio_is_handed_to_photon_once(self):
        calls = []

        def poster(text, voice_id, api_key):
            self.assertEqual(text, "Dry sun supports the stress.")
            self.assertEqual(voice_id, "voice-test")
            self.assertTrue(api_key)
            return b"ID3fake-mp3"

        def send(message, photo_path=None, audio_path=None):
            calls.append((message, photo_path, audio_path))
            self.assertTrue(Path(audio_path).is_file())
            return {"stdout": "ok"}

        env = {
            "ELEVENLABS_API_KEY": "test-key",
            "ELEVENLABS_VOICE_ID": "voice-test",
            "PHOTON_API_KEY": "test-photon",
        }
        with patch.dict(os.environ, env):
            with patch("ground_station.photon_alert.send_photon_alert", send):
                error = deliver_summary.deliver("Dry sun supports the stress.", poster=poster)
        self.assertEqual(error, "")
        self.assertEqual(len(calls), 1)
        self.assertIsNone(calls[0][1])


if __name__ == "__main__":
    unittest.main()
