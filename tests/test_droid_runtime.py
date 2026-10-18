"""Exercise entrypoint defaults and cleanup without opening any device/window."""

from __future__ import annotations

import sys
import unittest
from contextlib import ExitStack
from unittest.mock import MagicMock, patch

import numpy as np

from ground_station.agent.session import main as session_main
from ground_station import voice_mission
from ground_station.phone_command import BridgeSettings


class RuntimeTests(unittest.TestCase):
    def test_sdk_entrypoint_defaults_to_mock(self):
        with patch.object(sys, "argv", ["session"]), patch.object(voice_mission, "main", return_value=0):
            self.assertEqual(session_main(), 0)
            self.assertIn("--mock", sys.argv)
            self.assertEqual(sys.argv[1:3], ["--controller", "droid"])

    def test_sdk_entrypoint_real_mode_requires_explicit_flag(self):
        with patch.object(sys, "argv", ["session", "--allow-real-flight"]), patch.object(
            voice_mission, "main", return_value=0
        ):
            session_main()
            self.assertNotIn("--mock", sys.argv)

    def test_unsafe_droid_flags_rejected_before_drone_construction(self):
        with patch.object(sys, "argv", ["demo", "--controller", "droid", "--no-photon", "--mock", "--auto"]), \
             patch.object(voice_mission, "load_dotenv"), \
             patch.object(voice_mission, "MockDrone") as mock, \
             patch.object(voice_mission, "TelloDrone") as tello:
            self.assertEqual(voice_mission.main(), 1)
            mock.assert_not_called()
            tello.assert_not_called()

    def environment(self, stack):
        stack.enter_context(patch.object(sys, "argv", [
            "demo", "--controller", "droid", "--mock", "--target", "tag", "--no-photon",
            "--fake-analysis",
        ]))
        stack.enter_context(patch.object(voice_mission, "load_dotenv"))
        drone = stack.enter_context(patch.object(voice_mission, "MockDrone")).return_value
        drone.get_frame.return_value = None
        tello = stack.enter_context(patch.object(voice_mission, "TelloDrone"))
        worker = MagicMock()
        worker.ready.wait.return_value = True
        worker.failed.is_set.return_value = False
        factory = stack.enter_context(patch("ground_station.agent.worker.DroidWorker", return_value=worker))
        for name in ("imshow", "destroyAllWindows"):
            stack.enter_context(patch.object(voice_mission.cv2, name))
        stack.enter_context(patch.object(voice_mission.cv2, "waitKey", return_value=ord("q")))
        return drone, tello, worker, factory

    def test_local_loop_and_cleanup_do_not_need_a_model_turn(self):
        with ExitStack() as stack:
            drone, tello, worker, factory = self.environment(stack)
            self.assertEqual(voice_mission.main(), 0)
            tello.assert_not_called()
            worker.start.assert_called_once()
            worker.close.assert_called_once()
            drone.close.assert_called_once()
            drone.takeoff.assert_not_called()

    def test_camera_startup_failure_closes_resources(self):
        with ExitStack() as stack:
            drone, tello, worker, factory = self.environment(stack)
            drone.stream_on.side_effect = RuntimeError("simulated camera failure")
            with self.assertRaises(RuntimeError):
                voice_mission.main()
            drone.close.assert_called_once()
            worker.close.assert_called_once()
            worker.start.assert_not_called()
            tello.assert_not_called()

    def test_unacknowledged_takeoff_still_attempts_landing(self):
        with ExitStack() as stack:
            drone, tello, worker, factory = self.environment(stack)
            worker.start.side_effect = lambda _: factory.call_args.args[0].issue("drone-1", "start_survey")
            drone.takeoff.side_effect = RuntimeError("simulated lost takeoff ACK")
            with self.assertRaises(RuntimeError):
                voice_mission.main()
            drone.land.assert_called_once()
            drone.close.assert_called_once()
            tello.assert_not_called()

    def test_photon_disabled_requests_one_survey(self):
        with ExitStack() as stack:
            drone, tello, worker, factory = self.environment(stack)
            self.assertEqual(voice_mission.main(), 0)
            start_prompt = worker.start.call_args.args[0]
            self.assertIn("request one survey", start_prompt)
            tello.assert_not_called()

    def test_real_adapter_crop_analysis_still_requires_local_approval(self):
        with ExitStack() as stack:
            drone, tello, worker, factory = self.environment(stack)
            sys.argv.remove("--mock")
            sys.argv.remove("--fake-analysis")
            sys.argv.extend(["--allow-real-flight", "--crop-analysis"])
            real_fake = tello.return_value
            real_fake.get_frame.return_value = None
            real_fake.battery.return_value = 90
            requests = []
            worker.start.side_effect = lambda _: requests.append(
                factory.call_args.args[0].issue("drone-1", "start_survey"))
            self.assertEqual(voice_mission.main(), 0)
            self.assertEqual(requests[0]["reason"], "local_approval_required")
            self.assertFalse(factory.call_args.args[0].auto_approve)
            real_fake.takeoff.assert_not_called()
            real_fake.close.assert_called_once()

    def test_default_does_not_start_an_http_command_listener(self):
        with ExitStack() as stack:
            self.environment(stack)
            bridge = stack.enter_context(patch("ground_station.phone_command.PhoneCommandBridge"))
            old = stack.enter_context(patch("ground_station.operator_http.OperatorCommandServer"))
            self.assertEqual(voice_mission.main(), 0)
            bridge.assert_not_called()
            old.assert_not_called()

    def test_outbound_photon_configuration_never_enables_another_ingress(self):
        with ExitStack() as stack:
            _, _, worker, _ = self.environment(stack)
            sys.argv.remove("--no-photon")
            stack.enter_context(patch("ground_station.photon.PhotonConfig.load", return_value=MagicMock()))
            stack.enter_context(patch("ground_station.photon.PhotonClient"))
            stack.enter_context(patch("ground_station.photon.PhotonNotifier"))
            stack.enter_context(patch("ground_station.voice_note.VoiceNoteSynthesizer.from_env", return_value=None))
            legacy = stack.enter_context(patch("ground_station.photon_inbound.PhotonInbound"))
            old = stack.enter_context(patch("ground_station.operator_http.OperatorCommandServer"))
            bridge = stack.enter_context(patch("ground_station.phone_command.PhoneCommandBridge"))
            self.assertEqual(voice_mission.main(), 0)
            legacy.assert_not_called()
            old.assert_not_called()
            bridge.assert_not_called()
            self.assertIn("Do not start yet", worker.start.call_args.args[0])

    def test_bad_phone_settings_fail_before_opening_devices(self):
        for flags in (["--phone-commands"], ["--phone-command-env", "/nonexistent/private.env"]):
            with patch.object(sys, "argv", ["demo", "--mock", "--no-voice", "--no-photon", *flags]), \
                 patch.object(voice_mission, "load_dotenv"), \
                 patch.dict("os.environ", {"COMMAND_BRIDGE_TOKEN": ""}), \
                 patch.object(voice_mission, "MockDrone") as drone:
                self.assertEqual(voice_mission.main(), 1)
                drone.assert_not_called()

    def test_phone_mode_waits_instead_of_requesting_an_automatic_survey(self):
        with ExitStack() as stack:
            drone, _, worker, _ = self.environment(stack)
            sys.argv.append("--phone-commands")
            stack.enter_context(patch("ground_station.phone_command.BridgeSettings.load",
                                      return_value=BridgeSettings("synthetic-test-token-for-runtime-0000", 0)))
            self.assertEqual(voice_mission.main(), 0)
            self.assertIn("Do not start yet", worker.start.call_args.args[0])
            drone.takeoff.assert_not_called()

    def test_real_phone_start_needs_preflight_but_not_local_y(self):
        for camera, battery, expected in ((True, 90, True), (False, 90, False),
                                           (True, 10, False), (True, None, False)):
            with self.subTest(camera=camera, battery=battery), ExitStack() as stack:
                _, tello, worker, factory = self.environment(stack)
                sys.argv.remove("--mock")
                sys.argv.remove("--fake-analysis")
                sys.argv.extend(["--allow-real-flight", "--phone-commands"])
                drone = tello.return_value
                drone.get_frame.return_value = np.zeros((64, 64, 3), dtype=np.uint8) if camera else None
                drone.battery.return_value = battery
                stack.enter_context(patch("ground_station.phone_command.BridgeSettings.load",
                                          return_value=BridgeSettings("synthetic-test-token-for-runtime-0000", 0)))
                stack.enter_context(patch.object(voice_mission.cv2, "waitKey", side_effect=[-1, ord("q")]))

                def start(bridge):
                    bridge.gateway.request_start()
                    bridge.ready.set()

                stack.enter_context(patch("ground_station.phone_command.PhoneCommandBridge.start",
                                          autospec=True, side_effect=start))
                worker.submit.side_effect = lambda *_: (
                    factory.call_args.args[0].issue("drone-1", "start_survey") is not None
                )
                self.assertEqual(voice_mission.main(), 0)
                self.assertEqual(drone.takeoff.call_count, int(expected))
                self.assertFalse(factory.call_args.args[0].auto_approve)


if __name__ == "__main__":
    unittest.main()
