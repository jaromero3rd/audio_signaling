# Embedded Factory Droid runtime

One official SDK `Session` handles operator intent and native crop images.
Operator activation is the command chain: voice on the phone → iMessage →
Photon inbound → `DroidVoiceGateway` → Droid. The field data stream (photos,
telemetry, crop scores, weather join, outbound alerts) is separate and never
starts a flight. The host owns approval, local steering, capture paths,
notification recipients, and every hardware call. See [setup](../../SETUP.md).

## Tools and authority

Exactly four mission tools are registered under `agridrone`:

| Tool | Contract |
| --- | --- |
| `get_mission_status()` | Reads host state, stop latch, capture and limits; required before non-emergency action |
| `issue_drone_command(drone_id, action)` | Only start_survey, capture_photo, continue_survey, land, abort for the known drone |
| `get_survey_context(assessment=None)` | Reads validated analysis, local/Chalk features, last five field-scoped SQLite records; image turns submit strict JSON here first |
| `send_photon_alert()` | No arguments; requests a host-formatted current-analysis notification to the fixed operator |

The optional assessment **string** contains only `health_score` and
`analysis_summary`. The host supplies `field_id`. It accepts a finite score in
`[0, 1]` and a nonblank summary up to 3,000 characters, with no extra fields.
One accepted assessment cannot be replaced during a turn.

An image turn follows this protocol:

1. Host attaches JPEG/PNG/GIF/WebP bytes, nonempty and at most 5 MiB, through
   `session.stream(..., images=[Image.from_bytes(...)])`.
2. Droid reads status and submits its JSON to `get_survey_context`.
3. Host validates it, obtains optional Chalk features, binds it to the still-live
   capture, and records it. Stale, stopped and expired captures cannot be accepted.
4. Droid reads the returned context, calls a decision tool and optionally the
   unchanged alert tool, then finishes with the **same strict JSON**.
5. Host validates final JSON before committing staged alert/decision requests.
   Requests marked `staged_until_validated` are not executed or delivered.
   Land/abort remain immediate, including before analysis validation.

No JSON-only narration is sufficient: a decision tool call is required.
Malformed or changed final JSON cannot send alerts or release the hold.
The host does not start a second vision session or a second decision turn.
`--fake-analysis` explicitly uses a simulated fixture and normal decision turn.
It never replaces failed real analysis.

## Gates and lifecycle

`DroidWorker` serializes turns off the camera/RC thread. Startup waits for
Photon inbound text; start queues intent on `DroidVoiceGateway`, and emergency
stop directly latches the host driver. Photon is not another mission authority.

Real starts/photos require local `y` approval with a 60-second TTL.
Starts also require camera/battery preflight. A queued request is
not takeoff. One survey, two extra photos, 12 mission-tool calls per turn,
16 turns, and a default 60-second model timeout remain in effect.
The camera loop owns a fixed 90-second analysis deadline across all reimages.
Failures, timeouts, cancellation, bad JSON, or no actionable decision stop and
request landing. Space stops immediately; `q` stops and exits.

`continue_survey` requires current accepted analysis, no reimage/approval
pending, and a live hold. The host validates that the requested decision
actually releases the hold, stops, or queues a reimage/approval.

CLI Script/ToolSearch are available only to discover/invoke the four mission
tools. The permission adapter accepts only our MCP actions. Shell/file access,
foreign MCP servers and task delegation are denied. Startup verifies the
effective tool inventory. This is a tool-policy boundary, **not an OS sandbox**.

The CLI runs in a temporary directory. Photon, ElevenLabs (`ELEVENLABS_`),
Chalk and other service credentials are suppressed in its environment.
Factory authentication remains available. Native image bytes and validated
observations go to Factory on the data chain. Operator commands are phone →
Photon inbound. Formatted alerts/audio go to Photon outbound. Model
summaries and history remain untrusted data.

## Features and persistence

`SurveyPipeline` preserves the local healthy/watch/critical thresholds and
optional Chalk named-query evaluation. Configured Chalk errors fail closed.
Observations and decisions persist in ignored `data/surveys.sqlite3`.
History is field-scoped. An incomplete decision stays pending in the audit
log and does not resume a flight after restart.

Chalk Volume helpers in `store.py` and the older `DroidSession` are separate,
loaded lazily, and not part of the mission runtime. Their snapshots/log/cache
do not replace the pipeline's local SQLite history.

## Photon: command ingress and data-chain messaging

Fixed host events cover acknowledged takeoff/landing, saved photo, completion,
abort, and controller failure. Droid may also request a current-analysis alert.
The host, not the model, selects delivery:

- Critical (`health_score < 0.4` or critical Chalk flag): URGENT text and voice note.
- Otherwise text only, unless the operator passes `--voice-notes`.

Voice notes use bounded, in-memory ElevenLabs Flash v2.5 MP3 synthesis.
Text is attempted independently first. Missing/failed TTS is reported without
private payloads. Photon uses multipart/related attachment uploads with
content-hash keys, upload dedupe and bounded retries. Attachment-not-ready
responses reuse the same message key and body.

**Photon cannot place calls.** An urgent voice note is an ordinary iMessage
audio attachment, not telephony and not a guaranteed native voice-message UI.
The queue is best effort, not durable. Accepted never means delivered.
Photo upload is supported at the client boundary. Crop-analysis JPEGs travel
on the data chain outbound. Operator activation uses the same space inbound
(phone → Photon → Droid). The TypeScript agent is that inbound bridge, not a
connectivity echo.

## Verification

```bash
python -m unittest discover -s tests -v
```

This uses fake SDK sessions, in-memory TTS/Photon HTTP
responses and fake hardware. No live credentials are needed.

`python tests/live_droid_pipeline.py` is a separately authorized Factory smoke
test using a generated real PNG in the persistent session. Photon and
ElevenLabs remain fake; it opens no devices and sends no real messages.
`python -m ground_station.agent.session --check-sdk` is a live SDK/tool-inventory
check without inference or hardware, not an offline test.
