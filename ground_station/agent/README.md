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
python -m ground_station.agent.session --target tag --target-tag 12 --home-tag 12 --no-photon
```

The SDK launcher adds `--mock` unless `--allow-real-flight` is explicit.
It opens a webcam/display, not a headless simulator. A matching AprilTag
lets the existing survey state machine follow its normal target path.
Use `--camera` to choose the webcam, `--droid-model` for a Factory model ID,
and `--droid-prompt` for the initial operator instruction.

Alternatively, use `python demo.py --controller droid --mock ...`.
That shared entrypoint requires explicit `--mock` or `--allow-real-flight`
in Droid mode. `--auto` is rejected in Droid mode because it bypasses the
controlled start tool. Grok Voice is not started alongside Droid.

## Tools and authority

| Tool | Host behavior |
|------|---------------|
| `get_mission_status()` | Reads logical mission state, mock/real mode, limits, stop latch, and pending approval. This is not independent physical telemetry. |
| `issue_drone_command(drone_id, action)` | Accepts only the configured drone and `start_survey`, `capture_photo`, `land`, or `abort`. No raw RC, waypoints, paths, code, or arbitrary command arguments. |

`start_survey` requests the existing takeoff/target/photo/home/landing sequence.
`capture_photo` queues an extra still for the camera thread; its return value
does not claim that capture succeeded. `land`/`abort` latch a stop and request
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
real-flight approval.

Real hardware requires **both** `--allow-real-flight` and local approval.
`--tello-iface` preserves the existing USB Wi-Fi dongle configuration.
Internet stays on the other interface. These are prototype controls, not
flight-safety certification. Existing camera freshness detection, synchronous
Tello calls, positioning, detection, and physical operating conditions still
require operator review. No actual flight is needed to validate the SDK.

## SDK session boundary

[controller.py](controller.py) uses one `Session` with sequential
`session.stream(...)` turns. It registers two authenticated loopback MCP
tools using the official SDK. [worker.py](worker.py) owns that session on a
separate thread so model latency does not block the camera/RC loop.

The native tool allowlist is the two qualified AgriDrone tool IDs.
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

The host feeds photo metadata back into the same session. It does not upload
the image. `DroidController.submit_analysis()` accepts a strict external
`field_id`, finite `health_score` in `[0, 1]`, and bounded `analysis_summary`.
It does not create that analysis. Summaries and filenames are untrusted data.

Chalk querying/storage, Grok image understanding, Daytona execution and
automatic session resumption are not wired into this runtime. Existing
[Chalk state helpers](store.py) remain optional and load lazily.

## Photon system-to-user messaging

[../photon.py](../photon.py) sends fixed host-generated notifications for
acknowledged takeoff/landing, a saved photo, completion, abort, or controller
failure. Droid cannot choose a recipient or supply arbitrary message text.
No photo, local path, credentials, or analysis summary is sent in these alerts.

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
```

Offline tests use fake sessions, in-memory HTTP responses and fake hardware.
They cover approval/stop gates, budgets, schema validation, session lifecycle,
entrypoint defaults, cleanup, Photon routing/idempotency and credential omission.
They do not connect to a drone, open a camera, or send a real message.
