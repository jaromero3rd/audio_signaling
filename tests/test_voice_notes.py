"""ElevenLabs TTS and Photon multipart contract, no live credentials or recipients."""

import hashlib
import json
import unittest
from email import policy
from email.parser import BytesParser
from unittest.mock import patch

import httpx

import test_photon as photon_tests
from test_photon import rid
from ground_station.photon import PhotonClient, PhotonError, PhotonNotifier
from ground_station.voice_note import MAX_AUDIO_BYTES, VoiceNoteError, VoiceNoteSynthesizer

AUDIO = b"ID3fake-mp3-fixture"


class TTSTests(unittest.TestCase):
    def test_flash_tts_is_bounded_in_memory_with_fake_http(self):
        requests = []
        def wire(request):
            requests.append(request)
            return httpx.Response(200, content=AUDIO, headers={"content-type": "audio/mpeg"})
        tts = VoiceNoteSynthesizer("test-only", "test-voice", transport=httpx.MockTransport(wire))
        self.addCleanup(tts.close)
        self.assertEqual(tts.synthesize("Host formatted alert"), AUDIO)
        request = requests[0]
        self.assertEqual(request.url.path, "/v1/text-to-speech/test-voice")
        self.assertEqual(request.url.params["output_format"], "mp3_22050_32")
        self.assertEqual(json.loads(request.content), {"text": "Host formatted alert",
                                                      "model_id": "eleven_flash_v2_5"})

    def test_invalid_error_empty_and_oversized_audio_are_redacted(self):
        for status, content, mime in [(403, b"private provider error", "application/json"),
                                      (200, b"", "audio/mpeg"), (200, b"{}", "application/json"),
                                      (200, b"x" * (MAX_AUDIO_BYTES + 1), "audio/mpeg")]:
            tts = VoiceNoteSynthesizer("test-only", "test-voice", transport=httpx.MockTransport(
                lambda request: httpx.Response(status, content=content, headers={"content-type": mime})))
            try:
                with self.assertRaises(VoiceNoteError) as raised:
                    tts.synthesize("Host formatted alert")
                self.assertNotIn("private provider", str(raised.exception))
            finally:
                tts.close()


