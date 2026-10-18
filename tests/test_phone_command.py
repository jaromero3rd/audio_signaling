"""Authenticated phone ingress tests. Only optional ephemeral loopback sockets."""

import http.client
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from ground_station.agent.driver import MissionDriver
from ground_station.phone_command import BridgeSettings, MAX_BODY, PhoneCommandBridge, PhoneCommandGateway

TOKEN = "synthetic-test-token-not-a-secret-0000"
NOW = 1_800_000_000


class Mission:
    def __init__(self):
        self.phase = "idle"
        self.starts = 0

    def snapshot(self):
        return {"phase": self.phase}

    def request_start(self):
        self.phase = "takeoff"
        self.starts += 1
        return {"ok": True}

    def request_abort(self):
        self.phase = "abort"
        return {"ok": True}


class PhoneBridgeTests(unittest.TestCase):
    def setUp(self):
        self.gateway = MagicMock()
        self.gateway.request_start.return_value = {"ok": True, "status": "queued_for_droid"}
        self.gateway.request_abort.return_value = {"ok": True, "status": "landing_requested"}
        self.bridge = PhoneCommandBridge(self.gateway, token=TOKEN, port=0)
        self.addCleanup(self.bridge.close)
        clock = patch("ground_station.phone_command.time.time", return_value=NOW)
        clock.start()
        self.addCleanup(clock.stop)

    def dispatch(self, command="start", receipt="message_1", issued=NOW, **extra):
        body = json.dumps({"command": command, "id": receipt, "issued_at": issued, **extra}).encode()
        return self.bridge.dispatch(f"Bearer {TOKEN}", body)

    def test_requires_auth_and_exact_schema(self):
        for auth in ("", "Bearer wrong", f"bearer {TOKEN}"):
            self.assertEqual(self.bridge.dispatch(auth, b"{}")[0], 401)
        self.assertEqual(self.bridge.dispatch(f"Bearer {TOKEN}", b"x" * (MAX_BODY + 1))[0], 413)
        for body in (b"[]", b"{", b"null", b'{"text":"start survey"}', b"\xff"):
            self.assertEqual(self.bridge.dispatch(f"Bearer {TOKEN}", body)[0], 400)
        for command in ("capture_photo", "START", "start; land", [], None):
            self.assertEqual(self.dispatch(command)[0], 400)
        self.assertEqual(self.dispatch(text="start")[0], 400)
        self.gateway.request_start.assert_not_called()

    def test_requires_recent_finite_timestamp_and_safe_id(self):
        for issued in (True, None, "now", float("nan"), float("inf"), 10**400):
            self.assertEqual(self.dispatch(issued=issued)[0], 400)
        for issued in (NOW - 31, NOW + 6):
            self.assertEqual(self.dispatch(issued=issued)[0], 409)
        for receipt in ("", "a" * 129, "../secret", None):
            self.assertEqual(self.dispatch(receipt=receipt)[0], 400)
        self.gateway.request_start.assert_not_called()

    def test_duplicate_preserves_success_and_rejection_without_requeue(self):
        first = self.dispatch()
        again = self.dispatch()
        self.assertEqual(first[0], 200)
        self.assertFalse(first[1]["executed"])
        self.assertTrue(again[1]["duplicate"])
        self.gateway.request_start.assert_called_once()
        self.assertEqual(self.dispatch("land")[0], 409)
        self.gateway.request_start.return_value = {"ok": False, "status": "rejected"}
        self.assertFalse(self.dispatch(receipt="rejected")[1]["ok"])
        self.assertFalse(self.dispatch(receipt="rejected")[1]["ok"])
        self.assertEqual(self.gateway.request_start.call_count, 2)

    def test_exceptions_are_sanitized_and_not_retried(self):
        self.gateway.request_start.side_effect = RuntimeError(TOKEN)
        self.assertEqual(self.dispatch()[0], 503)
        self.assertEqual(self.dispatch()[0], 503)
        self.gateway.request_start.assert_called_once()
        self.assertNotIn(TOKEN, repr(self.dispatch()))

    def test_bounded_cache_never_blocks_stop(self):
        for index in range(256):
            self.dispatch(receipt=f"message_{index}")
        self.assertEqual(self.dispatch(receipt="full")[0], 429)
        self.assertTrue(self.dispatch("abort", "stop")[1]["ok"])
        self.assertTrue(self.dispatch("land", "land")[1]["ok"])
        self.assertEqual(len(self.bridge._receipts), 256)
        self.assertEqual(self.gateway.request_abort.call_count, 2)
        with patch("ground_station.phone_command.time.time", return_value=NOW + 36):
            self.assertEqual(self.dispatch(receipt="fresh", issued=NOW + 36)[0], 200)
        self.assertEqual(len(self.bridge._receipts), 1)

    def test_loopback_http_readiness_auth_path_and_shutdown(self):
        self.bridge.start()
        self.assertTrue(self.bridge.ready.wait(2))
        self.assertFalse(self.bridge.failed.is_set())
        self.assertEqual(self.bridge._httpd.server_address[0], "127.0.0.1")
        for path, auth, status in (("/command", "", 401), ("/other", TOKEN, 404),
                                   ("/command", TOKEN, 200)):
            connection = http.client.HTTPConnection("127.0.0.1", self.bridge.port, timeout=2)
            try:
                connection.request("POST", path, json.dumps({
                    "command": "abort", "id": "http_1", "issued_at": NOW,
                }), {"Authorization": f"Bearer {auth}", "Content-Type": "application/json"})
                response = connection.getresponse()
                self.assertEqual(response.status, status)
                response.read()
            finally:
                connection.close()
        self.gateway.request_abort.assert_called_once()
        self.bridge.close()
        self.assertFalse(self.bridge._thread.is_alive())

    def test_bind_failure_is_not_readiness(self):
        with patch("ground_station.phone_command.ThreadingHTTPServer", side_effect=OSError):
            with self.assertRaises(OSError):
                self.bridge.start()
        self.assertTrue(self.bridge.failed.is_set())


