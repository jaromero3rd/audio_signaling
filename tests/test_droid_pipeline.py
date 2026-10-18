"""Exercise real mission, driver, worker, MCP handlers and HTTP boundaries."""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import httpx
import numpy as np
from pydantic import ValidationError

from ground_station.agent.analysis import AnalysisError, FakeImageAnalyzer, DroidImageAnalyzer, SurveyAnalysis
from ground_station.agent.controller import DroidController, TOOL_IDS
from ground_station.agent.driver import MissionDriver
from ground_station.agent.pipeline import SurveyPipeline
from ground_station.agent.worker import DroidVoiceGateway, DroidWorker
from ground_station.mission import ANALYSIS_HOLD_S, Gains, SurveyMission


def mission():
    with patch("ground_station.mission.AprilTagDetector") as detector:
        detector.return_value.detect.return_value = None
        return SurveyMission(target_kind="tag", family="36h11", target_tag=12,
                             home_tag=12, gains=Gains(), analysis_required=True)


def capture_phase(value):
    with value._lock:
        value.flying = True
        value._set_phase_locked("survey")
    tick = value.tick(np.zeros((100, 100, 3), dtype=np.uint8), time.time())
    assert tick.action == "photo"
    assert value.snapshot()["phase"] == "await_analysis"


class DecisionStream:
    def __init__(self, session, prompt):
        self.session = session
        self.prompt = prompt
        self.result = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    def __aiter__(self):
        return self

    async def __anext__(self):
        tools = {tool.name: tool for tool in self.session.config.mcp_servers[0].tools}
        await tools["get_mission_status"].handler({})
        if '"kind": "survey_analysis"' in self.prompt:
            context = json.loads(await tools["get_survey_context"].handler({}))
            self.session.contexts.append(context)
            if self.session.alert:
                await tools["send_photon_alert"].handler({})
            action = self.session.decisions.pop(0)
            if action:
                result = json.loads(await tools["issue_drone_command"].handler(
                    {"drone_id": "drone-1", "action": action}))
                self.session.actions.append(result)
        elif ('"kind": "voice_start"' in self.prompt
              or "Inspect the status and request one survey." in self.prompt):
            await tools["issue_drone_command"].handler({"drone_id": "drone-1", "action": "start_survey"})
        self.result = SimpleNamespace(success=True, text="Decision confirmed by tools", session_id="persistent")
        raise StopAsyncIteration


class DecisionSession:
    instances = []
    decisions = ["continue_survey"]
    alert = False

    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)
        self.decisions = list(type(self).decisions)
        self.contexts = []
        self.actions = []
        self.prompts = []
        self.closed = False
        type(self).instances.append(self)

    async def open(self):
        pass

    async def close(self):
        self.closed = True

    async def list_tools(self):
        return [SimpleNamespace(id=name, allowed=True) for name in TOOL_IDS]

    async def list_mcp_servers(self):
        return SimpleNamespace(servers=[])

    def stream(self, prompt, *, timeout):
        self.prompts.append(prompt)
        return DecisionStream(self, prompt)


class VisionTests(unittest.IsolatedAsyncioTestCase):
    async def test_native_image_and_strict_schema_not_a_path(self):
        analyzer = DroidImageAnalyzer()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "capture.jpg"
            path.write_bytes(b"\xff\xd8\xfftest-image")
            image = analyzer.image(path)
            self.assertEqual(image.source.media_type, "image/jpeg")
            self.assertNotIn(directory, repr(image))
            result = analyzer.parse('{"health_score":0.8,"analysis_summary":"Limited evidence."}', "field-1")
            self.assertEqual(result.field_id, "field-1")
            self.assertEqual(result.health_score, 0.8)

    async def test_invalid_or_incomplete_analysis_never_becomes_an_observation(self):
        analyzer = DroidImageAnalyzer()
        for text in ('not JSON', '{}', '```json\n{}\n```', '[]',
                     '{"health_score":NaN,"analysis_summary":"x"}',
                     '{"health_score":true,"analysis_summary":"x"}',
                     '{"health_score":2.0,"analysis_summary":"x"}',
                     '{"health_score":0.8,"analysis_summary":" "}',
                     '{"health_score":0.1,"health_score":0.8,"analysis_summary":"x"}',
                     '{"health_score":0.8,"analysis_summary":"x","extra":"no"}'):
            with self.subTest(text=text), self.assertRaises(AnalysisError):
                analyzer.parse(text, "field-1")


class PipelineTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "capture.jpg"
        self.path.write_bytes(b"\xff\xd8\xffsimulated")
        self.mission = mission()
        self.driver = MissionDriver(self.mission, mock=True)
        self.pipeline = SurveyPipeline(self.driver, FakeImageAnalyzer(),
                                       history_path=Path(self.directory.name) / "surveys.sqlite3")
        DecisionSession.decisions = ["continue_survey"]
        DecisionSession.alert = False

    async def test_capture_analysis_mcp_decision_changes_actual_mission_and_persists(self):
        capture_phase(self.mission)
        capture_id = self.driver.begin_analysis()
        async with DroidController(self.driver, pipeline=self.pipeline, session_factory=DecisionSession) as controller:
            await controller.handle("Inspect status and wait")
            result = await self.pipeline.process_photo(controller, self.path, capture_id)
            self.assertTrue(result["ok"])
            self.assertEqual(self.mission.snapshot()["phase"], "turn_home")
            session = DecisionSession.instances[-1]
            self.assertEqual(len(session.prompts), 2)
            self.assertEqual(session.contexts[0]["latest"]["vision_source"], "fake")
            self.assertEqual(len(self.pipeline.context()["history"]), 1)
            self.assertNotEqual(self.pipeline.context()["history"][0]["decision"], "pending")
        reopened = SurveyPipeline(self.driver, FakeImageAnalyzer(), history_path=self.pipeline.history_path)
        self.assertEqual(len(reopened.context()["history"]), 1)

    async def test_reimage_is_executed_by_camera_mailbox_and_reuses_session(self):
        DecisionSession.decisions = ["capture_photo", "continue_survey"]
        capture_phase(self.mission)
        deadline = self.mission._analysis_deadline
        async with DroidController(self.driver, pipeline=self.pipeline, session_factory=DecisionSession) as controller:
            await self.pipeline.process_photo(controller, self.path, self.driver.begin_analysis())
            self.assertEqual(self.mission.snapshot()["phase"], "await_analysis")
            self.assertTrue(self.driver.take_photo_request())
            self.assertFalse(self.driver.issue("drone-1", "continue_survey")["ok"])
            await self.pipeline.process_photo(controller, self.path, self.driver.begin_analysis())
            self.assertEqual(self.mission.snapshot()["phase"], "turn_home")
            self.assertEqual(self.mission._analysis_deadline, deadline)
            self.assertEqual(len(DecisionSession.instances[-1].prompts), 2)

    async def test_late_result_after_stop_cannot_decide_or_alert(self):
        capture_phase(self.mission)
        capture_id = self.driver.begin_analysis()
        self.driver.stop()
        result = await self.pipeline.process_photo(None, self.path, capture_id)
        self.assertEqual(result["reason"], "stale_or_stopped_analysis")
        self.assertIsNone(self.pipeline.latest)
        self.assertFalse(self.pipeline.send_alert()["ok"])

    async def test_old_capture_cannot_release_new_capture(self):
        capture_phase(self.mission)
        old = self.driver.begin_analysis()
        self.driver.begin_analysis()
        result = await self.pipeline.process_photo(None, self.path, old)
        self.assertEqual(result["reason"], "stale_or_stopped_analysis")
        self.assertFalse(self.driver.snapshot()["analysis_ready"])

    async def test_narration_without_tool_decision_fails_closed(self):
        DecisionSession.decisions = [None]
        capture_phase(self.mission)
        async with DroidController(self.driver, pipeline=self.pipeline, session_factory=DecisionSession) as controller:
            with self.assertRaisesRegex(RuntimeError, "actionable"):
                await self.pipeline.process_photo(controller, self.path, self.driver.begin_analysis())
            self.assertTrue(self.driver.snapshot()["stopped"])

    async def test_alert_tool_uses_only_host_registered_observation(self):
        class Notifier:
            def notify_analysis(inner, analysis):
                inner.analysis = analysis
                return True
        notifier = Notifier()
        self.pipeline.photon = notifier
        DecisionSession.alert = True
        capture_phase(self.mission)
        async with DroidController(self.driver, pipeline=self.pipeline, session_factory=DecisionSession) as controller:
            await self.pipeline.process_photo(controller, self.path, self.driver.begin_analysis())
            self.assertEqual(notifier.analysis["health_score"], 0.8)
            controller._active = True
            with self.assertRaises(ValidationError):
                await controller.tools[3].handler({"text": "arbitrary", "recipient": "other"})

    async def test_real_reimage_waits_for_approval_and_timeout_revokes_it(self):
        self.driver.mock = False
        self.driver.auto_approve = False
        DecisionSession.decisions = ["capture_photo"]
        capture_phase(self.mission)
        async with DroidController(self.driver, pipeline=self.pipeline, session_factory=DecisionSession) as controller:
            await self.pipeline.process_photo(controller, self.path, self.driver.begin_analysis())
            self.assertFalse(self.driver.take_photo_request())
            self.assertEqual(self.driver.snapshot()["pending_approval"], "capture_photo")
            self.mission.tick(np.zeros((100, 100, 3), dtype=np.uint8), self.mission._analysis_deadline + 1)
            self.assertFalse(self.driver.approve_pending()["ok"])

    async def test_feature_history_is_field_scoped(self):
        capture_phase(self.mission)
        async with DroidController(self.driver, pipeline=self.pipeline, session_factory=DecisionSession) as controller:
            await self.pipeline.process_photo(controller, self.path, self.driver.begin_analysis())
        other = SurveyPipeline(self.driver, FakeImageAnalyzer(), field_id="other",
                               history_path=self.pipeline.history_path)
        self.assertEqual(other.context()["history"], [])

    async def test_enabled_chalk_features_reach_droid_context(self):
        calls = []

        def evaluate(**kwargs):
            calls.append(kwargs)
            return {"field_survey.health_flag": "healthy",
                    "field_survey.recommended_action": "continue",
                    "field_survey.severity": 0.2}

        self.pipeline.chalk = True
        self.pipeline.chalk_branch = "test-deployment"
        capture_phase(self.mission)
        with patch.dict(sys.modules, {
            "ground_station.chalk_quality": SimpleNamespace(evaluate_survey_named=evaluate),
        }):
            async with DroidController(self.driver, pipeline=self.pipeline, session_factory=DecisionSession) as controller:
                await self.pipeline.process_photo(controller, self.path, self.driver.begin_analysis())
        self.assertEqual(calls[0]["branch"], "test-deployment")
        self.assertEqual(calls[0]["health_score"], 0.8)
        context = DecisionSession.instances[-1].contexts[0]
        self.assertEqual(context["latest"]["features"]["source"], "chalk")

    async def test_configured_chalk_failure_does_not_silently_use_local_features(self):
        def evaluate(**kwargs):
            raise RuntimeError("Simulated feature outage")

        self.pipeline.chalk = True
        capture_phase(self.mission)
        with patch.dict(sys.modules, {
            "ground_station.chalk_quality": SimpleNamespace(evaluate_survey_named=evaluate),
        }):
            with self.assertRaises(RuntimeError):
                await self.pipeline.process_photo(None, self.path, self.driver.begin_analysis())
        self.assertIsNone(self.pipeline.latest)
        self.assertFalse(self.driver.snapshot()["analysis_ready"])


