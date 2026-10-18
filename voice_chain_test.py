#!/usr/bin/env python3
"""Phase-1 voice-chain test: /mic loopback → segment → Scribe → intent → dispatch.

Runs the exact byte contract the UNO Q will produce (f32le 48 kHz, chunk 1024,
JSON base64, seq) against a local in-process sensor_recv server — no board needed.

Sources:
  --say "text"   macOS `say` synthesizes speech; tests transcription with no mic
  --file PATH    raw f32le 48 kHz PCM file (e.g. data/unoq_mic.f32le captures)
  --mic          live Mac mic through MicTransfer (the UNO Q code path itself)

Dispatch:
  (default)      print each transcript/intent only
  --photon       also iMessage each intent to the configured user (real send)

Examples:
  .venv/bin/python voice_chain_test.py --say "text the user saying the survey is done"
  .venv/bin/python voice_chain_test.py --mic --photon
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
import threading
import time
import wave
from http.server import ThreadingHTTPServer
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "uno_q_field_station" / "python"))

from ground_station.envutil import load_dotenv
from ground_station.sensor_recv import TelemetryStore, make_handler
from ground_station.voice_ingest import IN_RATE, MicSegmenter
from ground_station.voice_scribe import (
    PhotonEchoDispatcher, PrintDispatcher, VoiceCommandService,
)
from mic_transfer import post_mic_chunk

CHUNK_SAMPLES = 1024


def say_to_pcm(text: str) -> bytes:
    """macOS `say` → WAV s16 48 kHz → f32le 48 kHz bytes."""
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        wav_path = Path(tmp.name)
    try:
        subprocess.run(
            ["say", "-o", str(wav_path), "--data-format=LEI16@48000", text],
            check=True, capture_output=True,
        )
        with wave.open(str(wav_path), "rb") as wav:
            if wav.getnchannels() != 1 or wav.getsampwidth() != 2:
                raise RuntimeError("say produced a non mono-s16 WAV")
            s16 = np.frombuffer(wav.readframes(wav.getnframes()), dtype="<i2")
    except subprocess.CalledProcessError as error:
        raise RuntimeError(f"say failed: {error.stderr.decode(errors='replace')[:200]}") from None
    finally:
        wav_path.unlink(missing_ok=True)
    return (s16.astype(np.float32) / 32768.0).astype("<f4").tobytes()


def file_to_pcm(path: Path) -> bytes:
    if not path.is_file():
        raise RuntimeError(f"PCM file does not exist: {path}")
    return path.read_bytes()


def post_pcm(url: str, pcm: bytes, *, pad_silence_s: float = 1.5) -> int:
    """Push PCM through the UNO Q wire contract. Returns chunks sent."""
    silence = np.zeros(int(IN_RATE * pad_silence_s), dtype="<f4").tobytes()
    payload_pcm = pcm + silence
    seq = 0
    sent = 0
    for off in range(0, len(payload_pcm), CHUNK_SAMPLES * 4):
        chunk = payload_pcm[off:off + CHUNK_SAMPLES * 4]
        seq += 1
        ok, err = post_mic_chunk(url, device_id="loopback-test", pcm=chunk, seq=seq)
        if not ok:
            print(f"/mic POST failed at seq {seq}: {err}")
            break
        sent += 1
    print(f"posted {sent} mic chunks ({len(pcm) / 4 / IN_RATE:.1f}s audio)")
    return sent


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--say", metavar="TEXT", help="synthesize via macOS say")
    src.add_argument("--file", type=Path, help="raw f32le 48 kHz PCM file")
    src.add_argument("--mic", action="store_true", help="live Mac mic via MicTransfer")
    p.add_argument("--mic-seconds", type=float, default=8.0)
    p.add_argument("--device", default="MacBook Pro Microphone")
    p.add_argument("--photon", action="store_true",
                   help="iMessage 'text the user …' intents to the configured user")
    p.add_argument("--port", type=int, default=0, help="0 = pick a free port")
    p.add_argument("--threshold", type=float, default=0.02)
    args = p.parse_args()

    load_dotenv()

    dispatcher = PrintDispatcher()
    photon_client = None
    if args.photon:
        from ground_station.photon import PhotonClient, PhotonConfig, PhotonError

        config = PhotonConfig.load()
        if config is None:
            print("Photon is not configured.")
            return 1
        photon_client = PhotonClient(config)
        try:
            photon_client.check()
        except PhotonError as error:
            photon_client.close()
            print(f"Photon route check failed: {error}")
            return 1
        dispatcher = PhotonEchoDispatcher(photon_client)
        print("Photon echo on.")

    outcomes: list[dict] = []
    service = VoiceCommandService(dispatcher, on_intent=outcomes.append)
    inflight = [0]
    _handle = service.handle_utterance

    def counted(device_id: str, wav: bytes) -> dict:
        inflight[0] += 1
        try:
            return _handle(device_id, wav)
        finally:
            inflight[0] -= 1

    service.handle_utterance = counted
    segmenter = MicSegmenter(service.enqueue, peak_threshold=args.threshold)
    service.start()

    with tempfile.TemporaryDirectory() as tmp:
        store = TelemetryStore(Path(tmp) / "t.jsonl", Path(tmp) / "mic.f32le",
                               mic_sink=segmenter.feed)
        server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(store))
        port = server.server_address[1]
        threading.Thread(target=server.serve_forever, daemon=True).start()
        url = f"http://127.0.0.1:{port}/mic"
        print(f"/mic receiver up on {url}")

        try:
            if args.mic:
                from mic_transfer import MicTransfer

                tx = MicTransfer(url=url, device_id="loopback-test",
                                 device=args.device)
                tx.start()
                print(f"Speak now — recording {args.mic_seconds:.0f}s from "
                      f"'{args.device}' through the UNO Q relay path...")
                time.sleep(args.mic_seconds)
                tx.stop()
                print(f"mic transfer: sent={tx.sent} dropped={tx.dropped}")
                segmenter.flush()
            else:
                try:
                    pcm = say_to_pcm(args.say) if args.say is not None else file_to_pcm(args.file)
                except RuntimeError as error:
                    print(str(error))
                    return 1
                if not pcm:
                    print("empty audio source")
                    return 1
                if post_pcm(url, pcm) == 0:
                    return 1

            # Wait until every queued utterance produced an outcome or error.
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline and not service.errors:
                bufs_empty = all(not d.buf for d in segmenter._devices.values())
                if outcomes and bufs_empty and service.utterances.empty() and not inflight[0]:
                    break
                time.sleep(0.2)
        finally:
            server.shutdown()
            segmenter.flush()
            service.close()
            if photon_client is not None:
                photon_client.close()

    if service.errors:
        print("voice service hit errors; see messages above")
        return 1
    for outcome in outcomes:
        print(f"intent={outcome.get('intent')} transcript={outcome.get('transcript')!r} "
              f"result={outcome.get('result')}")
    if not outcomes:
        print("No utterance produced an intent (nothing heard or empty transcript).")
        return 1
    if all(outcome.get("intent") == "empty" for outcome in outcomes):
        print("Scribe returned only empty transcripts.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
