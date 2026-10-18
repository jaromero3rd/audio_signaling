"""Reassemble /mic chunks into utterances: seq order → energy VAD → s16 16 kHz WAV.

Consumes the payload dicts posted by uno_q_field_station/python/mic_transfer.py
(format f32le, 48 kHz, mono, chunk 1024). The UNO Q stays a dumb PCM pipe; all
segmentation and conversion happen here.
"""

from __future__ import annotations

import base64
import io
import threading
import wave
from collections.abc import Callable

import numpy as np

IN_RATE = 48000
OUT_RATE = 16000


def f32le_to_wav16k(pcm: bytes) -> bytes:
    """f32le 48 kHz → s16le 16 kHz WAV (3:1 boxcar decimation; fine for speech)."""
    samples = np.frombuffer(pcm, dtype="<f4")
    n = (len(samples) // 3) * 3
    down = samples[:n].reshape(-1, 3).mean(axis=1)
    s16 = np.clip(down * 32767.0, -32768, 32767).astype("<i2")
    out = io.BytesIO()
    with wave.open(out, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(OUT_RATE)
        wav.writeframes(s16.tobytes())
    return out.getvalue()


class _Device:
    __slots__ = ("seq", "ts", "heard", "quiet_s", "buf")

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.seq = None
        self.ts = 0.0
        self.heard = False
        self.quiet_s = 0.0
        self.buf = bytearray()


class MicSegmenter:
    """feed(/mic payload) → on_utterance(device_id, wav_bytes)."""

    def __init__(
        self,
        on_utterance: Callable[[str, bytes], None],
        *,
        peak_threshold: float = 0.02,
        silence_s: float = 1.2,
        max_utterance_s: float = 15.0,
        gap_s: float = 2.0,
    ) -> None:
        self.on_utterance = on_utterance
        self.peak_threshold = peak_threshold
        self.silence_s = silence_s
        self.max_utterance_s = max_utterance_s
        self.gap_s = gap_s
        self._devices: dict[str, _Device] = {}
        self._lock = threading.Lock()

    def _emit(self, device_id: str, dev: _Device) -> None:
        if dev.heard and dev.buf:
            self.on_utterance(device_id, f32le_to_wav16k(bytes(dev.buf)))
        dev.reset()

    def feed(self, payload: dict) -> None:
        """Accept one /mic payload. Chunks out of contract are skipped."""
        if (payload.get("format") != "f32le" or payload.get("sample_rate") != IN_RATE
                or payload.get("channels", 1) != 1):
            return
        try:
            pcm = base64.b64decode(payload.get("audio_b64") or "")
        except Exception:
            return
        if not pcm or len(pcm) % 4:
            return
        device_id = str(payload.get("device_id") or "unknown")
        seq = payload.get("seq")
        try:
            ts = float(payload.get("ts") or 0.0)
        except (TypeError, ValueError):
            ts = 0.0
        chunk_s = len(pcm) / 4 / IN_RATE

        with self._lock:
            dev = self._devices.setdefault(device_id, _Device())
            # Forward seq gaps are dropped chunks (audio glitch): keep appending.
            # Only a regress/restart or a long timestamp break breaks the utterance.
            broken = (
                (isinstance(seq, int) and dev.seq is not None and seq <= dev.seq)
                or (ts and dev.ts and ts - dev.ts > self.gap_s)
            )
            if broken and dev.buf:
                self._emit(device_id, dev)
            if isinstance(seq, int):
                dev.seq = seq
            if ts:
                dev.ts = ts

            dev.buf.extend(pcm)
            if float(np.abs(np.frombuffer(pcm, dtype="<f4")).max()) > self.peak_threshold:
                dev.heard, dev.quiet_s = True, 0.0
            elif dev.heard:
                dev.quiet_s += chunk_s

            if (dev.heard and dev.quiet_s >= self.silence_s) or (
                len(dev.buf) / 4 / IN_RATE >= self.max_utterance_s
            ):
                self._emit(device_id, dev)

    def flush(self) -> None:
        """Emit any in-progress utterances (call on shutdown)."""
        with self._lock:
            for device_id, dev in self._devices.items():
                self._emit(device_id, dev)
