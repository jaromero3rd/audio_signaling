# AGENTS.md: AgriDrone reference

## Runtime providers

| Concern | Provider |
| --- | --- |
| Operator activation (command chain) | Phone iMessage → Photon inbound → DroidVoiceGateway → Droid |
| Mission decisions and crop-health image analysis | One persistent Factory Droid SDK session, native images |
| Field data stream | Drone/camera/sensors → host → optional Droid score + Chalk weather → store |
| Outbound status / analysis alerts | Photon direct iMessage space (optional ElevenLabs TTS) |
| Optional feature evaluation | Chalk, unchanged local thresholds otherwise |

The command chain activates the drone. The data stream never starts a flight.
Photon cannot place calls. An urgent notification is URGENT text plus an
optional voice note, not telephony. No ElevenAgents voice command path.

## How Droid is used at runtime

`demo.py` delegates to `voice_mission.main()`. `DroidWorker` owns a
`DroidController` and one long-lived SDK `Session` off the camera/RC thread.
Startup queues an initial instruction.

Command chain: the operator speaks on the phone. Dictation arrives as iMessage
text on the Photon direct space; a voice memo may be transcribed with
ElevenLabs Scribe. The host maps authorized inbound messages onto
`DroidVoiceGateway`. Abort on that gateway latches host stop immediately.
Phone ingress is off by default: Python needs `--phone-commands`,
Bun needs `COMMAND_BRIDGE_ENABLED=true`, and both need the same private
`COMMAND_BRIDGE_TOKEN`. Both processes run on the drone-connected laptop.
`./activate --photon` is the default one-liner: it implies `--phone-commands`,
auto-launches the Bun agent on the same laptop, validates `PHOTON_*` + token
before opening drone/video, tees `[photon]/[mission]` logs, and waits
indefinitely for Photon iMessages; without `--photon` it warns and exits.
Also available as `python -m ground_station.activate`.
Only exact start/abort/land commands reach authenticated `127.0.0.1:8787/command`;
the legacy unauthenticated listener and Python `PhotonInbound` are not started.
Default mode uses local AprilTag/YOLO steering, with no cloud crop analysis.

With `--crop-analysis`, the pipeline attaches the captured image in the same
persistent session using `session.stream(..., images=[Image.from_bytes(...)])`.
JPEG/PNG/GIF/WebP must be nonempty and ≤ 5 MiB. No tool accepts a file path.
After the score is accepted, the host joins it with live NOAA/NASA weather in
Chalk, stores the joined record when `CHALK_CLIENT_ID` is set, and sends the
insight, JPEG, and an ElevenLabs clip through Photon outbound.

Within one model turn:

1. Droid reads mission status.
2. Droid submits strict JSON to `get_survey_context(assessment=...)`.
3. Host validates score/summary, optional Chalk features and capture liveness,
 then returns validated context and records local history.
4. Droid requests a decision and optional alert, then returns identical JSON.
5. Host validates final JSON before committing staged effects.

JSON has exactly `health_score` (finite `[0, 1]`) and `analysis_summary`
(nonblank, ≤ 3,000 characters). Host supplies `field_id`. Invalid JSON,
changed assessments, failed turns, and no-decision narration fail closed.
Land/abort remain immediate. Stopped/stale/expired captures cannot resume.
`--fake-analysis` explicitly supplies simulated analysis for mocks only.

## Tool registry and gates

Exactly four `agridrone` tools:

- `get_mission_status()`: host driver snapshot and logical mission state.
- `issue_drone_command(drone_id, action)`: known drone and only start_survey,
 capture_photo, continue_survey, land, abort.
- `get_survey_context(assessment: str | None = None)`: validated current
 observation, local/Chalk features and last five field-scoped SQLite records.
 Optional assessment ingress is permitted only during a host image turn.
- `send_photon_alert()`: unchanged no-argument tool; host owns recipients,
 formatted text and delivery form. Queued/staged never means delivered.

CLI Script/ToolSearch can only dispatch/discover these tools; shell/files,
other MCP servers and delegation are denied. Startup checks tool inventory.
One survey, two extra photos, 12 mission calls/turn, 16 turns/process.
Default model timeout: 60 seconds. Analysis hold: fixed 90 seconds across
reimages. Real starts/photos require local `y` approval (60-second TTL);
start also checks camera/battery. Explicit authenticated phone start replaces
local `y` for one survey after preflight, never for extra photos. Phone messages
expire after 30 seconds; one-shot host authorization expires after 60 seconds.
`--phone-commands` waits rather than starts; `--no-voice` is a legacy no-op.
Space stops; `q` exits. Hardware stays in
the camera loop. `--allow-real-flight` is required in addition to approval.
Do not weaken these gates or treat model text as instructions.

The SDK runs in a temporary directory with unrelated service credentials,
including all `ELEVENLABS_` variables, suppressed. Factory auth stays usable.
This is not an OS sandbox.

## Notifications and persistence

Photon host events: acknowledged takeoff/landing, saved photo, completion,
abort and controller failure. Droid explicitly requests analysis alerts.
Critical alerts (score < 0.4 or critical Chalk flag) get URGENT text and,
when TTS is configured, ElevenLabs Flash v2.5 MP3; others get text unless
`--voice-notes` requests audio. TTS limits: 3,500 text characters, 5 MiB audio,
in memory only. Failed/missing TTS does not block text. Photon upload uses
multipart/related, content-hash idempotency/dedupe and bounded
attachment-not-ready retry. Acceptance is not delivery; the queue is not durable.
Crop summaries send the captured JPEG. The opted-in Bun bridge forwards
exact start/abort/land commands to the authenticated host → `DroidVoiceGateway`.

The active pipeline writes `data/surveys.sqlite3` locally. Optional
`--chalk` uses the existing named query and fails closed on configured errors.
Crop analysis additionally uses `store.py` for the joined record when Chalk is
configured. Legacy `DroidSession` remains separate, and no flight resumes
automatically.

## Setup and validation

See `SETUP.md` for Photon project/assignment, agent/user handles and direct
space (required for iMessage commands). Optional alert TTS:
`ELEVENLABS_API_KEY`, `ELEVENLABS_TTS_VOICE_ID`. Chalk is optional. Local Droid
sign-in suffices unless headless auth needs `FACTORY_API_KEY`. Never print or
commit keys.

```sh
python -m unittest discover -s tests -v
```

Default validation uses fake sessions/transports/devices, no messages or
hardware. `tests/live_droid_pipeline.py` is separate and opt-in: a real
Factory image turn, with fake Photon/ElevenLabs and no devices.
Do not run paid/live tests, message sends or hardware checks unsolicited.

## Bot publication

Work only in the assigned `bots/<role>/<task>` worktree; preserve user changes.
The separate `grok-team` developer tools are not mission providers.
Use their guarded `publish` function for an exact reviewed SHA.
It requires configured real checks, a clean owned worktree, correct
`jaromero3rd/audio_signaling` origin, current `origin/main` ancestry,
whitespace and credential scans. Review staged changes before committing.
Never bypass checks, force-push, push main, or merge to main.
