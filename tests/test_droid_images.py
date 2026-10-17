"""Native image turns with fake SDK transport, never a real provider or device."""

import json
import base64
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from test_droid_pipeline import DecisionSession, capture_phase, mission
from ground_station.agent.analysis import AnalysisError, DroidImageAnalyzer, MAX_IMAGE_BYTES
from ground_station.agent.controller import ControllerError, DroidController
from ground_station.agent.driver import MissionDriver
from ground_station.agent.pipeline import SurveyPipeline

ASSESSMENT = '{"health_score":0.2,"analysis_summary":"Visible discoloration; uncertain estimate."}'


class ImageStream:
    def __init__(self, session):
        self.session = session
        self.result = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    def __aiter__(self):
        return self

    async def __anext__(self):
        s = self.session
        tools = {t.name: t.handler for t in s.config.mcp_servers[0].tools}
        await tools["get_mission_status"]({})
        if s.early_decision:
            await tools["issue_drone_command"]({"drone_id": "drone-1", "action": "continue_survey"})
        context = json.loads(await tools["get_survey_context"]({"assessment": s.assessment}))
        s.contexts.append(context)
        if s.alert:
            await tools["send_photon_alert"]({})
        if s.before_decision:
            s.before_decision()
        if s.decision:
            result = json.loads(await tools["issue_drone_command"](
                {"drone_id": "drone-1", "action": s.decision}))
            if s.decision in ("land", "abort"):
                assert result["stopped"] is True
            else:
                assert result["executed"] is False
        if s.before_final:
            s.before_final()
        self.result = SimpleNamespace(success=s.success, text=s.final, session_id="persistent")
        raise StopAsyncIteration


class ImageSession(DecisionSession):
    assessment = ASSESSMENT
    final = ASSESSMENT
    decision = "continue_survey"
    early_decision = False
    success = True
    before_decision = None
    before_final = None
    alert = True

    def stream(self, prompt, *, timeout, images=None):
        if images is None:
            return super().stream(prompt, timeout=timeout)
        self.prompts.append(prompt)
        self.images = images
        return ImageStream(self)


class ImageTurnTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "capture.png"
        self.path.write_bytes(b"\x89PNG\r\n\x1a\nfixture")
        self.mission = mission()
        self.driver = MissionDriver(self.mission, mock=True)
        self.alerts = []
        self.pipeline = SurveyPipeline(
            self.driver, DroidImageAnalyzer(),
            photon=SimpleNamespace(notify_analysis=lambda a: self.alerts.append(a) or True),
            history_path=Path(self.temp.name) / "history.db",
        )

    def factory(self, **overrides):
        def create(**kwargs):
            session = ImageSession(**kwargs)
            session.__dict__.update(overrides)
            return session
        return create

    async def run_photo(self, controller):
        capture_phase(self.mission)
        return await self.pipeline.process_photo(controller, self.path, self.driver.begin_analysis())

    async def test_same_persistent_turn_has_image_validated_context_and_decision(self):
        def staged():
            self.assertEqual(self.mission.phase, "await_analysis")
            self.assertFalse(self.alerts)

        async with DroidController(self.driver, pipeline=self.pipeline,
                                   session_factory=self.factory(before_final=staged)) as controller:
            first = await controller.handle("Inspect and wait")
            result = await self.run_photo(controller)
            s = ImageSession.instances[-1]
            self.assertEqual(first["session_id"], result["session_id"])
            self.assertEqual(len(s.prompts), 2)
            self.assertEqual(s.images[0].source.media_type, "image/png")
            self.assertNotIn(str(self.path), s.prompts[-1])
            self.assertEqual(s.contexts[0]["latest"]["features"]["health_flag"], "critical")
            self.assertEqual(self.mission.phase, "turn_home")
            self.assertEqual(len(self.alerts), 1)
            self.assertEqual(self.alerts[0]["vision_source"], "droid")

    async def test_bad_final_output_never_executes_staged_decision_or_notification(self):
        async with DroidController(self.driver, pipeline=self.pipeline,
                                   session_factory=self.factory(final="not JSON")) as controller:
            with self.assertRaises(AnalysisError):
                await self.run_photo(controller)
            self.assertEqual(self.mission.phase, "abort")
            self.assertFalse(self.alerts)
            self.assertTrue(self.driver.snapshot()["stopped"])

    async def test_invalid_assessment_fails_before_action(self):
        async with DroidController(self.driver, pipeline=self.pipeline,
                                   session_factory=self.factory(assessment="{}")) as controller:
            with self.assertRaises(AnalysisError):
                await self.run_photo(controller)
            self.assertIsNone(self.pipeline.latest)
            self.assertFalse(self.alerts)
            self.assertEqual(self.mission.phase, "abort")

    async def test_different_final_assessment_fails_closed(self):
        async with DroidController(self.driver, pipeline=self.pipeline,
                                   session_factory=self.factory(final=ASSESSMENT.replace("0.2", "0.8"))) as controller:
            with self.assertRaises(ControllerError):
                await self.run_photo(controller)
            self.assertFalse(self.alerts)

    async def test_json_without_decision_tool_is_not_enough(self):
        async with DroidController(self.driver, pipeline=self.pipeline,
                                   session_factory=self.factory(decision=None)) as controller:
            with self.assertRaisesRegex(ControllerError, "actionable"):
                await self.run_photo(controller)
            self.assertFalse(self.alerts)

    async def test_decision_before_validated_context_is_rejected(self):
        async with DroidController(self.driver, pipeline=self.pipeline,
                                   session_factory=self.factory(early_decision=True)) as controller:
            with self.assertRaisesRegex(ControllerError, "validated"):
                await self.run_photo(controller)
            self.assertIsNone(self.pipeline.latest)
            self.assertFalse(self.alerts)

    async def test_operator_stop_during_turn_cancels_staged_effects(self):
        async with DroidController(self.driver, pipeline=self.pipeline,
                                   session_factory=self.factory(before_final=self.driver.stop)) as controller:
            await self.run_photo(controller)
            self.assertEqual(self.mission.phase, "abort")
            self.assertFalse(self.alerts)

    async def check_emergency_alert(self, action):
        def stopped_before_final():
            self.assertTrue(self.driver.snapshot()["stopped"])
            self.assertEqual(self.mission.phase, "abort")
            self.assertFalse(self.alerts)

        async with DroidController(
            self.driver, pipeline=self.pipeline,
            session_factory=self.factory(decision=action, before_final=stopped_before_final),
        ) as controller:
            await self.run_photo(controller)
            self.assertEqual(len(self.alerts), 1)
            self.assertEqual(self.alerts[0]["health_score"], 0.2)
            self.assertTrue(self.driver.snapshot()["stopped"])

    async def test_land_keeps_validated_critical_alert_and_stops_immediately(self):
        await self.check_emergency_alert("land")

    async def test_abort_keeps_validated_critical_alert_and_stops_immediately(self):
        await self.check_emergency_alert("abort")

    async def test_operator_stop_before_model_land_cancels_alert(self):
        async with DroidController(
            self.driver, pipeline=self.pipeline,
            session_factory=self.factory(decision="land", before_decision=self.driver.stop),
        ) as controller:
            await self.run_photo(controller)
            self.assertFalse(self.alerts)

    async def test_operator_stop_after_model_land_cancels_alert(self):
        async with DroidController(
            self.driver, pipeline=self.pipeline,
            session_factory=self.factory(decision="land", before_final=self.driver.stop),
        ) as controller:
            await self.run_photo(controller)
            self.assertFalse(self.alerts)

    async def test_expired_hold_before_model_land_cancels_alert(self):
        def expire():
            self.mission._analysis_deadline = 0

        async with DroidController(
            self.driver, pipeline=self.pipeline,
            session_factory=self.factory(decision="land", before_decision=expire),
        ) as controller:
            await self.run_photo(controller)
            self.assertFalse(self.alerts)

    async def test_invalid_final_after_model_land_cancels_alert(self):
        async with DroidController(
            self.driver, pipeline=self.pipeline,
            session_factory=self.factory(decision="land", final="not JSON"),
        ) as controller:
            with self.assertRaises(AnalysisError):
                await self.run_photo(controller)
            self.assertFalse(self.alerts)
            self.assertTrue(self.driver.snapshot()["stopped"])

    async def test_failed_turn_after_model_land_cancels_alert(self):
        async with DroidController(
            self.driver, pipeline=self.pipeline,
            session_factory=self.factory(decision="land", success=False),
        ) as controller:
            with self.assertRaises(ControllerError):
                await self.run_photo(controller)
            self.assertFalse(self.alerts)

    async def test_chalk_is_evaluated_in_same_turn_before_decision(self):
        self.pipeline.chalk = True
        evaluate = lambda **kw: {"field_survey.health_flag": "critical", "field_survey.severity": 0.8,
                                "field_survey.recommended_action": "alert-operator"}
        with patch.dict("sys.modules", {"ground_station.chalk_quality":
                                       SimpleNamespace(evaluate_survey_named=evaluate)}):
            async with DroidController(self.driver, pipeline=self.pipeline,
                                       session_factory=self.factory()) as controller:
                await self.run_photo(controller)
                self.assertEqual(ImageSession.instances[-1].contexts[0]["latest"]["features"]["source"], "chalk")

    async def test_image_failure_before_sdk_turn_still_stops_host(self):
        self.path.write_bytes(b"not an image")
        capture_phase(self.mission)
        with self.assertRaises(AnalysisError):
            await self.pipeline.process_photo(None, self.path, self.driver.begin_analysis())
        self.assertTrue(self.driver.snapshot()["stopped"])
        self.assertFalse(self.alerts)


class SmokeHarnessTests(unittest.IsolatedAsyncioTestCase):
    async def test_opt_in_smoke_wiring_with_fake_sdk_and_real_decodable_png(self):
        import cv2
        import numpy as np
        import live_droid_pipeline

        def controller(*args, **kwargs):
            return DroidController(*args, **kwargs, session_factory=ImageSession)

        with patch.object(live_droid_pipeline, "DroidController", side_effect=controller):
            await live_droid_pipeline.check(None)
        image = ImageSession.instances[-1].images[0]
        data = base64.b64decode(image.source.data)
        self.assertEqual(cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR).shape,
                         (128, 128, 3))


class AttachmentTests(unittest.TestCase):
    def test_supported_magic_types_and_five_mebibyte_boundary(self):
        analyzer = DroidImageAnalyzer()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "host-capture"
            for data, mime in [(b"\xff\xd8\xffx", "image/jpeg"), (b"\x89PNG\r\n\x1a\nx", "image/png"),
                               (b"GIF89ax", "image/gif"), (b"GIF87ax", "image/gif"),
                               (b"RIFF1234WEBPx", "image/webp")]:
                path.write_bytes(data)
                self.assertEqual(analyzer.image(path).source.media_type, mime)
            path.write_bytes(b"\xff\xd8\xff" + b"x" * (MAX_IMAGE_BYTES - 3))
            analyzer.image(path)
            for data in (b"", b"text", b"RIFF1234NOPE", b"\xff\xd8\xff" + b"x" * MAX_IMAGE_BYTES):
                path.write_bytes(data)
                with self.assertRaises(AnalysisError):
                    analyzer.image(path)
            with self.assertRaises(AnalysisError):
                analyzer.image(Path(directory) / "missing.png")
