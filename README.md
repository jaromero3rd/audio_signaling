# AgriDrone: Voice-Directed Field Survey Agent

AgriDrone is a HackHarvard 2026 prototype for voice-directed Tello field surveys. ElevenLabs transcribes operator speech, Factory Droid handles high-level mission requests, and a local vision/state-machine loop steers the drone. Optional crop analysis adds a Droid photo assessment, live NOAA/NASA weather context, survey history, and a Photon phone summary.

**This is a prototype, not a flight-safety system or verified crop diagnosis.** Code integration does not prove that hardware, cloud deployments, weather feeds, or message delivery work in your environment.

## What We Built

- **Voice layer:** ElevenLabs `scribe_v1` transcribes microphone audio. Local keyword routing queues a Droid start, requests status, or aborts without a Droid turn. Spoken abort still depends on cloud transcription; the keyboard stop does not.
- **Flight layer:** The local mission state machine handles takeoff, target approach, photo capture, home search, and landing through Tello or a webcam mock.
- **SDK controller:** One persistent Factory Droid session uses four restricted mission tools. With `--crop-analysis`, the same session assesses the attached survey photo; it never performs per-frame steering.
- **System notifications:** The host sends mission-status and Droid-requested analysis alerts through a verified private Photon assignment.
- **Features and history:** Crop-analysis runs keep local SQLite history, attempt a Chalk weather query, and fall back to the same live-weather functions on the laptop. Chalk Volume writes and phone summaries are best effort.

## Current Implementation

| Feature | Implementation status | Try it |
|---------|-----------------------|--------|
| **ElevenLabs voice** | Speech-to-text with start/status/abort routing | Set `ELEVENLABS_API_KEY` and speak |
| **Local flight control** | Tello adapter and webcam mock | Takeoff → target → photo → home/land |
| **Wheat/AprilTag detection** | Live ONNX or `.pt` detector, or AprilTag | Use the bundled ONNX model, supply weights, or use a printed tag |
| **Photo capture & logging** | Saves local stills and paths | View `captures/` |
| **Droid photo score** | SDK image attachment and structured score | `--crop-analysis` |
| **Weather context** | Chalk query or local fallback joins the score with NWS, GFS, and NASA POWER | `python -m ground_station.weather_smoke` checks the public feeds without Chalk |
| **Survey persistence** | Local SQLite plus optional Chalk Volume latest-field record and decision log | Configure Chalk service credentials; no automatic flight resumption |
| **Status and analysis alerts** | Host notifier sends fixed status texts and Droid-requested analysis texts | Configure a verified private Photon assignment |
| **Phone summary** | Separate sender attempts text, survey photo, and ElevenLabs audio | Requires ElevenLabs credentials, Bun, and configured `photon-imessage-agent` routing |
| **UNO Q sensors** | Separate Bluetooth and HTTP telemetry experiments | See [sensor setup](dht11_temp_only/README.md) and [field station](uno_q_field_station/README.md) |

These are implementation boundaries, not a claim that every service is configured, running, or flight-tested.

## How They're Integrated

### Default Mission, No Crop Analysis

```text
Operator speech → ElevenLabs → local routing → persistent Droid
                                                |
                                   mission tools / host checks
                                                |
             Camera → AprilTag/YOLO → local steering → photo
                                                |
                                     home search → landing
```

`demo.py` defaults to Droid control and a webcam mock. Crop analysis is **off** unless `--crop-analysis` or `--fake-analysis` is supplied. `--no-voice` requests a survey on startup rather than waiting for speech. The active mission does not call xAI; older Grok helpers and the separate developer coordination service remain in the repository.

### Optional Crop-Analysis Flow

```text
Saved photo → fixed-deadline analysis hold
                    |
       same Droid session → validated score/summary
                    |
       Chalk weather query or local live-weather fallback
                    |
       best-effort Volume record / phone summary
                    |
       SQLite history → same Droid → bounded decision
                    |
       reimage / analysis alert / return home / land
```

SDK/model work runs off the camera/RC thread. The host validates the score and capture before acting. Reimages never renew the 90-second analysis hold. `--fake-analysis` simulates only the image score, not weather, storage, Droid calls, or phone sends.

## Why We Chose Each Service

### ElevenLabs
`scribe_v1` decodes operator audio. `eleven_multilingual_v2` speaks the Chalk insight on the phone. Neither call flies the drone or scores the crop.

### Chalk
Online resolvers compute `healthy` / `watch` / `critical`, severity, and recommended action from the image score. A separate resolver adds live NOAA NWS, Open-Meteo GFS, and NASA POWER context at the David Rubenstein Treehouse. Weather can suggest reimaging through `justified_action` without changing the image-only labels.

The current crop-analysis launcher attempts this weather query even without `--chalk`. A failed query falls back locally and labels its source. `--chalk` / `--chalk-branch` control a separate image-only named-query path, not the launcher's weather query. Deployment and outbound cloud access need separate validation.

### Photon
The host notifier sends fixed status texts and tool-requested analysis texts to a configured private operator. A separate summary path uses `photon-imessage-agent` to upload the photo and an ElevenLabs audio clip, then send the insight text. The Chalk resolver attempts this first; the laptop retries when success was not reported. Acceptance is not confirmed delivery.

**Current limitation:** `--no-photon` disables the host notifier, not the separate crop-summary path. To avoid real messages, use the no-analysis examples below and do not enable crop analysis with live messaging credentials. Mock and fake-analysis runs are not messaging sandboxes.

