# AGENTS.md — AgriDrone Agent Reference

---

## Section 1: How Droid is Used at Runtime

### Session lifecycle

`voice_mission.main()` creates a `DroidWorker` (in `ground_station/agent/worker.py`) which
runs a `DroidController` (in `ground_station/agent/controller.py`) in a background thread.
`DroidController` manages a long-lived Factory Droid SDK `Session`.

On startup, `droid.start(instruction)` queues an initial instruction (e.g. "inspect status
and wait for an operator voice request"). The SDK session is kept open for the process
lifetime. On exit, `droid.close()` stops the driver and cancels the background thread.

`droid_session.py` contains an older `DroidSession` / `_MockDroidSession` class. It is not
called from `voice_mission.py` and is not part of the active code path.

### Decision loop — default mode (no `--crop-analysis`)

In the default configuration, AprilTag/YOLO steer the drone locally and Droid only handles
high-level start/land decisions. The mission loop calls Droid via voice (`DroidVoiceGateway`)
or the `--no-voice` host fallback; no photo analysis flows through Droid.

### Decision loop — crop-analysis mode (`--crop-analysis` / `--fake-analysis`)

When crop analysis is enabled, `voice_mission` builds a `SurveyPipeline` and passes it to
`DroidWorker`. After a photo is captured, the loop calls `droid.submit_photo(path)`, which:

```
drone captures photo
  → DroidWorker.submit_photo()
      → SurveyPipeline.process_photo()
          → controller.assess_photo()      # Droid image attachment; or FakeImageAnalyzer
              returns: health_score, analysis_summary
          → driver.accept_analysis()      # host validates capture_id still live
          → evaluate_survey(include_weather=True)
              Chalk joins the score with live NOAA / NASA weather
              on failure: justify_score() on the laptop
          → store.set_last_survey() + append_decision() when CHALK_CLIENT_ID is set
          → Photon text + photo + ElevenLabs speech if Chalk did not already deliver
          → controller.handle()           # Droid chooses the next flight tool
              → model reads get_mission_status + get_survey_context
              → model issues issue_drone_command / send_photon_alert
```

Analysis is also logged to a local SQLite file at `data/surveys.sqlite3`.
The mission does not call xAI. `voice_grok.py` is not started.

### State persistence

The joined survey is written to a Chalk Volume when `CHALK_CLIENT_ID` is set.
SQLite at `data/surveys.sqlite3` is still written either way. The volume name
comes from `CHALK_STATE_VOLUME` (default: `"droid-mission-state"`).

| Path in volume | Content |
|---|---|
| `mission_state.json` | Latest mission snapshot |
| `decision_log.jsonl` | Append-only log of decisions, one JSON per line |
| `fields/<field_id>.json` | Most recent `evaluate_survey` result per field |

Persistence is handled by `ground_station/agent/store.py` via the Chalk Volume gRPC API
(chalkpy `VolumeService`). Access is through the ChalkClient's authenticated server channel.

---

## Section 2: Tool Registry & Schema

`DroidController` registers exactly four tools, grouped as an MCP server named
`"agridrone"`. The model may only call these; all shell/file/other MCP access is blocked.
Tool budget: 12 calls per model turn, 16 turns per process.

### `get_mission_status`

```python
get_mission_status() -> str  # JSON
```

Returns `driver.snapshot()`: drone_id, mock flag, stopped, survey_started,
extra_photos_remaining, pending_approval, capture_id, analysis_ready, photo_queued,
and the nested mission snapshot. Call this before issuing any command.

### `issue_drone_command`

```python
issue_drone_command(
    drone_id: str,    # must match the driver's drone_id, e.g. "drone-1"
    action:   Action, # "start_survey" | "capture_photo" | "continue_survey" | "land" | "abort"
) -> str  # JSON: {"ok": bool, ...}
```

Routes to `driver.issue()`. Real hardware actions (`start_survey`, `capture_photo`) require
local operator approval (press `y` in the HUD); the response will contain
`"reason": "local_approval_required"` until approved. Approval has a 60-second TTL.
`land` and `abort` always execute immediately.

### `get_survey_context`

```python
get_survey_context() -> str  # JSON
```

Returns the pipeline's current context: `field_id`, `latest` analysis (the most recently
accepted `SurveyAnalysis`), and `history` (last 5 survey records from the local SQLite log).
Returns a placeholder dict when no pipeline is configured.

### `send_photon_alert`

```python
send_photon_alert() -> str  # JSON: {"ok": bool, "status": str, "delivery_confirmed": false}
```

No parameters. Queues a host-formatted iMessage notification for the current validated
analysis by calling `pipeline.send_alert()` → `photon.notify_analysis()`. Returns
`{"ok": False, "reason": "photon_not_configured"}` if the Photon notifier was not started
(i.e. `--no-photon` was passed or Photon env vars are absent). Requires crop-analysis mode
to have a live analysis in the pipeline.

---

## Section 3: Grok Team Bot Coordination

### Five roles

