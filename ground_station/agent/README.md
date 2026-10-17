# Embedded Factory Droid mission controller

Factory Droid makes bounded, high-level mission requests. The existing
`SurveyMission` and camera loop still perform local steering and call the
Tello/webcam adapter. This does not replace `djitellopy` with a different
hardware protocol.

## Start safely

Install the root Python requirements and authenticate the installed `droid`
CLI, or provide `FACTORY_API_KEY`. The SDK is pinned to `droid-sdk[mcp]==0.5.0`.

From the repository root, check the real SDK wiring without opening a camera,
running a model turn, sending a message, or connecting to a drone:

```bash
python -m ground_station.agent.session --check-sdk
```

Start the webcam mock, with cloud messaging explicitly disabled:

```bash
python -m ground_station.agent.session --target tag --target-tag 12 --home-tag 12 --no-voice --fake-analysis --no-photon
```

The SDK launcher adds `--mock` unless `--allow-real-flight` is explicit.
It opens a webcam/display, not a headless simulator. A matching AprilTag
lets the existing survey state machine follow its normal target path.
Use `--camera` to choose the webcam, `--droid-model` for a Factory model ID,
and `--droid-prompt` for the initial operator instruction. `--fake-analysis`
is an explicit simulation, never a cloud-error fallback, and is forbidden on
real hardware. Omit it and configure `XAI_API_KEY` for actual Grok image analysis.
`--vision-model` or `XAI_VISION_MODEL` selects the image-understanding model.

`python demo.py` now defaults to Droid and a webcam mock. Omit `--no-voice`
to start Grok Voice alongside Droid: voice starts queue intent to the same
SDK session, not directly to the mission. Emergency abort remains immediate.
With voice enabled, Droid waits for a spoken request unless a custom prompt
requests otherwise. `--no-voice` requests one survey on startup.
`--auto` is rejected in Droid mode. The explicit `--controller voice` path
preserves the legacy direct voice/auto demo without the analysis pipeline.

## Tools and authority

| Tool | Host behavior |
|------|---------------|
| `get_mission_status()` | Reads logical mission state, mock/real mode, limits, stop latch, and pending approval. This is not independent physical telemetry. |
| `issue_drone_command(drone_id, action)` | Accepts only the configured drone and `start_survey`, `capture_photo`, `continue_survey`, `land`, or `abort`. No raw RC, waypoints, paths, code, or arbitrary command arguments. |
| `get_survey_context()` | Reads the current validated analysis, labeled local/Chalk features, and five field-scoped local history records. |
| `send_photon_alert()` | Queues a host-formatted current-analysis alert to the fixed operator. Accepts no text, paths, or recipients; queued is not delivered. |

`start_survey` requests the existing takeoff/target/photo/home/landing sequence.
`capture_photo` queues an extra still for the camera thread; its return value
does not claim that capture succeeded. `continue_survey` releases a live
analysis hold only after the host has accepted the current analysis and no
reimage/approval is pending. `land`/`abort` latch a stop and request
landing; they do not claim that the drone has physically landed.

Only the host UI can approve real starts and extra photos. A pending request
expires after 60 seconds. In the focused camera window:

- `y`: approve the displayed pending request. Starts also need a non-stale
  frame according to the existing loop and at least 20% reported battery.
- Space: latch a stop, revoke pending approval, and request landing.
- `q`/Escape: stop and exit. Cleanup attempts landing if takeoff was requested,
  even if its acknowledgement was lost.

A process permits one survey and two extra photo requests. Each SDK turn has
a default 60-second timeout and 12 mission-tool calls; a controller permits
16 turns. Queues are bounded. Failures, cancellation, or exhausted budgets
latch a stop. Restarting the process requires a fresh SDK session and fresh
real-flight approval. At photo capture the mission hovers in `await_analysis`.
The host uploads JPEG/PNG bytes (at most 10 MiB) to xAI's `/v1/responses`,
validates the structured score/summary, and feeds it to the same Droid session.
The hold has a fixed 90-second deadline across all reimages. Invalid analysis,
worker failure, or a Droid response without a decision fails closed; the
deadline also requests landing independently of cloud completion.

The pipeline writes observations and decisions to ignored `data/surveys.sqlite3`.
`--field-id` scopes the latest five records. It does not resume flight on restart.
`--chalk --chalk-branch <deployment>` invokes the existing named query after each
analysis and validates its outputs; failures stop rather than silently falling
back. Without `--chalk`, features explicitly identify local thresholds.

Real hardware requires **both** `--allow-real-flight` and local approval.
`--tello-iface` preserves the existing USB Wi-Fi dongle configuration.
Internet stays on the other interface. These are prototype controls, not
flight-safety certification. Existing camera freshness detection, synchronous
Tello calls, positioning, detection, and physical operating conditions still
require operator review. No actual flight is needed to validate the SDK.

