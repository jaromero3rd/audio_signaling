"""Offline regressions: no network, CLI process, camera or drone required."""

from __future__ import annotations

import asyncio
import json
import sys
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from droid_sdk import (
    ExecuteAction,
    McpToolAction,
    PermissionOption,
    PermissionRequest,
    ToolConfirmationOutcome as Outcome,
    ToolUseBlock,
)
from pydantic import ValidationError

from ground_station.agent.controller import (
    ControllerError,
    DroidController,
    SurveyAnalysis,
    TOOL_BUDGET,
    TOOL_IDS,
    TURN_BUDGET,
)
from ground_station.agent.driver import MissionDriver


class FakeMission:
    def __init__(self):
        self.phase = "idle"
        self.starts = 0
        self.aborts = 0

    def snapshot(self):
        return {"phase": self.phase, "flying": False}

    def request_start(self):
        if self.phase not in ("idle", "done"):
            return {"ok": False, "reason": "busy"}
        self.starts += 1
        self.phase = "takeoff"
        return {"ok": True, "phase": self.phase}

    def request_abort(self):
        self.aborts += 1
        self.phase = "abort"
        return {"ok": True}


class FakeStream:
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
        if self.session.error:
            raise self.session.error
        for _ in range(self.session.calls):
            await self.session.config.mcp_servers[0].tools[0].handler({})
        self.result = SimpleNamespace(success=self.session.success, text="checked", session_id="same-session")
        raise StopAsyncIteration


class FakeSession:
    instances = []

    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)
        self.prompts = []
        self.closed = False
        self.success = True
        self.calls = 0
        self.error = None
        self.extra_tool = False
        self.extra_server = False
        self.__class__.instances.append(self)

    async def open(self):
        pass

    async def close(self):
        self.closed = True

    async def interrupt(self):
        pass

    async def list_tools(self):
        tools = [SimpleNamespace(id=tool_id, allowed=True) for tool_id in TOOL_IDS]
        if self.extra_tool:
            tools.append(SimpleNamespace(id="Execute", allowed=True))
        return tools

    async def list_mcp_servers(self):
        names = ["agridrone", "other"] if self.extra_server else ["agridrone"]
        return SimpleNamespace(servers=[SimpleNamespace(name=name) for name in names])

    def stream(self, prompt, *, timeout):
        self.prompts.append(prompt)
        return FakeStream(self)


class DriverTests(unittest.TestCase):
    def setUp(self):
        self.mission = FakeMission()
        self.driver = MissionDriver(self.mission, mock=False)

    def test_real_start_requires_local_approval(self):
        result = self.driver.issue("drone-1", "start_survey")
        self.assertEqual(result["reason"], "local_approval_required")
        self.assertEqual(self.mission.starts, 0)
        self.assertTrue(self.driver.approve_pending(flight_ready=True)["ok"])
        self.assertEqual(self.mission.starts, 1)

    def test_stop_revokes_pending_and_is_latched(self):
        self.driver.issue("drone-1", "start_survey")
        self.driver.stop()
        self.assertFalse(self.driver.approve_pending()["ok"])
        self.assertEqual(self.driver.issue("drone-1", "start_survey")["reason"], "operator_stop_latched")
        self.assertEqual(self.mission.starts, 0)

    def test_local_approval_alone_does_not_bypass_preflight(self):
        self.driver.issue("drone-1", "start_survey")
        self.assertEqual(
            self.driver.approve_pending()["reason"], "camera_and_battery_preflight_required"
        )
        self.assertEqual(self.mission.starts, 0)

    def test_expired_approval_does_not_fly(self):
        with patch("ground_station.agent.driver.time.monotonic", return_value=1):
            self.driver.issue("drone-1", "start_survey")
        with patch("ground_station.agent.driver.time.monotonic", return_value=62):
            self.assertFalse(self.driver.approve_pending()["ok"])
        self.assertEqual(self.mission.starts, 0)

    def test_pending_request_cannot_be_replaced(self):
        self.driver.issue("drone-1", "start_survey")
        self.assertEqual(self.driver.issue("drone-1", "capture_photo")["reason"], "another_approval_pending")
        self.driver.approve_pending(flight_ready=True)
        self.assertFalse(self.driver.take_photo_request())

    def test_mock_start_is_bounded_to_one_survey(self):
        driver = MissionDriver(self.mission, mock=True)
        self.assertTrue(driver.issue("drone-1", "start_survey")["ok"])
        self.mission.phase = "done"
        self.assertEqual(driver.issue("drone-1", "start_survey")["reason"], "one_survey_per_process")
        self.assertEqual(self.mission.starts, 1)

    def test_photo_is_queued_once_not_captured_in_tool(self):
        self.driver.issue("drone-1", "capture_photo")
        self.assertFalse(self.driver.take_photo_request())
        self.driver.approve_pending()
        self.assertTrue(self.driver.take_photo_request())
        self.assertFalse(self.driver.take_photo_request())

    def test_unknown_command_or_drone_cannot_mutate(self):
        for drone_id, action in [("other", "start_survey"), ("drone-1", "execute")]:
            with self.assertRaises(ValueError):
                self.driver.issue(drone_id, action)
        self.assertEqual(self.mission.starts, 0)

    def test_extra_photos_cannot_create_an_endless_event_loop(self):
        driver = MissionDriver(self.mission, mock=True)
        for _ in range(2):
            self.assertTrue(driver.issue("drone-1", "capture_photo")["ok"])
            self.assertTrue(driver.take_photo_request())
        self.assertEqual(
            driver.issue("drone-1", "capture_photo")["reason"], "extra_photo_limit_reached"
        )

    def test_land_and_abort_do_not_wait_for_approval(self):
        for action in ("land", "abort"):
            self.assertEqual(self.driver.issue("drone-1", action)["status"], "landing_requested")

    def test_cleanup_does_not_turn_a_completed_mission_into_abort(self):
        self.mission.phase = "done"
        self.driver.stop()
        self.assertEqual(self.mission.phase, "done")
        self.assertTrue(self.driver.snapshot()["stopped"])

    def test_package_does_not_eagerly_load_chalk(self):
        self.assertNotIn("ground_station.agent.store", sys.modules)


class ControllerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.mission = FakeMission()
        self.driver = MissionDriver(self.mission, mock=True)
        self.controller = DroidController(self.driver, session_factory=FakeSession)

    async def test_persistent_session_across_turns_and_cleanup(self):
        async with self.controller as controller:
            first = await controller.handle("Read status")
            second = await controller.handle("Read status again")
            session = FakeSession.instances[-1]
            directory = session.cwd
            self.assertEqual(first["session_id"], second["session_id"])
            self.assertEqual(len(session.prompts), 2)
            self.assertTrue(directory.exists())
        self.assertTrue(session.closed)
        self.assertFalse(directory.exists())

    async def test_actual_mcp_schema_rejects_code_and_unknown_fields(self):
        async with self.controller as controller:
            controller._active = True
            handler = controller.tools[1].handler
            for arguments in [
                {"drone_id": "drone-1", "action": "execute"},
                {"drone_id": "drone-1", "action": "land", "code": "anything"},
            ]:
                with self.assertRaises(ValidationError):
                    await handler(arguments)
            self.assertEqual(self.mission.aborts, 0)

    async def test_mcp_command_reaches_only_host_driver(self):
        async with self.controller as controller:
            controller._active = True
            result = json.loads(await controller.tools[1].handler(
                {"drone_id": "drone-1", "action": "start_survey"}
            ))
            self.assertTrue(result["ok"])
            self.assertEqual(self.mission.starts, 1)

    async def test_tools_are_inert_outside_an_active_turn(self):
        with self.assertRaises(ControllerError):
            await self.controller.tools[0].handler({})

    async def test_failed_turn_aborts_and_revokes_commands(self):
        async with self.controller as controller:
            FakeSession.instances[-1].success = False
            with self.assertRaises(ControllerError):
                await controller.handle("Inspect")
            self.assertTrue(self.driver.snapshot()["stopped"])

    async def test_timeout_or_cancellation_aborts(self):
        for error in (TimeoutError(), asyncio.CancelledError()):
            async with self.controller as controller:
                FakeSession.instances[-1].error = error
                with self.assertRaises(type(error)):
                    await controller.handle("Inspect")
                self.assertTrue(self.driver.snapshot()["stopped"])

    async def test_budget_aborts_and_latches_stop(self):
        async with self.controller as controller:
            FakeSession.instances[-1].calls = TOOL_BUDGET + 1
            with self.assertRaises(ControllerError):
                await controller.handle("Inspect")
            self.assertTrue(self.driver.snapshot()["stopped"])

    async def test_turn_budget_blocks_unbounded_event_feedback(self):
        async with self.controller as controller:
            for _ in range(TURN_BUDGET):
                await controller.handle("Read status")
            with self.assertRaises(ControllerError):
                await controller.handle("One more")
            self.assertEqual(len(FakeSession.instances[-1].prompts), TURN_BUDGET)
            self.assertTrue(self.driver.snapshot()["stopped"])

    async def test_unrestricted_native_tool_blocks_startup(self):
        def factory(**kwargs):
            session = FakeSession(**kwargs)
            session.extra_tool = True
            return session
        controller = DroidController(self.driver, session_factory=factory)
        with self.assertRaises(ControllerError):
            async with controller:
                self.fail("must fail closed")
        self.assertTrue(FakeSession.instances[-1].closed)

    async def test_foreign_mcp_server_blocks_startup(self):
        def factory(**kwargs):
            session = FakeSession(**kwargs)
            session.extra_server = True
            return session
        controller = DroidController(self.driver, session_factory=factory)
        with self.assertRaises(ControllerError):
            async with controller:
                self.fail("must fail closed")

    async def test_cli_discovery_is_allowed_but_shell_stays_denied(self):
        async with self.controller as controller:
            session = FakeSession.instances[-1]
            original = session.list_tools
            async def inventory():
                return [*(await original()), SimpleNamespace(id="ToolSearch", allowed=True)]
            session.list_tools = inventory
            await controller._verify_tools()
            session.extra_tool = True
            with self.assertRaises(ControllerError):
                await controller._verify_tools()

    async def test_unrelated_service_keys_are_not_forwarded_to_cli(self):
        with patch.dict("os.environ", {"PHOTON_API_KEY": "test-private-placeholder",
                                      "XAI_API_KEY": "test-private-placeholder"}):
            async with self.controller:
                runtime = FakeSession.instances[-1].runtime
                self.assertEqual(runtime.env["PHOTON_API_KEY"], "")
                self.assertEqual(runtime.env["XAI_API_KEY"], "")
                self.assertNotIn("FACTORY_API_KEY", runtime.env)

    async def test_factory_failure_stops_driver_and_removes_workspace(self):
        def broken(**kwargs):
            raise RuntimeError("setup failure")
        controller = DroidController(self.driver, session_factory=broken)
        with self.assertRaises(RuntimeError):
            async with controller:
                self.fail("must fail closed")
        self.assertTrue(self.driver.snapshot()["stopped"])
        self.assertIsNone(controller._directory)

    async def test_analysis_is_validated_before_any_turn(self):
        async with self.controller as controller:
            for score in (-1, 2, float("nan"), float("inf"), True):
                with self.assertRaises(ValidationError):
                    await controller.submit_analysis(
                        {"field_id": "field-1", "health_score": score, "analysis_summary": "observation"}
                    )
            self.assertFalse(FakeSession.instances[-1].prompts)
            result = await controller.submit_analysis(
                SurveyAnalysis(field_id="field-1", health_score=0.7, analysis_summary="observation")
            )
            self.assertTrue(result["ok"])

    def test_timeout_must_be_finite_and_bounded(self):
        for timeout in (0, -1, float("nan"), float("inf"), 301):
            with self.assertRaises(ValueError):
                DroidController(self.driver, timeout=timeout)


