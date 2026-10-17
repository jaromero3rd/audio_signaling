# AgriDrone: Voice-Directed Field Survey Agent

AgriDrone is a HackHarvard 2026 prototype for autonomous drone surveys guided by voice commands. You speak a request ("start survey"), the drone captures field photos, analyzes crop health, makes intelligent decisions, and alerts you to problems—all coordinated through voice.

## What We Built

A complete field-to-alert loop with three working layers:

- **Voice layer**: Say "start survey" and the drone listens, executes, reports back (via Grok).
- **Flight layer**: Autonomous takeoff, target search, photo capture, and landing (Tello drone + local control loop).
- **Decision layer**: Captured photos feed into crop-health analysis and feature evaluation (Chalk), ready to trigger alerts or operator handoffs (Photon).

The demo flies a mock or real Tello drone, detects wheat heads or AprilTags, captures photos, and prepares them for cloud analysis. You can pilot by voice or let the drone run in full-auto mode.

## What's Implemented & Working

| Feature | Status | Try it |
|---------|--------|--------|
| **Grok Voice control** | Fully working | Speak commands; drone responds |
| **Local flight control** | Fully working | Takeoff → search → photo → land via Tello or webcam mock |
| **Wheat/AprilTag detection** | Fully working | Live YOLO detection and steering |
| **Photo capture & logging** | Fully working | Saves still images, records paths |
| **Chalk feature evaluation** | Helpers ready | Health scoring and decision rules defined |
| **Daytona cloud sandbox** | SDK wrapper ready | Can run analysis scripts in the cloud |
| **Mission state storage** | Chalk Volume helpers ready | JSON snapshots and decision logs on Volume |
| **Embedded Droid session** | Not yet built | Hooks for runtime agent planned |
| **Photon alerts** | Not yet built | Placeholder for operator notifications |
| **End-to-end photo → decision → alert** | Not yet wired | Pieces exist separately |

**Key point**: The pieces work independently. The full loop (capture → analyze → decide → alert) is ready to wire together.

## How They're Integrated

### The Voice-Guided Mission Flow

```text
You:  "Start survey"
        ↓
    Grok Voice (listens and interprets)
        ↓
    Local SurveyMission state machine
    (takes off, searches for target, approaches)
        ↓
    Webcam or Tello camera feeds wheat/tag detector
        ↓
    Steering calculations adjust yaw, pitch, throttle
        ↓
    Photo captured when target is centered
        ↓
    Path logged in mission memory
        ↓
    (Future) Chalk analyzes health, Droid decides, Photon alerts
```

Each layer is a separate Python module. Grok owns voice; the mission controls flight; the detector steers toward crops. Photos are the handoff point between the local loop and cloud analysis.

## Why We Chose Each Service

### Grok (xAI)
- **What it does**: Realtime voice sessions that understand natural requests and call custom tools.
- **Why we need it**: A drone shouldn't require a UI or joystick. Farmers want to say "check the north field" and have it work.
- **How it works**: WebSocket connection streams your voice, Grok transcribes and interprets, and calls our `start_survey` / `get_mission_status` / `abort_mission` tools. We get conversational AI + structured task dispatch for free.

### Chalk (Canonical)
- **What it does**: Online feature computation and schema-backed decision rules.
- **Why we need it**: Crop health is not a single number—it's health score + soil moisture + pest pressure + weather. Chalk lets us define these computations once and reuse them reliably.
- **How it works**: We upload a photo and health estimate; Chalk computes derived features (`healthy` / `watch` / `critical`) and recommended actions (`continue` / `reimage` / `alert-operator`). Same logic runs in the cloud, on the edge, or in reports.

### Daytona (Factory)
- **What it does**: Ephemeral cloud sandboxes for running arbitrary code.
- **Why we need it**: Analysis code (crop modeling, ML inference, data processing) shouldn't run on the drone or the laptop. Daytona spins up a sandbox, we send Python scripts, get results back, and tear down.
- **How it works**: The Droid agent (still building) will generate analysis scripts based on mission context; Daytona runs them safely and isolated, and the sandbox auto-cleans after 15 idle minutes. Costs scale to actual compute, not always-on servers.

### Photon
- **What it does**: Unified alert/messaging platform for operator notifications.
- **Why we need it**: When the drone finds a problem, someone needs to know. Photon can send a photo, health score, and recommended action to a phone/dashboard in one API call.
- **How it works**: Mission stores the alert in Photon; operators see it on their device with enough context to act (photo + score + field ID + recommendation).

### Chalk Volume (for mission state)
- **What it does**: Durable JSON storage of mission snapshots and decision logs.
- **Why we need it**: If the laptop crashes mid-mission, we want to know what happened and resume from the last good state.
- **How it works**: After each decision, we append a log entry; the latest survey result is cached separately. Cloud-backed, so it survives restarts and can be queried later.

### Droid (Factory)
- **What it does**: An AI agent that sits inside your runtime and generates safe, task-specific code.
- **Why we need it**: Instead of hardcoding every decision rule, Droid will reason about mission context ("we're at 40% battery, target is 500m away, weather is changing") and generate the right next action—inspect more fields or land now.
- **How it works**: Not yet implemented, but the flow is: photo + mission state → Droid session → generated script → Daytona execution → validated action → local control or alert.

## Quick Start

### 1. Webcam Mock (No Drone, No Cloud)

Easiest way to see the full flow:

```bash
python demo.py --mock --target tag --target-tag 12 --home-tag 12 --no-voice --auto
```

You'll need:
- Python 3.9+
- OpenCV (for display)
- A printed AprilTag ID 12 (generate with `ground_station/print_apriltag.py --id 12`)

This opens your webcam and prints simulated flight commands. Point the tag at the camera and watch it steer toward it, "capture" a photo, and "land."

### 2. Add Voice (Requires xAI API Key)

Omit `--no-voice` and `--auto`:

```bash
export XAI_API_KEY=your_key_here
python demo.py --mock --target tag --target-tag 12 --home-tag 12
```

Now say "start survey" into your microphone. The drone will listen and respond.

### 3. Real Tello Drone

Remove `--mock`:

```bash
python demo.py --target tag --target-tag 12 --home-tag 12 --no-voice --auto
```

**Requires**:
- Tello within Wi-Fi range (connected to the laptop)
- Battery charged
- Clear flying space (5m+ radius)
- A printed AprilTag visible to the camera

**Be cautious**: this actually flies. Review the code and test indoors first.

## For Details

See [README_TECHNICAL.md](README_TECHNICAL.md) for:
- Full integration status (what's wired, what's not)
- Deep dive into each component (code structure, API, limitations)
- All entrypoints and smoke tests
- Repository structure
- Schema definitions and feature resolvers

## License and Attribution

HackHarvard 2026 submission. Uses the [Global Wheat Head Dataset 2021](https://kaggle.com/datasets/vbookshelf/global-wheat-head-dataset-2021) (CC BY 4.0) and YOLOv8 (Ultralytics, AGPL-3.0).
