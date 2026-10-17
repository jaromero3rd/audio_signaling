"""Bounded in-memory ElevenLabs Flash v2.5 TTS for host-formatted alerts."""

from __future__ import annotations

import os
import re
import time

import httpx

MAX_AUDIO_BYTES = 5 * 1024 * 1024
MAX_TEXT_CHARS = 3500


class VoiceNoteError(RuntimeError):
    pass


class VoiceNoteSynthesizer:
    def __init__(self, key: str, voice_id: str, *, transport=None):
        if not key or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", voice_id):
            raise VoiceNoteError("Configure ELEVENLABS_API_KEY and ELEVENLABS_TTS_VOICE_ID")
        self.voice_id = voice_id
        self._http = httpx.Client(
            base_url="https://api.elevenlabs.io",
            headers={"xi-api-key": key}, timeout=15,
            follow_redirects=False, trust_env=False, transport=transport,
        )

    @classmethod
    def from_env(cls):
        key = os.environ.get("ELEVENLABS_API_KEY", "")
        voice = os.environ.get("ELEVENLABS_TTS_VOICE_ID", "")
        return cls(key, voice) if key and voice else None

    def synthesize(self, text: str) -> bytes:
        if not isinstance(text, str) or not 1 <= len(text) <= MAX_TEXT_CHARS:
            raise VoiceNoteError("Voice-note text exceeds the host limit")
        try:
            deadline = time.monotonic() + 20
            with self._http.stream(
                "POST", f"/v1/text-to-speech/{self.voice_id}",
                params={"output_format": "mp3_22050_32"},
                headers={"accept": "audio/mpeg"},
                json={"text": text, "model_id": "eleven_flash_v2_5"},
            ) as response:
                response.raise_for_status()
                if response.headers.get("content-type", "").split(";")[0] != "audio/mpeg":
                    raise ValueError("Unexpected audio type")
                audio = bytearray()
                for chunk in response.iter_bytes(chunk_size=16384):
                    if len(audio) + len(chunk) > MAX_AUDIO_BYTES or time.monotonic() > deadline:
                        raise ValueError("Voice note exceeded bounds")
                    audio.extend(chunk)
                if not audio:
                    raise ValueError("Empty audio")
                return bytes(audio)
        except (httpx.HTTPError, ValueError):
            raise VoiceNoteError("ElevenLabs voice-note synthesis failed") from None

    def close(self):
        self._http.close()
