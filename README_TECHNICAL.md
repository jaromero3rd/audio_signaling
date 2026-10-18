# AgriDrone technical guide

## Provider and authority boundaries

| Concern | Provider / owner |
| --- | --- |
| Operator activation (command chain) | Phone voice → iMessage → Photon inbound → DroidVoiceGateway → Droid |
| Mission reasoning and crop-health image input | Persistent Factory Droid SDK session |
| Field data stream | Drone/camera/sensors → host → optional Droid score + Chalk → store |
| Data-chain alerts and spoken summaries | Photon outbound + ElevenLabs TTS voice notes |
| Optional crop decision features | Chalk; local thresholds otherwise |
| Per-frame detection, RC, hardware and emergency stop | Local Python host |

**Photon cannot place calls.** Urgent means an URGENT text plus an MP3 audio
attachment, not a call. No telephony provider is part of this architecture.

## HackHarvard prize mapping

Judge index for [hhuh.io](https://hhuh.io/) tracks and sponsor challenges.
Runtime protocol stays in the sections below.

### Factory Droid SDK

Droid does real work in the running product. `demo.py` →
`ground_station.voice_mission.main()` → `DroidWorker` owns one
`DroidController` and one long-lived `droid_sdk.Session`
(`ground_station/agent/controller.py`). The session registers the
`agridrone` MCP server with four tools:

| Tool | File / contract |
| --- | --- |
| `get_mission_status()` | Host driver snapshot |
| `issue_drone_command(drone_id, action)` | start_survey, capture_photo, continue_survey, land, abort |
| `get_survey_context(assessment=None)` | Validated score + optional Chalk + last five SQLite rows |
| `send_photon_alert()` | No-argument request; host owns text, recipients, JPEG |

`--crop-analysis` attaches the survey JPEG as native
`Image.from_bytes(...)` on that same session (JPEG/PNG/GIF/WebP, nonempty,
≤ 5 MiB). Host YOLO/AprilTag in `vision.py` / `apriltag.py` owns per-frame
steering. Droid never chooses file paths or RC sticks. Startup checks the
tool inventory. See **How Droid is used at runtime** below.

### Chalk

`chalk/features.py` defines `FieldSurvey` online resolvers:
`compute_health_flag`, `compute_severity`, `compute_recommended_action`, and
`justify_image_with_live_weather` (30 s timeout). The last resolver calls
`chalk/weather_live.py` `fetch_weather` / `justify` and writes `insight` plus
`justified_action`. Named query `evaluate_field_survey` is in
`chalk/named_queries.py`. Host path: `ground_station/chalk_quality.py` with
`--chalk` (fail closed). Labeled agent evals: `python -m ground_station.chalk_eval`
(`ground_station/chalk_eval.py`). Joined records go through `agent/store.py`
when `CHALK_CLIENT_ID` is set.

### SpaceXAI

This repo was built in Cursor. Space data in the product is NASA POWER
hourly `ALLSKY_SFC_SW_DWN` (satellite-derived all-sky surface shortwave,
community `AG`) from `https://power.larc.nasa.gov/api/temporal/hourly/point`,
implemented in `weather_live._nasa()`. NOAA NWS observations/forecast and
Open-Meteo GFS precipitation/cloud/shortwave are fetched in the same
`fetch_weather` call. `justify()` uses that irradiance to support or
confound the canopy score (dark/cloud → reimage; bright-dry stress → keep
the photo action). This is a query-time satellite product, not a radio
downlink on the airframe. `grok-team/` is optional Grok CLI coordination
and is not a mission provider. Runtime TTS is ElevenLabs Flash v2.5.

### Hardware and field sensors

| Piece | Role | Path |
| --- | --- | --- |
| DJI Tello / webcam mock | Aerial still + RC | `drone_interface/tello_client.py` |
| Arduino UNO Q | Field station MCU + Linux | `uno_q_field_station/` |
| DHT11 on **D2** | Temp °C/°F, RH %, heat index | `uno_q_field_station/WIRING_DHT11.md` |
| LDR on **A0** | Light proxy | same |
| microSD **D10** | Offline env log | MCU sketch |
| Laptop `sensor_recv` | `POST /telemetry`, `GET /latest` | `ground_station/sensor_recv.py` |

The UNO Q posts sensor rows; it does not activate the Tello. Operator
start/abort stays on the Photon command chain.

### Sustainability / ASUS

Wheat-head YOLO was trained on ASUS hardware
(`ground_station/train_wheat.py`, `wheat_detection/train.py` on Global Wheat
Head Dataset 2021), exported to `models/wheat_*.onnx`, and run on the host
with `--target wheat`. Crop-health JSON plus Chalk weather/satellite
justification is the targeted follow-up path (alert / reimage / continue).
ASUS content challenge is public social posts
with `#ASUSEducation` `#hackharvard2026` `#agentcomputer`.

### Photon 2.0

Inbound: `photon-imessage-agent` on the drone laptop, authenticated
`127.0.0.1:8787/command` and `/intent`. Outbound: `ground_station/photon.py`
status text, analysis JPEG, optional ElevenLabs MP3. Same direct iMessage
space.

## How Droid is used at runtime

`demo.py` calls `ground_station.voice_mission.main()`. The default is a webcam
mock unless `--allow-real-flight` is explicit. Droid owns high-level decisions
without laptop voice. `--no-voice` is a legacy no-op and never grants approval.
The legacy voice/auto controller is no longer supported.

`DroidWorker` runs one `DroidController` in a background thread, serializing
`Session.stream` turns. Operator activation is a **command chain** separate
from the **data stream**.

Command chain: the operator speaks on the phone. iMessage dictation arrives as
plain text on the Photon direct space; a voice memo arrives as audio on that
same space and may be transcribed with ElevenLabs Scribe. The host maps an
authorized bounded command onto `DroidVoiceGateway` (start queues intent,
abort/land latch host stop). Neither Photon nor Droid calls the drone adapter.
Opt-in phone start replaces local `y` only after camera/battery preflight.

Phone ingress requires explicit opt-in on both processes. Outbound Photon
configuration alone never opens a mission command listener.

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
- Real starts/photos need local `y`; an opt-in authenticated phone start
  supplies one-shot start authorization only. Approval TTL is 60 seconds.
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
`DroidVoiceGateway.request_start` or `request_abort`.
Queued start is never takeoff. Spacebar remains the fast emergency stop.
The TypeScript agent in `photon-imessage-agent` is the Photon inbound
bridge: only exact start/abort/land utterances are POSTed as bounded JSON
to `127.0.0.1:8787/command`, with a shared bearer token. Run it on the
drone-connected laptop. Python `--phone-commands` and Bun
`COMMAND_BRIDGE_ENABLED=true` are both required; default is no listener
and no forwarding. `--phone-commands` waits rather than starts.
Neither the old unauthenticated `operator_http` listener nor the standalone
Python `PhotonInbound` subscriber is started. Only Bun subscribes for commands.

The original Photon receipt time must be within 30 seconds, with 5 seconds
future skew. Python rechecks timestamps, exact schema and ID, authenticates
with constant-time comparison, limits bodies to 1 KiB and deduplicates up
to 256 expiring receipts. Bun uses a 2.5-second timeout and no redirects.
The host authorizes one start for 60 seconds and the camera loop consumes it
only after fresh video and battery ≥ 20%; real extra photos still need `y`.
Abort/land latch stop without a Droid turn and revoke outstanding starts.
Receipts are not a durable command queue; no automatic flight resumes.
See [setup](SETUP.md#enable-the-same-laptop-bridge).

Field PCM from the UNO Q (`sensor_recv` `/mic`) is **data**, not activation.
See [VOICE_CHAIN_SPEC.md](VOICE_CHAIN_SPEC.md).

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
budgets, credential suppression, authenticated phone ingress, TTS HTTP and Photon
multipart/retry/dedupe/urgency. They open no devices and send nothing.

`tests/live_droid_pipeline.py` is separately opt-in and billable: a generated
PNG reaches a real authenticated persistent Factory session, with fake Photon
and ElevenLabs. It is not part of fake-only validation.

Publish only a reviewed exact SHA on `bots/<role>/<task>` through the guarded
publisher, with configured checks, clean owned worktree, correct origin,
credential scan and current `origin/main` ancestry. Never push or merge to main.
