# AgriDrone: Technical Reference

**For the overview, pitch, and quick start, see [README.md](README.md).**

This document covers implementation details, integration status, code structure, and limitations for each component.

## Implementation Map

This table shows what code exists and what's wired together:

| Module | What exists | Current integration boundary |
|--------|-------------|------------------------------|
| [Ground-station mission](#ground-station-mission-and-drone-interface) | Tello/webcam adapter, local control, capture and optional analysis hold | Default Droid control and mock hardware; explicit legacy voice/auto path |
| [Wheat AprilTag detection](#wheat-head-and-apriltag-detection) | Live ONNX/`.pt`/AprilTag detection, standalone image inference, training/export scripts | Finds target bounding boxes; does not produce crop-health scores |
| [ElevenLabs voice](#elevenlabs-voice) | Microphone speech-to-text and spoken phone summary | Transcript goes to Droid; stop stays local |
| [Chalk feature evaluation](#chalk-feature-evaluation) | Image-only labels, live-weather resolver, named-query helper | Crop analysis attempts a weather query even without `--chalk`; local live-weather fallback |
| [Chalk mission state](#chalk-volume-backed-mission-state) | Snapshot helpers, decision log, latest-survey cache | Crop pipeline attempts survey/cache writes; no automatic flight-snapshot save or restore |
| [Grok team coordination](#grok-team-coordination) | Local CLI queue/history and checked Git publisher | Independent developer service; publisher can push bot branches, never main |
| [Droid and Photon](#how-droid-is-used-at-runtime) | Persistent SDK, optional photo assessment, restricted tools, host text notifier | Separate crop-summary bridge can also send photo/audio attachments |
| [UNO Q sensors](#uno-q-field-stations) | Bluetooth sensors and a separate HTTP telemetry/microphone receiver | Not joined into the active crop pipeline or mission speech input |
| [Audio signaling](#standalone-audio-signaling) | Speaker-to-microphone ggwave text sender/receiver | Independent utilities; not connected to survey mission |

**Default versus optional:** Droid makes high-level mission requests while local vision steers. Photo assessment, weather context, SQLite survey history, Volume writes, and crop-summary delivery are enabled only by the Droid crop-analysis path. Visual health estimates are not verified diagnoses.

## Current Runtime Flow

The default Droid path, with no crop analysis, is:

```text
Operator speech → ElevenLabs scribe_v1 → text
        ↓
Persistent Factory Droid tools and host approval
        ↓
Local SurveyMission state machine
        ↓
Webcam mock or Tello camera
        ↓
YOLO wheat-head or AprilTag detection
        ↓
Local steering → photo capture → home search → landing
```

With `--crop-analysis` (or `--fake-analysis` for a mock score), photo capture instead enters:

```text
Saved photo → await_analysis (fixed 90-second deadline)
        ↓
Droid scores the attached JPEG (structured SurveyAnalysis; host keeps field_id)
        ↓
Host accepts the current capture; Chalk weather query or local live-weather fallback
        ↓
Best-effort Chalk Volume writes and separate phone-summary send
        ↓
SQLite observation → same Droid session reviews survey context
        ↓
Bounded reimage / text alert / return home / land → SQLite decision
```

Photo handling currently:
- Saves the still image to disk
- Records its path in the mission log
- With `--crop-analysis`, attaches that file to the Droid session (5 MiB JPEG/PNG/GIF/WebP limit)

The active mission does not call `api.x.ai`. `GrokImageAnalyzer`, `voice_grok.py`, and other older xAI helpers remain available but are not selected by this launcher. Explicit `--controller voice` uses the local state machine directly and ElevenLabs for microphone transcription; it does not construct the SDK crop pipeline.

`--fake-analysis` replaces only the photo assessment. It does not disable Factory inference, public weather calls, Volume writes, or the phone-summary sender. Use mocked providers in the offline tests for an isolated pipeline check.

## Ground-station mission and drone interface

**Source**: [demo.py](demo.py), [voice_mission.py](ground_station/voice_mission.py), [mission.py](ground_station/mission.py), [tello_client.py](drone_interface/tello_client.py), [control.py](ground_station/control.py).

### Overview

`demo.py` is the entrypoint that delegates to the ground-station layer. `SurveyMission` represents the survey as explicit phases:

- **Takeoff**: Motor start and altitude gain
- **Target search**: Rotate and scan for a target (wheat or AprilTag)
- **Target approach**: Move closer while keeping the target centered
- **Photo capture**: Stabilize and snap an image when the target is in frame and well-sized
- **Optional analysis hold**: Hover while the crop pipeline runs; abort when the fixed deadline expires
- **Home search**: Turn and scan for the home marker (target and home both default to tag 12)
- **Home approach**: Move back to base
- **Landing or abort**: Safe touchdown or manual recovery

Each camera-loop tick produces bounded yaw, forward, vertical, and lateral steering values. The entrypoint sends these to either a `TelloDrone` adapter (for live flight) or a `MockDrone` (for simulation). Droid requests mission-level actions, not raw remote-control values.

### Drone Adapters

**TelloDrone**: Uses `djitellopy` for:
- Camera stream capture (RGB frames from Tello's onboard camera)
- Remote-control commands (takeoff, land, move in body-relative directions)
- Battery status reads
- Photo capture and local storage

This is a Tello-specific adapter, not a general DJI UAV interface or waypoint planner.

**MockDrone**: Uses the laptop's webcam and prints simulated flight commands to stdout instead of flying. Useful for testing without hardware.

### Control Layer

The controller (`control.py`) uses the target's position and apparent size in the image to calculate bounded steering commands:
- **Yaw**: Rotate toward the target's center-x
- **Forward**: Approach the target if it's far; back away if it's too close
- **Vertical**: Adjust altitude toward target center-y

The mission also handles frame-absence timeouts. If no target is seen for N frames, the mission enters search mode.

The active survey loop uses `mission.py` and `control.py` for steering. [steer_wheat.py](ground_station/steer_wheat.py) and [steer_tag.py](ground_station/steer_tag.py) are separate steering entrypoints, not the mission's control backend.

## Wheat-head and AprilTag detection

**Source**: [vision.py](ground_station/vision.py), [apriltag.py](ground_station/apriltag.py), [wheat_detection/](wheat_detection/) directory.

### Wheat-head Detection

Two paths exist:

1. **Live mission path**: `YoloWheatHeadDetector` uses ONNX Runtime for `.onnx` files or Ultralytics for `.pt` files, predicts wheat-head bounding boxes, and combines boxes into a target region for steering.
   - Default weights: `models/wheat_detection.onnx` when present, otherwise `models/wheat_detection.pt`.
   - With `--target auto`, a missing checkpoint selects AprilTag detection.
   - Explicit `--target wheat` without weights selects a simple HSV color detector for computer tests, not trained wheat detection.
   - This path **does not** export to ONNX automatically.

2. **Standalone ONNX inference**: [detect.py](wheat_detection/detect.py) uses ONNX Runtime with [models/wheat_detection.onnx](models/wheat_detection.onnx).
   - Resizes and pads images
   - Filters detections by confidence
   - Removes overlapping boxes
   - Returns bounding boxes in original-image coordinates
   - The live mission reuses this detector when its selected weights are ONNX.

### AprilTag Detection

Uses OpenCV's AprilTag dictionaries. Tags can serve as both survey targets (wheat stand-in) and home markers.

- [print_apriltag.py](ground_station/print_apriltag.py): Generates printable markers (family `36h11` recommended).
- An HSV color-based detector is also available for manual wheat-steering demo, but it is **not** a crop-health model.

### Important Limitation

**Detection is not crop-health analysis.**

Bounding boxes and head counts do not supply health scores. With `--crop-analysis`, `DroidController.assess_photo()` uses `session.stream(..., images=[Image.from_path(...)], output=SurveyAnalysis)`. The host validates a finite score in `[0, 1]`, a 1–3,000-character summary, and the host-owned `field_id`. Attachments must be nonempty JPEG/PNG/GIF/WebP files no larger than 5 MiB. The selected Droid model must support images and structured output; there is no automatic second vision provider.

`--fake-analysis` skips this assessment call and returns a fixed score of `0.8` with a `SIMULATED` summary. It is rejected with real hardware.

## ElevenLabs voice

**Source**: [voice_eleven.py](ground_station/voice_eleven.py).

### Overview

The laptop records a silence-delimited utterance and posts it to ElevenLabs speech-to-text (`scribe_v1`). Only explicit commands such as "survey the wheat", "start survey", or "take off" queue a start. Status questions and negated start requests do not authorize flight. Stop / abort / land / emergency keywords stop locally; other sentences become status turns for Droid.

Recording uses 16 kHz mono PCM, a 0.7-second silence boundary, and a maximum utterance of about 15 seconds. This is an utterance-based HTTP call, not the older realtime Grok WebSocket bridge.

The spoken phone summary is a separate ElevenLabs text-to-speech call (`eleven_multilingual_v2`) made while Chalk, or the laptop fallback, delivers the insight. Phase chatter is printed on the laptop and is not spoken to the phone.

### Prerequisites

- `ELEVENLABS_API_KEY` when voice is on.
- `ELEVENLABS_VOICE_ID` for the spoken summary.
- Microphone access. `--no-voice` skips speech-to-text.

### Limitations

- ElevenLabs does not fly and does not see the photo.
- `voice_grok.py` remains in the tree and is not started.
- A spoken abort bypasses Droid reasoning **after transcription**, but still depends on the microphone and ElevenLabs response. Space in the focused camera window is the cloud-independent stop.
- `--no-voice` disables microphone transcription, not summary text-to-speech, and changes real-flight approval behavior as described below.

## Chalk Feature Evaluation

**Source**: [features.py](chalk/features.py), [weather_live.py](chalk/weather_live.py), [chalk_client.py](ground_station/chalk_client.py), [named_queries.py](chalk/named_queries.py), [chalk_quality.py](ground_station/chalk_quality.py), [chalk_eval.py](ground_station/chalk_eval.py).

### Survey Schema

`FieldSurvey` defines a single survey record with:
- `id`: Unique survey identifier (UUID)
- `field_id` / `drone_id`: Mission context
- `photo_url`: Reference to the captured image
- `health_score`: Supplied crop-health estimate (0.0–1.0), not calibrated confidence
- `analysis_summary`: Human-readable findings or model output
- `latitude` / `longitude`: Field point for the live-weather resolver. The mission default is the David Rubenstein Treehouse.
- `ts`: Feature timestamp
- Decision-related outputs (feature flags, action recommendations)
- `insight` and `justified_action`: Weather explanation of the image score
- Weather measurements/timestamps, `weather_error`, and `delivery_error`

### Feature Computation

`evaluate_survey()` sends the Droid score to a deployed Chalk feature service. Image-only queries request three computed features:

| Feature | Resolver behavior |
|---------|-------------------|
| `health_flag` | `healthy` if score ≥ 0.7, `watch` if score ≥ 0.4 but < 0.7, else `critical` |
| `severity` | 1.0 − health_score, clamped [0, 1], rounded to 3 decimals |
| `recommended_action` | `continue`, `reimage`, or `alert-operator`, based on health_flag |

These are deterministic online resolvers. Chalk does **not** examine the photo or generate a health score; the caller must supply the analysis. Image-only action labels do not execute commands. The separate weather resolver also attempts phone delivery, so a weather-enabled query is not necessarily read-only.

### Live Weather Context

`justify_image_with_live_weather` fetches NWS, GFS, and NASA data, writes `insight` and `justified_action`, and attempts the phone send. An empty `delivery_error` means the sender reported acceptance, not confirmed delivery. The laptop attempts delivery when the weather source is local, the field is absent, or it reports an error. No weather dataset is checked into the repository.

The default point is the David Rubenstein Treehouse, 20 Western Avenue, Allston, at 42.3635, -71.1253. If `api.weather.gov/points` rejects that coordinate, the resolver refetches Harvard Yard at 42.3744, -71.1169 and the insight says the Treehouse grid was unavailable.

Three public sources are read at query time:

- NOAA NWS forecast and the latest station observation from `api.weather.gov`. No key. The client sends the User-Agent `AgriDrone HackHarvard 2026 (field survey)`.
- NOAA GFS through Open-Meteo `models=gfs_seamless`: precipitation, cloud cover, and shortwave radiation. Open-Meteo data is CC BY 4.0. GFS assimilates satellite observations. It is a model, not a raw satellite image.
- NASA POWER hourly `ALLSKY_SFC_SW_DWN`, satellite-derived surface sunlight. The newest valid hour inside the last 120 days is kept, and the insight cites that timestamp. This source can lag substantially; it is not guaranteed to describe current conditions.

Current sunlight rules prefer GFS over lagged NASA values. `justified_action` can request a reimage for a healthy score under poor light, or for a critical score with storm/high-wind interference. It does not replace `health_flag`, `severity`, or `recommended_action`; Droid receives the combined context and still chooses bounded actions.

The launcher constructs `SurveyPipeline(weather=True, deliver=True)` whenever Droid crop analysis is enabled. It calls `evaluate_survey(..., include_weather=True)` even without `--chalk`. If the query fails or omits the insight, the laptop uses the same live-weather functions and labels the source `local live weather`; unavailable weather leaves an image-only explanation.

**Flag boundary:** the `--chalk` named-query branch runs only when the pipeline's `weather` option is false. The current launcher's weather path does not forward `--chalk-branch` and does not use `evaluate_survey_named()` validation. Do not assume those flags select or strictly validate the active weather query.

Cloud feature deployment, outbound weather access, and summary-delivery dependencies must be verified separately. A cloud resolver receives the laptop's photo path as a string, not an uploaded image; that file normally does not exist in Chalk. The local sender can use the actual capture. Weather, Volume, and summary failures are best effort, but their latency can still consume the fixed analysis-hold deadline.

```bash
python -m ground_station.weather_smoke
```

This command calls public weather APIs, prints current readings and sample justifications, and needs no Chalk credentials. It does not invoke the delivery resolver or send phone messages.

### Feature Deployment

Feature definitions are deployed separately via the [chalk/](chalk/) directory and Chalk CLI:

```bash
cd chalk && chalk apply --branch schedtest
```

Simply calling the Python helper does **not** deploy resolvers or configure storage. You must have a Chalk workspace and the CLI installed. `chalk/chalk.yaml` selects Python 3.12. `schedtest` is a development branch used by the smoke helper, not evidence that the current weather features are deployed or that the mission queries that branch.

### Named Query, Monitoring, Evaluation

- `evaluate_field_survey`: Preserves the image-only inputs and three decision outputs; no coordinates or phone-send side effects.
- `evaluate_field_survey_weather`: Separate weather-enabled query with coordinates, context, and best-effort summary delivery.
- `evaluate_survey_named()`: Selects the weather query only with `include_weather=True`, checks score ranges, and validates image-label outputs. Both query definitions require deployment before use.
- Monitoring definition: Charts the named query's success ratio (not crop-model accuracy or mission completion).
- Daily heartbeat resolver: Declared in `chalk/named_queries.py` as an `@online(cron="1d")` resolver; existence does not prove it's scheduled.

### Smoke Tests

[chalk_eval.py](ground_station/chalk_eval.py): Runs eight labeled boundary cases and checks the returned flags, severity values, and actions. It exits nonzero on any mismatch, resolver error, or failed query.

[chalk_quality.py](ground_station/chalk_quality.py): Named-query smoke call explicitly targeting the `schedtest` branch.

These are cloud evaluation calls, not offline unit tests. The `schedtest` branch is a development branch; production availability is not guaranteed.

### Important Limitation

Feature resolvers compute query-time outputs. The repository's helper notes describe a Chalk environment without an online/offline feature store; resolver code alone does not establish durable feature history.

The mission separately persists SQLite history and attempts Volume writes (below). A Volume record is not a configured feature time-series store.

## Chalk Volume-backed Mission State

**Source**: [ground_station/agent/store.py](ground_station/agent/store.py). Background caveats in [ground_station/agent/NOTES.md](ground_station/agent/NOTES.md).

### Overview

This module provides durable JSON-file storage via Chalk's Volume gRPC API:

| Stored file | Purpose |
|-------------|---------|
| `mission_state.json` | Generic mission snapshot helper; not automatically written by the current launcher |
| `decision_log.jsonl` | Append-style decision log, including the crop pipeline's survey records |
| `fields/<field_id>.json` | Most recently cached joined survey result per field |

Volume is created on first use if it doesn't exist. Environment variable `CHALK_STATE_VOLUME` overrides the default name (`droid-mission-state`).

### Public API

- `load_mission_state()`: Fetch the latest mission state.
- `save_mission_state(state)`: Overwrite the mission state.
- `append_decision(entry)`: Append a decision log entry.
- `decision_log()`: Fetch all decisions since mission start.
- `get_last_survey(field_id)`: Get the most recent survey result for a field.
- `set_last_survey(field_id, result)`: Cache a survey result.

### Important Limitations

- This is **remote object storage**, not a hosted AI agent session or online feature store.
- Appending a decision **rewrites the entire log** on each write; concurrent writers are not coordinated.
- The implementation relies on private Chalk SDK channel access and generated gRPC types. SDK compatibility must be checked when dependencies change.

### Integration Status

With crop analysis enabled, `SurveyPipeline._store_volume()` calls `set_last_survey()` and `append_decision()` after the weather join. It checks for `CHALK_CLIENT_ID` before trying; the write still requires valid Chalk authentication and Volume access. Missing credentials or failed writes become `volume_error` rather than aborting the mission.

The launcher does not call `save_mission_state()` or `load_mission_state()`. These survey records do not restore flight state or resume a mission. Local observations and Droid decisions are stored separately in `data/surveys.sqlite3`.

## Grok Team Coordination

**Source and operating guide**: [grok-team/README.md](grok-team/README.md).

### Overview

A separate Bun/TypeScript service that launches the installed Grok CLI on demand with five specialized roles:
- **Coordinator**: Orchestrates multi-agent tasks.
- **Drone**: Suggests flight plans and safety checks.
- **Logistics**: Estimates time, distance, battery.
- **Vision**: Analyzes photos for crop health.
- **Release**: Manages version control and deployment.

Each role runs as an independent CLI invocation. Handoff depth is bounded to prevent infinite agent loops.

### Data Storage

Private SQLite database stores:
- Job queue and history
- Shared conversation context across agents

### Constraints

These agents are **local CLI coordination tools**:
- No shell, filesystem, GitHub, messaging, or hardware access.
- Not provisioned as cloud Grok Bots.
- Not embedded in the drone runtime.

### Discord Collector (Optional)

An optional Discord collector reads selected recent announcements from configured announcement channels and provides them as untrusted reference material. It does **not** post replies or operate the mission. Collector is not connected by default; merely having the code does not enable it. Bot authorization and channel configuration require separate setup steps.

### Git Publisher (Host-Controlled)

A deterministic publisher can check committed changes in owned worktrees and push exact checked SHAs to an allowed bot branch. This is a **host-controlled Git workflow**, not model-controlled flight logic. It does not automatically merge or push to `main`. Credentials and state data remain private; follow the package guide for setup and validation.

## How Droid Is Used at Runtime

The embedded controller is implemented in [agent/controller.py](ground_station/agent/controller.py), with a mock-first [session launcher](ground_station/agent/session.py). [worker.py](ground_station/agent/worker.py) owns its long-lived session on a background thread. The older `droid_session.py` is not the active controller.

One `droid-sdk[mcp]==0.5.0` session handles initial instructions, voice requests, optional photo assessment, and subsequent survey decisions. The camera/RC loop remains separate from model latency.

| Mission tool | Authority |
|--------------|-----------|
| `get_mission_status()` | Reads logical mission state, limits, stop latch, and pending approval; not independent physical telemetry |
| `issue_drone_command(drone_id, action)` | Only `start_survey`, `capture_photo`, `continue_survey`, `land`, or `abort` for the configured drone |
| `get_survey_context()` | Current validated assessment/features and the latest five field-scoped SQLite records, or a placeholder when no pipeline is configured |
| `send_photon_alert()` | Queues current-analysis text through the host notifier; accepts no recipients, paths, or arbitrary text and does not confirm delivery |

The session restricts execution to these mission tools plus SDK `ToolSearch`/`Script` support for invoking them. Shell, file tools, task delegation, and foreign MCP servers are not authorized. Startup checks the effective tool inventory. The CLI runs in a temporary directory with known unrelated service credentials cleared; this is a tool-policy boundary, not an OS sandbox.

The host permits one survey, two extra photos, 12 mission-tool calls per turn, and 16 model turns. Photo-assessment turns also consume that turn budget. The default turn timeout is 60 seconds. During crop analysis the local loop hovers with a fixed 90-second deadline; reimages never extend it. Invalid analysis, controller failure, or a decision turn with no actionable result stops the mission.

### Entrypoint and Approval Behavior

| Mode | Current behavior |
|------|------------------|
| Default `demo.py` or SDK launcher | Droid controller, webcam mock unless `--allow-real-flight`; no crop pipeline unless requested |
| Droid with voice enabled | Waits for a spoken request by default; real starts/photos need local `y` approval, expiring after 60 seconds |
| Droid with `--no-voice` | Requests one survey at startup and sets `auto_approve=True`, **including on real hardware**; a host fallback can issue the start if Droid has not |
| Explicit `--controller voice` | Legacy direct mission gateway; `--no-voice` requires `--auto`, and omitting `--mock` selects real Tello without the Droid approval gate |

`--auto` is rejected in Droid mode. Space latches a stop; `q`/Escape stops and exits. In the voice-enabled approval path, starts require the camera/battery preflight check. Do not assume the `--no-voice` auto-approval path provides the same gate. Avoid remote `--droid-machine` / `--droid-e2b` for flight: the mission MCP tools remain on this laptop's loopback interface.

### Photon Delivery Paths

[photon.py](ground_station/photon.py) sends fixed mission-status messages and host-formatted current-analysis text to one verified private human/agent direct space. Configured mock notifications carry `MOCK`. Its bounded queue retries transient failures and deduplicates events within a run; it is not a durable outbox.

The separate crop-summary path is [deliver_summary.py](chalk/deliver_summary.py) → [photon_alert.py](ground_station/photon_alert.py) → `photon-imessage-agent/src/alert.ts`. It synthesizes an MP3 with ElevenLabs, uploads the capture and audio, and sends insight text. It needs Bun 1.3+, installed package dependencies, the package's private Photon configuration, and a saved operator-space/agent checkpoint. The Python helper does not pass `--space`/`--agent` overrides.

`--photon-env` configures only the host notifier. Bun loads `photon-imessage-agent/.env` separately. The summary helper also checks process-level `PHOTON_API_KEY`, `ELEVENLABS_API_KEY`, and `ELEVENLABS_VOICE_ID`. An empty `delivery_error` reports sender acceptance, not delivery; partial sends can occur before a later error.

**`--no-photon` disables only the host notifier.** Crop analysis still constructs the weather/delivery path, including potential cloud-resolver sends. `--mock`, `--fake-analysis`, and `--no-voice` do not sandbox or disable that sender. Use the default no-analysis path or mocked providers when real messaging is not intended. Incoming Photon messages do not control the mission; the package's echo receiver is a separate service.

`python -m ground_station.agent.session --check-sdk` verifies the effective tool boundary without opening hardware or running inference. `python -m ground_station.photon --check` checks the configured assignment and routing without sending a message.

Droid and Chalk do not perform per-frame steering. AprilTag alignment, movement commands, landing, and abort handling remain inside the local control loop. Additional [SDK/host-notifier notes](ground_station/agent/README.md) include older Grok and approval descriptions that do not all match the current launcher.

### Current Gaps

- Droid image scores are model estimates, not validated agronomic diagnoses. The authenticated SDK smoke test simulates the image provider and Photon transport.
- The active weather join does not honor `--chalk-branch` or the separate named-query validator. Deployment and cloud egress are not established by local tests.
- A cloud resolver cannot read a laptop-local photo path. Full cloud summary delivery additionally needs Bun, package files, credentials, and routing available there.
- `--no-photon` is not a global delivery switch; fake image analysis can still cause real weather, storage, and messaging work.
- Real `--no-voice` runs auto-approve starts/photos, unlike the voice-enabled approval path.
- Volume writes and notification delivery are best effort. Automatic flight resumption and incoming phone mission commands are not implemented.
- Weather, storage, and sender latency can exhaust the analysis-hold deadline; independent local timeout/stop handling remains essential.
- The PRD describes demo criteria and acceptance targets, not a completed demo checklist.

## UNO Q Field Stations

These are separate hardware experiments, not dependencies of the default webcam/Tello survey:

- [dht11_temp_only](dht11_temp_only/README.md) reads DHT11 and photoresistor values and streams JSON over Bluetooth. [bt_sensor_recv.py](ground_station/bt_sensor_recv.py) supports BLE discovery (`--scan`) with the optional `bleak` dependency, or classic serial (`--com COM5`) with `pyserial`.
- [uno_q_field_station](uno_q_field_station/README.md) bridges MCU sensor readings to UNO Q Linux, with HTTP telemetry transfer, raw microphone transfer, and offline buffering. [sensor_recv.py](ground_station/sensor_recv.py) receives `/telemetry` and `/mic` and stores local JSONL/PCM files.

The HTTP receiver does not feed its microphone bytes to `ElevenVoiceBridge`, and sensor telemetry is not joined into the current Droid/Chalk crop assessment. Some component guides still name the earlier Grok voice path; the active mission microphone uses ElevenLabs.

## Standalone Audio Signaling

**Source**: [send.py](send.py) and [recv.py](recv.py).

These utilities encode short text as ggwave audio and decode it from microphone input.

- **Sender**: Plays encoded audio via `aplay` or `ffplay`.
- **Receiver**: Listens via `sounddevice` and decodes.

They are **independent speaker/microphone experiments**, not a radio link, photo transport, or command channel for the mission.

## Local Setup Entrypoints

All commands run from the repository root in an activated virtual environment. Install [requirements.txt](requirements.txt); prefer Python 3.11+ because some analysis helpers use `asyncio.timeout`. For dataset tools or training, also install [wheat_detection/requirements.txt](wheat_detection/requirements.txt).

Root requirements do not pin the Chalk SDK version; verify compatibility in your environment.

### Component Prerequisites

| Component | Configuration needed |
|-----------|----------------------|
| Legacy webcam/tag demo without voice | Webcam access and OpenCV display; no cloud credentials |
| ElevenLabs voice input | `ELEVENLABS_API_KEY`, microphone access, internet; no voice ID needed for transcription |
| Chalk queries / Volume | `CHALK_CLIENT_ID`, `CHALK_CLIENT_SECRET`, real `CHALK_ENVIRONMENT` ID; optional `CHALK_STATE_VOLUME` override |
| Grok team CLI | Local Grok CLI installed; see [grok-team/README.md](grok-team/README.md) |
| Factory Droid controller | Authenticated `droid` CLI or `FACTORY_API_KEY`; pinned `droid-sdk[mcp]`; image-capable model for `--crop-analysis` |
| Photon system notifications | Private Beta assignment and existing direct space; see the [runtime guide](ground_station/agent/README.md) |
| Crop phone summary | `ELEVENLABS_API_KEY`, `ELEVENLABS_VOICE_ID`, process-level `PHOTON_API_KEY`, Bun, and configured/checkpointed `photon-imessage-agent` |
| UNO Q Bluetooth receiver | `bleak` for BLE or `pyserial` for classic serial; neither is in root requirements |

Use [.env.example](.env.example) for the root configuration template and keep credentials private. Root `.env` is loaded by the mission, but the Bun messaging package uses its own configuration. Setting variables does not deploy features or provision a phone conversation.

### Quickstart: Webcam Mock

```bash
python demo.py --controller voice --mock --target tag --target-tag 12 --home-tag 12 --no-voice --auto --no-photon
```

Needs:
- An activated Python environment with root dependencies
- OpenCV
- A printed AprilTag (family `36h11`, ID 12)

Print the tag with:

```bash
python ground_station/print_apriltag.py --id 12
```

This opens your webcam, prints simulated flight commands, and steers toward the tag if visible.

### Add Voice (ElevenLabs Required)

Authenticate Factory and configure `ELEVENLABS_API_KEY` privately, then switch to the default Droid controller:

```bash
python demo.py --mock --target tag --target-tag 12 --home-tag 12 --no-photon
```

Say "start survey" when prompted. `ELEVENLABS_VOICE_ID` is only required for spoken phone summaries, not voice input.

### Real Tello (Caution)

Use the voice-enabled Droid path, enable hardware explicitly, speak a survey request, then approve the pending request with local `y`:

```bash
python demo.py --allow-real-flight --target tag --target-tag 12 --home-tag 12 --no-photon
```

**Requirements**:
- Tello connected through the USB Wi-Fi dongle; laptop Wi-Fi keeps internet
- Battery charged
- Clear flying space (5m+ radius)
- Visible AprilTag

**This actually flies.** Review the code, test the mock first, and use an authorized controlled area with operator supervision. Do not add `--no-voice` expecting the same approval gate: the current code then auto-approves starts and extra photos.

## All Entrypoints and Smoke Tests

| Command | Effect | Prerequisites |
|---------|----|---|
| `python demo.py --controller voice --mock --target tag --target-tag 12 --home-tag 12 --no-voice --auto --no-photon` | Legacy local mission on webcam | OpenCV, printed AprilTag |
| `python -m ground_station.agent.session --no-voice --no-photon` | SDK-driven webcam survey, no photo assessment/weather/summary | Factory authentication, OpenCV, visible target |
| `python -m ground_station.agent.session --no-voice --crop-analysis` | Webcam survey plus real Droid photo assessment, weather and best-effort summary/storage | Image-capable Droid model; integration credentials as needed; can send real messages |
| `python -m ground_station.agent.session --no-voice --fake-analysis` | Same crop pipeline with a simulated image score, not isolated providers | Factory authentication, OpenCV; can still call weather/storage/messaging |
| `python tests/live_droid_pipeline.py` | Headless real SDK decision/alert integration, simulated providers | Factory authentication; no hardware or real messaging |
| `python wheat_detection/detect.py path/to/field.png` | ONNX image inference | ONNX dependencies, input image |
| `python wheat_detection/predict.py path/to/field.png --device cpu` | Ultralytics inference | `.pt` checkpoint |
| `python -m ground_station.chalk_eval` | Authenticated cloud Chalk queries | Chalk credentials, deployed features |
| `python -m ground_station.chalk_quality` | Named-query smoke call on `schedtest` | Chalk credentials |
| `python -m ground_station.weather_smoke` | Public NOAA/GFS/NASA readings and sample weather explanations; no phone send | Internet, root Python dependencies; no Chalk credentials |
| `python -m unittest discover -s tests -v` | Offline mission/controller/Photon tests | Root Python dependencies |
| `python -m unittest discover -s test -p 'test_*.py' -v` | Additional offline Chalk/vision-helper/voice/weather/summary tests | Root Python dependencies |
| `python -m ground_station.bt_sensor_recv --scan` | Nearby BLE discovery; no drone control | Bluetooth access, `bleak` |

The SDK launcher runs the connected runtime; the opt-in headless SDK test exercises authenticated decisions with simulated providers and does not prove live image assessment or message delivery. Public-weather smoke calls do not validate Chalk deployment. The separate weather-enabled named query can attempt summary delivery; image-only cloud smoke helpers do not request it.

## Repository Structure

```text
audio_signaling/
├── demo.py                              # Mission entrypoint
├── drone_interface/
│   └── tello_client.py                  # Tello adapter
├── ground_station/
│   ├── voice_mission.py                 # Voice or SDK mission/control loop
│   ├── photon.py                        # Host-owned system notifications
│   ├── photon_alert.py                  # Bun photo/audio summary bridge
│   ├── mission.py                       # State machine and phases
│   ├── control.py                       # Steering calculations
│   ├── voice_eleven.py                  # ElevenLabs speech-to-text
│   ├── voice_grok.py                    # Unused by the mission
│   ├── vision.py                        # Vision helpers
│   ├── apriltag.py                      # AprilTag detection
│   ├── steer_wheat.py                   # Wheat-head steering
│   ├── steer_tag.py                     # AprilTag steering
│   ├── chalk_client.py                  # Feature query helper
│   ├── chalk_quality.py                 # Named-query smoke test
│   ├── chalk_eval.py                    # Labeled eval
│   ├── chalk_eval_mission.py            # Local weather justification / legacy logging helper
│   ├── weather_smoke.py                 # Public-feed smoke check, no message send
│   ├── sensor_recv.py                   # UNO Q HTTP telemetry and raw microphone receiver
│   ├── bt_sensor_recv.py                # BLE / classic-serial sensor receiver
│   └── agent/
│       ├── session.py                   # Mock-first SDK entrypoint
│       ├── controller.py                # Persistent session, photo assessment and MCP tools
│       ├── driver.py                    # Host approval and bounded mission commands
│       ├── worker.py                    # Off-thread SDK lifecycle
│       ├── analysis.py                  # Assessment schema, Droid/fake markers, older Grok analyzer
│       ├── pipeline.py                  # Weather join, SQLite, Volume and summary orchestration
│       ├── README.md                    # Additional SDK and host-notifier notes
│       ├── store.py                     # Chalk Volume state helpers
│       └── NOTES.md                     # Storage investigation caveats
├── chalk/
│   ├── features.py                      # Survey schema and resolvers
│   ├── named_queries.py                 # Query, chart, heartbeat definitions
│   ├── weather_live.py                  # NOAA/NASA fetch and weather justification rules
│   ├── deliver_summary.py               # ElevenLabs audio and Photon summary sender
│   └── chalk.yaml                       # Feature deployment config
├── photon-imessage-agent/               # Separate Bun echo agent and attachment sender
├── dht11_temp_only/                     # UNO Q Bluetooth sensor app
├── uno_q_field_station/                 # UNO Q HTTP telemetry / mic-transfer app
├── config/dongles.json                  # Tello USB Wi-Fi dongle mapping
├── grok-team/                           # Separate developer coordination service
│   └── README.md                        # Grok CLI agent guide
├── wheat_detection/
│   ├── detect.py                        # ONNX inference
│   ├── predict.py                       # Ultralytics inference
│   ├── train.py                         # Training script
│   ├── download_dataset.py              # Dataset setup
│   ├── prepare_labels.py                # Label preparation
│   ├── export_onnx.py                   # ONNX export
│   └── requirements.txt                 # Inference/training dependencies
├── models/
│   └── wheat_detection.onnx             # ONNX inference model
├── tests/                              # Offline mission/SDK/Photon tests + opt-in live SDK smoke
├── test/                               # Additional Chalk/vision/voice/weather/summary tests
├── send.py / recv.py                    # Independent ggwave utilities
├── requirements.txt                     # Root dependencies
├── .env.example                         # Environment template
├── README.md                            # Pitch and quick start
├── README_TECHNICAL.md                  # This file
├── prd.md                               # Target requirements and unfinished work
└── SETUP.md                             # Extended setup guide
```

## License and Attribution

HackHarvard 2026 submission. Uses the [Global Wheat Head Dataset 2021](https://kaggle.com/datasets/vbookshelf/global-wheat-head-dataset-2021) (CC BY 4.0) and YOLOv8 (Ultralytics, AGPL-3.0).
