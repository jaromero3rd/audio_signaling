"""Photon wire-contract and routing tests using an in-memory HTTP transport."""

from __future__ import annotations

import json
import threading
import unittest

import httpx

from ground_station.photon import PhotonClient, PhotonConfig, PhotonError, PhotonNotifier


def rid(kind, number=0):
    return f"pho_{kind}_{number:026d}"


class PhotonTests(unittest.TestCase):
    def setUp(self):
        self.config = PhotonConfig(
            key="test-only-not-a-real-key", project=rid("prj"), assignment=rid("asg"),
            agent="+15550000001", user="+15550000002",
        )
        self.requests = []
        self.space = {
            "id": rid("spc"), "projectId": self.config.project, "platform": "imessage",
            "kind": "direct", "participantIds": [rid("usr", 1), rid("usr", 2)],
        }
        self.actors = {
            rid("usr", 1): {"id": rid("usr", 1), "kind": "agent", "handle": self.config.agent,
                            "platform": "imessage", "projectId": self.config.project},
            rid("usr", 2): {"id": rid("usr", 2), "kind": "human", "handle": self.config.user,
                            "platform": "imessage", "projectId": self.config.project},
        }
        self.assignment = {
            "projectId": self.config.project, "platform": "imessage", "releasedAt": None,
            "handle": self.config.user, "assignedPhoneNumber": self.config.agent,
        }

    def transport(self, request):
        self.requests.append(request)
        path = request.url.path
        base = f"/v1/projects/{self.config.project}"
        if path == base:
            body = {"projectId": self.config.project, "deletedAt": None}
        elif "/assignments/" in path:
            body = self.assignment
        elif path == f"{base}/spaces":
            body = {"items": [self.space]}
        elif "/users/" in path:
            body = self.actors[path.rsplit("/", 1)[-1]]
        elif path == f"{base}/messages" and request.method == "POST":
            body = {"id": rid("msg"), "status": "queued"}
        elif "/messages/" in path:
            body = {"id": rid("msg"), "status": "delivered"}
        else:
            return httpx.Response(404)
        return httpx.Response(200, json=body)

    def client(self):
        client = PhotonClient(self.config, transport=httpx.MockTransport(self.transport))
        self.addCleanup(client.close)
        return client

    def test_check_is_read_only_and_routes_exact_recipient(self):
        client = self.client()
        client.check()
        self.assertTrue(all(request.method == "GET" for request in self.requests))
        self.assertEqual(client._route, (self.space["id"], rid("usr", 1)))

    def test_send_uses_fixed_text_route_and_idempotency_not_threads(self):
        client = self.client()
        client.check()
        result = client.send("photo", "same-run:photo", mock=True)
        request = self.requests[-1]
        body = json.loads(request.content)
        self.assertEqual(body["spaceId"], self.space["id"])
        self.assertEqual(body["senderId"], rid("usr", 1))
        self.assertNotIn("parentMessageId", body)
        self.assertIn("MOCK", body["content"]["text"])
        self.assertEqual(request.headers["idempotency-key"], "agridrone-v1:same-run:photo")
        self.assertEqual(result["status"], "queued")
        self.assertEqual(client.delivery_status(result["id"]), "delivered")

    def test_send_before_check_and_arbitrary_content_are_rejected(self):
        client = self.client()
        with self.assertRaises(PhotonError):
            client.send("photo", "event", mock=True)
        client.check()
        count = len(self.requests)
        with self.assertRaises(PhotonError):
            client.send("arbitrary message supplied by a model", "event", mock=True)
        self.assertEqual(len(self.requests), count)

    def test_wrong_recipient_cannot_be_used(self):
        self.actors[rid("usr", 2)]["handle"] = "+15550000003"
        client = self.client()
        with self.assertRaises(PhotonError):
            client.check()
        self.assertIsNone(client._route)
        self.assertFalse(any(request.method == "POST" for request in self.requests))

    def test_analysis_alert_routes_validated_result_and_deduplicates_receipt(self):
        client = self.client()
        notifier = PhotonNotifier(client, mock=True)
        notifier.start()
        analysis = {
            "receipt": f"{'a' * 32}:1", "field_id": "field-1", "health_score": 0.2,
            "analysis_summary": "Visible discoloration. Needs operator review.", "vision_source": "droid",
        }
        self.assertTrue(notifier.notify_analysis(analysis))
        self.assertFalse(notifier.notify_analysis(analysis))
        notifier.close()
        posts = [request for request in self.requests if request.method == "POST"]
        self.assertEqual(len(posts), 1)
        body = json.loads(posts[0].content)
        self.assertEqual(body["spaceId"], self.space["id"])
        self.assertIn("health score 0.20", body["content"]["text"])
        self.assertIn("Visible discoloration", body["content"]["text"])
        self.assertNotIn("receipt", body["content"]["text"])

    def test_fake_analysis_is_labeled_and_invalid_payload_never_sent(self):
        client = self.client()
        client.check()
        analysis = {"field_id": "field-1", "health_score": 0.8,
                    "analysis_summary": "Fixture", "vision_source": "fake"}
        client.send("analysis", "receipt", mock=True, analysis=analysis)
        self.assertIn("SIMULATED", json.loads(self.requests[-1].content)["content"]["text"])
        count = len(self.requests)
        with self.assertRaises(PhotonError):
            client.send("analysis", "receipt", mock=True, analysis={**analysis, "health_score": 2})
        self.assertEqual(count, len(self.requests))

    def test_groups_and_cross_project_users_are_rejected(self):
        for invalid in ("group", "cross-project"):
            with self.subTest(invalid=invalid):
                self.space["kind"] = "group" if invalid == "group" else "direct"
                if invalid == "cross-project":
                    self.actors[rid("usr", 1)]["projectId"] = rid("prj", 9)
                with self.assertRaises(PhotonError):
                    self.client().check()

    def test_released_assignment_blocks_all_sends(self):
        self.assignment["releasedAt"] = "2026-10-17T00:00:00Z"
        with self.assertRaises(PhotonError):
            self.client().check()
        self.assertFalse(any(request.method == "POST" for request in self.requests))

    def test_route_discovery_follows_empty_pages(self):
        original = self.transport
        def transport(request):
            if request.url.path.endswith("/spaces") and "pageToken" not in request.url.params:
                self.requests.append(request)
                return httpx.Response(200, json={"items": [], "nextPageToken": "next"})
            return original(request)
        client = PhotonClient(self.config, transport=httpx.MockTransport(transport))
        self.addCleanup(client.close)
        client.check()
        self.assertTrue(any(request.url.params.get("pageToken") == "next" for request in self.requests))

    def test_api_errors_do_not_expose_private_payloads(self):
        def transport(request):
            return httpx.Response(403, json={"detail": "private-data-not-for-logs"})
        client = PhotonClient(self.config, transport=httpx.MockTransport(transport))
        self.addCleanup(client.close)
        with self.assertRaises(PhotonError) as raised:
            client.check()
        self.assertNotIn("private-data", str(raised.exception))
        self.assertNotIn(self.config.key, repr(self.config))

    def test_notifier_is_nonblocking_and_deduplicates(self):
        client = self.client()
        notifier = PhotonNotifier(client, mock=True)
        notifier.start()
        threads = [threading.Thread(target=lambda: notifier.notify("photo")) for _ in range(10)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        notifier.notify("test")
        notifier.notify("unknown")
        notifier.close()
        posts = [request for request in self.requests if request.method == "POST"]
        self.assertEqual(len(posts), 1)

    def test_transient_retry_reuses_identical_key_and_body(self):
        attempts = []
        original = self.transport
        def transport(request):
            if request.method == "POST":
                attempts.append((request.headers["idempotency-key"], request.content))
                if len(attempts) == 1:
                    return httpx.Response(503)
            return original(request)
        client = PhotonClient(self.config, transport=httpx.MockTransport(transport))
        notifier = PhotonNotifier(client, mock=True)
        notifier.start()
        notifier.notify("photo")
        notifier.close()
        self.assertEqual(len(attempts), 2)
        self.assertEqual(attempts[0], attempts[1])


if __name__ == "__main__":
    unittest.main()