class PermissionTests(unittest.TestCase):
    def action(self, server="agridrone", name="get_mission_status"):
        return McpToolAction(
            tool_use=ToolUseBlock(id="tool-1", name="MCPTool", input={}),
            tool_name=name, actual_tool_name=name, server_name=server, impact_level="low",
        )

    def request(self, actions):
        return PermissionRequest(
            actions=actions,
            options=[PermissionOption("Once", Outcome.PROCEED_ONCE),
                     PermissionOption("Cancel", Outcome.CANCEL)],
        )

    def test_only_own_tools_get_once_permission(self):
        self.assertEqual(
            DroidController.permission(self.request([self.action()])).selected_option, Outcome.PROCEED_ONCE
        )
        for actions in ([], [self.action(server="other")], [self.action(name="approve_pending")]):
            self.assertEqual(
                DroidController.permission(self.request(actions)).selected_option, Outcome.CANCEL
            )

    def test_mixed_or_shell_request_is_denied(self):
        shell = ExecuteAction(
            tool_use=ToolUseBlock(id="tool-2", name="Execute", input={}),
            full_command="echo nope", command="echo",
        )
        self.assertEqual(
            DroidController.permission(self.request([self.action(), shell])).selected_option, Outcome.CANCEL
        )


class ExistingMissionRegressionTests(unittest.TestCase):
    def test_repeated_start_does_not_deadlock(self):
        from ground_station.mission import Gains, SurveyMission

        mission = SurveyMission(
            target_kind="tag", family="36h11", target_tag=12, home_tag=12, gains=Gains()
        )
        self.assertTrue(mission.request_start()["ok"])
        responses = []
        thread = threading.Thread(target=lambda: responses.append(mission.request_start()), daemon=True)
        thread.start()
        thread.join(timeout=1)
        self.assertFalse(thread.is_alive(), "repeated start deadlocked in snapshot()")
        self.assertFalse(responses[0]["ok"])


if __name__ == "__main__":
    unittest.main()
