# AgriDrone: Photon-Directed Field Surveys

A prototype with two independent chains: operator activation over iMessage, and
a field data stream for photos, telemetry, and crop summaries.

A phone-directed Tello survey photographs a crop stand. A persistent Factory
Droid session scores the still. Chalk joins that score with live NOAA weather
and NASA satellite sunlight. Photon texts the operator the insight and JPEG.

## HackHarvard 2026

Built for these tracks and sponsor challenges on [hhuh.io](https://hhuh.io/).
See [README_TECHNICAL.md](README_TECHNICAL.md) for file-level mapping.

**Hardware.** DJI Tello (or webcam mock) flies the survey. An Arduino UNO Q
rides the field kit with a DHT11 (temperature °C/°F, relative humidity, heat
index) and an LDR light sensor. The MCU posts telemetry to the laptop
(`POST /telemetry`); a USB mic and microSD log are optional data-stream
paths. Sensors never start a flight.

**Sustainability — ASUS Save the Earth.** The point is fewer wasted scouting
trips and more targeted follow-up. Local YOLO finds the wheat poster; Droid
scores the canopy; Chalk checks whether NASA/NOAA light and storm support
that score; Photon delivers the insight. The wheat-head detector was trained
on ASUS hardware, then exported to ONNX for the host (`--target wheat`).
ASUS content posts: public, tag @asususa / @hhuh.hackathon, hashtags
`#ASUSEducation` `#hackharvard2026` `#agentcomputer`.

**Factory Droid SDK.** Droid is the in-product mission brain: one persistent
Python `droid_sdk.Session`, native image bytes on `--crop-analysis`, and
exactly four tools (`get_mission_status`, `issue_drone_command`,
`get_survey_context`, `send_photon_alert`). Host YOLO/AprilTag steers RC.
The README_TECHNICAL section “How Droid is used at runtime” is the Factory
protocol.

**Chalk.** Query-time `FieldSurvey` features (`health_flag`, `severity`,
`recommended_action`) plus a live NOAA/NASA justification. Named query
`evaluate_field_survey`. Labeled evals: `python -m ground_station.chalk_eval`.
`--chalk` fails closed.

**SpaceXAI.** Built in Cursor. NASA POWER `ALLSKY_SFC_SW_DWN` satellite
sunlight is a live Chalk input and can change the insight (reimage vs stand
by the photo). NOAA GFS shortwave is the other irradiance source.

**Photon 2.0.** The farmer’s channel: iMessage in (`start survey` / `abort` /
`land`), insight + JPEG out, optional ElevenLabs voice note on critical
alerts.

| Concern | Provider |
| --- | --- |
| Operator activation (command chain) | Phone iMessage → Photon inbound → DroidVoiceGateway → Droid |
| Mission decisions and crop-health image analysis | One persistent Factory Droid SDK session, native image input |
| Field data stream | Drone/camera/sensors → host → optional Droid score + Chalk weather → store |
| Data-chain alerts back to the operator | Photon outbound: text, survey JPEG, optional ElevenLabs voice note |
| Optional decision features | Chalk, or explicitly labeled local thresholds |

The command chain activates the mission. The data stream never starts a flight.
No ElevenAgents voice command path. Phone ingress is opt-in on both processes.
`--phone-commands` waits for the phone, even with `--no-photon`.
`--no-voice` is a legacy no-op.

**Photon cannot place calls.** An urgent alert is text marked URGENT plus an
optional audio attachment, not a telephone call. No telephony integration is used.
Crop-health scores are uncertain visual estimates, not verified diagnoses.

## Command chain (activate)

```text
operator iMessage text (or phone dictation → text)
    → Photon direct space (inbound)
    → same-laptop authenticated opt-in bridge → DroidVoiceGateway
    → persistent Droid queues start_survey
    → one-shot phone authorization + camera/battery preflight
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
Real starts and extra photos require local `y` approval,
except an explicit authenticated phone start replaces `y` for one survey after
camera/battery preflight. Only the camera loop calls hardware.
`--allow-real-flight` is still required. See [phone setup](SETUP.md#enable-the-same-laptop-bridge).

## Setup and safe checks

Use Python 3.10+ and install `requirements.txt`. Authenticate the local `droid`
CLI. Copy `.env.example` privately and follow [SETUP.md](SETUP.md) to configure
Photon (inbound commands + outbound alerts).

Default one-liner (arms and waits indefinitely for Photon):

```bash
./activate --mock --photon
./activate --allow-real-flight --photon --auto --crop-analysis --field-id field-1
```

`--photon` implies `--phone-commands` and auto-launches the Bun agent. Without it, `activate` warns and exits.

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

With the phone bridge configured, text the agent `start survey`, `abort`, or
`land`. To enable native
image analysis, replace `--fake-analysis` with `--crop-analysis`.
Both use the same persistent Droid session. Wheat poster YOLO (320 ONNX):
`--target wheat --wheat-model poster`.

Photon credentials enable **real iMessages even in mock mode**, labeled MOCK.
`--no-photon` disables Python outbound only; omit `--phone-commands` and do not
run Bun for a local-only demo. Critical analysis alerts requested by
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

UNO Q field sensors: DHT11 DATA on **D2**, photoresistor divider on **A0**,
optional microSD CS on **D10**. `python -m ground_station.sensor_recv` serves
live temperature and humidity at `/latest`. Wiring:
[uno_q_field_station/WIRING_DHT11.md](uno_q_field_station/WIRING_DHT11.md).

HackHarvard 2026 prototype. Uses the Global Wheat Head Dataset 2021 (CC BY 4.0)
and Ultralytics YOLO (AGPL-3.0). These controls are not flight-safety certification.
