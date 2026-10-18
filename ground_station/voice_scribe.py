"""Utterance WAV → ElevenLabs Scribe → intent → dispatcher (gateway or test echo).

Mode A from VOICE_CHAIN_SPEC.md: file-based STT per utterance, needs only
ELEVENLABS_API_KEY. Abort/stop bypasses Droid latency via the dispatcher's
request_abort(); everything else queues intent or forwards transcript text.
"""

from __future__ import annotations

import os
import queue
import re
import threading

ABORT = re.compile(r"\b(abort|stop|halt|emergency|kill|land now|land immediately)\b", re.I)
STATUS = re.compile(r"\b(status|report|how'?s the (mission|drone|survey)|what'?s (the status|happening))\b", re.I)
START = re.compile(r"\b(survey|start|take ?off|takeoff|go fly|launch|scan|find (the )?(crop|wheat)s?)\b", re.I)
TEXT_USER = re.compile(
    r"(?:text|message|tell|send)\s+(?:the\s+)?user\s*(?:saying|that|to\s+say)?\s*(.*)",
    re.I | re.S,
)


def transcribe(wav: bytes, *, client=None) -> str:
    if client is None:
        from elevenlabs.client import ElevenLabs

        client = ElevenLabs(api_key=os.environ["ELEVENLABS_API_KEY"])
    result = client.speech_to_text.convert(
        model_id="scribe_v1", file=wav, language_code="eng")
    return (getattr(result, "text", "") or "").strip()


def map_intent(transcript: str, dispatcher) -> dict:
    """One transcript → one intent dict. Abort is checked first and bypasses Droid."""
    if ABORT.search(transcript):
        result = dispatcher.request_abort() if hasattr(dispatcher, "request_abort") else {"ok": True}
        return {"intent": "abort", "result": result}
    if STATUS.search(transcript):
        result = dispatcher.snapshot() if hasattr(dispatcher, "snapshot") else {"ok": False}
        return {"intent": "status", "result": result}
    if TEXT_USER.search(transcript):
        body = TEXT_USER.search(transcript).group(1).strip() or transcript
        if hasattr(dispatcher, "text_user"):
            result = dispatcher.text_user(body)
            return {"intent": "text_user", "body": body, "result": result}
        return {"intent": "text_user", "body": body, "result": {"ok": False, "reason": "no_text_user"}}
    if START.search(transcript):
        result = dispatcher.request_start() if hasattr(dispatcher, "request_start") else {"ok": True}
        return {"intent": "start_survey", "result": result}
    if hasattr(dispatcher, "submit_instruction"):
        result = dispatcher.submit_instruction(transcript)
        return {"intent": "instruction", "result": result}
    return {"intent": "unmatched", "result": {"ok": False, "reason": "no_instruction_sink"}}


class PrintDispatcher:
    """Gateway-shaped stand-in for tests: logs intents, executes nothing."""

    controlled_by_droid = True

    def __init__(self) -> None:
        self.intents: list[dict] = []

    def request_start(self) -> dict:
        return {"ok": True, "status": "queued_for_droid", "executed": False}

    def request_abort(self) -> dict:
        return {"ok": True, "stopped": True}

    def snapshot(self) -> dict:
        return {"phase": "idle", "stopped": False}

    def submit_instruction(self, text: str) -> dict:
        return {"ok": True, "submitted": text[:200]}


class PhotonEchoDispatcher(PrintDispatcher):
    """PrintDispatcher + iMessage echo of each transcript/intent to the configured user."""

    def __init__(self, photon_client) -> None:
        super().__init__()
        self._photon = photon_client

    def text_user(self, body: str) -> dict:
        return send_text(self._photon, f"Voice command: {body}")


def send_text(client, text: str) -> dict:
    """Arbitrary text over a verified Photon route (test/demo path)."""
    import uuid

    from ground_station.photon import PhotonError

    if client._route is None:
        raise PhotonError("Photon routing has not been verified")
    space_id, sender_id = client._route
    return client._request(
        "POST", f"/v1/projects/{client.config.project}/messages",
        headers={"idempotency-key": f"agridrone-voice:{uuid.uuid4().hex}"},
        json={"spaceId": space_id, "senderId": sender_id,
              "content": {"type": "text", "format": "plain", "text": text[:1000]}},
    )


class VoiceCommandService:
    """Consumes (device_id, wav) from a queue; transcribes and dispatches intents."""

    def __init__(self, dispatcher, *, client=None, on_intent=None) -> None:
        self.dispatcher = dispatcher
        self.client = client
        self.on_intent = on_intent
        self.utterances: queue.Queue[tuple[str, bytes]] = queue.Queue(maxsize=8)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.errors = 0

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="voice-scribe", daemon=True)
        self._thread.start()

    def enqueue(self, device_id: str, wav: bytes) -> None:
        try:
            self.utterances.put_nowait((device_id, wav))
        except queue.Full:
            pass

    def handle_utterance(self, device_id: str, wav: bytes) -> dict:
        transcript = transcribe(wav, client=self.client)
        if not transcript:
            return {"intent": "empty", "transcript": ""}
        outcome = map_intent(transcript, self.dispatcher)
        outcome["transcript"] = transcript
        outcome["device_id"] = device_id
        return outcome

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                device_id, wav = self.utterances.get(timeout=0.1)
            except queue.Empty:
                continue
            try:
                outcome = self.handle_utterance(device_id, wav)
            except Exception:
                self.errors += 1
                print("Voice command failed; no private payload logged.")
                continue
            print(f"[voice:{device_id}] {outcome.get('transcript')!r} → {outcome.get('intent')}")
            if self.on_intent is not None:
                self.on_intent(outcome)

    def close(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
