"""Send one Chalk insight to the operator phone.

Text and the survey photo go through Photon. The spoken copy is ElevenLabs
TTS. This module does not score crops and does not call xAI.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import urllib.request
from pathlib import Path

TTS_URL = "https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"


def deliver(insight: str, photo_path: str | None = None, *, poster=None) -> str:
    """Return "" when Photon accepted the summary, or a delivery_error string.

    A missing key returns before any network call. Callers must not treat a
    string error as a failed flight.
    """
    key = os.environ.get("ELEVENLABS_API_KEY", "").strip()
    voice_id = (
        os.environ.get("ELEVENLABS_VOICE_ID", "").strip()
        or os.environ.get("ELEVENLABS_TTS_VOICE_ID", "").strip()
    )
    if not key or not voice_id:
        return "ELEVENLABS_API_KEY or a voice id is not set"
    if not os.environ.get("PHOTON_API_KEY", "").strip():
        return "PHOTON_API_KEY is not set"
    text = " ".join((insight or "").split())
    if not text:
        return "insight is empty"
    if photo_path and not Path(photo_path).is_file():
        return "survey photo is not on this machine"
    audio_path = None
    try:
        audio = (poster or _synthesize)(text, voice_id, key)
        if not isinstance(audio, (bytes, bytearray)) or not audio:
            return "ElevenLabs returned empty audio"
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as handle:
            handle.write(audio)
            audio_path = handle.name
        _send(text, photo_path, audio_path)
    except Exception as exc:
        return str(exc)[:300]
    finally:
        if audio_path:
            try:
                os.unlink(audio_path)
            except OSError:
                pass
    return ""


def _synthesize(text: str, voice_id: str, api_key: str) -> bytes:
    request = urllib.request.Request(
        TTS_URL.format(voice_id=voice_id),
        data=json.dumps({"text": text, "model_id": "eleven_multilingual_v2"}).encode(),
        headers={
            "xi-api-key": api_key,
            "Content-Type": "application/json",
            "Accept": "audio/mpeg",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read()


def _send(text: str, photo_path: str | None, audio_path: str) -> None:
    root = Path(__file__).resolve().parents[1]
    if str(root) not in sys.path:
        sys.path.append(str(root))
    from ground_station.photon_alert import send_photon_alert

    send_photon_alert(text, photo_path=photo_path, audio_path=audio_path)