class PhoneApprovalTests(unittest.TestCase):
    def setUp(self):
        self.mission = Mission()
        self.driver = MissionDriver(self.mission, mock=False, phone_commands=True)
        self.voice = MagicMock()
        self.voice.request_start.return_value = {"ok": True, "status": "queued_for_droid"}
        self.phone = PhoneCommandGateway(self.voice, self.driver)

    def test_only_explicit_phone_request_can_replace_y_after_preflight(self):
        self.driver.issue("drone-1", "start_survey")
        self.assertIsNone(self.driver.approve_phone_start(flight_ready=True))
        self.assertTrue(self.phone.request_start()["ok"])
        self.assertIsNone(self.driver.approve_phone_start(flight_ready=False))
        self.assertEqual(self.mission.starts, 0)
        self.assertTrue(self.driver.approve_phone_start(flight_ready=True)["ok"])
        self.assertEqual(self.mission.starts, 1)
        self.assertFalse(self.driver.auto_approve)
        self.assertFalse(self.phone.request_start()["ok"])
        self.driver.issue("drone-1", "capture_photo")
        self.assertIsNone(self.driver.approve_phone_start(flight_ready=True))
        self.assertFalse(self.driver.take_photo_request())

    def test_disabled_busy_repeated_and_expired_authorization(self):
        disabled = MissionDriver(Mission(), mock=False)
        self.assertFalse(disabled.authorize_phone_start()["ok"])
        with patch("ground_station.agent.driver.time.monotonic", return_value=1):
            self.assertTrue(self.phone.request_start()["ok"])
            self.assertFalse(self.phone.request_start()["ok"])
            self.driver.issue("drone-1", "start_survey")
        with patch("ground_station.agent.driver.time.monotonic", return_value=62):
            self.assertIsNone(self.driver.approve_phone_start(flight_ready=True))
        self.assertEqual(self.mission.starts, 0)
        self.mission.phase = "takeoff"
        self.assertFalse(self.phone.request_start()["ok"])

    def test_failed_queue_cancels_authorization(self):
        self.voice.request_start.return_value = {"ok": False}
        self.assertFalse(self.phone.request_start()["ok"])
        self.driver.issue("drone-1", "start_survey")
        self.assertIsNone(self.driver.approve_phone_start(flight_ready=True))

    def test_stop_revokes_phone_start_without_queue_or_model(self):
        self.phone.request_start()
        self.driver.issue("drone-1", "start_survey")
        # Actual abort sink, not a fake model turn.
        self.voice.request_abort.side_effect = self.driver.stop
        self.assertTrue(self.phone.request_abort()["ok"])
        self.assertIsNone(self.driver.approve_phone_start(flight_ready=True))
        self.assertFalse(self.phone.request_start()["ok"])
        self.assertEqual(self.mission.starts, 0)


class SettingsTests(unittest.TestCase):
    def test_private_file_is_not_exported(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict("os.environ", {}, clear=True):
            path = Path(directory) / "phone.env"
            path.write_text(f"COMMAND_BRIDGE_TOKEN={TOKEN}\nCOMMAND_BRIDGE_PORT=8788\n")
            self.assertEqual(BridgeSettings.load(path), BridgeSettings(TOKEN, 8788))
            import os
            self.assertNotIn("COMMAND_BRIDGE_TOKEN", os.environ)

    def test_settings_fail_closed_without_printing_values(self):
        for value in ("", "short", "x" * 31, "x" * 31 + "\n", "é" * 32, "\x01" * 32):
            with patch.dict("os.environ", {"COMMAND_BRIDGE_TOKEN": value}, clear=True):
                with self.assertRaises(ValueError):
                    BridgeSettings.load()
        for value in ("0", "65536", "remote", "-1"):
            with patch.dict("os.environ", {"COMMAND_BRIDGE_TOKEN": TOKEN, "COMMAND_BRIDGE_PORT": value}):
                with self.assertRaises(ValueError):
                    BridgeSettings.load()
