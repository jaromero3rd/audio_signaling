# AgriDrone: Voice-Directed Field Survey Agent

AgriDrone is a HackHarvard 2026 prototype for voice-directed Tello field surveys, controlled by an embedded Factory Droid session. It detects wheat heads or AprilTags, analyzes captured images with Grok, and makes bounded reimage, alert, and return-home decisions.

**The image-analysis decision loop is connected.** Grok's visual assessment is an uncertain model estimate, not a verified crop diagnosis. Real hardware, live vision credentials, and cloud feature deployments still need operator validation.

## What We Built

- **Voice layer:** Grok Voice queues survey intent to Droid, reads status, and stops immediately through the host.
- **Flight layer:** The local mission state machine handles takeoff, target approach, photo capture, home search, and landing through Tello or a webcam mock.
- **SDK controller:** One persistent Factory Droid session receives validated image analysis and makes tool-driven decisions while local flight control stays independent.
- **System notifications:** The host sends mission-status and Droid-requested analysis alerts through a verified private Photon assignment.
- **Features and history:** Local SQLite survey/decision history is persistent. `--chalk` evaluates fresh analyses through the deployed Chalk named query.

## What's Implemented & Working

| Feature | Implementation status | Try it |
|---------|-----------------------|--------|
| **Grok Voice control** | Connected to mission tools | Speak commands after configuring xAI/audio |
| **Local flight control** | Tello adapter and webcam mock | Takeoff → target → photo → home/land |
| **Wheat/AprilTag detection** | Live `.pt` detector or AprilTag | Supply weights or use a printed tag |
| **Photo capture & logging** | Saves local stills and paths | View `captures/` |
| **Chalk feature evaluation** | Optional runtime query and cloud regression runner | Enable `--chalk`; run labeled boundary cases separately |
| **Mission state storage** | Standalone Chalk Volume helpers | Not called by the mission |
| **Embedded Droid session** | Persistent SDK session and restricted tools | Mock-first launcher below |
| **Photon system notifications** | Host-owned mission hooks | Configure the existing private iMessage assignment |
| **Photon image-alert helper** | Standalone upstream bridge | See `photon-imessage-agent/`; not called by this mission runtime |
| **Image-analysis decision loop** | Connected and simulated end-to-end | Photo → Grok → Droid → bounded action/alert |

These are implementation boundaries, not a claim that every service is configured, running, or flight-tested.

## How They're Integrated

### The Default Mission Flow

```text
Operator speech → Grok Voice → persistent Factory Droid
                                      |
                           host tools / local approval
                                      |
            Camera → detection → local steering → photo
                                      |
                       hover with a fixed deadline
                                      |
              Grok image understanding → validated analysis
                                      |
             local history + optional Chalk → same Droid
                                      |
              reimage / Photon alert / return home / land
```

Grok handles conversation and image understanding; Droid makes high-level decisions. The local controller owns steering. Emergency stop bypasses all cloud work.

### SDK Execution Boundary

```text
Initial operator instruction → persistent Droid SDK session
                                      |
                          restricted mission tools
                                      |
                     host validation / local approval
                                      |
                  existing mission and camera/RC loop
                                      |
              validated analysis → same Droid session
              mission/analysis alerts → Photon user
```

Voice is an input adapter to Droid, not a second flight authority. SDK/model latency runs off the camera/RC thread. Image bytes go to xAI; only validated analysis goes to Droid. Reimages never renew the 90-second analysis hold.

## Why We Chose Each Service

### Grok (xAI)
Realtime speech connects natural requests to `start_survey`, `get_mission_status`, and `abort_mission`. This voice bridge does not perform crop-health image analysis.

### Chalk
Online resolvers compute `healthy` / `watch` / `critical`, severity, and recommended action from Grok's health score when `--chalk` is enabled. Without it, clearly labeled local thresholds provide context. Chalk does not inspect the image.

### Photon
The host sends status notifications and validated analysis text to the configured human through an existing direct iMessage space. Droid can request an alert but cannot choose recipients or arbitrary text. This mission runtime does not send image attachments or accept incoming mission commands.

### Chalk Volume
JSON snapshot, decision-log, and latest-survey storage helpers exist separately. The mission does not save through them or automatically resume flight after a restart.

### Droid (Factory)
The official Python SDK provides one persistent session with status, command, survey-context, and analysis-alert tools. The host bounds commands, approvals, queues, timeouts, turns, and extra photos. See the [SDK runtime guide](ground_station/agent/README.md) for the exact authority boundary.

## Quick Start

Install [requirements.txt](requirements.txt) in a Python 3.10+ virtual environment. Commands below run from the repository root.

### 1. Webcam Mock (No Drone, No Cloud)

```bash
python demo.py --controller voice --mock --target tag --target-tag 12 --home-tag 12 --no-voice --auto --no-photon
```

This opens the webcam/display and prints simulated flight commands. Use a matching printed AprilTag; generate ID 12 with `ground_station/print_apriltag.py --id 12`. It is not a headless test.

### 2. Add Voice

Configure `XAI_API_KEY` privately, authenticate the `droid` CLI, and ensure working microphone/speaker devices. The default controller is Droid and defaults to a webcam mock:

```bash
python demo.py --mock --target tag --target-tag 12 --home-tag 12 --no-photon
```

### 3. Use the Embedded Droid SDK

With an authenticated `droid` CLI or `FACTORY_API_KEY`:

```bash
python -m ground_station.agent.session --check-sdk
python -m ground_station.agent.session --target tag --target-tag 12 --home-tag 12 --no-voice --no-photon
```

The check opens no hardware and runs no model turn. The second command requests a webcam-mock survey and needs `XAI_API_KEY` for real image analysis. Add `--fake-analysis` for explicitly simulated results without xAI. Real SDK-controlled flight requires explicit `--allow-real-flight` and local `y` approval. Space latches a stop; `q` quits.

Run `python tests/live_droid_pipeline.py` for a headless authenticated SDK smoke test. It uses one real Droid session and simulated image/Photon providers, opens no devices, and sends no real messages.

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
