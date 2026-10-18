# Voice command chain spec — UNO Q mic → ElevenLabs → Droid SDK → drone

Purpose: define the voice-command path end to end and certify the
transcription link **before** the UNO Q microphone hardware is pushed, so
that when field testing starts, a failure can be attributed to the mic/relay
segment — not to ElevenLabs or intent handling.

## Chain overview

```
voice → UNO Q mic → UNO Q Linux relay → ground laptop ingest
      → utterance segmentation → ElevenLabs Scribe → intent map
      → DroidVoiceGateway / DroidWorker → Droid SDK tools → drone
```

Voice processing happens in exactly one place: an ElevenLabs call on the
ground laptop. The UNO Q is a dumb PCM pipe. Droid remains the only
mission-decision maker; voice submits *intent*, the host owns approval.

## Segment status

| # | Segment | Status | Code |
|---|---------|--------|------|
| 1 | Mic capture on UNO Q (f32le, 48 kHz, chunk 1024, mono) | Exists | `uno_q_field_station/python/mic_capture.py` |
| 2 | Transfer to laptop: `POST /mic`, JSON + base64 PCM, `seq` | Exists | `uno_q_field_station/python/mic_transfer.py` |
| 3 | Laptop ingest: `/mic` → append `data/unoq_mic.f32le` + meta jsonl | Exists | `ground_station/sensor_recv.py` `ingest_mic` |
| 4 | Seq-ordered reassembly → utterance segmentation (VAD) → WAV | **Built** | `ground_station/voice_ingest.py` (`MicSegmenter`), wired via `sensor_recv --voice` |
| 5 | ElevenLabs transcription | **Proven** end-to-end (Scribe file mode) | `voice_scribe.py transcribe`; agent path: `voice_eleven.py` |
| 6 | Transcript → intent → Droid queue | **Built** for intents; freeform/text need gateway methods | `voice_scribe.py map_intent`, `worker.py DroidVoiceGateway` |
| 7 | Droid SDK session → mission tools → drone | Exists | `agent/controller.py`, `agent/driver.py` |

## Wire contract (segment 2 — already implemented, do not change)

`POST http://<laptop>:8765/mic`, `Content-Type: application/json`:

```json
{
  "device_id": "unoq-field-01", "source": "unoq_mic",
  "format": "f32le", "sample_rate": 48000, "channels": 1, "chunk": 1024,
  "seq": 1, "ts": 1729..., "audio_b64": "<base64 PCM>"
}
```

Notes: chunks are 1024 float32 samples (~21 ms). `seq` increments per chunk;
drops are possible (UDP-like semantics — tolerate gaps, do not assume
contiguity). Offline fallback on the UNO Q appends to
`data/mic_offline.f32le` **without** seq/ts — replayed audio is opaque.

## Ground-station ingest (segment 4 — to build)

Extend `sensor_recv` (or a sibling consumer of the same `/mic` handler) so
accepted PCM is also pushed to a segmenter, not only to the raw file.

- **Reassembly**: order by `seq` per `device_id`; reset on `seq` regress or
  >2 s gap. A lost chunk is an audio glitch, not an error — keep going.
- **Segmentation** (energy VAD, same approach validated in
  `voice_photon_test.py`): utterance starts when chunk peak > threshold,
  ends after ~1.2 s below it. Cap utterances at 15 s. Tune threshold on the
  UNO Q mic's noise floor, not the Mac's.
- **Conversion**: f32le 48 kHz → s16le 16 kHz WAV (3:1 decimation is fine
  for speech) → bytes for Scribe.
- **Output**: utterance queue — the transcription stage consumes it. Raw
  file dump stays as debug/audit.

## Transcription (segment 5)

Two modes; **Mode A is the certification target now**, Mode B stays the
dashboard production path.

- **Mode A — Scribe file STT** (proven today): per-utterance
  `client.speech_to_text.convert(model_id="scribe_v1", file=<wav>,
  language_code="eng")`. Needs only `ELEVENLABS_API_KEY` (set in `.env`).
  One billable request per utterance. No partial transcripts — transcript
  arrives after utterance end + ~1–3 s.
