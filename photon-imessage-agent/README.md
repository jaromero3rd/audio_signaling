# AgriDrone iMessage connection test

This is a **Photon 2.0 Beta** agent, not the legacy `spectrum-ts` product.
It uses `@photon-ai/api` and the beta Socket.IO event stream. The legacy
Photon CLI and `create-spectrum-project` do not configure beta projects.

The mission command chain is: operator voice on the phone → this iMessage
space → Photon inbound → Python host → Droid. This TypeScript process is a
connectivity echo, not the Droid mapper. The data stream (survey JPEG, status
text, voice notes) uses the same space outbound from Python `photon.py`.

Only iMessage is handled. Authorized plain text or a voice memo is transcribed
with ElevenLabs Scribe. This test replies with the transcript and, if you ask
`what's two plus two` or say `pi`, the answer. It does not activate the drone.

```text
Heard: what's two plus two
Answer: 4
Test only. Drone not activated.
```

## Local commands

From this directory, using Bun 1.3 or later:

```bash
bun install --frozen-lockfile
bun run check
bun test
bun run src/index.ts --check
bun start
```

Bun loads this directory's private `.env`. Copy `.env.example` only when setting
up a new environment, then supply the beta API key, project ID, test sender,
assigned agent number, and active assignment ID. Never commit these values.
The root repository's `.env` is not loaded by this agent.

`--check` verifies the project's credentials and active sender assignment
without subscribing or sending messages. Startup performs the same checks,
then prints `READY` only after Photon acknowledges the subscription.

The test sender must start a new direct iMessage conversation with their
assigned agent number after startup. A shared line cannot initiate an
unsolicited conversation. The sender must use the exact phone identity
registered in Photon, not an Apple-account email or SMS.

## Safety and recovery

- Only the configured human sender, agent, project, and direct iMessage
  conversations can trigger replies. Other channels and outbound events
  cannot trigger an echo loop.
- Logs do not include keys, phone numbers, or inbound message text. Replies
  deliberately echo text back into the same authorized conversation.
- Replies use the incoming Space ID, receiving agent User ID, and message ID.
  A stable idempotency key protects against duplicate sends during replay.
- Sends omit `parentMessageId`: this shared iMessage Space rejects threaded
  replies with `409 SPACE_NOT_SENDABLE`. Ordinary sends stay in the same chat.
- Events are processed serially. Private `.state/` checkpoints advance only
  after successful handling. Preserve that directory across restarts.
- A successful send means acceptance, not delivery. A `message.delivered`
  event is logged separately when observed.
- Stop with Ctrl-C. Run one copy of this test agent at a time.

The setup key currently also permits checking the assignment. A runtime-only
key needs `project:read` and `project:write`; this startup check additionally
needs `platforms:read`. Billing access is not needed.

## Official beta references

- https://photon.codes/docs/beta/get-started/project
- https://photon.codes/docs/beta/get-started/imessage
- https://photon.codes/docs/beta/websocket/connect
- https://photon.codes/docs/beta/websocket/session
- https://photon.codes/docs/beta/websocket/cursors
