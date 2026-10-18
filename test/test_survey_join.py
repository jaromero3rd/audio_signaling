"""Chalk joins a Droid score with live weather. No drone and no network.

Run with: PYTHONPATH=. .venv/bin/python test/test_survey_join.py
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.append(str(Path(__file__).resolve().parents[1] / "chalk"))

from ground_station.agent.analysis import FakeImageAnalyzer, SurveyAnalysis
from ground_station.agent.pipeline import SurveyPipeline


class Driver:
    drone_id = "drone-1"


class JoinTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "capture.jpg"
        self.path.write_bytes(b"\xff\xd8\xffsimulated")
        self.pipeline = SurveyPipeline(
            Driver(), FakeImageAnalyzer(),
            history_path=Path(self.directory.name) / "surveys.sqlite3",
            weather=True, deliver=True,
        )
        self.analysis = SurveyAnalysis(
            field_id="field-1", health_score=0.8,
            analysis_summary="SIMULATED result for pipeline testing.",
        )

    def features(self):
        return {
            "source": "local_thresholds",
            "health_flag": "healthy",
            "recommended_action": "continue",
            "severity": 0.2,
        }

    def test_chalk_delivery_is_not_repeated_and_the_record_is_stored(self):
        stored = []

        def evaluate_survey(**kwargs):
            self.assertEqual(kwargs["health_score"], 0.8)
            self.assertTrue(kwargs["include_weather"])
            return {
                "field_survey.health_flag": "healthy",
                "field_survey.severity": 0.2,
                "field_survey.recommended_action": "continue",
                "field_survey.insight": "NOAA does not contradict a healthy canopy.",
                "field_survey.justified_action": "continue",
                "field_survey.delivery_error": "",
            }

        def remember(field_id, result):
            stored.append((field_id, result))

        env = {"CHALK_CLIENT_ID": "present", "ELEVENLABS_API_KEY": "present", "PHOTON_API_KEY": "present"}
        features = self.features()
        with patch.dict(os.environ, env):
            with patch("ground_station.chalk_client.evaluate_survey", evaluate_survey):
                with patch("ground_station.agent.store.set_last_survey", remember):
                    with patch("ground_station.agent.store.append_decision", lambda entry: stored.append(entry)):
                        with patch("deliver_summary.deliver", side_effect=AssertionError("phone sent twice")):
                            self.pipeline.record_observation(self.analysis, features, self.path)
        self.assertEqual(features["weather_source"], "chalk")
        self.assertEqual(features["insight"], "NOAA does not contradict a healthy canopy.")
        self.assertEqual(features["delivery_error"], "")
        self.assertEqual(stored[0][0], "field-1")
        self.assertEqual(stored[1]["kind"], "survey")

    def test_chalk_failure_uses_local_weather_and_a_phone_failure_still_decides(self):
        def justify_score(score, summary=""):
            return {
                "insight": "Dry sun supports the stress.",
                "justified_action": "continue",
                "weather_source": "local live weather",
            }

        def deliver(insight, photo_path=None, poster=None):
            self.assertEqual(insight, "Dry sun supports the stress.")
            self.assertTrue(self.path.samefile(photo_path))
            return "photon down"

        features = self.features()
        env = {"CHALK_CLIENT_ID": "", "ELEVENLABS_API_KEY": "present", "PHOTON_API_KEY": "present"}
        with patch.dict(os.environ, env):
            with patch("ground_station.chalk_client.evaluate_survey", side_effect=RuntimeError("undeployed")):
                with patch("ground_station.chalk_eval_mission.justify_score", justify_score):
                    with patch("deliver_summary.deliver", deliver):
                        self.pipeline.record_observation(self.analysis, features, self.path)
        self.assertEqual(features["weather_source"], "local live weather")
        self.assertEqual(features["justified_action"], "continue")
        self.assertEqual(features["delivery_error"], "photon down")
        self.assertIn("volume write skipped", features["volume_error"])

        failed = self.features()
        with patch.dict(os.environ, env):
            with patch("ground_station.chalk_client.evaluate_survey", side_effect=RuntimeError("undeployed")):
                with patch("ground_station.chalk_eval_mission.justify_score", side_effect=RuntimeError("offline")):
                    self.pipeline.record_observation(self.analysis, failed, self.path)
        self.assertEqual(failed["weather_source"], "unavailable")
        self.assertEqual(failed["justified_action"], "continue")


if __name__ == "__main__":
    unittest.main()
