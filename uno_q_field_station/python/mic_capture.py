"""Microphone input matching repo recv.py (sounddevice, 48 kHz float32).

Same capture contract as the ground-station ggwave listener:
  SAMPLE_RATE = 48000, CHUNK = 1024, channels=1, dtype=float32
"""

from __future__ import annotations

import queue
import threading
import time
from collections.abc import Iterator
from typing import Any

import numpy as np

SAMPLE_RATE = 48000
CHUNK = 1024


def resolve_device(device: str | int | None) -> str | int | None:
    if device is None:
        return None
    if isinstance(device, int):
        return device
    text = str(device).strip()
    if text.isdigit():
        return int(text)
    return text or None


def list_devices() -> str:
    import sounddevice as sd

    return str(sd.query_devices())


def mic_chunks(
    device: str | int | None = None,
    *,
    show_level: bool = False,
    stop: threading.Event | None = None,
) -> Iterator[bytes]:
    """Yield raw float32 PCM chunks from the default (or named) input device."""
    import sounddevice as sd

    q: queue.Queue[bytes] = queue.Queue()
    stop = stop or threading.Event()

    def callback(indata, frames, time_info, status):  # noqa: ANN001
        if status:
            print(f"audio status: {status}", flush=True)
        try:
            q.put_nowait(bytes(indata))
        except queue.Full:
            pass

    with sd.RawInputStream(
        samplerate=SAMPLE_RATE,
        blocksize=CHUNK,
        device=resolve_device(device),
        channels=1,
        dtype="float32",
        callback=callback,
    ):
        last_meter = 0.0
        while not stop.is_set():
            try:
                chunk = q.get(timeout=0.25)
            except queue.Empty:
                continue
            if show_level and time.time() - last_meter > 0.25:
                peak = float(np.abs(np.frombuffer(chunk, dtype=np.float32)).max())
                bar = "#" * min(40, int(peak * 80))
                print(f"\r  mic level {bar:<40}", end="", flush=True)
                last_meter = time.time()
            yield chunk


def chunk_peak(chunk: bytes) -> float:
    """Peak level 0..1 — same metric recv.py prints for ggwave."""
    if not chunk:
        return 0.0
    return float(np.abs(np.frombuffer(chunk, dtype=np.float32)).max())


def chunk_to_float32(chunk: bytes) -> np.ndarray:
    return np.frombuffer(chunk, dtype=np.float32).copy()


class MicMonitor:
    """Background mic reader; exposes the latest float32 window for analysis."""

    def __init__(
        self,
        *,
        device: str | int | None = None,
        window_chunks: int = 8,
        show_level: bool = False,
    ) -> None:
        self.device = device
        self.window_chunks = max(1, window_chunks)
        self.show_level = show_level
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._chunks: list[np.ndarray] = []
        self._last_peak = 0.0
        self._last_ts = 0.0
        self._error: str | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="mic-monitor", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2.0)

    def _run(self) -> None:
        try:
            for raw in mic_chunks(self.device, show_level=self.show_level, stop=self._stop):
                samples = chunk_to_float32(raw)
                peak = chunk_peak(raw)
                with self._lock:
                    self._chunks.append(samples)
                    if len(self._chunks) > self.window_chunks:
                        self._chunks = self._chunks[-self.window_chunks :]
                    self._last_peak = peak
                    self._last_ts = time.time()
        except Exception as exc:  # noqa: BLE001
            self._error = str(exc)
            print(f"mic capture failed: {exc}", flush=True)

    def latest_window(self) -> tuple[np.ndarray | None, dict[str, Any]]:
        with self._lock:
            meta = {
                "peak": self._last_peak,
                "ts": self._last_ts,
                "chunks": len(self._chunks),
                "error": self._error,
                "sample_rate": SAMPLE_RATE,
                "chunk": CHUNK,
            }
            if not self._chunks:
                return None, meta
            return np.concatenate(self._chunks), dict(meta)
