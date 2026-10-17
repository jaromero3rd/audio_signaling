# AgriDrone: Field Survey Agent

AgriDrone is a HackHarvard 2026 prototype for voice-directed drone surveys. The repository contains a local Tello/webcam mission demo, wheat-head detection, a Grok Voice bridge, standalone Chalk and Daytona integration modules, and a separate Grok CLI coordination service.

**The full photo → crop-health analysis → Chalk/Daytona decision loop is not implemented yet.** An embedded Factory Droid mission controller and host-owned Photon notifications now exist; see the [SDK runtime guide](ground_station/agent/README.md). An implemented module does not mean its cloud service is configured, deployed, or currently running.

The [PRD](prd.md) describes the target product and track priorities, not a record of completed integrations. Track goals include SpaceXAI, Factory Droid SDK, Photon 2.0, Daytona, Chalk, and ASUS Sustainability.

## Implementation map

| Module | What exists | Current integration boundary |
|--------|-------------|------------------------------|
| [Ground-station mission](#ground-station-mission-and-drone-interface) | Tello/webcam adapter, survey state machine, local control loop, photo capture | Optional Grok Voice or Factory Droid controller; Chalk/Daytona remain standalone |
| [Wheat and AprilTag detection](#wheat-head-and-apriltag-detection) | Live detection, an ONNX image detector, training and export scripts | Finds targets and bounding boxes; does not produce crop-health scores |
| [Grok Voice](#grok-voice) | Realtime audio session with start, status, and abort tools | Calls the local mission state machine, not an embedded Droid session |
| [Chalk feature evaluation](#chalk-feature-evaluation) | Feature resolvers, query helpers, named query, monitoring definition, labeled eval cases | Standalone helpers; the mission does not invoke them |
| [Chalk mission state](#chalk-volume-backed-mission-state) | Volume-backed snapshots, decision log, and latest-survey cache | Storage helpers exist but are not called by the mission |
| [Daytona sandbox](#daytona-sandbox-execution) | Real SDK sandbox creation, code execution, file upload, and deletion | Standalone wrapper and smoke entrypoint; no mission/runtime caller |
| [Grok team coordination](#grok-team-coordination) | Five local CLI roles, shared queue/history, optional read-only Discord feed, checked bot-branch publishing | Separate developer workflow; never controls drone hardware |
| [Embedded Droid and Photon](#how-droid-is-used-at-runtime) | Persistent SDK session, restricted mission tools, local approval, fixed operator notifications | Mock-first; no image-health analyzer or incoming Photon mission commands |
| [Audio signaling](#standalone-audio-signaling) | Speaker-to-microphone ggwave text sender and receiver | Independent utilities; not connected to the survey mission |

## Current runtime flow

The implemented mission path is:

```text
Operator speech, or explicit --auto startup
                    |
          Grok Voice tool call
          (when voice is enabled)
                    |
          Local SurveyMission state machine
                    |
          Webcam mock or Tello camera
                    |
          YOLO wheat-head or AprilTag detection
                    |
          Local steering, photo capture, return/land phases
                    |
          Photo path recorded in mission memory
```

Photo handling saves a still, records its path, and prints that path. In Droid mode it also queues a photo-metadata event to the persistent SDK session and, when configured, a fixed Photon notification. It does not request Grok image understanding, evaluate Chalk features, save to Chalk, or run Daytona scripts.

## Ground-station mission and drone interface

**Source:** [demo.py](demo.py), [voice_mission.py](ground_station/voice_mission.py), [mission.py](ground_station/mission.py), [tello_client.py](drone_interface/tello_client.py), and [control.py](ground_station/control.py).

`demo.py` delegates to the ground-station entrypoint. `SurveyMission` represents the survey as explicit phases: takeoff, target search, target approach, photo capture, home search/approach, and landing or abort. Each camera-loop tick produces a requested action and steering values. The entrypoint sends those actions to the selected drone adapter.

`TelloDrone` uses `djitellopy` for the camera stream, remote-control commands, battery reads, and photo capture. `MockDrone` uses a laptop webcam and prints simulated flight commands instead of flying. This is a Tello implementation, not a general DJI adapter or a waypoint planner.

The controller uses the target's position and apparent size in the image to calculate bounded yaw, forward, and vertical commands. The mission includes search/approach timeouts and a frame-absence abort path. These controls are prototype behavior, not evidence of production flight safety.

Photos are saved under `captures/`, and the current mission snapshot lives in process memory. Neither the snapshot nor the photo is automatically uploaded by this loop. Additional manual steering demos are in [steer_wheat.py](ground_station/steer_wheat.py) and [steer_tag.py](ground_station/steer_tag.py).

## Wheat-head and AprilTag detection

**Source:** [vision.py](ground_station/vision.py), [apriltag.py](ground_station/apriltag.py), and [wheat_detection/](wheat_detection/).

There are two wheat-detector paths:

- **Live mission:** `YoloWheatHeadDetector` loads an Ultralytics `.pt` checkpoint, predicts wheat-head boxes, and combines boxes into a target region for steering. Its default checkpoint is `models/wheat_detection.pt`; supply or train that checkpoint before using this path. The mission's automatic target selection falls back to AprilTag when the checkpoint is absent.
- **Standalone image inference:** [detect.py](wheat_detection/detect.py) uses ONNX Runtime with the tracked [models/wheat_detection.onnx](models/wheat_detection.onnx) model. It resizes/pads images, filters detections by confidence, removes overlapping boxes, and returns boxes in original-image coordinates. This ONNX detector is not the live mission's backend.

AprilTag detection uses OpenCV's AprilTag dictionaries. Tags can stand in for the survey target and identify the home marker. [print_apriltag.py](ground_station/print_apriltag.py) generates printable markers. An HSV color-based detector is also available as an explicit option in the manual wheat-steering demo; it is not a crop-health model.

The `wheat_detection/` scripts cover dataset download, label preparation, training, prediction, and ONNX export. Its [requirements file](wheat_detection/requirements.txt) includes inference/training dependencies that are not all in the root requirements.

**Detection is not crop-health analysis.** Bounding boxes and head counts do not supply the `health_score` expected by Chalk. The PRD's Grok image-understanding step still needs implementation and a defined result schema.

## Grok Voice

**Source:** [voice_grok.py](ground_station/voice_grok.py).

`GrokVoiceBridge` opens an authenticated realtime WebSocket session with xAI, streams microphone PCM audio, and plays returned speech. It exposes three tools: `start_survey`, `get_mission_status`, and `abort_mission`.

Those tool calls delegate to the local `SurveyMission` instance. Mission phase changes can also be sent back to the voice session as spoken context. Grok handles conversation and high-level mission requests; the Python state machine and controller calculate flight actions.

This path needs `XAI_API_KEY`, internet access, and working microphone/speaker devices. A real Tello uses the laptop's Wi-Fi connection, so the demo assumes a separate internet path such as USB tethering. Grok Voice is separate from Grok image understanding and from the developer coordination roles below.

## Chalk feature evaluation

**Source:** [features.py](chalk/features.py), [chalk_client.py](ground_station/chalk_client.py), [named_queries.py](chalk/named_queries.py), [chalk_quality.py](ground_station/chalk_quality.py), and [chalk_eval.py](ground_station/chalk_eval.py).

### What Chalk computes

`FieldSurvey` defines a survey ID, field/drone identifiers, photo URL, a supplied health score, analysis text, and decision-related outputs. `evaluate_survey()` sends those inputs to Chalk and requests three computed features:

| Feature | Resolver behavior |
|---------|-------------------|
| `health_flag` | `healthy` for scores at least 0.7, `watch` for scores at least 0.4 but below 0.7, otherwise `critical` |
| `severity` | One minus the health score clamped to [0, 1], rounded to three decimals |
| `recommended_action` | `continue`, `reimage`, or `alert-operator`, based on the health flag |

These are deterministic online resolvers. Chalk does not examine the photo or generate the health score. The caller must supply the analysis, and returned action labels do not themselves execute commands or send alerts.

The definitions are deployed separately from [chalk/](chalk/), using its `chalk.yaml` configuration and the Chalk CLI. Calling a Python helper does not deploy missing resolvers or configure storage.

### Named query, monitoring, and evaluation

`evaluate_field_survey` gives the evaluation contract a stable query name. `evaluate_survey_named()` invokes that name, checks the input score range, and validates returned flag/action values and severity consistency.

The monitoring definition charts that named query's success ratio. It measures query success, not crop-model accuracy or mission completion. A daily heartbeat resolver is also defined; its declaration is not proof that a production schedule is running, and durable heartbeat output requires a configured online store.

`chalk_eval.py` submits five labeled health-score cases to the live feature service and prints expected-versus-returned flags/actions. It is a standalone cloud evaluation, not an offline unit test or part of the flight loop. The named-query smoke entrypoint in `chalk_quality.py` explicitly targets the `schedtest` branch; deployment to that branch does not establish production availability.

### What is not persisted

The helpers were written for an environment without an online/offline feature store. They compute decision features per query; they do not implement a survey/decision feature-write pipeline or historical trend queries. `query_chalk_features()` is a placeholder that returns an explanatory note, not field history.

Persisting survey features for later analysis requires the appropriate storage configuration and write/query paths. The separate Volume storage below should not be presented as historical feature serving.

## Chalk Volume-backed mission state

**Source:** [store.py](ground_station/agent/store.py); background and deployment caveats are in [agent/NOTES.md](ground_station/agent/NOTES.md).

This module provides durable JSON-file storage through Chalk's Volume gRPC API:

| Stored file | Purpose |
|-------------|---------|
| `mission_state.json` | Latest mission snapshot |
| `decision_log.jsonl` | Decision entries with timestamps |
| `fields/<field_id>.json` | Most recently cached survey result for a field |

The public helpers load/save the snapshot, append/read decisions, and get/set the latest survey. The Volume is created on first use if needed; `CHALK_STATE_VOLUME` overrides its default name, `droid-mission-state`.

This is remote object storage, not a hosted AI agent session, an online feature store, or a historical survey database. Appending a decision reads and rewrites the whole log; concurrent writers are not coordinated. The implementation also relies on private Chalk SDK channel access and generated gRPC types, so SDK compatibility must be checked when dependencies change.

**Integration status:** the current mission does not call these helpers. Their existence does not mean running the drone demo persists state or decisions.

## Daytona sandbox execution

**Source:** [daytona_runner.py](ground_station/sandbox/daytona_runner.py).

`DroidSandbox` is an actual Daytona SDK wrapper, not a mock. Constructing it requests an ephemeral cloud sandbox with project labels and a default 15-minute idle auto-stop interval. Reusing the same object allows successive operations in the same sandbox.

The wrapper supports:

- Python execution through `run_code()`, with a timeout and returned exit code, stdout, and stderr.
- Shell execution through `exec()`.
- Uploading script contents through `write_file()`.
- Sandbox deletion through `close()`; using the wrapper as a context manager calls cleanup on exit.

Authentication/configuration comes from the environment loaded by the module. `DAYTONA_API_KEY` is required for the cloud connection; `DAYTONA_API_URL` and `DAYTONA_TARGET` are deployment-specific overrides when needed.

The module's standalone smoke entrypoint creates a sandbox, runs a Python version check, and deletes the sandbox. **Running it provisions a real cloud resource**, subject to account limits and usage costs.

**Integration status:** no mission or embedded Droid runtime currently calls this wrapper. It does not run the local flight-control loop or provide a connection from a cloud sandbox to the Tello. The intended use is isolated execution of future Droid-generated analysis/planning scripts, with results returned to a validated local action path.

## Grok team coordination

**Source and operating guide:** [grok-team/README.md](grok-team/README.md).

This separate Bun/TypeScript service launches the installed Grok CLI on demand for five roles: coordinator, drone, logistics, vision, and release. A private SQLite database stores jobs and shared conversation history. Handoffs are bounded to prevent an indefinite agent-to-agent loop.

The model receives no shell, filesystem, GitHub, messaging, or hardware tools. These are local CLI coordination agents, not provisioned native cloud Grok Bots and not the embedded drone runtime.

An optional Discord collector reads only configured announcement channels and supplies selected recent announcements as untrusted reference material. It does not post replies or operate the mission. The collector is not connected merely because its code exists; bot authorization and channel configuration are separate setup steps.

A deterministic publisher can check committed changes in owned worktrees and push the exact checked SHA to an allowed bot branch. This is host-controlled Git workflow, not model-controlled flight logic. It does not automatically merge or push `main`. Credentials and `.state/` data remain private; follow the package guide for setup and validation.

## How Droid is used at runtime

Factory Droid is now an optional high-level mission controller, not the low-level Tello driver. The [SDK runtime guide](ground_station/agent/README.md) covers the runnable launcher, tools, real-flight approval, limits, and verification commands.

`python -m ground_station.agent.session` defaults to a webcam mock. A persistent `droid-sdk` session calls `get_mission_status` and `issue_drone_command`. Host validation and local approval sit between those calls and the existing mission state machine. Camera/RC control stays on the local loop, independent of model latency.

[Photon notifications](ground_station/photon.py) use the existing private Beta iMessage assignment and exact direct user/agent space. The host sends fixed status messages; the model has no messaging tool, recipient selector, or access to Photon credentials. Configured mock runs send real iMessages labeled `MOCK`; use `--no-photon` for local-only runs.

The missing pieces remain the Grok image-health analyzer, automatic Chalk/Daytona orchestration, durable mission storage wiring, and incoming Photon mission commands. `submit_analysis()` accepts validated external results but does not generate them; `query_chalk_features()` remains a placeholder.

## Standalone audio signaling

**Source:** [send.py](send.py) and [recv.py](recv.py).

These utilities encode short text as ggwave audio and decode it from microphone input. The sender plays through `aplay` or `ffplay`; the receiver uses `sounddevice`. They are independent speaker/microphone experiments, not a radio link, photo transport, or current mission-command channel.

## Local setup and entrypoints

Run Python commands from the repository root. Create a virtual environment and install [requirements.txt](requirements.txt); install [wheat_detection/requirements.txt](wheat_detection/requirements.txt) additionally for ONNX inference or the dataset/training tools. The root requirements do not pin the Chalk or Daytona SDK versions, so verify compatibility in your environment.

Use the blank [.env.example](.env.example) as a template for your private root `.env` without overwriting an existing configuration. Configure only the components you intend to run, and never commit credentials.

| Component | Configuration needed |
|-----------|----------------------|
| Webcam/tag demo without voice | Webcam access and OpenCV display; no cloud credentials |
| Grok Voice | `XAI_API_KEY`, audio-device access, and internet |
| Chalk helpers | `CHALK_CLIENT_ID`, `CHALK_CLIENT_SECRET`, and the actual `CHALK_ENVIRONMENT` ID, not a guessed display name such as `dev`; optional `CHALK_STATE_VOLUME` |
| Daytona wrapper | `DAYTONA_API_KEY`; API URL/target overrides only when required |
| Grok coordination service | Bun, Grok CLI authentication, and package-specific configuration from its guide |
| Factory Droid runtime | Authenticated `droid` CLI or `FACTORY_API_KEY`, plus the pinned SDK/MCP extra |
| Photon system notifications | Private Beta project/assignment, agent/user handles, and an existing exact direct space; see the SDK runtime guide |

Start with a **webcam mock**, not a real drone:

```bash
python demo.py --mock --target tag --target-tag 12 --home-tag 12 --no-voice --auto
```

This opens the webcam/display and prints simulated flight commands. It needs a visible matching AprilTag to follow the normal target path; it is not a headless automated test. Generate the marker with `ground_station/print_apriltag.py` using family `36h11` and ID `12`. In the focused demo window, Space requests abort and `q` quits.

To enable spoken requests, configure xAI and audio access, omit `--no-voice`, and omit `--auto` if you want to wait for a spoken start. **Do not remove `--mock` unless you intend real Tello operation and have reviewed the flight setup.** The real adapter can take off and land; a command example is not a safety certification.

Other existing entrypoints:

| Command | Effect and prerequisites |
|---------|--------------------------|
| `python wheat_detection/detect.py path/to/field.png` | Local ONNX image inference; requires ONNX dependencies and an input image |
| `python wheat_detection/predict.py path/to/field.png --device cpu` | Ultralytics inference; requires the `.pt` checkpoint |
| `python -m ground_station.chalk_eval` | Authenticated cloud queries against deployed Chalk features |
| `python -m ground_station.chalk_quality` | Authenticated named-query smoke call on the `schedtest` branch |
| `python -m ground_station.sandbox.daytona_runner` | Creates, exercises, and deletes a real Daytona sandbox |

These entrypoints exercise separate components. None launches the missing full Droid/Chalk/Daytona/Photon mission loop. Cloud smoke calls are not prerequisites for reading the code or running the voice-disabled webcam demo.

## Repository structure

```text
audio_signaling/
├── demo.py                         # Local mission entrypoint
├── drone_interface/
│   └── tello_client.py             # Tello adapter and webcam mock
├── ground_station/
│   ├── voice_mission.py            # Camera/control loop
│   ├── mission.py                  # Survey state machine
│   ├── voice_grok.py               # Realtime voice tools
│   ├── vision.py                   # Live .pt detector and HSV option
│   ├── apriltag.py                 # Target/home marker detection
│   ├── control.py                  # Visual steering calculations
│   ├── chalk_client.py             # Feature query helper and history placeholder
│   ├── chalk_quality.py            # Named-query helper and result checks
│   ├── chalk_eval.py               # Labeled live-service eval
│   ├── agent/
│   │   ├── store.py                # Chalk Volume state helpers, not a Droid session
│   │   └── NOTES.md                # Storage investigation and caveats
│   └── sandbox/
│       └── daytona_runner.py       # Cloud sandbox wrapper
├── chalk/
│   ├── features.py                 # Survey schema and online resolvers
│   ├── named_queries.py            # Query, chart, and heartbeat definitions
│   └── chalk.yaml                  # Feature deployment configuration
├── grok-team/                      # Separate developer coordination service
├── wheat_detection/                # Offline inference, dataset, training, export
├── models/wheat_detection.onnx     # ONNX inference model, not the live .pt weights
├── send.py / recv.py               # Independent ggwave text transport
├── requirements.txt
├── .env.example
└── prd.md                          # Target requirements and unfinished work
```

## License and attribution

HackHarvard 2026 submission. Uses the [Global Wheat Head Dataset 2021](https://kaggle.com/datasets/vbookshelf/global-wheat-head-dataset-2021) (CC BY 4.0) and YOLOv8 (Ultralytics, AGPL-3.0).
