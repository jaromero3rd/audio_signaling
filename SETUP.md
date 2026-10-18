# AgriDrone: setup only you can complete

This checklist covers accounts, billing, credentials, consent, and hardware
access required by `prd.md`. It does not choose an architecture or install app
dependencies. Leave boxes unchecked until you have verified them yourself.

Already reported: Droid installed, Cursor ready, and Chalk workspace open. An
open dashboard does not confirm API access.

## 1. ElevenLabs: operator speech and the spoken phone summary (P0)

The mission does not call xAI. Droid scores the survey photo. ElevenLabs only
decodes the microphone and speaks Chalk's insight.

- [ ] Create an ElevenLabs API key and save it privately as `ELEVENLABS_API_KEY`.
- [ ] Copy a voice id into `ELEVENLABS_VOICE_ID` for the spoken summary.
- [ ] Confirm speech-to-text (`scribe_v1`) and text-to-speech
  (`eleven_multilingual_v2`) are available on that key.

**Ready when:** both names are saved. Live speech calls remain to be tested.
`grok-team/` is a separate developer bot and is not part of this flight setup.

## 2. Photon 2.0: phone messaging (P0)

- [ ] Open [Photon 2.0 early access](https://photon.codes/beta) and follow its
  current onboarding. If access is gated, request it through the hackathon sponsor.
- [ ] Create or select the team's project and generate its API key.
- [ ] Complete account/channel ownership verification for the demo messaging
  channel in the dashboard. You do not need to enable every channel.
- [ ] Save `PHOTON_API_KEY` privately. Record the project ID and the agent's
  channel identity/User ID if issued (`PHOTON_PROJECT_ID`,
  `PHOTON_AGENT_USER_ID`).
- [ ] Check messaging credits and channel restrictions. Have your own test phone
  ready and identify the intended recipient privately.
- [ ] Save a link to the Photon 2.0 developer docs supplied by the dashboard or
  sponsor, including inbound-event and authentication instructions.

**Ready when:** the project and channel are active, credentials are saved, and
you know the test recipient. Sending, receiving, and photo delivery still need
live tests. Do not buy hardware or set up a Mac bridge unless your chosen
channel's current onboarding explicitly requires it.

Webhook implementation, callback URLs, and tunnels can wait until there is an
app to receive events.

## 3. Factory / Droid and Cursor (P0)

- [ ] In Cursor, open this repository and run `droid --version` in its terminal.
- [ ] Run `droid` and complete browser sign-in if prompted. Confirm the intended
  Factory account has model access and sufficient credits.
- [ ] Each teammate completes their own sign-in. Do not share login credentials.
- [ ] If a separate runtime environment later needs independent authentication,
  create a Factory API key and save it privately as `FACTORY_API_KEY`.

**Ready when:** Droid starts authenticated inside Cursor. The Python SDK can use
local Droid authentication, so a separate Factory API key is not mandatory for
local runtime use. SDK installation and the persistent runtime session are
implementation tasks, not prerequisites you must manually build.

The PRD specifies Droid CLI inside Cursor. No additional native Cursor-agent
setup is required for that workflow.

## 4. Grok Bots: team communication

- [ ] Confirm the hackathon's exact Grok Bot product, creation instructions, and
  eligibility rules with the SpaceXAI organizer.
- [ ] Create a shared project bot (or role bots) through that supported workflow,
  complete any required account authorization, and invite the team.
- [ ] Keep the bot's non-secret access link and the official track instructions
  ready for the agents. Record decisions later without credentials or private
  conversation content.

**Ready when:** teammates can access the intended bot. Installing Grok alone
does not establish this. An xAI API key is not proof that the hackathon bot is
set up; do not assume it means an X bot or a Telegram bot.

## 5. Chalk: features and evaluation (P1)

- [ ] In the open workspace, confirm the intended team/project and access to a
  development environment. Record its environment ID, not just its display name.
  Use the existing development/default environment if suitable.
- [ ] Open **Settings > Service Tokens** and create service credentials. Save
  the client ID and client secret privately as `CHALK_CLIENT_ID` and
  `CHALK_CLIENT_SECRET`.
- [ ] Check permissions for querying and writing the demo features. Ensure the
  developer account also has permission to deploy feature definitions; ask a
  workspace admin if access is missing.
- [ ] Record the environment ID as `CHALK_ENVIRONMENT` and any custom API endpoint
  as `CHALK_API_SERVER`.

**Ready when:** credentials, the correct environment, and necessary permissions
are available. Feature definitions, deployments, queries, writes, and evaluation
code are work for the coding agents.

If using the Chalk CLI later, its browser authorization (`chalk login`) is a
step you complete yourself. Do not paste `chalk config` or `chalk token` output
into chat: it can expose credentials.

## 6. Hardware and operating-system permissions

- [ ] For real-drone testing, provide the exact model and official SDK/interface
  docs, complete any vendor developer registration/pairing, and establish the
  laptop's radio/Wi-Fi connection.
- [ ] Confirm you can receive a photo through the vendor-supported workflow.
  Remove propellers or use the vendor's safe bench-test procedure. Setup does
  not authorize flight or movement.
- [ ] Arrange operator permission, a safe test location, and any required flight
  authorization before a live flight. Simulation has no hardware setup.
- [ ] On macOS, allow microphone access for the app actually capturing voice
  (Cursor/terminal or browser) when requested. For a browser demo, also allow
  site microphone access. Check input/output devices and speaker volume.
- [ ] If using sample crop photos, make available images you have permission to
  process with external APIs. Avoid sensitive locations or personal content.

**Ready when:** the capture device has microphone permission and, for real
drones, safe photo access and the vendor interface are available. ggwave is
optional (P2); its dependency installation can wait.

## 7. ASUS content requirement (P1)

- [ ] Obtain the official challenge rules, exact required tags/mentions,
  submission destination, and deadline from the organizer.
- [ ] Have access to the social account you intend to use and obtain consent
  from anyone who will appear in the content.

**Ready when:** account access and posting requirements are known. Publishing is
a later action that you control, not something the agents should do unprompted.

## Store credentials safely

Use a private local `.env` file, edited directly in your editor, or your secret
manager. Do not paste secrets into chat, screenshots, command history, or
committed files. `.gitignore` excludes `.env` and `.env.*` files, except the
non-secret template name `.env.example`.

Suggested names (not an instruction to commit values):

| Service | Secret values | Non-secret configuration to record |
| --- | --- | --- |
| ElevenLabs | `ELEVENLABS_API_KEY` | `ELEVENLABS_VOICE_ID` |
| Photon | `PHOTON_API_KEY` | `PHOTON_PROJECT_ID`, `PHOTON_AGENT_USER_ID`, channel |
| Factory | `FACTORY_API_KEY` only if needed | Otherwise use local Droid sign-in |
| Chalk | `CHALK_CLIENT_ID`, `CHALK_CLIENT_SECRET` | `CHALK_ENVIRONMENT`, optional `CHALK_API_SERVER` |

These are handoff names, not a claim that every future SDK automatically loads
`.env`. SDK configuration and `.env` loading will be wired during implementation.
Expose credentials only to the component that needs them.

### Local credential-presence check

From the repository root, this checks the three external services' credential
names in the process environment or a simple `KEY=VALUE` `.env` file. It does
not execute the file, print values, or contact any provider. Use real values,
not placeholders. It is not a full dotenv parser or an authentication test.

```bash
python3 - <<'PY'
import os
from pathlib import Path

values = {}
path = Path(".env")
if path.is_file():
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        name, sep, value = line.partition("=")
        if sep:
            values[name.strip()] = value.strip().strip("\"'")
values.update(os.environ)
services = {
    "ElevenLabs": ("ELEVENLABS_API_KEY", "ELEVENLABS_VOICE_ID"),
    "Photon": ("PHOTON_API_KEY",),
    "Chalk": ("CHALK_CLIENT_ID", "CHALK_CLIENT_SECRET"),
}
for service, names in services.items():
    status = "present" if all(values.get(name, "").strip() for name in names) else "missing"
    print(f"{service}: {status}")
PY
```

Before creating `.env`, you can verify ignore rules without exposing its contents:

```bash
git check-ignore .env .env.local
```

For services without a credential check (Grok Bots, hardware, ASUS), use their
manual readiness checks above. There is no generic command proving those are
ready.

## Handoff to coding agents

Tell the agents which checklist items are ready or blocked and where credentials
are stored, not their values. Provide official docs links and non-secret
project/environment identifiers. Report any budget limits.

No live API verification has been performed by this checklist. Explicitly
authorize any paid model calls, outbound test messages, or feature writes
before those tests run. Drone motion and public posting need
separate authorization.

DroneDeploy is not in `prd.md`; no DroneDeploy account, key, or purchase is
required. Repository scaffolding, `AGENTS.md`, dependency installation, SDK
wiring, and tests can be handled by the coding agents later.

## References

- [Project requirements](prd.md)
- [xAI voice documentation](https://docs.x.ai/developers/model-capabilities/audio/voice-agent)
- [Factory Python SDK authentication](https://docs.factory.ai/sdk/python.md)
- [Photon 2.0 announcement and API example](https://photon.codes/blog/introducing-photon-2.0)
- [Chalk authentication and service tokens](https://docs.chalk.ai/docs/online-authentication)
