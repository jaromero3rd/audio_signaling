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
number (not ElevenAgents). The authenticated Bun-to-Python bridge is the
only mission ingress. Neither the legacy `OperatorCommandServer` nor the
standalone Python `PhotonInbound` is started by `demo.py`.

1. Use the Photon direct space in the next section. The operator's iPhone is
   the microphone. Dictation sends plain text; a voice memo sends audio.
2. Run both the Bun agent and Python mission on the **same drone-connected
   laptop**, using the opt-in configuration below. Another laptop cannot
   discover or control the drone through this loopback bridge.
3. Only the configured operator's exact `start survey`, `abort`, or `land`
   utterances are forwarded. A phone start authorizes one survey without a
   second local `y`, only after fresh video and battery ≥ 20%. Extra photos
   and starts not authorized by phone still require local `y` on real hardware.
   Queued start never means takeoff.
4. Spacebar on the laptop remains the fast emergency stop. An iMessage abort
   is slower than local stop.
5. Photon cannot place calls. This is iMessage in and iMessage out.

Phone ingress is **off by default**. Both Python `--phone-commands` and Bun
`COMMAND_BRIDGE_ENABLED=true` are required. `--phone-commands` waits for the
phone instead of automatically requesting a survey. Laptop voice activation
is not used; `--no-voice` is a legacy no-op. Field PCM is data, not activation.

### Enable the same-laptop bridge

Configure Photon in `photon-imessage-agent/.env` for one operator
(`PHOTON_USER_HANDLE`, or matching legacy `PHOTON_TEST_SENDER`).
Generate a separate ignored token file once from the repository root.
This writes the random token privately without printing it or overwriting
an existing file:

```bash
umask 077
python3 - <<'PY'
import secrets
from pathlib import Path
with Path(".env.phone-command").open("x") as file:
    file.write("COMMAND_BRIDGE_ENABLED=true\n")
    file.write("COMMAND_BRIDGE_TOKEN=" + secrets.token_urlsafe(32) + "\n")
    file.write("COMMAND_BRIDGE_PORT=8787\n")
PY
```

In two terminals on the drone-connected laptop:

```bash
# Repository root: webcam mock, no Python outbound notifications
python demo.py --mock --target tag --no-photon \
  --phone-commands --phone-command-env .env.phone-command

# Separate terminal, from photon-imessage-agent:
bun --env-file=.env --env-file=../.env.phone-command run src/index.ts
```

This still uses real Factory inference and Photon iMessages. Do not launch it
as an offline test. Wait for both Python's `Phone commands ready` and Bun's
`READY` subscription acknowledgment, then send `start survey` from the
configured phone to its assigned agent number. `--no-photon` disables Python
outbound notifications only, not the Bun process's replies or opt-in ingress.

For supervised real flight, replace `--mock` with `--allow-real-flight`.
The laptop must already own the Tello connection and have working video.
The phone does not select or discover drones. `--allow-real-flight` is always
required for real hardware, even with phone authorization.

The bridge binds only `127.0.0.1:8787/command` and requires the shared bearer
token. Change `COMMAND_BRIDGE_PORT` in the shared file if needed; remote URLs
are not supported. Keep the file private and rotate the token if exposed.
Messages must carry Photon's original receipt time, at most 30 seconds old
(5 seconds future skew allowed). Expired/replayed commands cannot create
another start; in-memory receipt dedupe is bounded. Each process permits one
survey. Voice memos use the same grammar/freshness checks after transcription;
dictation text is more reliable. Questions, negations and compound requests
are not commands. Abort/land latch host stop without a model turn, but network
and event-queue delays mean they do not replace the local emergency stop.
## Optional ElevenLabs alert voice notes

Not required for flight. If you pass `--voice-notes` and set
`ELEVENLABS_API_KEY` + `ELEVENLABS_TTS_VOICE_ID`, critical analysis alerts can
include Flash v2.5 MP3. Missing TTS never blocks Photon text.

**Photon cannot place calls.** Urgent means text (+ optional voice note), not
telephony.

## Photon direct iMessage space

This space is both command ingress (phone → Photon → Droid) and data-chain
egress (status, JPEG, voice note). Configure an active Photon Beta project
and iMessage assignment for one agent and one consenting operator. An
existing **direct** space must contain exactly those two identities. Save:

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
Mock flight is **not** mock messaging. `--no-photon` disables Python outbound;
the separately launched Bun agent still sends command replies.

## Chalk (optional)

For `--chalk`, supply `CHALK_CLIENT_ID`, `CHALK_CLIENT_SECRET`, and the actual
environment id in `CHALK_ENVIRONMENT`. The runtime uses the deployed named
query and select a deployment with `--chalk-branch` if needed.

## Real flight

Real Tello operation requires `--allow-real-flight`, safe operator-supervised
conditions, a fresh camera frame, battery preflight, and either local `y`
or the explicit one-shot phone start authorization above.
Default radios put Tello on Mac Wi-Fi and internet on USB Personal Hotspot.
The UNO Q joins that phone's Wi-Fi; `field_links` supports explicit network
setup and `--dongle N` remains a fallback. See the README networking section.

Wheat poster detector (320 ONNX):

```bash
python demo.py --target wheat --wheat-model poster ...
```