## SDK session boundary

[controller.py](controller.py) uses one `Session` with sequential
`session.stream(...)` turns. It registers four authenticated loopback MCP
tools using the official SDK. [worker.py](worker.py) owns that session on a
separate thread so model latency does not block the camera/RC loop.

The native tool allowlist is the four qualified AgriDrone tool IDs.
Shell, filesystem, web, task delegation, and script tools are not authorized.
CLI 0.244 always exposes read-only `ToolSearch` discovery; discovery does not
authorize another tool. Startup verifies the effective allowlist and fails
closed if another executable tool or foreign configured MCP server appears.
SDK permission requests may approve only one call to one of our own tools.

The session runs in a temporary directory outside the repository. Known
unrelated service credential variables, including Photon, Chalk, xAI and
Daytona, are cleared in the CLI subprocess environment. Factory authentication
and its required runtime environment remain available. This is a tool-policy
boundary, **not an OS sandbox**; the CLI uses the operator's normal login and
may retain its own transcripts under its normal settings.

The host, not Droid, uploads captured image bytes to Grok. Validated analysis
contains a host-selected `field_id`, finite `health_score` in `[0, 1]`, and
bounded `analysis_summary`. Summaries, history, and filenames are untrusted data.
`submit_analysis()` remains a validation-only ingress for external callers;
the camera runtime uses [pipeline.py](pipeline.py) to bind results to captures.

Daytona execution, Chalk Volume storage, and automatic session resumption
remain separate. This runtime generates no scripts or unrestricted code.
Existing [Chalk state helpers](store.py) remain optional and load lazily.

## Photon system-to-user messaging

[../photon.py](../photon.py) sends fixed host-generated notifications for
acknowledged takeoff/landing, a saved photo, completion, abort, or controller
failure. Droid may additionally request a current-analysis alert. The host
formats only the validated field/score/summary with an uncertainty label;
fake results are marked SIMULATED. Droid cannot choose a recipient or supply
arbitrary message text. No photo, local path, or credentials are sent.

Reuse the existing private echo-agent configuration with:

```bash
python -m ground_station.photon --env /absolute/path/to/private/photon.env --check
python -m ground_station.agent.session --photon-env /absolute/path/to/private/photon.env
```

The first command is read-only. The second opens the webcam mock and **sends
real iMessages labeled `MOCK`** when mission events occur. Use `--no-photon`
to disable notifications. The older echo receiver does not need to be running
for these outbound notifications.

Required private values:

- `PHOTON_API_KEY`, `PHOTON_PROJECT_ID`, `PHOTON_ASSIGNMENT_ID`
- `PHOTON_AGENT_HANDLE` and `PHOTON_USER_HANDLE`
- The existing echo config's `PHOTON_TEST_SENDER` is accepted as the user handle.
- Optionally `PHOTON_SPACE_ID` to disambiguate multiple matching direct spaces.

For local default configuration, put only
`PHOTON_ENV_FILE=/absolute/path/to/private/photon.env` in ignored
`.env.droid.local`. The private file is parsed without exporting its credentials
to the model subprocess. Root `.env` and process environment are also supported;
an explicit Photon environment file takes precedence.

Readiness checks the active project/assignment and requires an existing direct
space with exactly the configured human and agent. It does not create users or
spaces, send a test, or claim outbound delivery has been proven. Shared iMessage
spaces use ordinary messages, never unsupported `parentMessageId` replies.

Notifications run off the flight loop, deduplicate each event within the run,
and retry transient errors at most three times with the same idempotency key.
Accepted does not mean delivered. This bounded queue is best effort, not a
durable outbox; process exit may leave delivery pending.

Only with explicit permission to send a real setup confirmation:

```bash
python -m ground_station.photon --env /absolute/path/to/private/photon.env --send-test
```

This sends one fixed test and polls its delivery status. It does not operate
hardware or grant flight authority. Incoming Photon mission commands are not
implemented, and the echo agent remains a separate service.

## Verification

```bash
python -m unittest discover -s tests -v
python tests/live_droid_pipeline.py
```

Offline tests use fake sessions, in-memory HTTP responses and fake hardware.
They cover approval/stop gates, budgets, schema validation, session lifecycle,
entrypoint defaults, cleanup, Photon routing/idempotency and credential omission.
They also exercise the actual worker, mission, persistent controller, reimage
mailbox, history, Responses image wire contract, hold deadline, and analysis
alert routing. The second command is opt-in: it uses the authenticated real
Factory SDK/model with simulated vision and Photon HTTP responses.
Neither command connects to a drone, opens a camera, or sends a real message.