class WorkerTests(unittest.TestCase):
    def test_demo_camera_loop_runs_actual_worker_through_capture_and_return(self):
        from contextlib import ExitStack
        from ground_station import voice_mission
        from ground_station.vision import Box

        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            value = mission()
            value.target_det.detect.return_value = Box(10, 10, 80, 80, 1.0, "tag")
            value.home_det.detect.return_value = Box(10, 10, 80, 80, 1.0, "tag")
            stack.enter_context(patch.object(value, "_reached", return_value=True))
            stack.enter_context(patch.object(voice_mission, "SurveyMission", return_value=value))
            stack.enter_context(patch.object(voice_mission, "ROOT", Path(directory)))
            stack.enter_context(patch.object(voice_mission, "load_dotenv"))
            stack.enter_context(patch.object(sys, "argv", [
                "demo", "--fake-analysis", "--no-photon", "--hz", "200",
            ]))
            drone = stack.enter_context(patch.object(voice_mission, "MockDrone")).return_value
            tello = stack.enter_context(patch.object(voice_mission, "TelloDrone"))
            drone.get_frame.return_value = np.zeros((100, 100, 3), dtype=np.uint8)
            drone.battery.return_value = 90
            path = Path(directory) / "capture.jpg"
            path.write_bytes(b"\xff\xd8\xfffixture")
            drone.capture_photo.return_value = path
            DecisionSession.decisions = ["continue_survey"]
            DecisionSession.alert = False
            workers = []

            def factory(*args, **kwargs):
                worker = DroidWorker(*args, **kwargs, controller_factory=lambda *a, **kw:
                                     DroidController(*a, **kw, session_factory=DecisionSession))
                workers.append(worker)
                return worker

            stack.enter_context(patch("ground_station.agent.worker.DroidWorker", side_effect=factory))
            # Offline: skip live NOAA/NASA weather (network) so the camera loop can finish.
            stack.enter_context(patch(
                "ground_station.agent.pipeline.SurveyPipeline._join_weather",
                side_effect=RuntimeError("offline test"),
            ))

            def _offline_justify(self, analysis, features):
                features["insight"] = analysis.analysis_summary
                features["justified_action"] = "continue"
                features["weather_source"] = "offline"

            stack.enter_context(patch(
                "ground_station.agent.pipeline.SurveyPipeline._justify_locally",
                _offline_justify,
            ))
            stack.enter_context(patch.object(voice_mission.cv2, "imshow"))
            stack.enter_context(patch.object(voice_mission.cv2, "destroyAllWindows"))
            stack.enter_context(patch.object(voice_mission, "draw_hud", return_value=drone.get_frame.return_value))
            iterations = 0

            def key(_):
                nonlocal iterations
                iterations += 1
                if iterations > 300:
                    raise AssertionError("Mission did not complete")
                if value.phase == "done":
                    return ord("q")
                # Fast-forward only flight dwell timers, never the analysis deadline.
                value._phase_t = time.time() - 8
                return -1

            stack.enter_context(patch.object(voice_mission.cv2, "waitKey", side_effect=key))
            self.assertEqual(voice_mission.main(), 0)
            self.assertEqual(value.phase, "done")
            drone.takeoff.assert_called_once()
            drone.capture_photo.assert_called_once()
            drone.land.assert_called_once()
            drone.close.assert_called_once()
            tello.assert_not_called()
            self.assertFalse(workers[0].failed.is_set())
            self.assertEqual(len(DecisionSession.instances[-1].prompts), 2)
            self.assertEqual(len(workers[0].pipeline.context()["history"]), 1)

    def test_vision_failure_in_actual_worker_latches_stop(self):
        class BrokenAnalyzer(FakeImageAnalyzer):
            async def analyze(self, path, field_id):
                raise AnalysisError("Simulated provider outage")

        with tempfile.TemporaryDirectory() as directory:
            value = mission()
            capture_phase(value)
            driver = MissionDriver(value, mock=True)
            pipeline = SurveyPipeline(driver, BrokenAnalyzer(), history_path=Path(directory) / "history.db")
            worker = DroidWorker(driver, pipeline=pipeline,
                                 controller_factory=lambda *args, **kwargs: DroidController(
                                     *args, **kwargs, session_factory=DecisionSession))
            path = Path(directory) / "capture.jpg"
            path.write_bytes(b"\xff\xd8\xfffixture")
            try:
                worker.start("Inspect and wait")
                self.assertTrue(worker.ready.wait(3))
                self.assertTrue(worker.submit_photo(path))
                self.assertTrue(worker.failed.wait(3))
                self.assertTrue(driver.snapshot()["stopped"])
                self.assertEqual(value.phase, "abort")
            finally:
                worker.close()

    def test_real_worker_routes_voice_and_photo_through_one_sdk_session(self):
        with tempfile.TemporaryDirectory() as directory:
            value = mission()
            driver = MissionDriver(value, mock=True)
            pipeline = SurveyPipeline(driver, FakeImageAnalyzer(), history_path=Path(directory) / "history.db")
            DecisionSession.decisions = ["continue_survey"]
            DecisionSession.alert = False
            completed = threading.Event()
            worker = DroidWorker(driver, pipeline=pipeline, on_result=lambda result: completed.set(),
                                 controller_factory=lambda *args, **kwargs: DroidController(
                                     *args, **kwargs, session_factory=DecisionSession))
            try:
                worker.start("Inspect and wait")
                self.assertTrue(worker.ready.wait(3))
                self.assertTrue(completed.wait(3))
                completed.clear()
                gateway = DroidVoiceGateway(worker, driver)
                self.assertFalse(gateway.request_start()["executed"])
                self.assertTrue(completed.wait(3))
                self.assertEqual(value.snapshot()["phase"], "takeoff")
                completed.clear()
                capture_phase(value)
                path = Path(directory) / "capture.jpg"
                path.write_bytes(b"\xff\xd8\xfffixture")
                self.assertTrue(worker.submit_photo(path))
                self.assertTrue(completed.wait(3))
                self.assertEqual(value.snapshot()["phase"], "turn_home")
                self.assertEqual(len(DecisionSession.instances[-1].prompts), 3)
                self.assertEqual(len(worker.results), 3)
                self.assertFalse(worker.failed.is_set())
                gateway.request_abort()
                self.assertTrue(driver.snapshot()["stopped"])
            finally:
                worker.close()
            self.assertTrue(DecisionSession.instances[-1].closed)

    def test_missing_capture_stops_instead_of_feeding_a_path(self):
        value = mission()
        driver = MissionDriver(value, mock=True)
        worker = DroidWorker(driver)
        self.assertFalse(worker.submit_photo(None))
        self.assertTrue(worker.failed.is_set())
        self.assertTrue(driver.snapshot()["stopped"])

    def test_hold_stays_stationary_and_times_out_without_cloud(self):
        value = mission()
        capture_phase(value)
        frame = np.zeros((100, 100, 3), dtype=np.uint8)
        tick = value.tick(frame, value._analysis_deadline - 1)
        self.assertEqual((tick.lr, tick.fwd, tick.up, tick.yaw), (0, 0, 0, 0))
        tick = value.tick(frame, value._analysis_deadline + 1)
        self.assertEqual(tick.action, "land")
        self.assertEqual(value.snapshot()["phase"], "abort")

    def test_analysis_timeout_latches_stop_and_is_not_a_completed_survey(self):
        from ground_station.voice_mission import latch_mission_abort, survey_finished

        value = mission()
        driver = MissionDriver(value, mock=True)
        capture_phase(value)
        frame = np.zeros((100, 100, 3), dtype=np.uint8)
        tick = value.tick(frame, value._analysis_deadline + 1)
        self.assertEqual(tick.action, "land")
        self.assertEqual(value.snapshot()["phase"], "abort")
        self.assertFalse(driver.snapshot()["stopped"])
        self.assertTrue(latch_mission_abort(driver, value.snapshot()["phase"]))
        self.assertTrue(driver.snapshot()["stopped"])
        self.assertFalse(driver.approve_pending()["ok"])
        generation = driver.snapshot()["stop_generation"]
        self.assertFalse(latch_mission_abort(driver, "abort"))
        self.assertEqual(driver.snapshot()["stop_generation"], generation)
        value.tick(frame, value._analysis_deadline + 2)
        self.assertEqual(value.snapshot()["phase"], "done")
        self.assertFalse(survey_finished(
            value.snapshot()["phase"], flying=False, stopped=driver.snapshot()["stopped"],
        ))
        self.assertTrue(survey_finished("done", flying=False, stopped=False))


if __name__ == "__main__":
    unittest.main()
