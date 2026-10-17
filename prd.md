# PRD: AgriDrone — Field Survey Agent System
**HackHarvard 2026**  
Tracks: SpaceXAI, Factory Droid SDK, Photon 2.0, Chalk, ASUS Sustainability

---

## 1. Overview

AgriDrone is a ground-station + drone system that surveys agricultural fields (demo with wheat), analyzes photos for crop health, decides next actions, and alerts the operator.

**Team workflow**
- All coding is done with **Factory Droid CLI inside Cursor**.
- Coordination, planning, and status updates happen through **Grok Bots** (bonus for SpaceXAI track).

**Primary tracks hit**
- SpaceXAI (Grok Voice API + Cursor + Grok Bot)
- Factory Droid SDK (embedded runtime agent doing real work)
- Photon 2.0
- Chalk
- ASUS content challenge

---

## 2. Team Setup (do this first)

### A. Droid CLI + Cursor
1. Install Droid CLI:
   ```bash
   curl -fsSL https://app.factory.ai/cli | sh
   droid login
   ```
2. In Cursor, open the terminal and run `droid`. The Factory extension auto-installs for context sharing.
3. Create `AGENTS.md` at the repo root (Droid reads this automatically).

### B. Grok Bots for team communication
- Create one Grok Bot per major role or a single shared project bot.
- Use it for task handoffs, architecture decisions, blockers, status updates, and PRD refinement.
- Document usage for the SpaceXAI bonus.
- Keep a short log of important bot decisions that can be committed as notes.

### C. Suggested repo structure
```
agri-drone/
├── AGENTS.md
├── README.md
├── prd.md
├── ground-station/
│   ├── vision/          # Grok image understanding
│   ├── agent/           # embedded Droid session
│   ├── voice/           # Grok Voice API
│   └── messaging/       # Photon 2.0
├── drone-interface/     # commands / photo receive
├── chalk/               # feature definitions + evals
└── docs/
```

---

## 3. Functional Requirements

| ID   | Requirement                                                                 | Track      | Priority |
|------|-----------------------------------------------------------------------------|------------|----------|
| FR-1 | Ground station receives photos from drone(s)                                | Core       | P0       |
| FR-2 | Grok image understanding analyzes each photo → structured crop-health result | Core       | P0       |
| FR-3 | Grok Voice API accepts spoken commands and issues spoken status             | SpaceXAI   | P0       |
| FR-4 | Photon 2.0 agent receives text commands and pushes photo + analysis alerts  | Photon     | P0       |
| FR-5 | Persistent Factory Droid session receives analysis + state and chooses high-level mission actions; it does not perform frame-by-frame steering | Factory | P0 |
| FR-6 | Droid returns only structured, allowlisted actions; the runtime does not execute agent-generated code | Core | P0 |
| FR-7 | Survey inputs and decisions are stored/served by Chalk; deterministic resolvers compute decision features, while Droid performs mission reasoning | Chalk | P1 |
| FR-8 | Optional short ggwave signaling for flags                                   | Nice-to-have | P2    |
| FR-9 | Public content (vlog/post) with required ASUS tags                          | ASUS       | P1       |

---

## 4. Architecture (runtime)

```
User (phone / voice)
    │
    ├── Photon 2.0 ──────────► Ground Station
    └── Grok Voice API ──────► Ground Station
                                  │
                                  ▼
                         Drones fly & capture photos
                                  │
                                  ▼
                    Grok Image Understanding
                    (regular Grok model + image input)
                                  │
                                  ▼
                    Factory Droid Session (embedded)
                    tools: drone_command, reimage,
                           photon_alert, chalk_query
                                  │
                     ┌────────────┴──────────��         ▼
              Commands back to drones
              Alerts back to user via Photon
```

**Important distinction**
- Grok Imagine is **not** used for detection (it only generates/edits images).
- Image analysis uses a regular Grok model via the responses API with image input.
- The local controller owns AprilTag tracking, frame-by-frame steering, landing, aborts, and every other safety-critical action.
- Chalk computes declared features; it does not autonomously reason about or execute the next flight.
- Droid selects bounded high-level actions. Unknown actions are rejected, and the runtime does not execute Droid-generated scripts.
- SpaceXAI requirement is satisfied by **Grok Voice API** + Cursor + Grok Bot.

---

## 5. Embedded Droid SDK Requirements

Droid must do real work in the running product (not just help write code).

- Start a long-lived Droid session when the ground station starts.
- Expose tools the session can call:
  - `issue_drone_command(drone_id, waypoints, action)`
  - `request_reimage(drone_id, lat, lon)`
  - `send_photon_alert(message, photo_url)`
  - `query_chalk_features(field_id)`
- On every new vision result, feed the analysis + current mission state into the session.
- Droid returns a structured high-level action. The local state machine validates and translates allowed actions into flight behavior.
- Do not request or execute agent-generated code.
- README must contain a clear section titled **“How Droid is used at runtime”** describing the exact flow.

---

## 6. Non-functional Requirements & Constraints

- Prioritize the P0 loop: photo → vision analysis → Droid decision → command/alert.
- Keep frame-by-frame flight control local and deterministic; cloud latency or failure must not prevent landing or abort.
- Reject unknown Droid actions and tool calls before they reach the mission state machine.
- Assume 1–2 drones with camera and a radio/Wi-Fi link to the laptop.
- All API keys via environment variables; never commit secrets.
- Cursor + Droid CLI is the only coding interface.
- Grok Bots are the coordination layer (document for SpaceXAI bonus).

---

## 7. Success Criteria (demo)

1. Operator sends a voice or Photon command: “survey the wheat.”
2. Drone captures a photo → Grok analyzes it → health score and notes are returned.
3. Chalk computes deterministic features; embedded Droid selects a bounded high-level action.
4. The local controller validates the request before changing flight behavior.
5. An alert with the photo arrives on the operator’s phone via Photon.
6. README clearly explains Droid runtime usage; a public content post exists for ASUS.

---

## 8. Immediate Next Steps

1. Create the repo and add this `prd.md` plus a skeleton `AGENTS.md`.
2. Everyone installs the Droid CLI and confirms `droid` works inside Cursor.
3. Create shared Grok Bot(s) and post the first task breakdown.
4. Suggested split:
   - Photo receive + Grok image understanding
   - Grok Voice + Photon agent
   - Embedded Droid session + tools
   - Chalk feature/state integration
   - Structured Droid action validation and local mission handoff
5. First integration goal: photo in → structured analysis out.

---

*Last updated: HackHarvard 2026*
