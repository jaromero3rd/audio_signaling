"""ElevenLabs speech-to-text for operator commands.

The transcript is text for the Droid session. This module does not fly the
drone, score a photo, or call any xAI endpoint.
"""

from __future__ import annotations

import io
import json
import os
import queue
import re
import threading
import urllib.request
import wave

STT_URL = "https://api.elevenlabs.io/v1/speech-to-text"
RATE = 16000
CHUNK = 1600
SILENCE_S = 0.7
SPEECH_RMS = 500
MAX_UTTERANCE_S = 15

_ABORT = ("emergency", "abort", "stop", "land", "landing")
_START_COMMAND = re.compile(
    r"^(?:please\s+)?(?:start(?:\s+(?:(?:a|the)\s+)?(?:survey|mission))?"
    r"|survey\s+(?:(?:a|the)\s+)?(?:field|wheat|crops)|take\s*off)"
    r"(?:\s+please)?[.!?]?$",
    re.IGNORECASE,
)


def pcm_to_wav(pcm: bytes, rate: int = RATE) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(pcm)
    return buf.getvalue()


def _has(text: str, phrases: tuple[str, ...]) -> bool:
    folded = text.lower()
    return any(
        re.search(rf"(?<![a-z]){re.escape(phrase)}(?![a-z])", folded)
        for phrase in phrases
    )


def transcribe(wav_bytes: bytes, *, poster=None) -> str:
    """Return the transcript. `poster(wav_bytes, api_key)` replaces the HTTP call in tests."""
    key = os.environ.get("ELEVENLABS_API_KEY", "")
    if not key:
        raise RuntimeError("ELEVENLABS_API_KEY is required for voice input")
    if not wav_bytes:
        raise RuntimeError("Empty utterance")
    body = (poster or _post_stt)(wav_bytes, key)
    text = body.get("text") if isinstance(body, dict) else None
    if not isinstance(text, str):
        raise RuntimeError("ElevenLabs returned no transcript")
    return text.strip()


def route_transcript(text: str, gateway) -> str:
    """Map one transcript to a local abort, a Droid start, or a status turn."""
    cleaned = " ".join(text.split())
    if not cleaned:
        return "ignore"
    if _has(cleaned, _ABORT):
        gateway.request_abort()
        return "abort"
    if _START_COMMAND.fullmatch(cleaned):
        gateway.request_start()
        return "start"
    worker = getattr(gateway, "worker", None)
    if worker is not None:
        worker.submit(
            f"The operator said: {cleaned[:500]}. Read status and answer briefly. "
            "Do not start a survey.",
            {"kind": "voice_status", "transcript": cleaned[:500]},
        )
    return "status"


def _post_stt(wav_bytes: bytes, api_key: str) -> dict:
    boundary = "----agridrone-stt"
    body = b"".join([
        f"--{boundary}\r\n".encode(),
        b'Content-Disposition: form-data; name="model_id"\r\n\r\nscribe_v1\r\n',
        f"--{boundary}\r\n".encode(),
        b'Content-Disposition: form-data; name="file"; filename="utterance.wav"\r\n',
        b"Content-Type: audio/wav\r\n\r\n",
        wav_bytes,
        b"\r\n",
        f"--{boundary}--\r\n".encode(),
    ])
    request = urllib.request.Request(
        STT_URL,
        data=body,
        headers={
            "xi-api-key": api_key,
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = json.loads(response.read().decode())
    if not isinstance(payload, dict):
        raise RuntimeError("ElevenLabs returned an unexpected transcript")
    return payload


def _rms(pcm: bytes) -> float:
    if len(pcm) < 2:
        return 0.0
    count = len(pcm) // 2
    total = 0
    for index in range(count):
        sample = int.from_bytes(pcm[index * 2:index * 2 + 2], "little", signed=True)
        total += sample * sample
    return (total / count) ** 0.5


class ElevenVoiceBridge:
    def __init__(self, gateway) -> None:
        self.gateway = gateway
        self.ready = threading.Event()
        self.failed = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._audio: queue.Queue[bytes] = queue.Queue(maxsize=64)

    def run_forever(self) -> None:
        if not os.environ.get("ELEVENLABS_API_KEY"):
            raise RuntimeError("Set ELEVENLABS_API_KEY for voice input")
        import sounddevice as sd

        def callback(indata, frames, time_info, status) -> None:
            try:
                self._audio.put_nowait(bytes(indata))
            except queue.Full:
                pass

        with sd.InputStream(
            samplerate=RATE, channels=1, dtype="int16", blocksize=CHUNK, callback=callback,
        ):
            self.ready.set()
            spoken = bytearray()
            silent_for = 0.0
            heard = False
            while not self._stop.is_set():
                try:
                    chunk = self._audio.get(timeout=0.2)
                except queue.Empty:
                    continue
                spoken.extend(chunk)
                if _rms(chunk) >= SPEECH_RMS:
                    heard = True
                    silent_for = 0.0
                elif heard:
                    silent_for += (len(chunk) / 2) / RATE
                if not heard and len(spoken) > RATE * 4:
                    del spoken[:-RATE * 2]
                    continue
                if heard and (silent_for >= SILENCE_S or len(spoken) > RATE * 2 * MAX_UTTERANCE_S):
                    wav = pcm_to_wav(bytes(spoken))
                    spoken.clear()
                    heard = False
                    silent_for = 0.0
                    self._route_wav(wav)

    def _route_wav(self, wav: bytes) -> None:
        try:
            text = transcribe(wav)
        except Exception as exc:
            print(f"voice transcript failed: {exc}")
            return
        if not text:
            return
        kind = route_transcript(text, self.gateway)
        print(f"heard ({kind}): {text}")

    def notify(self, text: str) -> None:
        if text:
            print(f"voice status: {text}")

    def close(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=3)


def start_voice_thread(gateway) -> ElevenVoiceBridge:
    bridge = ElevenVoiceBridge(gateway)
    thread = threading.Thread(target=_run_voice, args=(bridge,), name="eleven-voice", daemon=True)
    bridge._thread = thread
    thread.start()
    return bridge


def _run_voice(bridge: ElevenVoiceBridge) -> None:
    try:
        bridge.run_forever()
    except Exception as exc:
        bridge.failed.set()
        print(f"ElevenLabs voice stopped: {exc}")
    finally:
        bridge.ready.set()
