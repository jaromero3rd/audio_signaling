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
