# AgriDrone: Technical Reference

**For the overview, pitch, and quick start, see [README.md](README.md).**

This document covers implementation details, integration status, code structure, and limitations for each component.

## Implementation Map

This table shows what code exists and what's wired together:

| Module | What exists | Current integration boundary |
|--------|-------------|------------------------------|
| [Ground-station mission](#ground-station-mission-and-drone-interface) | Tello/webcam adapter, survey state machine, local control loop, photo capture | Connected to local detection and optional Grok Voice; not wired to Chalk, Daytona, or Droid |
| [Wheat AprilTag detection](#wheat-head-and-apriltag-detection) | Live detection via ONNX image detector, training export scripts | Finds target bounding boxes; does not produce crop-health scores |
| [Grok Voice](#grok-voice) | Realtime audio session start, status, abort tools | Calls local mission state machine, not an embedded Droid session |
| [Chalk feature evaluation](#chalk-feature-evaluation) | Feature resolvers, query helpers, named query, monitoring definition, labeled eval cases | Standalone helpers; mission does not invoke them |
| [Chalk mission state](#chalk-volume-backed-mission-state) | Volume-backed snapshots, decision log, latest-survey cache | Storage helpers exist but not called by mission |
| [Daytona sandbox](#daytona-sandbox-execution) | SDK wrapper and mission/runtime coordination | Standalone smoke test; no mission or Droid integration yet |
| [Grok team coordination](#grok-team-coordination) | CLI queue/history, read-only bot-branch publisher | Independent developer coordination service; not mission-critical |
| [Photon](#how-droid-is-used-at-runtime) | Runtime session placeholder, registered tools | Photo-to-alert adapter not implemented |
| [Audio signaling](#standalone-audio-signaling) | Speaker-to-microphone ggwave text sender/receiver | Independent utilities; not connected to survey mission |

**Key insight**: Each component is functionally complete **in isolation**. The end-to-end pipeline (photo → Chalk → Droid → Daytona → Photon) is not yet wired.

## Current Runtime Flow

The only implemented mission path today is:

```text
Operator speech or explicit --auto startup
        ↓
Grok Voice tool call (if voice enabled)
        ↓
Local SurveyMission state machine
        ↓
Webcam mock or Tello camera
        ↓
YOLO wheat-head or AprilTag detection
        ↓
Local steering, photo capture, return/land phases
        ↓
Photo path recorded in mission memory
```

Photo handling currently:
- Saves the still image to disk
- Records its path in the mission log
- Prints the path to stdout

It does **not**:
- Automatically request Grok image understanding
- Evaluate Chalk features
- Save mission state to Chalk
- Run Daytona scripts
- Send Photon alerts

## Ground-station mission and drone interface

**Source**: [demo.py](demo.py), [voice_mission.py](ground_station/voice_mission.py), [mission.py](ground_station/mission.py), [tello_client.py](drone_interface/tello_client.py), [control.py](ground_station/control.py).

### Overview

`demo.py` is the entrypoint that delegates to the ground-station layer. `SurveyMission` represents the survey as explicit phases:

- **Takeoff**: Motor start and altitude gain
- **Target search**: Rotate and scan for a target (wheat or AprilTag)
- **Target approach**: Move closer while keeping the target centered
- **Photo capture**: Stabilize and snap an image when the target is in frame and well-sized
- **Home search**: Rotate to find the home marker (usually a different tag)
- **Home approach**: Move back to base
- **Landing or abort**: Safe touchdown or manual recovery

Each camera-loop tick produces a requested action—yaw, forward, and vertical steering values. The entrypoint sends these actions to either a `TelloDrone` adapter (for live Tello flight) or a `MockDrone` (for simulation).

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

The mission also handles frame-absence timeouts—if no target is seen for N frames, the mission enters search mode.

Both wheat-head and AprilTag steering is delegated to [steer_wheat.py](ground_station/steer_wheat.py) and [steer_tag.py](ground_station/steer_tag.py).

## Wheat-head and AprilTag detection

**Source**: [vision.py](ground_station/vision.py), [apriltag.py](ground_station/apriltag.py), [wheat_detection/](wheat_detection/) directory.

### Wheat-head Detection

Two paths exist:

1. **Live mission path**: `YoloWheatHeadDetector` loads an Ultralytics `.pt` checkpoint, predicts wheat-head bounding boxes, and combines boxes into a target region for steering.
   - Default checkpoint: `models/wheat_detection.pt`
   - If the checkpoint is absent, the mission falls back to AprilTag detection.
   - This path **does not** export to ONNX automatically.

2. **Standalone ONNX inference**: [detect.py](wheat_detection/detect.py) uses ONNX Runtime with [models/wheat_detection.onnx](models/wheat_detection.onnx).
   - Resizes and pads images
   - Filters detections by confidence
   - Removes overlapping boxes
   - Returns bounding boxes in original-image coordinates
   - This ONNX detector is **not** the live mission's backend; it's provided for offline analysis.

### AprilTag Detection

Uses OpenCV's AprilTag dictionaries. Tags can serve as both survey targets (wheat stand-in) and home markers.

- [print_apriltag.py](ground_station/print_apriltag.py): Generates printable markers (family `36h11` recommended).
- An HSV color-based detector is also available for manual wheat-steering demo, but it is **not** a crop-health model.

### Important Limitation

**Detection is not crop-health analysis.**

Bounding boxes and head counts do not supply the `health_score` expected by Chalk's feature evaluators. The PRD's Grok image-understanding step (converting a photo to health confidence) is still unimplemented. You must define the result schema before wiring this to Chalk.

## Grok Voice

**Source**: [voice_grok.py](ground_station/voice_grok.py).

### Overview

`GrokVoiceBridge` opens an authenticated realtime WebSocket session with xAI's Grok service, streams microphone PCM audio, and receives transcriptions and tool calls.

### Tool Bindings

The bridge registers three tools that the Grok model can call:

- `start_survey`: Initiates the mission state machine (takeoff, search, photo, land).
- `get_mission_status`: Returns current mission phase and any recent error.
- `abort_mission`: Cleanly shuts down flight and lands the drone.

Grok interprets spoken requests, transcribes them, and decides which tool to call. For example, "start survey" → `start_survey()`.

### Prerequisites

- `XAI_API_KEY`: API key for xAI (required to authenticate the WebSocket).
- Microphone and speaker access (system audio permission).
- Internet connection to reach xAI's WebSocket endpoint.

### Limitations

- The bridge is not a full Droid session. It's a thin tool-calling interface.
- Photos are not automatically sent to Grok for analysis; you must implement that separately.
- The mission's internal state machine drives flight, not Grok's reasoning.

## Chalk Feature Evaluation

**Source**: [features.py](chalk/features.py), [chalk_client.py](ground_station/chalk_client.py), [named_queries.py](chalk/named_queries.py), [chalk_quality.py](ground_station/chalk_quality.py), [chalk_eval.py](ground_station/chalk_eval.py).

### Survey Schema

`FieldSurvey` defines a single survey record with:
- `id`: Unique survey identifier (UUID)
- `field_id` / `drone_id`: Mission context
- `photo_url`: Reference to the captured image
- `health_score`: Supplied confidence (0.0–1.0) for crop health
- `analysis_summary`: Human-readable findings or model output
- `ts`: Feature timestamp
- Decision-related outputs (feature flags, action recommendations)

### Feature Computation

`evaluate_survey()` sends inputs to a deployed Chalk feature service and requests three computed features:

| Feature | Resolver behavior |
|---------|-------------------|
| `health_flag` | `healthy` if score ≥ 0.7, `watch` if score ≥ 0.4 but < 0.7, else `critical` |
| `severity` | 1.0 − health_score, clamped [0, 1], rounded to 3 decimals |
| `recommended_action` | `continue`, `reimage`, or `alert-operator`, based on health_flag |

These are deterministic online resolvers. Chalk does **not** examine the photo or generate a health score; the caller must supply the analysis. Returned action labels do not themselves execute commands or send alerts.

### Feature Deployment

Feature definitions are deployed separately via the [chalk/](chalk/) directory and Chalk CLI:

```bash
cd chalk && chalk apply --branch schedtest
```

Simply calling the Python helper does **not** deploy resolvers or configure storage. You must have a Chalk workspace and the CLI installed.

### Named Query, Monitoring, Evaluation

- `evaluate_field_survey`: Provides a stable query name for feature evaluation.
- `evaluate_survey_named()`: Invokes the named query, checks score ranges, validates returned values.
- Monitoring definition: Charts the named query's success ratio (not crop-model accuracy or mission completion).
- Daily heartbeat resolver: Declared in `chalk/named_queries.py` as an `@online(cron="1d")` resolver; existence does not prove it's scheduled.

### Smoke Tests

[chalk_eval.py](ground_station/chalk_eval.py): Runs eight labeled boundary cases and checks the returned flags, severity values, and actions. It exits nonzero on any mismatch, resolver error, or failed query.

[chalk_quality.py](ground_station/chalk_quality.py): Named-query smoke call explicitly targeting the `schedtest` branch.

These are cloud evaluation calls, not offline unit tests. The `schedtest` branch is a development branch; production availability is not guaranteed.

### Important Limitation

These are query-time feature computations — nothing is persisted, because this Chalk environment has no online or offline store configured.

Persisting survey features for later analysis requires appropriate storage configuration. The separate Volume storage (see below) is for mission state snapshots and decision logs, not a feature time-series database.

## Chalk Volume-backed Mission State

**Source**: [ground_station/agent/store.py](ground_station/agent/store.py). Background caveats in [ground_station/agent/NOTES.md](ground_station/agent/NOTES.md).

### Overview

This module provides durable JSON-file storage via Chalk's Volume gRPC API:

| Stored file | Purpose |
|-------------|---------|
| `mission_state.json` | Latest mission snapshot (flight phase, battery, GPS, etc.) |
| `decision_log.jsonl` | Decision entries with timestamps |
| `fields/<field_id>.json` | Most recently cached survey result per field |

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

The current mission does **not** call these helpers. Storage helpers exist, but the local flight loop does not persist state to Volume.

## Daytona Sandbox Execution

**Source**: [ground_station/sandbox/daytona_runner.py](ground_station/sandbox/daytona_runner.py).

### Overview

`DroidSandbox` is an actual Daytona SDK wrapper, not a mock. Constructing an instance provisions a real ephemeral cloud sandbox project with a default 15-minute idle auto-stop interval. Reusing the same object allows successive operations in the same sandbox.

### Supported Operations

- **Python execution**: `run_code(script, timeout)` → returns exit code, stdout, stderr.
- **Shell execution**: `exec(command)` → returns exit code, stdout, stderr.

### Prerequisites

- `DAYTONA_API_KEY`: API credentials.
- `DAYTONA_API_URL`: Endpoint (default `https://app.daytona.io/api`).
- `DAYTONA_TARGET`: Optional region override.

### Smoke Test

[ground_station/sandbox/daytona_runner.py](ground_station/sandbox/daytona_runner.py) main:
- Creates a sandbox
- Runs a Python version check
- Deletes the sandbox

**Running this provisions real cloud resources**, subject to account limits and usage costs.

### Integration Status

No mission or embedded Droid runtime currently calls this wrapper. It is intended for isolated execution of Droid-generated analysis and planning scripts. Results are returned to the host for validation before local action or alert.

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

**Current status: Embedded runtime described in the PRD is not yet implemented.**

Factory's Droid is currently used as the team's coding interface only. The `ground_station/agent/` package contains Chalk-backed state helpers but not a session launcher. There is no runnable `ground_station/agent/session.py`.

### Intended Flow (Not Yet Implemented)

1. Analyze captured photo into a validated crop-health result.
2. Evaluate decision features in Chalk and load mission context.
3. Feed analysis and context into an embedded Droid session.
4. Droid generates scripts or action recommendations.
5. Generated scripts run inside Daytona, not on the host.
6. Validate proposed actions before local device control or Photon alert.
7. Save mission state and decisions; add feature persistence separately for historical analysis.

### Current Gaps

- No ground-station Photon adapter for photo/analysis alerts or incoming mission commands.
- Credential placeholders do not implement the adapter.
- The PRD describes demo criteria and acceptance targets, not a completed demo checklist.

## Standalone Audio Signaling

**Source**: [send.py](send.py) and [recv.py](recv.py).

These utilities encode short text as ggwave audio and decode it from microphone input.

- **Sender**: Plays encoded audio via `aplay` or `ffplay`.
- **Receiver**: Listens via `sounddevice` and decodes.

They are **independent speaker/microphone experiments**, not a radio link, photo transport, or command channel for the mission.

## Local Setup Entrypoints

All commands run from the repository root. Create a virtual environment and install [requirements.txt](requirements.txt). For ONNX inference, dataset tools, or training, also install [wheat_detection/requirements.txt](wheat_detection/requirements.txt).

Root requirements do not pin Chalk or Daytona SDK versions; verify compatibility in your environment.

### Component Prerequisites

| Component | Configuration needed |
|-----------|----------------------|
| Webcam/tag demo without voice | Webcam access (OpenCV display); no cloud credentials |
| Grok Voice | `XAI_API_KEY`, audio-device access, internet |
| Chalk helpers | `CHALK_CLIENT_ID`, `CHALK_CLIENT_SECRET`, real `CHALK_ENVIRONMENT` ID, `CHALK_STATE_VOLUME` name |
| Daytona | `DAYTONA_API_KEY`, API URL, target environment |
| Grok team CLI | Local Grok CLI installed; see [grok-team/README.md](grok-team/README.md) |

Setting environment variables does not enable missing integrations; it just makes them available if called.

### Quickstart: Webcam Mock

```bash
python demo.py --mock --target tag --target-tag 12 --home-tag 12 --no-voice --auto
```

Needs:
- Python 3.9+
- OpenCV
- A printed AprilTag (family `36h11`, ID 12)

Print the tag with:

```bash
python ground_station/print_apriltag.py --id 12
```

This opens your webcam, prints simulated flight commands, and steers toward the tag if visible.

### Add Voice (xAI Required)

Omit `--no-voice` and `--auto`:

```bash
export XAI_API_KEY=<your_key>
python demo.py --mock --target tag --target-tag 12 --home-tag 12
```

Say "start survey" when prompted.

### Real Tello (Caution)

Remove `--mock`:

```bash
python demo.py --target tag --target-tag 12 --home-tag 12 --no-voice --auto
```

**Requirements**:
- Tello connected to laptop Wi-Fi
- Battery charged
- Clear flying space (5m+ radius)
- Visible AprilTag

**This actually flies.** Review the code, test indoors, and ensure you're authorized before running.

## All Entrypoints and Smoke Tests

| Command | Effect | Prerequisites |
|---------|----|---|
| `python demo.py --mock --target tag --target-tag 12 --home-tag 12 --no-voice --auto` | Local mission on webcam | OpenCV, printed AprilTag |
| `python wheat_detection/detect.py path/to/field.png` | ONNX image inference | ONNX dependencies, input image |
| `python wheat_detection/predict.py path/to/field.png --device cpu` | Ultralytics inference | `.pt` checkpoint |
| `python -m ground_station.chalk_eval` | Authenticated cloud Chalk queries | Chalk credentials, deployed features |
| `python -m ground_station.chalk_quality` | Named-query smoke call on `schedtest` | Chalk credentials |
| `python -m ground_station.sandbox.daytona_runner` | Creates, exercises, deletes Daytona sandbox | Daytona credentials (provisioning real resources) |

These exercise individual components. **None launches the full end-to-end loop.** Cloud smoke calls are not prerequisites for reading or running the local webcam demo.

## Repository Structure

```text
audio_signaling/
├── demo.py                              # Mission entrypoint
├── drone_interface/
│   └── tello_client.py                  # Tello adapter
├── ground_station/
│   ├── voice_mission.py                 # Voice-driven mission
│   ├── mission.py                       # State machine and phases
│   ├── control.py                       # Steering calculations
│   ├── voice_grok.py                    # Grok Voice bridge
│   ├── vision.py                        # Vision helpers
│   ├── apriltag.py                      # AprilTag detection
│   ├── steer_wheat.py                   # Wheat-head steering
│   ├── steer_tag.py                     # AprilTag steering
│   ├── chalk_client.py                  # Feature query helper
│   ├── chalk_quality.py                 # Named-query smoke test
│   ├── chalk_eval.py                    # Labeled eval
│   ├── agent/
│   │   ├── store.py                     # Chalk Volume state helpers
│   │   └── NOTES.md                     # Storage investigation caveats
│   └── sandbox/
│       └── daytona_runner.py            # Daytona SDK wrapper
├── chalk/
│   ├── features.py                      # Survey schema and resolvers
│   ├── named_queries.py                 # Query, chart, heartbeat definitions
│   └── chalk.yaml                       # Feature deployment config
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
