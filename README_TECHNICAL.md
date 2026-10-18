# AgriDrone technical guide

## Provider and authority boundaries

| Concern | Provider / owner |
| --- | --- |
| Operator activation (command chain) | Phone voice → iMessage → Photon inbound → DroidVoiceGateway → Droid |
| Mission reasoning and crop-health image input | Persistent Factory Droid SDK session |
| Field data stream | Drone/camera/sensors → host → optional Droid score + Chalk → store |
| Data-chain alerts and spoken summaries | Photon outbound + ElevenLabs TTS voice notes |
| Laptop voice fallback | ElevenAgents client tools on the ground laptop |
| Optional crop decision features | Chalk; local thresholds otherwise |
| Per-frame detection, RC, hardware and emergency stop | Local Python host |

**Photon cannot place calls.** Urgent means an URGENT text plus an MP3 audio
attachment, not a call. No telephony provider is part of this architecture.

## How Droid is used at runtime

`demo.py` calls `ground_station.voice_mission.main()`. The default is a webcam
mock unless `--allow-real-flight` is explicit. Droid owns high-level decisions
even with `--no-voice`; that flag never grants real-hardware approval.
The legacy direct voice/auto controller is no longer supported.

`DroidWorker` runs one `DroidController` in a background thread, serializing
`Session.stream` turns. Operator activation is a **command chain** separate
from the **data stream**.

Command chain: the operator speaks on the phone. iMessage dictation arrives as
plain text on the Photon direct space; a voice memo arrives as audio on that
same space and may be transcribed with ElevenLabs Scribe. The host maps an
authorized inbound message onto `DroidVoiceGateway` (start queues intent,
status reads a driver snapshot, abort latches host stop). Neither Photon nor
Droid calls the drone adapter. Local `y` still approves real takeoff.

Laptop ElevenAgents is a fallback on the same gateway. `--no-voice` skips
laptop voice and does not disable Photon command ingress.

The data stream never activates a flight. Default mode uses local
AprilTag/YOLO detection, with no cloud crop diagnosis.
`--crop-analysis` enables the data-stream image turn:

```text
camera photo → host capture ID + fixed hold deadline
    → pipeline → same Droid session with native Image
    → get_mission_status
    → get_survey_context(assessment="<strict JSON>")
    → host schema validation + optional Chalk + SQLite
    → decision tool and optional no-argument alert tool
    → matching final JSON → host commits staged effects
    → reimage / return home / landing request
```

The optional assessment string is the same two-field JSON expected as final
output: `{"health_score": 0.0, "analysis_summary": "visible evidence"}`.
The host supplies the field ID, rejects extra fields/nonfinite/out-of-range
scores and blank/oversized summaries, and never clips a bad score.
Context is returned within the same model turn, so the model can use fresh
Chalk/local features without opening another session or decision turn.

Alerts and non-emergency commands are staged until final output validates.
Land/abort always execute immediately. Operator stop, stale captures, expired
holds, invalid final JSON, or failed SDK results cannot commit staged effects.
A successful JSON reply without a decision tool is insufficient.

Native input allows nonempty JPEG, PNG, GIF and WebP at most 5 MiB. Paths are
host-only, never tool parameters. Photo bytes go to Factory, not another
image provider. `--fake-analysis` is an explicit mock fixture, not fallback.
Analysis summaries and history are untrusted data, never tool instructions.

## Local mission and hardware

`mission.py` owns deterministic takeoff, seek/approach target, survey,
turn/search/approach home, landing and abort states. `vision.py` provides
YOLO/ONNX/HSV wheat detection and `apriltag.py` uses OpenCV ArUco tags.
Detections supply geometry, not crop-health scores.

`drone_interface/tello_client.py` wraps `djitellopy` or webcam mock behavior.
`tello_net.py` and `field_links.py` support built-in Mac Wi-Fi for Tello and
USB Personal Hotspot for internet, with a USB Wi-Fi dongle fallback. The UNO Q
joins the phone's Wi-Fi and discovers the laptop on its hotspot LAN.
No validation requires network reconfiguration or hardware.

Safety controls:

- One survey and at most two extra photos per process.
- Real start and extra-photo requests need local `y` approval; 60-second TTL.
- Start approval also checks fresh video and battery.
- Space latches stop; `q`/Escape stops and exits.
- Capture queues, SDK turns and notification queues are bounded.
- Model timeout defaults to 60 seconds; 12 mission calls/turn, 16 turns/process.
- The analysis hold has a fixed 90-second deadline, unchanged by reimages.
- Cloud failure/cancellation, malformed output or no actionable decision
  requests stop/landing; local deadline handling remains independent.
- Cleanup attempts landing after requested takeoff, even if ACK was lost.

These are prototype safeguards, not flight-safety certification.

## Command chain: phone → Photon → Droid

Photon inbound on the configured direct space is the operator activation path.
Authorized plain-text iMessages are command intent. iPhone dictation is text
before Photon sees it. Voice memos are attachments on the same space.

