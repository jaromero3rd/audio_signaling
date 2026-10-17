"""Capture mic PCM (same sounddevice setup as recv.py) and POST chunks to ground.

Farmer voice commands / Grok Voice run on the laptop (ground_station/voice_grok.py),
not on the UNO Q. This module only records and relays audio bytes.
"""

from __future__ import annotations

import base64
import json
import threading
import time
import urllib.error
import urllib.request
from typing import Any

from mic_capture import CHUNK, SAMPLE_RATE, mic_chunks


def post_mic_chunk(
    url: str,
    *,
    device_id: str,
    pcm: bytes,
    seq: int,
    timeout_s: float = 2.0,
) -> tuple[bool, str]:
    payload = {
        "device_id": device_id,
        "source": "unoq_mic",
        "format": "f32le",
        "sample_rate": SAMPLE_RATE,
        "channels": 1,
        "chunk": CHUNK,
        "seq": seq,
        "ts": time.time(),
        "audio_b64": base64.b64encode(pcm).decode("ascii"),
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            return 200 <= getattr(resp, "status", 200) < 300, body
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return False, str(exc)


class MicTransfer:
    """Background: read mic → POST raw chunks to the ground computer."""

    def __init__(
        self,
        *,
        url: str,
        device_id: str = "unoq-field-01",
        device: str | int | None = None,
        timeout_s: float = 2.0,
        offline_path: Any | None = None,
    ) -> None:
        self.url = url
        self.device_id = device_id
        self.device = device
        self.timeout_s = timeout_s
        self.offline_path = offline_path
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.sent = 0
        self.dropped = 0
        self._seq = 0

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="mic-transfer", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2.0)

    def _buffer_offline(self, pcm: bytes) -> None:
        if self.offline_path is None:
            return
        path = self.offline_path
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("ab") as f:
            f.write(pcm)

    def _run(self) -> None:
        print(
            f"mic transfer: {SAMPLE_RATE} Hz f32le chunk={CHUNK} -> {self.url}",
            flush=True,
        )
        try:
            for pcm in mic_chunks(self.device, stop=self._stop):
                self._seq += 1
                ok, err = post_mic_chunk(
                    self.url,
                    device_id=self.device_id,
                    pcm=pcm,
                    seq=self._seq,
                    timeout_s=self.timeout_s,
                )
                if ok:
                    self.sent += 1
                else:
                    self.dropped += 1
                    self._buffer_offline(pcm)
                    if self.dropped % 50 == 1:
                        print(f"mic transfer fail ({err}); buffering locally", flush=True)
        except Exception as exc:  # noqa: BLE001
            print(f"mic transfer stopped: {exc}", flush=True)