class VoiceAttachmentTests(unittest.TestCase):
    def setUp(self):
        self.wire = photon_tests.PhotonTests()
        self.wire.setUp()
        self.sleeps = []
        self.uploads = []
        self.attachments = []
        self.not_ready = 0
        self.upload_failure = 0

    def transport(self, request):
        if request.url.path.endswith("/attachments"):
            self.uploads.append(request)
            if self.upload_failure:
                self.upload_failure -= 1
                return httpx.Response(503)
            return httpx.Response(200, json={"id": rid("att")})
        if request.method == "POST" and json.loads(request.content)["content"]["type"] == "attachment":
            self.attachments.append(request)
            if self.not_ready:
                self.not_ready -= 1
                return httpx.Response(409, json={"code": "ATTACHMENT_NOT_READY"}, headers={"retry-after": "5"})
        return self.wire.transport(request)

    def client(self):
        client = PhotonClient(self.wire.config, transport=httpx.MockTransport(self.transport),
                              sleep=self.sleeps.append)
        self.addCleanup(client.close)
        client.check()
        return client

    def test_multipart_metadata_content_hash_retry_and_upload_deduplication(self):
        client = self.client()
        self.upload_failure = 1
        attachment = client.upload_attachment(AUDIO, content_type="audio/mpeg")
        self.assertEqual(attachment, client.upload_attachment(AUDIO, content_type="audio/mpeg"))
        self.assertEqual(len(self.uploads), 2)
        first, second = self.uploads
        self.assertEqual(first.content, second.content)
        self.assertEqual(first.headers["idempotency-key"], second.headers["idempotency-key"])
        self.assertIn(hashlib.sha256(AUDIO).hexdigest(), first.headers["idempotency-key"])
        mime = BytesParser(policy=policy.default).parsebytes(
            f"Content-Type: {first.headers['content-type']}\r\n\r\n".encode() + first.content)
        parts = list(mime.iter_parts())
        self.assertEqual(len(parts), 2)
        self.assertEqual(json.loads(parts[0].get_content()), {
            "contentType": "audio/mpeg", "sizeBytes": len(AUDIO), "filename": "alert.mp3"})
        self.assertEqual(parts[1].get_payload(decode=True), AUDIO)
        self.assertEqual(int(first.headers["content-length"]), len(first.content))

    def test_not_ready_retry_reuses_identical_message_key_route_and_body(self):
        client = self.client()
        self.not_ready = 2
        client.send_attachment(client.upload_attachment(AUDIO, content_type="audio/mpeg"), "receipt")
        self.assertEqual(self.sleeps, [5, 5])
        self.assertEqual(len(self.attachments), 3)
        self.assertEqual(len({r.headers["idempotency-key"] for r in self.attachments}), 1)
        self.assertEqual(len({r.content for r in self.attachments}), 1)
        body = json.loads(self.attachments[0].content)
        self.assertEqual(body["spaceId"], self.wire.space["id"])
        self.assertEqual(body["senderId"], rid("usr", 1))

    def test_not_ready_retries_are_bounded(self):
        client = self.client()
        self.not_ready = 20
        with self.assertRaises(PhotonError):
            client.send_attachment(rid("att"), "receipt")
        self.assertEqual(len(self.attachments), 8)

    def analysis(self, score=0.2):
        return {"receipt": f"{'a' * 32}:1", "field_id": "field-1", "health_score": score,
                "analysis_summary": "Visible discoloration, uncertain estimate.", "vision_source": "droid"}

    def test_critical_analysis_gets_urgent_text_and_one_voice_note(self):
        self.assert_delivery(score=0.2, requested=False, expected_voice=True)

    def test_noncritical_is_text_only(self):
        self.assert_delivery(score=0.8, requested=False, expected_voice=False)

    def test_operator_can_request_noncritical_voice_notes(self):
        self.assert_delivery(score=0.8, requested=True, expected_voice=True)

    def assert_delivery(self, score, requested, expected_voice):
        tts_requests = []
        def wire(request):
            tts_requests.append(request)
            return httpx.Response(200, content=AUDIO, headers={"content-type": "audio/mpeg"})
        tts = VoiceNoteSynthesizer("test-only", "test-voice", transport=httpx.MockTransport(wire))
        notifier = PhotonNotifier(self.client(), mock=True, voice_notes=tts, voice_requested=requested)
        notifier.start()
        analysis = self.analysis(score)
        self.assertTrue(notifier.notify_analysis(analysis))
        self.assertFalse(notifier.notify_analysis(analysis))
        notifier.close()
        text_posts = [r for r in self.wire.requests if r.method == "POST"
                      and json.loads(r.content)["content"]["type"] == "text"]
        self.assertEqual(len(text_posts), 1)
        text = json.loads(text_posts[0].content)["content"]["text"]
        self.assertEqual("URGENT" in text, score < 0.4)
        self.assertEqual(len(tts_requests), int(expected_voice))
        self.assertEqual(len(self.uploads), int(expected_voice))
        self.assertEqual(len(self.attachments), int(expected_voice))
        if expected_voice:
            self.assertEqual(json.loads(tts_requests[0].content)["text"], text)

    def test_tts_failure_preserves_text_and_does_not_retry_text(self):
        tts = VoiceNoteSynthesizer("test-only", "test-voice", transport=httpx.MockTransport(
            lambda r: httpx.Response(503, json={"detail": "private"})))
        notifier = PhotonNotifier(self.client(), mock=True, voice_notes=tts)
        notifier.start()
        notifier.notify_analysis(self.analysis())
        notifier.close()
        self.assertEqual(len([r for r in self.wire.requests if r.method == "POST"]), 1)
        self.assertFalse(self.uploads)

    def test_invalid_attachments_cannot_use_network(self):
        client = self.client()
        for data, mime in [(b"", "audio/mpeg"), (b"x", "text/html"),
                           (b"x" * (MAX_AUDIO_BYTES + 1), "audio/mpeg")]:
            with self.assertRaises(PhotonError):
                client.upload_attachment(data, content_type=mime)
        self.assertFalse(self.uploads)

    def test_critical_analysis_has_priority_over_waiting_status_events(self):
        notifier = PhotonNotifier(self.client(), mock=True)
        notifier.notify("photo")
        notifier.notify_analysis(self.analysis())
        self.assertEqual(notifier._queue.get_nowait()[2], "analysis")
        notifier.close()
