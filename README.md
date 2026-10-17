# AgriDrone: Voice-Directed Field Survey Agent

AgriDrone is a HackHarvard 2026 prototype for voice-directed Tello field surveys, with an optional embedded Factory Droid controller. It detects wheat heads or AprilTags, captures local photos, and performs a bounded return/landing sequence.

**The crop-health analysis pipeline is not implemented.** Chalk and Daytona helpers exist separately. Droid mission control and fixed Photon system notifications are now wired into the local runtime, but they do not turn a photograph into a health assessment.

## What We Built

- **Voice layer:** Grok Voice can request a survey, read status, and abort.
- **Flight layer:** The local mission state machine handles takeoff, target approach, photo capture, home search, and landing through Tello or a webcam mock.
- **SDK controller:** A persistent Factory Droid session can request bounded mission actions through host-controlled tools.
- **System notifications:** The host can send fixed mission-status iMessages through a verified private Photon assignment.
- **Standalone cloud helpers:** Chalk computes features from a supplied health score; Daytona can execute scripts in an ephemeral sandbox. Neither is called by the mission yet.

## What's Implemented & Working

| Feature | Implementation status | Try it |
|---------|-----------------------|--------|
| **Grok Voice control** | Connected to mission tools | Speak commands after configuring xAI/audio |
| **Local flight control** | Tello adapter and webcam mock | Takeoff → target → photo → home/land |
| **Wheat/AprilTag detection** | Live `.pt` detector or AprilTag | Supply weights or use a printed tag |
| **Photo capture & logging** | Saves local stills and paths | View `captures/` |
| **Chalk feature evaluation** | Standalone helpers | Supply a health score to deployed resolvers |
| **Daytona cloud sandbox** | Standalone SDK wrapper | Separate cloud smoke test |
| **Mission state storage** | Standalone Chalk Volume helpers | Not called by the mission |
| **Embedded Droid session** | Persistent SDK session and restricted tools | Mock-first launcher below |
| **Photon system notifications** | Host-owned mission hooks | Configure the existing private iMessage assignment |
| **End-to-end crop-health decision loop** | Not wired | Image analyzer and cloud orchestration still missing |

These are implementation boundaries, not a claim that every service is configured, running, or flight-tested.

## How They're Integrated

### The Voice-Guided Mission Flow

```text
Operator speech → Grok Voice mission tools
                         |
                  Local SurveyMission
                         |
         Camera → wheat/tag detection → local steering
                         |
            Photo capture → home search → landing
```

Grok handles conversation; the local controller handles steering. The voice-disabled `--auto` path can start the same state machine directly.

### Optional Factory Droid Flow

```text
Initial operator instruction → persistent Droid SDK session
                                      |
                          restricted mission tools
                                      |
                     host validation / local approval
                                      |
                  existing mission and camera/RC loop
                                      |
                 photo metadata → same Droid session
                 fixed mission events → Photon user
```

Droid and Grok Voice are alternative controllers, not simultaneous authorities. SDK/model latency runs off the camera/RC thread. Photos remain local; their metadata is not an image-health result.

## Why We Chose Each Service

### Grok (xAI)
Realtime speech connects natural requests to `start_survey`, `get_mission_status`, and `abort_mission`. This voice bridge does not perform crop-health image analysis.

### Chalk
Online resolvers compute `healthy` / `watch` / `critical`, severity, and recommended action from a supplied health score. Chalk does not inspect the image, and the mission does not currently invoke these helpers.

### Daytona
The real SDK wrapper creates a sandbox, executes code, and supports cleanup. Future generated analysis scripts could run there instead of on the host. The current Droid runtime exposes no code-execution tool and does not call Daytona.

### Photon
The host sends fixed status notifications to the configured human through an existing direct iMessage space. The model cannot choose recipients or message text. Photo/health-result attachments and incoming mission commands are not implemented.

### Chalk Volume
JSON snapshot, decision-log, and latest-survey storage helpers exist separately. The mission does not save through them or automatically resume flight after a restart.

### Droid (Factory)
The official Python SDK provides one persistent session with `get_mission_status` and `issue_drone_command`. The host bounds commands, approvals, queues, timeouts, turns, and extra photos. See the [SDK runtime guide](ground_station/agent/README.md) for the exact authority boundary.

## Quick Start

Install [requirements.txt](requirements.txt) in a Python 3.10+ virtual environment. Commands below run from the repository root.

### 1. Webcam Mock (No Drone, No Cloud)

```bash
python demo.py --mock --target tag --target-tag 12 --home-tag 12 --no-voice --auto
```

This opens the webcam/display and prints simulated flight commands. Use a matching printed AprilTag; generate ID 12 with `ground_station/print_apriltag.py --id 12`. It is not a headless test.

### 2. Add Voice

Configure `XAI_API_KEY` privately and ensure working microphone/speaker devices. Omit `--no-voice` and `--auto` to wait for a spoken request:

```bash
python demo.py --mock --target tag --target-tag 12 --home-tag 12
```

### 3. Use the Embedded Droid SDK

With an authenticated `droid` CLI or `FACTORY_API_KEY`:

```bash
python -m ground_station.agent.session --check-sdk
python -m ground_station.agent.session --target tag --target-tag 12 --home-tag 12 --no-photon
```

The check opens no hardware and runs no model turn. The second command defaults to the webcam mock. Real SDK-controlled flight requires explicit `--allow-real-flight` and local `y` approval. Space latches a stop; `q` quits.

### 4. Configure System-to-User Photon Messages

```bash
python -m ground_station.photon --env /absolute/path/to/private/photon.env --check
python -m ground_station.agent.session --photon-env /absolute/path/to/private/photon.env
```

The first command only verifies routing. The second runs the mock and sends real mission-status iMessages labeled `MOCK`. Keep credentials private. The [runtime guide](ground_station/agent/README.md#photon-system-to-user-messaging) explains default configuration, limits, and the explicit test-send command.

### Real Tello Operation

Review the existing flight implementation and network setup before enabling hardware. Built-in Wi-Fi supplies internet; the Tello uses the USB dongle selected by `--tello-iface`.

The SDK entrypoint defaults to mock. The legacy voice/auto entrypoint does not: removing `--mock` there enables real Tello operation. These are prototype controls, not a flight-safety certification. Use an appropriate controlled area, charged battery, and operator supervision.

## For Details

- [SDK runtime and Photon guide](ground_station/agent/README.md): tools, approval, lifecycle, configuration, and tests.
- [README_TECHNICAL.md](README_TECHNICAL.md): modules, cloud helper boundaries, entrypoints, and repository structure.
- [PRD](prd.md): target requirements, including work that is not yet implemented.

## License and Attribution

HackHarvard 2026 submission. Uses the [Global Wheat Head Dataset 2021](https://kaggle.com/datasets/vbookshelf/global-wheat-head-dataset-2021) (CC BY 4.0) and YOLOv8 (Ultralytics, AGPL-3.0).
