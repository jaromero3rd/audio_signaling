"""Loopback Photon → Droid ingest. No network services."""

from __future__ import annotations

import json
import unittest
import urllib.error
import urllib.request

from ground_station.operator_http import OperatorCommandServer
from ground_station.voice_scribe import PrintDispatcher


class OperatorHttpTest(unittest.TestCase):
    def setUp(self):
        self.server = OperatorCommandServer(PrintDispatcher(), port=18766)
        self.server.start()
        self.url = "http://127.0.0.1:18766/operator-command"

    def tearDown(self):
        self.server.close()

    def post(self, payload):
        req = urllib.request.Request(
            self.url, data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"}, method="POST",
        )
        with urllib.request.urlopen(req, timeout=2) as resp:
            return json.loads(resp.read().decode())

    def test_survey_queues_start_without_executing(self):
        body = self.post({"text": "survey the wheat"})
        self.assertEqual(body["intent"], "start_survey")
        self.assertFalse(body["executed"])
        self.assertTrue(body["ok"])

    def test_abort_bypasses_droid_queue(self):
        body = self.post({"text": "abort now"})
        self.assertEqual(body["intent"], "abort")
        self.assertTrue(body["ok"])

    def test_empty_rejected(self):
        body = self.post({"text": "  "})
        self.assertFalse(body["ok"])
        self.assertEqual(body["reason"], "empty")

    def test_status_summarizes_snapshot(self):
        body = self.post({"text": "what's the status"})
        self.assertEqual(body["intent"], "status")
        self.assertTrue(body["ok"])
        self.assertIn("idle", (body.get("message") or "").lower())

    def test_text_user_without_sink_is_not_queued(self):
        body = self.post({"text": "text the user hello"})
        self.assertFalse(body["ok"])
        message = (body.get("message") or "").lower()
        self.assertTrue("no_text_user" in message or "rejected" in message)
        self.assertNotIn("queued", message)
        self.assertEqual(body.get("reason"), "no_text_user")


class RejectedStartHttpTest(unittest.TestCase):
    def setUp(self):
        class RejectStart:
            def request_start(self):
                return {"ok": False, "status": "rejected"}

        self.server = OperatorCommandServer(RejectStart(), port=18767)
        self.server.start()
        self.url = "http://127.0.0.1:18767/operator-command"

    def tearDown(self):
        self.server.close()

    def test_rejected_start_does_not_say_queued(self):
        req = urllib.request.Request(
            self.url, data=json.dumps({"text": "survey the wheat"}).encode(),
            headers={"Content-Type": "application/json"}, method="POST",
        )
        with urllib.request.urlopen(req, timeout=2) as resp:
            body = json.loads(resp.read().decode())
        self.assertEqual(body["intent"], "start_survey")
        self.assertFalse(body["ok"])
        self.assertNotIn("queued", (body.get("message") or "").lower())
