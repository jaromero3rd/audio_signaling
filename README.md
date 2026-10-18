# AgriDrone: Photon-Directed Field Surveys

A prototype with two independent chains: operator activation over iMessage, and
a field data stream for photos, telemetry, and crop summaries.

| Concern | Provider |
| --- | --- |
| Operator activation (command chain) | Phone iMessage → Photon inbound → DroidVoiceGateway → Droid |
| Mission decisions and crop-health image analysis | One persistent Factory Droid SDK session, native image input |
| Field data stream | Drone/camera/sensors → host → optional Droid score + Chalk weather → store |
| Data-chain alerts back to the operator | Photon outbound: text, survey JPEG, optional ElevenLabs voice note |
| Optional decision features | Chalk, or explicitly labeled local thresholds |

The command chain activates the mission. The data stream never starts a flight.
No ElevenAgents voice command path.

**Photon cannot place calls.** An urgent alert is text marked URGENT plus an
optional audio attachment, not a telephone call. No telephony integration is used.
Crop-health scores are uncertain visual estimates, not verified diagnoses.

## Command chain (activate)

```text
operator iMessage text (or phone dictation → text)
    → Photon direct space (inbound)
    → PhotonInbound / operator HTTP ingest → DroidVoiceGateway
    → persistent Droid queues start_survey
    → local y approval
    → drone adapter takeoff
```

Spacebar on the laptop remains the fast stop. An iMessage abort is slower than
local stop.

## Data stream (separate)

```text
camera → local AprilTag/YOLO steering → captured image
    → same Droid session: native Image attachment (only with --crop-analysis)
    → strict JSON → host validation → optional Chalk / history
    → same turn: decision tool + matching final JSON
    → bounded reimage / Photon outbound alert / return home / land
```

Default mode does not upload photos: Droid handles high-level requests while
AprilTag/YOLO steer locally. `--crop-analysis` enables the image turn and a
fixed 90-second hover deadline. Reimages do not renew that deadline.
After accepting the Droid score, crop analysis joins it with live NOAA/NASA
weather through Chalk, stores the joined record when `CHALK_CLIENT_ID` is set,
and sends the insight and JPEG through Photon (optional ElevenLabs clip).
Invalid JSON, a failed model turn, or narration without a decision stops the
mission and requests landing. Emergency stop bypasses cloud latency.

Image decisions and alerts are staged until the final assessment validates.
The host controls paths, recipients, alert formatting, limits, and approvals.
Real starts and extra photos require local `y` approval.
Only the camera loop calls hardware. `--allow-real-flight` is also required.

## Setup and safe checks

Use Python 3.10+ and install `requirements.txt`. Authenticate the local `droid`
CLI. Copy `.env.example` privately and follow [SETUP.md](SETUP.md) to configure
Photon (inbound commands + outbound alerts).

Offline, fake-only validation, with no model calls, messages, camera, or drone:

```bash
python -m unittest discover -s tests -v
```

Webcam demo with real Factory inference but no messages:

```bash
python demo.py --mock --target tag --target-tag 12 --home-tag 12 \
  --fake-analysis --no-photon
```

`--fake-analysis` is explicitly simulated and forbidden on real hardware.
It is not a fallback for a failed model. A webcam demo is not a headless test.

With Photon configured, text the agent `survey` / `abort`. To enable native
image analysis, replace `--fake-analysis` with `--crop-analysis`.
Both use the same persistent Droid session. Wheat poster YOLO (320 ONNX):
`--target wheat --wheat-model poster`.

Photon credentials enable **real iMessages even in mock mode**, labeled MOCK.
Keep `--no-photon` for local-only runs. Critical analysis alerts requested by
Droid get text plus a voice note when TTS is configured. Non-critical alerts
are text-only unless the operator passes `--voice-notes`.

The authenticated smoke test is opt-in:

```bash
python tests/live_droid_pipeline.py
```

It sends a generated PNG to the real Factory session, with fake Photon and
ElevenLabs transports and no hardware. It is not part of fake-only validation.

## Limits and references

- Native images: JPEG, PNG, GIF, WebP, nonempty, at most 5 MiB.
- Assessments: finite score in `[0, 1]`, nonblank summary up to 3,000 characters.
- One survey, two extra photos, 12 mission-tool calls per turn, 16 turns.
- Approval expires after 60 seconds. Space stops; `q` exits.
- Voice notes: in-memory MP3, Flash v2.5, at most 3,500 text characters / 5 MiB.
  Missing or failed TTS does not prevent the independent text attempt.
- Notifications are best effort, not a durable outbox. Accepted is not delivered.

See [AGENTS.md](AGENTS.md), [runtime guide](ground_station/agent/README.md),
[technical guide](README_TECHNICAL.md), and [requirements](prd.md).

## Field networking

Default radios use Mac Wi-Fi for Tello and USB Personal Hotspot for internet.
The UNO Q joins that phone's Wi-Fi and posts to the Mac on `172.20.10.0/28`.
For explicitly authorized network setup:

1. Connect the phone by USB, trust the Mac, and enable Personal Hotspot.
2. Run `python -m drone_interface.field_links --apply --tello-ssid TELLO-7`.
   This joins Tello and prioritizes the USB hotspot for internet.
3. Start `python -m ground_station.sensor_recv` for board telemetry.
4. Copy the UNO Q example config privately, set hotspot SSID/password, and
   leave `ground_station_url` as `"auto"`. Mic transfer is off by default.

Keep the phone within board Wi-Fi range. `--dongle N` remains a fallback.
Network setup does not authorize flight.

HackHarvard 2026 prototype. Uses the Global Wheat Head Dataset 2021 (CC BY 4.0)
and Ultralytics YOLO (AGPL-3.0). These controls are not flight-safety certification.
