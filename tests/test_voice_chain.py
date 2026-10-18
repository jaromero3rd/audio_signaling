"""Voice-chain unit tests: segmenter, WAV conversion, intent map. No services."""

from __future__ import annotations

import base64
import io
import unittest
import wave

import numpy as np

from ground_station.voice_ingest import IN_RATE, OUT_RATE, MicSegmenter, f32le_to_wav16k
from ground_station.voice_scribe import map_intent, PrintDispatcher, VoiceCommandService


def pcm_chunk(kind: str, chunks: int = 1) -> bytes:
    """Sine 'speech' or silence as f32le 48 kHz, `chunks` × 1024 samples."""
    n = 1024 * chunks
    if kind == "speech":
        t = np.arange(n) / IN_RATE
        samples = 0.3 * np.sin(2 * np.pi * 220 * t)
    else:
        samples = np.zeros(n, dtype=np.float64)
    return samples.astype("<f4").tobytes()


def payload(pcm: bytes, seq: int, ts: float = 0.0) -> dict:
    return {
        "device_id": "test-dev", "source": "unoq_mic", "format": "f32le",
        "sample_rate": IN_RATE, "channels": 1, "chunk": 1024,
        "seq": seq, "ts": ts, "audio_b64": base64.b64encode(pcm).decode(),
    }


class SegmenterTest(unittest.TestCase):
    def test_speech_then_silence_emits_one_utterance(self):
        out = []
        seg = MicSegmenter(lambda dev, wav: out.append(wav), silence_s=0.3)
        seg.feed(payload(pcm_chunk("speech", 5), seq=1, ts=1.0))
        self.assertEqual(out, [])
        seg.feed(payload(pcm_chunk("silence", 20), seq=2, ts=1.5))  # ~0.43 s quiet
        self.assertEqual(len(out), 1)
        with wave.open(io.BytesIO(out[0]), "rb") as wav:
            self.assertEqual(wav.getframerate(), OUT_RATE)
            self.assertEqual(wav.getnchannels(), 1)
            self.assertEqual(wav.getsampwidth(), 2)
            self.assertGreater(wav.getnframes(), 0)

    def test_silence_only_emits_nothing(self):
        out = []
        seg = MicSegmenter(lambda dev, wav: out.append(wav))
        seg.feed(payload(pcm_chunk("silence", 10), seq=1, ts=1.0))
        seg.flush()
        self.assertEqual(out, [])

    def test_forward_seq_gap_continues_utterance(self):
        # Dropped chunks are an audio glitch, not an utterance break.
        out = []
        seg = MicSegmenter(lambda dev, wav: out.append(wav), silence_s=0.3)
        seg.feed(payload(pcm_chunk("speech", 3), seq=1, ts=1.0))
        seg.feed(payload(pcm_chunk("speech", 3), seq=9, ts=1.2))
        self.assertEqual(out, [])
        seg.feed(payload(pcm_chunk("silence", 20), seq=10, ts=1.6))
        self.assertEqual(len(out), 1)

    def test_seq_regress_flushes_partial_utterance(self):
        out = []
        seg = MicSegmenter(lambda dev, wav: out.append(wav))
        seg.feed(payload(pcm_chunk("speech", 3), seq=5, ts=1.0))
        seg.feed(payload(pcm_chunk("speech", 3), seq=1, ts=1.2))  # sender restart
        self.assertEqual(len(out), 1)

    def test_ts_gap_flushes_partial_utterance(self):
        out = []
        seg = MicSegmenter(lambda dev, wav: out.append(wav), gap_s=2.0)
        seg.feed(payload(pcm_chunk("speech", 3), seq=1, ts=1.0))
        seg.feed(payload(pcm_chunk("speech", 3), seq=2, ts=5.0))  # 4 s stall
        self.assertEqual(len(out), 1)

    def test_missing_seq_still_accumulates(self):
        out = []
        seg = MicSegmenter(lambda dev, wav: out.append(wav))
        seg.feed(payload(pcm_chunk("speech", 3), seq=1, ts=1.0))
        seg.feed(payload(pcm_chunk("speech", 3), seq=None, ts=1.1))  # no seq: keep going
        self.assertEqual(out, [])
        seg.flush()
        self.assertEqual(len(out), 1)

    def test_stereo_or_wrong_rate_skipped(self):
        out = []
        seg = MicSegmenter(lambda dev, wav: out.append(wav))
        seg.feed(payload(pcm_chunk("speech"), seq=1) | {"channels": 2})
        seg.feed(payload(pcm_chunk("speech"), seq=2) | {"sample_rate": 16000})
        seg.flush()
        self.assertEqual(out, [])

    def test_bad_format_and_bad_b64_skipped(self):
        out = []
        seg = MicSegmenter(lambda dev, wav: out.append(wav))
        seg.feed({"format": "s16", "sample_rate": 16000, "device_id": "x", "seq": 1})
        seg.feed(payload(pcm_chunk("speech"), seq=1) | {"audio_b64": "!!!"})
        seg.flush()
        self.assertEqual(out, [])

    def test_devices_segmented_independently(self):
        out = []
        seg = MicSegmenter(lambda dev, wav: out.append((dev, wav)), silence_s=0.1)
        a = payload(pcm_chunk("speech", 3), seq=1, ts=1.0)
        b = payload(pcm_chunk("speech", 3), seq=1, ts=1.0) | {"device_id": "other"}
        seg.feed(a)
        seg.feed(b)
        seg.feed(payload(pcm_chunk("silence", 5), seq=2, ts=1.5) | {"device_id": "other"})
        self.assertEqual([dev for dev, _ in out], ["other"])
        seg.flush()
        self.assertEqual([dev for dev, _ in out], ["other", "test-dev"])

    def test_max_utterance_cap(self):
        out = []
        seg = MicSegmenter(lambda dev, wav: out.append(wav), max_utterance_s=0.2)
        seg.feed(payload(pcm_chunk("speech", 10), seq=1, ts=1.0))  # ~0.21 s > cap
        self.assertEqual(len(out), 1)


