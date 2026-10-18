# AgriDrone setup

Keep credentials in ignored `.env` files or a secret manager, never chat or Git.
Setup does not authorize test messages, model charges, or flight.

## Factory Droid

Install the root Python requirements in a Python 3.10+ virtual environment.
Run `droid` and sign in with your own Factory account. The runtime uses local
CLI authentication; `FACTORY_API_KEY` is optional for headless authentication.
Select an image-capable Factory model with `--droid-model` if the default
does not support vision. The SDK is pinned to `droid-sdk[mcp]==0.5.0`.
Keep mission control local: remote Computers cannot reach the laptop's
loopback MCP tools.

## Photon iMessage command input

Mission starts come from the operator's **direct iMessage** to the Photon agent
number (not ElevenLabs). The host subscribes to the project event stream and
classifies plain text:

- start: `survey`, `start`, `fly`, `go`, `wheat`, …
- abort: `abort`, `stop`, `land`, `emergency`, …

Queued is not executed. Real starts/photos still need local `y` approval.
Space is emergency stop. Configure Photon as below before flight.

## Optional ElevenLabs alert voice notes

Not required for flight. If you pass `--voice-notes` and set
`ELEVENLABS_API_KEY` + `ELEVENLABS_TTS_VOICE_ID`, critical analysis alerts can
include Flash v2.5 MP3. Missing TTS never blocks Photon text.

**Photon cannot place calls.** Urgent means text (+ optional voice note), not
telephony.

## Photon direct iMessage space

Configure an active Photon Beta project and iMessage assignment for one agent
and one consenting operator. An existing **direct** space must contain exactly
those two identities. Save:

```dotenv
PHOTON_API_KEY=
PHOTON_PROJECT_ID=
PHOTON_ASSIGNMENT_ID=
PHOTON_AGENT_HANDLE=
PHOTON_USER_HANDLE=
# Optional: PHOTON_SPACE_ID=, to disambiguate direct spaces
```

Handles are distinct E.164 numbers. The existing `PHOTON_TEST_SENDER` alias
can supply the user handle. An explicit `--photon-env /absolute/private/file`
or `PHOTON_ENV_FILE` can reuse a private config without copying values.

Read-only configuration check, only when live access is intended:

```bash
python -m ground_station.photon --env /absolute/private/photon.env --check
```

This checks assignment and exact routing without creating a space or sending.
Do not run `--send-test` unless the operator explicitly authorizes a real message.
Mock flight is **not** mock messaging; use `--no-photon` to suppress all sends.
Attachments use multipart/related uploads and stable content-hash idempotency.
`ATTACHMENT_NOT_READY` is retried at most eight sends with a bounded delay.
Acceptance is not delivery confirmation. The queue is bounded and not durable.

## Optional Chalk and hardware

For `--chalk`, supply `CHALK_CLIENT_ID`, `CHALK_CLIENT_SECRET`, and the actual
`CHALK_ENVIRONMENT` ID; deploy the existing `evaluate_field_survey` named
query and select a deployment with `--chalk-branch` if needed.
Configured Chalk failures stop the mission, not silently fall back.
Without Chalk, thresholds remain healthy ≥ 0.7, watch ≥ 0.4, otherwise critical.

Real Tello operation requires `--allow-real-flight`, safe operator-supervised
conditions, a fresh camera frame, battery preflight, and local `y` approval.
Default radios put Tello on Mac Wi-Fi and internet on USB Personal Hotspot.
The UNO Q joins that phone's Wi-Fi; `field_links` supports explicit network
setup and `--dongle N` remains a fallback. See the README networking section.
Space latches stop; `q` exits and requests landing as needed.

## Validation

```bash
python -m unittest discover -s tests -v
```

All default regression tests use fake sessions/transports/devices. They need
no service credentials and send nothing.
`python tests/live_droid_pipeline.py` is a separate opt-in, billable Factory
image test. Photon, ElevenLabs, camera and drone remain fake in that test.
