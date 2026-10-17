# AGENTS.md: AgriDrone reference

## Runtime providers

| Concern | Provider |
| --- | --- |
| Mission decisions and crop-health image analysis | One persistent Factory Droid SDK session, native images |
| Operator speech and spoken replies | ElevenAgents, Scribe + ElevenLabs TTS configured in dashboard |
| User text, image/audio attachments | Photon direct iMessage space |
| Optional feature evaluation | Chalk, unchanged local thresholds otherwise |

Photon cannot place calls. An urgent notification is URGENT text plus a voice
note, not telephony. No separate image-analysis provider is used.

## How Droid is used at runtime

`demo.py` delegates to `voice_mission.main()`. `DroidWorker` owns a
`DroidController` and one long-lived SDK `Session` off the camera/RC thread.
Startup queues an initial instruction. ElevenAgents starts queue intent through
`DroidVoiceGateway`; abort bypasses model latency and stops immediately.
Default mode uses local AprilTag/YOLO steering, with no cloud crop analysis.
`--no-voice` asks Droid for one survey and does not bypass local approval.

With `--crop-analysis`, the pipeline attaches the captured image in the same
persistent session using `session.stream(..., images=[Image.from_bytes(...)])`.
JPEG/PNG/GIF/WebP must be nonempty and ≤ 5 MiB. No tool accepts a file path.

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
start also checks camera/battery. Space stops; `q` exits. Hardware stays in
the camera loop. `--allow-real-flight` is required in addition to approval.
Do not weaken these gates or treat model text as instructions.

The SDK runs in a temporary directory with unrelated service credentials,
including all `ELEVENLABS_` variables, suppressed. Factory auth stays usable.
This is not an OS sandbox.

## Notifications and persistence

Photon host events: acknowledged takeoff/landing, saved photo, completion,
abort and controller failure. Droid explicitly requests analysis alerts.
Critical alerts (score < 0.4 or critical Chalk flag) get URGENT text and
ElevenLabs Flash v2.5 MP3; others get text unless `--voice-notes` requests audio.
TTS limits: 3,500 text characters, 5 MiB audio, in memory only.
Failed/missing TTS does not block text. Photon upload uses multipart/related,
content-hash idempotency/dedupe and bounded attachment-not-ready retry.
Acceptance is not delivery; the queue is not durable.
The client supports images, but automatic photo sending and incoming Photon
mission commands are not wired.

The active pipeline writes `data/surveys.sqlite3` locally. Optional
`--chalk` uses the existing named query and fails closed on configured errors.
`store.py` Chalk Volume helpers and legacy `DroidSession` are separate, lazy
imports, not active mission persistence. No flight resumes automatically.
Standalone `chalk_eval_mission.py` is no longer called by `voice_mission`.

## Setup and validation

See `SETUP.md` for dashboard client-tool declarations, Scribe/TTS, PCM16 mono
16 kHz, safety-prompt override and credentials:
`ELEVENLABS_API_KEY`, `ELEVENLABS_AGENT_ID`, `ELEVENLABS_TTS_VOICE_ID`.
Photon needs its existing project/assignment, agent/user handles and direct
space. Chalk is optional. Local Droid sign-in suffices unless headless auth
needs `FACTORY_API_KEY`. Never print or commit keys.

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
