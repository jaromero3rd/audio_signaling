# AGENTS.md — AgriDrone Agent Reference

---

## Section 1: How Droid is Used at Runtime

### Session lifecycle

`DroidSession` is instantiated once per run at the top of `voice_mission.main()`,
before the drone connects.  It loads persisted mission state from `.state/` and
registers the four tool callables.  On exit (normal, abort, or exception) the
`finally` block calls `droid.close()`, which flushes state to disk.

If the Factory Python SDK is not installed, `DroidSession` silently falls back to
`_MockDroidSession`, which applies threshold logic locally.  The tool registry
and call path are identical in both cases.

### Decision loop (per photo)

```
drone captures photo
  → analyze_photo_with_grok_async()     # background thread, non-blocking
      → vision_grok.analyze_photo()     # Grok multimodal API
          returns: health_score (0–1), analysis_summary
  → chalk_eval_mission.evaluate_and_log()
      → Chalk resolver pipeline
          returns: health_flag, severity, recommended_action
  → droid.feed_analysis(result)         # stores context in session
  → droid.get_decision()
      returns: {action, reason, tool_call?}
  → tool dispatch
```

### Example: critical health score

```
health_score = 0.28  →  action = "alert_operator"
  → droid._send_photon_alert(
        message  = "corn-north critical: health 0.28",
        photo_url = "captures/img_042.jpg"
    )
  → photon_alert.send_photon_alert() shells out to photon-imessage-agent/src/alert.ts
  → operator receives iMessage with photo attachment
```

Health thresholds used by the mock session (real Droid may differ):

| health_score | action |
|---|---|
| >= 0.7 | continue_survey |
| 0.4 – 0.69 | reimage |
| < 0.4 | alert_operator |

---

## Section 2: Tool Registry & Schema

All four tools are registered in `DroidSession.__init__` and callable by Droid
during `get_decision()`.  Signatures match `ground_station/agent/session.py`.

### `issue_drone_command`

```python
issue_drone_command(
    drone_id:  str,        # target drone identifier, e.g. "tello-0"
    waypoints: list,       # list of (lat, lon, alt) tuples
    action:    str,        # "survey" | "reimage" | "return" | "hover"
) -> {"ok": bool, "message": str}
```

Queues a flight command.  Appends a decision record to `.state/decisions.jsonl`.

### `request_reimage`

```python
request_reimage(
    drone_id: str,   # target drone
    lat:      float, # decimal degrees
    lon:      float, # decimal degrees
) -> {"ok": bool, "message": str}
```

Schedules the drone to re-photograph a specific coordinate.
Logged with type `"reimage_requested"`.

### `send_photon_alert`

```python
send_photon_alert(
    message:      str,            # alert text sent to operator
    photo_url:    str,            # local path or remote URL
    photo_base64: str | None = None,  # base64 bytes; takes priority over photo_url
) -> {"ok": bool, ...}
```

Delegates to `photon_client.send_alert()`, which calls
`photon_alert.send_photon_alert()`.  That shells out to
`photon-imessage-agent/src/alert.ts` via Bun.  Requires Bun >= 1.3 and
`bun install` inside `photon-imessage-agent/`.

### `query_chalk_features`

```python
query_chalk_features(
    field_id: str,   # field identifier, e.g. "field-0"
) -> dict            # Chalk feature evaluation result or local fallback
```

Queries the Chalk online feature pipeline for the given field.  In environments
without a configured Chalk store, returns a note directing callers to use
`chalk_client.evaluate_survey()` with fresh analysis data instead.

---

## Section 3: Grok Team Bot Coordination

### Five roles

| Role | Responsibility |
|---|---|
| `coordinator` | Decomposes tasks, routes work to other roles, tracks blockers |
| `drone` | Flight control implementation, hardware interface, stream handling |
| `logistics` | Deployment, environment setup, dependency management |
| `vision` | Grok image analysis, wheat detection, Chalk feature pipeline |
| `release` | Bot-branch checks, publish gating, credential scanning |

All five share a private task-queue history in `grok-team/.state/`.
Cross-session memory is disabled; each task receives only its explicit context.

### Task handoff pattern

```sh
# Submit a task to a role
bun run ask coordinator "Prepare bounded photo-to-analysis handoff"
# → prints job ID

# Poll result
bun run status <job-ID>

# Check all role states
bun run status
```

Tasks route through the local Grok CLI worker (`bun start`).  One worker process
serves all five roles sequentially.  Each task is capped at 90 seconds; roles
get at most three model calls per task.

### Bot branches workflow

```
bots/<role>/<task-slug>      # naming convention
```

The release role pushes reviewed commits to bot branches after passing:
- Python syntax check
- Whitespace lint
- Credential scan (no detected secrets)

Branch publication requires `jaromero3rd/audio_signaling` as origin and
current `origin/main` ancestry.  **Never merges to main automatically.**
Human review is required before any bot branch is integrated.

```sh
# After release bot pushes
git fetch origin
git switch --track origin/bots/drone/camera-receive
```

---

## Section 4: Setup for Team Members

### Prerequisites

- Python >= 3.10
- Bun >= 1.3 (for `photon-imessage-agent` and `grok-team`)
- Cursor with Factory Droid extension

### 1. Install Factory Droid CLI

Open this repository in Cursor and run in its terminal:

```sh
droid --version
droid   # complete browser sign-in if prompted
```

Each teammate signs in to their own Factory account.  Do not share credentials.
A separate `FACTORY_API_KEY` is only needed for non-interactive (CI/headless) use.

### 2. Create `.env`

Copy the template and fill in your values:

```sh
cp .env.example .env   # if present; otherwise create manually
```

Required keys:

```dotenv
XAI_API_KEY=           # xAI console → project API key
PHOTON_API_KEY=        # Photon 2.0 dashboard → project key
PHOTON_PROJECT_ID=
PHOTON_AGENT_USER_ID=
CHALK_CLIENT_ID=       # Chalk workspace → Settings > Service Tokens
CHALK_CLIENT_SECRET=
CHALK_ENVIRONMENT=     # development environment ID, not display name
FACTORY_API_KEY=       # only if running Droid outside Cursor
```

Verify presence without printing values:

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
for svc,keys in [('xAI',['XAI_API_KEY']),('Photon',['PHOTON_API_KEY']),('Chalk',['CHALK_CLIENT_ID','CHALK_CLIENT_SECRET'])]:
  print(svc, 'present' if all(e.get(k,'').strip() for k in keys) else 'MISSING')
"
```

### 3. Install Python dependencies

```sh
pip install -r requirements.txt
```

### 4. Install Photon agent dependencies

```sh
cd photon-imessage-agent
bun install
cd ..
```

### 5. Run end-to-end demo

```sh
# Mock mode (uses webcam, no real drone)
python demo.py --mock --auto

# Real Tello drone
python demo.py --auto
```

`--auto` starts the survey immediately once Grok and the stream are ready.
`--no-voice` skips Grok voice and uses the mission planner directly (requires `--auto`).

Watch for:

```
Droid session initialized.
Tello stream up.
Grok is running on this laptop.
Survey starting. Space = abort, q = quit.
```

A `health_score < 0.4` during the survey triggers `send_photon_alert()` and
delivers an iMessage to the configured operator.