The host, not the model, decides which inbound messages become
`DroidVoiceGateway.request_start`, `request_abort`, or a status snapshot.
Queued start is never takeoff. Spacebar remains the fast emergency stop.
The TypeScript agent in `photon-imessage-agent` is the Photon inbound
bridge: authorized text is POSTed to loopback ingest (`DROID_COMMAND_URL`,
default `http://127.0.0.1:8766/operator-command`) and mapped onto Droid.
Run `bun start` there next to `demo.py` / `voice_mission`. It does not fly
by itself; queued start is not takeoff.

Field PCM from the UNO Q (`sensor_recv` `/mic`) is **data**, not activation.
See [VOICE_CHAIN_SPEC.md](VOICE_CHAIN_SPEC.md).

## Laptop ElevenAgents fallback

`voice_eleven.py` uses the pinned ElevenLabs SDK, an authenticated private
dashboard agent and three locally registered client tools:
`start_survey`, `get_mission_status`, `abort_mission`.
Unexpected tool parameters are rejected. The bridge accepts only a
`DroidVoiceGateway`, not a direct mission object.

`SoundDeviceAudio` reuses callback-based mic/speaker plumbing at the SDK's
16 kHz signed PCM16 mono format. A bounded mic queue moves network sends off
the sounddevice callback. Output buffering preserves partial chunks and
supports interruption. Readiness is signaled only once audio starts, after the
SDK has connected. Startup failures and shutdown close streams.

The dashboard must declare matching client tools, PCM input/output, Scribe
transcription, an ElevenLabs voice, and permit the runtime safety-prompt override.
See [SETUP.md](SETUP.md). Contextual updates are labeled untrusted and do not
impersonate new operator requests. Queued start never means takeoff.

## Data chain: Photon outbound and voice notes

`photon.py` validates an active project/assignment and an existing direct
space with exactly the configured human and agent. It does not create users
or spaces. Message recipients, text formatting and filenames are host-owned.
The no-argument Droid alert tool cannot choose any of them.

`PhotonNotifier` runs off the RC loop, deduplicating events/analysis receipts.
It sends fixed status text and validated analysis text. The model must request
an analysis alert; no score threshold automatically creates one.
Once requested, the host chooses:

- Critical score/Chalk flag: URGENT text, then best-effort voice note.
- Non-critical: text only, or text/audio if the operator passed `--voice-notes`.

`voice_note.py` calls ElevenLabs Flash v2.5 with the same host-formatted alert.
MP3 data stays in memory (3,500 text characters, 5 MiB maximum; no disk files).
Text and TTS attempts are independent; failed/missing TTS is reported without
logging provider bodies, keys or alert content.

Photon attachment uploads use exactly two multipart/related parts, JSON
metadata and file bytes. Idempotency derives from content hash; cached upload
IDs avoid duplicate uploads. Transient uploads retry at most three times;
attachment sends retry at most eight times, including
`409 ATTACHMENT_NOT_READY`, with identical message keys and bodies.
Ordinary text has its own stable key.

Audio is an attachment, not guaranteed to display as Apple's native voice-note
bubble. Acceptance is not delivery. The in-memory queue is best effort and
process exit may leave sends pending. The client also supports image uploads;
crop analysis on the data chain sends the captured JPEG with the weather
insight. Operator activation uses the same space inbound (phone → Photon →
Droid). The TypeScript agent is the inbound bridge to that ingest, not a
connectivity echo. Configured mock missions still send real outbound
messages unless `--no-photon`.

## SDK isolation, features and storage

The session registers only the four `agridrone` mission tools. CLI
Script/ToolSearch exist only as dispatch/discovery for those tools; permission
handling rejects shell/file/foreign MCP actions and startup checks the allowlist.
The temporary working directory is outside the repository. Service variables,
including every `ELEVENLABS_` variable, are cleared for the subprocess.
Factory authentication is preserved. This is not an OS sandbox.

`pipeline.py` logs observations/decisions in local ignored
`data/surveys.sqlite3`, with five-record field-scoped context. With `--chalk`,
`chalk_quality.evaluate_survey_named` validates deployed query results;
failure stops, not silently falls back. Otherwise local score bands remain
healthy ≥ 0.7, watch ≥ 0.4, critical below 0.4.

`agent/store.py` separately offers Chalk Volume snapshots, JSONL decisions
and per-field caches. `chalk_eval_mission.py` is a standalone helper, not called
by the new mission path. The legacy `DroidSession` also remains separate and
loads lazily. No automatic flight resumption is implemented.

Other independent modules include UNO Q telemetry/mic transfer, sensor relays,
wheat model training/export, ggwave `send.py`/`recv.py`, and `grok-team` developer
coordination/publishing. None is an additional mission provider.

## Validation and publication

```bash
python -m unittest discover -s tests -v
```

Default tests use fakes only. They cover the actual worker/mission/driver,
image attachments and strict output, no-narration/stale/stop gates, approvals,
budgets, credential suppression, fake ElevenAgents/audio, TTS HTTP and Photon
multipart/retry/dedupe/urgency. They open no devices and send nothing.

`tests/live_droid_pipeline.py` is separately opt-in and billable: a generated
PNG reaches a real authenticated persistent Factory session, with fake Photon
and ElevenLabs. It is not part of fake-only validation.

Publish only a reviewed exact SHA on `bots/<role>/<task>` through the guarded
publisher, with configured checks, clean owned worktree, correct origin,
credential scan and current `origin/main` ancestry. Never push or merge to main.