| Role | Profile file | Responsibility |
|---|---|---|
| `coordinator` | `grok-team/profiles/coordinator.md` | Decomposes tasks, routes work, tracks blockers |
| `drone` | `grok-team/profiles/drone.md` | Flight control, hardware interface, stream handling |
| `logistics` | `grok-team/profiles/logistics.md` | Deployment, environment setup, dependency management |
| `vision` | `grok-team/profiles/vision.md` | Grok image analysis, wheat detection, Chalk feature pipeline |
| `release` | `grok-team/profiles/release.md` | Bot-branch checks, publish gating, credential scanning |

### Task commands (`grok-team/`)

```sh
bun run ask coordinator "Prepare bounded photo-to-analysis handoff"
bun run status
bun run bootstrap
bun run ship
bun run doctor
```

Tasks route through the local Grok CLI worker (`bun start`). One worker process serves all
five roles sequentially.

### Bot branches workflow

```
bots/<role>/<task-slug>      # naming convention
```

The release role pushes reviewed commits to bot branches after passing:
- Python syntax check
- Whitespace lint
- Credential scan (no detected secrets)

Branch publication requires `jaromero3rd/audio_signaling` as origin and current
`origin/main` ancestry. **Never merges to main automatically.** Human review is required
before any bot branch is integrated.

```sh
git fetch origin
git switch --track origin/bots/drone/camera-receive
```

---

## Section 4: Setup for Team Members

### Prerequisites

- Python >= 3.10
- Bun >= 1.3 (for `grok-team/`)
- Cursor with Factory Droid extension

### 1. Install Factory Droid CLI

Open this repository in Cursor and run in its terminal:

```sh
droid --version
droid   # complete browser sign-in if prompted
```

Each teammate signs in to their own Factory account. Do not share credentials.
`FACTORY_API_KEY` is only needed for non-interactive (CI/headless) use.

### 2. Create `.env`

```sh
cp .env.example .env   # if present; otherwise create manually
```

Required keys:

```dotenv
ELEVENLABS_API_KEY=     # ElevenLabs speech-to-text and the spoken summary
ELEVENLABS_VOICE_ID=    # voice used for the phone clip
CHALK_CLIENT_ID=        # Chalk workspace → Settings > Service Tokens
CHALK_CLIENT_SECRET=
CHALK_ENVIRONMENT=      # development environment ID, not display name
CHALK_STATE_VOLUME=     # optional; defaults to "droid-mission-state"
FACTORY_API_KEY=        # only if running Droid outside Cursor
```

Photon iMessage notifications (optional — supply via `--photon-env` or in `.env`):

```dotenv
PHOTON_API_KEY=
PHOTON_PROJECT_ID=      # pho_prj_... format
PHOTON_ASSIGNMENT_ID=   # pho_asg_... format
PHOTON_AGENT_HANDLE=    # E.164 phone number (agent/sender)
PHOTON_USER_HANDLE=     # E.164 phone number (recipient); or PHOTON_TEST_SENDER
```

Verify required keys without printing values:

```sh
python3 -c "
import os; from pathlib import Path
e = {}; p = Path('.env')
if p.is_file():
  for l in p.read_text().splitlines():
    l=l.strip()
    if l and not l.startswith('#') and '=' in l:
      k,_,v = l.partition('='); e[k]=v
e.update(os.environ)
for svc,keys in [('ElevenLabs',['ELEVENLABS_API_KEY','ELEVENLABS_VOICE_ID']),('Chalk',['CHALK_CLIENT_ID','CHALK_CLIENT_SECRET'])]:
  print(svc, 'present' if all(e.get(k,'').strip() for k in keys) else 'MISSING')
"
```

### 3. Install Python dependencies

```sh
pip install -r requirements.txt
```

### 4. Install grok-team dependencies

```sh
cd grok-team
bun install
cd ..
```

### 5. Run end-to-end demo

```sh
# Mock mode — uses webcam, no real drone, Droid high-level start/land only
python demo.py --mock

# Droid scores each survey photo; Chalk joins live weather
python demo.py --mock --crop-analysis

# Simulated crop analysis (no Droid image turn)
python demo.py --mock --fake-analysis

# Real Tello drone (Droid still requires local y approval for hardware actions)
python demo.py --allow-real-flight

# Skip voice; Droid requests one survey automatically
python demo.py --mock --no-voice
```

`demo.py` is a thin wrapper over `voice_mission.main()`. All flags pass through.

Watch for:

```
Droid session → local CLI (mission tools on this laptop)
Tello stream up.
ElevenLabs speech-to-text is running on this laptop.
Droid controls mission requests. y = approve pending request, space = stop, q = quit.
```

Photon notifications fire on takeoff, photo, land, complete, abort, and
`controller_failed`. `send_photon_alert` (the Droid tool) additionally delivers an
analysis-specific iMessage when the model judges the crop finding critical enough to
warrant operator attention. No threshold triggers this automatically; it requires
the model to call the tool during a crop-analysis turn.