class WavConversionTest(unittest.TestCase):
    def test_rate_and_duration(self):
        pcm = pcm_chunk("speech", 47)  # 48128 samples ≈ 1.003 s
        wav = f32le_to_wav16k(pcm)
        with wave.open(io.BytesIO(wav), "rb") as w:
            self.assertEqual(w.getframerate(), 16000)
            duration = w.getnframes() / w.getframerate()
            self.assertAlmostEqual(duration, 48128 / IN_RATE, delta=0.01)


class IntentMapTest(unittest.TestCase):
    def setUp(self):
        self.disp = PrintDispatcher()

    def test_abort_first_and_bypasses(self):
        out = map_intent("abort the mission now", self.disp)
        self.assertEqual(out["intent"], "abort")
        self.assertTrue(out["result"]["ok"])

    def test_status(self):
        out = map_intent("what's the status of the survey", self.disp)
        self.assertEqual(out["intent"], "status")

    def test_start_survey(self):
        out = map_intent("survey the wheat please", self.disp)
        self.assertEqual(out["intent"], "start_survey")

    def test_text_user_extracts_body(self):
        out = map_intent("text the user saying the survey is done", self.disp)
        self.assertEqual(out["intent"], "text_user")
        self.assertEqual(out["body"], "the survey is done")
        self.assertFalse(out["result"]["ok"])  # PrintDispatcher has no text_user

    def test_freeform_goes_to_submit_instruction(self):
        out = map_intent("hover a bit higher over the rows", self.disp)
        self.assertEqual(out["intent"], "instruction")
        self.assertEqual(out["result"]["submitted"], "hover a bit higher over the rows")

    def test_abort_beats_start_words(self):
        out = map_intent("stop the survey now", self.disp)
        self.assertEqual(out["intent"], "abort")

    def test_simulated_abort_is_honest(self):
        out = map_intent("abort", self.disp)
        self.assertTrue(out["result"]["simulated"])
        self.assertFalse(out["result"]["stopped"])

    def test_missing_abort_sink_reports_failure(self):
        out = map_intent("abort", object())
        self.assertFalse(out["result"]["ok"])
        self.assertEqual(out["result"]["reason"], "no_abort_sink")


class ServiceTest(unittest.TestCase):
    def test_handle_utterance_empty_transcript(self):
        service = VoiceCommandService(PrintDispatcher(), client=object())
        # Fake transcriber: patch transcribe via client object that returns empty.
        service.client = type("C", (), {
            "speech_to_text": type("S", (), {
                "convert": lambda self, **kw: type("R", (), {"text": ""})()
            })()
        })()
        out = service.handle_utterance("dev", b"RIFF")
        self.assertEqual(out["intent"], "empty")


if __name__ == "__main__":
    unittest.main()