### Chalk Volume
After a joined survey, the host attempts `fields/<field_id>.json` and `decision_log.jsonl` writes when `CHALK_CLIENT_ID` is set. Full service credentials are still required; missing credentials or write failures are recorded as `volume_error`. The mission does not save or restore its flight snapshot automatically.

### Droid (Factory)
The official Python SDK supplies high-level start/land decisions and optional structured photo assessment. The host bounds commands, queues, timeouts, turns, and extra photos. See [runtime details](README_TECHNICAL.md#how-droid-is-used-at-runtime) for the current tools and approval exceptions.

## Quick Start

Use a Python virtual environment, preferably Python 3.11+ (some analysis helpers use `asyncio.timeout`). Commands below run from the repository root:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

Copy [.env.example](.env.example) to a private `.env` if one does not already exist. Configure only the services you intend to use; never commit credentials.

### 1. Webcam Mock (No Drone, No Cloud)

```bash
python demo.py --controller voice --mock --target tag --target-tag 12 --home-tag 12 --no-voice --auto --no-photon
```

This opens the webcam/display and prints simulated flight commands. It requires neither Factory nor ElevenLabs authentication. Generate a matching printable tag with `python ground_station/print_apriltag.py --id 12`. It is not a headless test.

### 2. Add Voice

Configure `ELEVENLABS_API_KEY` privately, authenticate the `droid` CLI, and ensure a working microphone. The default controller is Droid and defaults to a webcam mock:

```bash
python demo.py --mock --target tag --target-tag 12 --home-tag 12 --no-photon
```

### 3. Use the Embedded Droid SDK

With an authenticated `droid` CLI or `FACTORY_API_KEY`:

```bash
python -m ground_station.agent.session --check-sdk
python -m ground_station.agent.session --target tag --target-tag 12 --home-tag 12 --no-voice --no-photon
```

The check opens no hardware and runs no model turn. The second command requests one webcam-mock survey, without crop analysis. Space latches a stop; `q` or Escape quits.

To assess a captured photo, add `--crop-analysis` and use an image-capable Droid model. This also enables live weather, best-effort storage, and the separate phone-summary attempt. `--fake-analysis` substitutes a clearly labeled simulated score and is rejected on real hardware. Read the [crop-analysis and messaging limits](README_TECHNICAL.md#current-gaps) before enabling either option.

Run `python tests/live_droid_pipeline.py` for a headless authenticated SDK smoke test. It uses one real Droid session and simulated image/Photon providers, opens no devices, and sends no real messages.

### 4. Configure System-to-User Photon Messages

```bash
python -m ground_station.photon --env /absolute/path/to/private/photon.env --check
python -m ground_station.agent.session --photon-env /absolute/path/to/private/photon.env
```

The first command only verifies routing. The second runs the mock and sends real mission-status iMessages labeled `MOCK`. The crop-summary sender additionally needs Bun 1.3+, dependencies and private configuration in [photon-imessage-agent](photon-imessage-agent/README.md), a saved operator-space checkpoint, `ELEVENLABS_API_KEY`, and `ELEVENLABS_VOICE_ID`. A `--photon-env` file configures the host notifier; it does not by itself configure that separate sender.

### Real Tello Operation

Review the existing flight implementation and network setup before enabling hardware. Built-in Wi-Fi supplies internet; the Tello uses the USB dongle selected by `--tello-iface`.

The Droid entrypoints require `--allow-real-flight` to enable Tello. With voice enabled, starts and extra photos wait for local `y` approval. **In the current code, `--no-voice` auto-approves those requests and can start flight without a keypress.** Do not use it as a safe readiness check.

The explicit legacy `--controller voice` path does not require `--allow-real-flight`: omitting `--mock` selects real Tello operation. Use a controlled area, charged battery, and operator supervision. These are prototype controls, not a flight-safety certification.

## Verification and Other Components

```bash
python -m unittest discover -s tests -v
python -m unittest discover -s test -p 'test_*.py' -v
```

The two directories contain offline tests for the mission/controller/Photon boundary and additional voice/weather/summary helpers. Authenticated SDK checks, public-weather smoke calls, real messaging, and hardware trials are separate.

- [UNO Q Bluetooth sensors](dht11_temp_only/README.md): DHT11/light telemetry to the laptop.
- [UNO Q HTTP field station](uno_q_field_station/README.md): telemetry and raw microphone transfer, not the mission's speech-input path.
- [Grok team](grok-team/README.md): separate local developer coordination and checked bot-branch publishing.
- `send.py` / `recv.py`: independent ggwave audio-text experiments, not a flight command channel.

## For Details

- [SDK runtime and Photon guide](ground_station/agent/README.md): additional SDK and host-notifier setup. Some older Grok/approval descriptions lag the current code; use the current-mode details in the technical README.
- [README_TECHNICAL.md](README_TECHNICAL.md): modules, cloud helper boundaries, entrypoints, and repository structure.
- [PRD](prd.md): target requirements, including work that is not yet implemented.

## License and Attribution

HackHarvard 2026 submission. Uses the [Global Wheat Head Dataset 2021](https://kaggle.com/datasets/vbookshelf/global-wheat-head-dataset-2021) (CC BY 4.0) and YOLOv8 (Ultralytics, AGPL-3.0).
