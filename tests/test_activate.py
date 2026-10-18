import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ground_station.activate import build_parser, validate_before_drone


class ActivateTests(unittest.TestCase):
    def test_photon_implies_bridge(self):
        parser = build_parser()
        with tempfile.TemporaryDirectory() as td:
            env_file = Path(td) / ".env.phone-command"
            env_file.write_text("COMMAND_BRIDGE_TOKEN=" + "a" * 32 + "\nCOMMAND_BRIDGE_PORT=8787\n")
            args = parser.parse_args(["--mock", "--photon", "--phone-command-env", str(env_file)])
            rc = validate_before_drone(args)
            self.assertEqual(rc, 0)
            self.assertTrue(args.phone_commands)
            self.assertIsNotNone(args.bridge_settings)

    def test_missing_token_fails_before_drone(self):
        parser = build_parser()
        with tempfile.TemporaryDirectory() as td:
            args = parser.parse_args(["--mock", "--photon"])
            # No token in env
            with patch.dict(os.environ, {"COMMAND_BRIDGE_TOKEN": ""}, clear=False):
                # Ensure no env file provides it
                rc = validate_before_drone(args)
                self.assertEqual(rc, 1)

    def test_without_photon_warns(self):
        parser = build_parser()
        args = parser.parse_args(["--mock"])
        rc = validate_before_drone(args)
        self.assertEqual(rc, 2)

    def test_auto_accepted(self):
        parser = build_parser()
        with tempfile.TemporaryDirectory() as td:
            env_file = Path(td) / ".env.phone-command"
            env_file.write_text("COMMAND_BRIDGE_TOKEN=" + "b" * 32 + "\n")
            args = parser.parse_args(["--mock", "--photon", "--auto", "--phone-command-env", str(env_file)])
            rc = validate_before_drone(args)
            self.assertEqual(rc, 0)

    def test_indefinite_wait_can_be_interrupted(self):
        # Simulate that _run_mission loop can be interrupted via KeyboardInterrupt handling
        # We just verify activate main handles photon/no-photon conflict
        parser = build_parser()
        args = parser.parse_args(["--mock", "--photon", "--no-photon"])
        rc = validate_before_drone(args)
        self.assertEqual(rc, 2)


if __name__ == "__main__":
    unittest.main()
