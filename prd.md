# AgriDrone product requirements

HackHarvard 2026 prototype. These requirements describe the intended supported
architecture, not a claim of live service or flight certification.

## Providers

| Concern | Provider |
| --- | --- |
| Operator activation (command chain) | Voice on phone → iMessage → Photon inbound → DroidVoiceGateway → Droid |
| Mission decisions and crop-health image analysis | Persistent Factory Droid SDK session with native image input |
| Field data stream | Drone/camera/sensors → host → optional Droid score + Chalk → store |
| Data-chain alerts | Photon outbound: text, images, ElevenLabs voice notes |
| Laptop voice fallback | ElevenAgents client tools |
| Optional deterministic features | Chalk, local thresholds otherwise |

Photon cannot place calls. Any “call” request means an urgent voice note, not
telephony. No separate vision or calling provider is required.

## Functional requirements

| ID | Requirement |
| --- | --- |
| FR-1 | Host receives a captured drone or mock photo through the local adapter |
| FR-2 | `--crop-analysis` sends that photo as native Image input in the persistent Droid session, validates strict crop-health JSON and requires a same-turn decision tool |
| FR-3 | Command chain: authorized Photon inbound (phone dictation or transcribed voice memo) queues Droid intent through DroidVoiceGateway; abort latches host stop. Laptop ElevenAgents client tools start_survey, get_mission_status, abort_mission are a fallback on the same gateway |
| FR-4 | Data chain: Photon sends host-formatted status/analysis text; critical requested alerts add URGENT text and an ElevenLabs voice-note attachment |
| FR-5 | Factory Droid performs high-level reasoning, never per-frame steering or unrestricted code execution |
| FR-6 | Host validates only allowlisted commands for the configured drone, enforces local approval and latches emergency stop |
| FR-7 | SQLite stores pipeline observations/decisions. Optional Chalk named query provides validated decision features; Volume helpers remain separate |
| FR-8 | ggwave and UNO Q telemetry/mic-transfer stay on the data stream; they do not activate the drone |

## Image-turn protocol

The host accepts nonempty JPEG/PNG/GIF/WebP up to 5 MiB. Droid receives image
bytes, not authority to choose paths. It reads status, submits strict
`{"health_score": 0.0, "analysis_summary": "visible evidence"}` to the optional
assessment argument of `get_survey_context`, reads validated local/Chalk
features and chooses a decision with `issue_drone_command`.

The host supplies the field ID, rejects nonfinite/out-of-range scores,
extra fields, blank/oversized summaries and stale captures. The final response
must be identical strict JSON. Decision/alert effects are staged until final
validation; emergency land/abort remain immediate.
No successful narration or JSON-only response can replace a decision tool.

## Safety and delivery requirements

- Local AprilTag/YOLO and the camera loop own all hardware and steering.
- One survey, two extra photos, 12 mission-tool calls/turn, 16 turns/process.
- Real starts/photos require local approval with a 60-second TTL, including
  `--no-voice`. Real flight also requires `--allow-real-flight`.
- Camera/battery preflight, local stop and cleanup remain host-owned.
- Analysis hold is fixed at 90 seconds across reimages.
- Invalid output, timeout, failed feature evaluation or no decision stops
  and requests landing. Cloud latency cannot block the local deadline.
- Voice starts are queued intent, never an executed takeoff claim.
- Model summaries, image content, history and status updates are untrusted.
- Droid's no-argument alert tool cannot select text, paths or recipients.
- Critical requested alerts get voice notes; non-critical are text-only unless
  the operator requests audio with `--voice-notes`.
- Voice notes use bounded in-memory Flash v2.5 MP3; failed/missing TTS does
  not prevent the independent text attempt. Photon acceptance is not delivery.
- Notifications deduplicate receipts and use stable idempotency keys/retries.
  They are best effort, not a durable outbox.

Operator activation uses Photon inbound on the same direct space
(phone → Photon → Droid). Crop-analysis JPEG sending is data-chain egress.
The TypeScript Photon agent is the inbound command bridge (text → loopback
ingest → Droid). It does not fly by itself. No automatic mission restart,
verified agronomic diagnosis or phone calling is claimed.

## Acceptance and publication

Fake-only tests must cover mission/approval/stop gates, one-session image and
decision flow, strict JSON failure, file limits, ElevenAgents client tools and
audio lifecycle, fake TTS HTTP, Photon multipart upload/dedupe/readiness retry,
urgency and credential suppression.

The separately opt-in SDK smoke test sends a generated real image through an
authenticated Factory session while Photon, ElevenLabs and hardware stay fake.
Default validation sends no messages and touches no hardware.

Commit and guarded-publish the exact reviewed SHA only to the assigned bot
branch. Never merge or push to main. Live flight, real messages and public
posting require separate explicit operator authorization.