- **Mode B — ElevenAgents session** (existing `voice_eleven.py`):
  persistent conversational session, Scribe inside, client tools mapped to
  `DroidVoiceGateway`. Feed relayed PCM via a queue-backed `AudioInterface`
  (drop-in for `SoundDeviceAudio`'s input side); keep Mac speaker or discard
  outbound audio. **Blocked on `ELEVENLABS_AGENT_ID` + dashboard client-tool
  declarations** — not required for certification.

## Intent mapping (segment 6)

Transcript is the operator-instruction channel; it still passes through the
existing gates (queued intent, local `y` approval for real starts/photos,
Space = hardware stop).

| Transcript pattern | Action |
|---|---|
| `abort`, `stop`, `land now`, `emergency` | `driver.stop()` **immediately** — bypasses Droid latency (parity with `request_abort`) |
| `status` / `what's the status` | `gateway.snapshot()` → log/speak verified phase |
| `survey`, `start`, `go fly`, `find crop` | `worker.submit("The operator requested a survey…", {"kind": "voice_start"})` |
| anything else | `worker.submit(transcript, {"kind": "voice_text"})` — Droid interprets within tool limits |

Abort ordering rule: run the abort regex on the transcript **before** any
Droid submission. File-mode Scribe has no partials, so abort latency is
utterance-end + STT (~2–4 s). If sub-second voice abort becomes a
requirement, that is the Mode B streaming path — out of scope for cert.

## Latency budget (Mode A)

| Hop | Budget |
|---|---|
| Utterance detect (silence tail) | ~1.2 s |
| Scribe transcribe | ~1–3 s |
| Intent map | <10 ms |
| Droid turn (non-abort) | seconds, per `--droid-timeout` |
| **Voice "abort" → drone stop** | ~2–4 s end to end |

## Certification test plan

Goal of each phase is to shrink the set of unproven segments so that a field
failure has exactly one place left to be.

| Phase | Audio source | Path exercised | Certifies | Status |
|---|---|---|---|---|
| 0 | MacBook mic, direct | mic → Scribe → Photon iMessage | API key, Scribe quality, intent→send path | **Done** (`voice_photon_test.py`: transcript "text the user saying the survey is done" → iMessage delivered) |
| 1 | MacBook mic, via **loopback relay** | `mic_transfer`/`post_mic_chunk` → `POST 127.0.0.1/mic` → reassembly → VAD → resample → Scribe → intent → (mock / Photon echo) | Every byte-level contract the UNO Q will produce: f32le/48k/chunk-1024/base64/seq + segmentation + resample | **Done** — `voice_chain_test.py` verified `text_user` and `abort` intents end-to-end |
| 2 | Recorded fixtures through Phase 1 path | Golden utterance corpus × noise/distance variants | Transcript accuracy + latency, repeatably, without hardware | To run |
| 3 | Real UNO Q mic over hotspot | Full chain | Only new variables: mic element, board timing, Wi-Fi loss | After teammates push |

Phase 1 needs no UNO Q: `mic_transfer.MicTransfer` already takes a `device=`
parameter — point it at the Mac's mic and at `http://127.0.0.1:8765/mic`
while `sensor_recv` runs locally. The UNO Q Python code runs unmodified.

**Phase 2 acceptance criteria** (tune numbers with the team):

- Command set: "survey the wheat", "start survey", "abort", "stop",
  "land now", "what's the status", "send the user a message".
- Clean speech, 1 m: ≥ 95% correct command classification; abort/stop
  variants 100%.
- Noisy/distant: report command-match rate; no abort miss tolerated.
- Measure p50/p95 utterance-end → transcript latency; confirm abort stays
  within budget.
- Fixtures saved as f32le 48 kHz raw (same as `data/unoq_mic.f32le`) so the
  corpus replays through the identical ingest.

## Remaining work

1. **Mission integration** — instantiate `VoiceCommandService` with a real
   `DroidVoiceGateway` inside the mission process (the standalone
   `sensor_recv --voice` path only simulates or echoes intents).
   `DroidVoiceGateway` currently lacks `submit_instruction` and `text_user`,
   so freeform transcripts and "text the user" intents report
   `no_instruction_sink`/`no_text_user` until those methods are added.
2. **Phase-2 fixture corpus** — golden utterances through
   `voice_chain_test.py --file`/`--mic` for repeatable accuracy + latency
   numbers.
3. **Hardware run** — real UNO Q mic on the hotspot; tune `MicSegmenter`
   `peak_threshold` / `--threshold` to the board's noise floor.

## Non-goals / safety invariants (unchanged)

- No flight without `--allow-real-flight` + local `y` approval; voice only
  ever queues intent.
- Space/q and `driver.stop()` stay the hard stops; a transcript that says
  "resume" after stop is data, not a resume command (stopped missions do not
  resume — AGENTS.md).
- No new credentials: Mode A needs only `ELEVENLABS_API_KEY`.
- UNO Q stays a dumb PCM pipe; do not add VAD or segmentation on the board
  (keeps board firmware/deps minimal and all tuning on the laptop).
