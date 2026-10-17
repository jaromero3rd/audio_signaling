"""Exercise entrypoint defaults and cleanup without opening any device/window."""

from __future__ import annotations

import sys
import unittest
from contextlib import ExitStack
from unittest.mock import MagicMock, patch

from ground_station.agent.session import main as session_main
from ground_station import voice_mission


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
        for flags in ([], ["--mock", "--auto"]):
            with patch.object(sys, "argv", ["demo", "--controller", "droid", "--no-photon", *flags]), \
                 patch.object(voice_mission, "load_dotenv"), \
                 patch.object(voice_mission, "MockDrone") as mock, \
                 patch.object(voice_mission, "TelloDrone") as tello:
                self.assertEqual(voice_mission.main(), 1)
                mock.assert_not_called()
                tello.assert_not_called()

    def environment(self, stack):
        stack.enter_context(patch.object(sys, "argv", [
            "demo", "--controller", "droid", "--mock", "--target", "tag", "--no-photon",
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


if __name__ == "__main__":
    unittest.main()
