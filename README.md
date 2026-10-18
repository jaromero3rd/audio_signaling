# AgriDrone: Photon-Directed Field Surveys

A prototype with local AprilTag/YOLO steering, bounded Factory Droid decisions,
and Photon iMessage commands plus notifications.

| Concern | Provider |
| --- | --- |
| Mission decisions and crop-health image analysis | One persistent Factory Droid SDK session, native image input |
| Operator mission commands | Photon inbound iMessage (text → start/abort) |
| Drone to user text, images, optional voice notes | Photon direct iMessage space |
| Optional decision features | Chalk, or explicitly labeled local thresholds |

**Photon cannot place calls.** An urgent alert is text marked URGENT plus an
optional audio attachment, not a telephone call. No telephony integration is used.
Crop-health scores are uncertain visual estimates, not verified diagnoses.

## How Droid is used at runtime

```text
Operator → Photon inbound text → DroidVoiceGateway → persistent Droid
                                                        ↓
                                            host tools / local approval
                                                        ↓
                           camera → local steering → captured image
                                                        ↓
                              same Droid session: native Image attachment
                                                        ↓
                   strict JSON → host validation → optional Chalk / history
                                                        ↓
                     same turn: decision tool + matching final JSON
                                                        ↓
                bounded reimage / Photon alert / return home / land
```

Default mode does not upload photos: Droid handles high-level requests while
AprilTag/YOLO steer locally. `--crop-analysis` enables the image turn and a
fixed 90-second hover deadline. Reimages do not renew that deadline.
After accepting the Droid score, crop analysis joins it with live NOAA/NASA
weather through Chalk, stores the joined record when `CHALK_CLIENT_ID` is set,
and sends the insight and JPEG through Photon (optional ElevenLabs clip).
Invalid JSON, a failed model turn, or narration without a decision stops the
mission and requests landing. Emergency stop bypasses cloud latency.

Image decisions and alerts are staged until the final assessment validates.
The host controls paths, recipients, alert formatting, limits, and approvals.
Real starts and extra photos require local `y` approval.
Only the camera loop calls hardware. `--allow-real-flight` is also required.

## Setup and safe checks

Use Python 3.10+ and install `requirements.txt`. Authenticate the local `droid`
CLI. Copy `.env.example` privately and follow [SETUP.md](SETUP.md) to configure
Photon (inbound commands + outbound alerts).

Offline, fake-only validation, with no model calls, messages, camera, or drone:

```bash
python -m unittest discover -s tests -v
```

Webcam demo with real Factory inference but no messages:

```bash
python demo.py --mock --target tag --target-tag 12 --home-tag 12 \
  --fake-analysis --no-photon
```

`--fake-analysis` is explicitly simulated and forbidden on real hardware.
It is not a fallback for a failed model. A webcam demo is not a headless test.

With Photon configured, text the agent `survey` / `abort`. To enable native
image analysis, replace `--fake-analysis` with `--crop-analysis`.
Both use the same persistent Droid session.

Photon credentials enable **real iMessages even in mock mode**, labeled MOCK.
Keep `--no-photon` for local-only runs. Critical analysis alerts requested by
Droid get text plus a voice note when TTS is configured. Non-critical alerts
are text-only unless the operator passes `--voice-notes`.

The authenticated smoke test is opt-in:

```bash
python tests/live_droid_pipeline.py
```

It sends a generated PNG to the real Factory session, with fake Photon and
ElevenLabs transports and no hardware. It is not part of fake-only validation.

## Limits and references

See [AGENTS.md](AGENTS.md) for tool inventory, gates, and runtime contracts.
See [prd.md](prd.md) for product requirements. Hardware and network notes live
in the ground-station and UNO Q docs.
