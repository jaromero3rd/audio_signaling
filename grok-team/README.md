# AgriDrone shared Grok CLI team

Five CLI role profiles share a private task queue and handoff history:
`coordinator`, `drone`, `logistics`, `vision`, and `release`.

These are **local CLI-based coordination agents**, not provisioned native cloud
Grok Bots. One worker starts Grok Build on demand and must stay running on this
computer. Default: `grok-4.7-build-fast`, low reasoning effort. Each task has at
most three role calls, limited to 90 seconds each. Normal account/API usage
limits and charges apply. The existing CLI login is reused without copying its
credential cache. Personal Grok cross-session memory is disabled.

Grok coordinates; Factory Droid in Cursor remains the coding interface.
The model receives no shell, filesystem, GitHub, messaging, MCP, or subagent
tools. This service never controls drone hardware.

## Run locally

Requires Bun and the installed Grok CLI. Publishing additionally uses existing
Git/`gh` authentication, without changing the configured Git identity.

```sh
cd grok-team
bun install
bun run check
bun test
bun run doctor
bun start
```

`bun run bootstrap` initializes all five roles using real Grok requests.
Run only one worker. Stop it with Ctrl-C or SIGTERM to the PID in
`.state/worker.pid`. Restart after editing `.env`.

From another terminal:

```sh
bun run ask coordinator "Prepare a bounded photo-to-analysis handoff"
bun run ask logistics "Summarize current organizer announcements and deadlines"
bun run status
```

`ask` prints a job ID; `bun run status <job-ID>` shows its result.
The five roles, their shared history, and announcements are private,
git-ignored data in `.state/`. Keep credentials and private material out of tasks.

## Discord: read-only followed announcements

The bot does **not** send Discord messages, register slash commands, create
webhooks, or modify servers. A bot ID and server ID alone cannot authorize reads.
Never use your personal Discord user token.

### 1. Follow announcements into your private server

1. Create a private Discord server you control, with a destination text channel
   such as `#hackharvard-announcements`.
2. In HackHarvard, open an eligible organizer/sponsor **Announcement** channel
   and choose **Follow**.
3. Select your private server and destination channel. You need **Manage
   Webhooks** in the destination; owning it supplies this.
4. Repeat for each eligible source channel.

**Limits:** Follow forwards only posts the source admins publish/crosspost.
It does not mirror every message or ordinary text/forum channels. If no Follow
button exists, ask HackHarvard admins for an announcement feed or authorized
read-only bot access. Do not automate your personal Discord account.

### 2. Create and authorize your read-only bot

1. At <https://discord.com/developers/applications>, create **AgriDrone Alerts**.
2. On **Bot**, enable **Message Content Intent**. No presence/member intents
   or public interaction endpoint are needed.
3. Copy **Application ID** and the bot token into this directory's private
   mode-0600 `.env`. Do not paste the token into chat.
4. Enable **Developer Mode** in Discord. Copy your **private server ID**, not
   HackHarvard's server ID, and the destination **channel ID(s)**.
5. Fill:

   ```dotenv
   DISCORD_APPLICATION_ID=
   DISCORD_BOT_TOKEN=
   DISCORD_GUILD_ID=
   DISCORD_CHANNEL_IDS=
   ```

   Channels are comma-separated. An app ID can generate an invite without a
   token, but a token and channel access are mandatory to actually read.
6. Enable **Guild Install** for this application. Run `bun run connect` to
   print the real app-specific invite. It requests only **View Channel** and
   **Read Message History** (`66560`), not Administrator or Send Messages.
   Install it in your private server, never HackHarvard.
7. Restrict the bot to the destination channels. Explicitly deny **Send
   Messages** and **Send Messages in Threads** in channel overrides, since
   invite permissions do not remove permissions inherited from `@everyone`.
   Deny **View Channel** elsewhere if other private-server channels are private.
8. Run `bun run connect` again to check read access, then restart the worker.
   Wait for `DISCORD READY`.

`connect` performs only read requests; it never posts, registers commands,
or changes permissions. If only an app ID is configured, it prints an invite
and honestly reports that reading remains unconfigured.

### Collector behavior

- Reads only the configured private server/channel allowlist.
- Receives new messages and edits, and refreshes the latest 50 messages per
  selected channel at startup and once per minute.
- Saves text and embed titles/descriptions privately; it does not download
  attachments or fetch links. It does not promise a full historical archive.
- Deduplicates by message ID and suppresses message bodies in logs.
- Omits recognized credentials from stored announcement text.
- Gives Grok the latest ten selected announcements, at most 1,500 characters
  each, only when a role task is requested. No per-announcement model loop.
- Treats feed text as untrusted reference material, not authority to push code,
  operate hardware, change policy, or send messages.

This Discord connection is an **input feed**, not a team-chat bot interface.
Role conversations currently use the local CLI. No Discord account, bot,
server invite, or Follow subscription is provisioned until you complete the
account steps above.

## Checked bot-branch publication

Your selected local policy is **Python syntax + whitespace + credential
guards**, not functional tests. The Python checker parses tracked sources
without importing/executing project code. It does not type-check TypeScript,
validate drone behavior, or prove integrations work. Add real tests when ready.

The private `.env` holds the selected `PUBLISH_CHECKS_JSON` command. New installs
default to disabled until an operator chooses checks. Additional commands use
JSON argv arrays, never chat-supplied shell strings. For example, for a branch
containing this package:

```dotenv
PUBLISH_CHECKS_JSON=[["bun","run","--cwd","grok-team","check"],["bun","run","--cwd","grok-team","test"]]
```

Dependencies must already exist in that worktree. Installs are not automatic.
Arbitrary test commands execute locally: use only reviewed/trusted team code.

Create a bot-owned worktree before handing coding to Droid:

```sh
bun run workspace drone camera-receive
```

It starts `bots/drone/camera-receive` from `origin/main` under
`.state/worktrees/`. Open the printed path in Cursor and use Factory Droid
there. Commit only your own reviewed changes, following repository commit
requirements and using the configured Git identity.

Every 30 seconds the worker queues new commits in owned worktrees.
Publishing requires the exact `jaromero3rd/audio_signaling` origin, a known-role
`bots/<role>/<task>` branch, current `origin/main` ancestry, a clean worktree,
no private/generated files or binary changes in any new commit, no detected
credentials, passing whitespace/check commands, and an unchanged branch/commit
after checking. It then pushes the explicit checked SHA to that bot branch.

**Never main. Never force push.** No automatic staging, commits, merges,
rebases, PRs, or pulls over the user's checkout. Failed commits are not retried
in a loop. Commit a fix or use `bun run ship bots/drone/camera-receive`.
The scanner cannot guarantee detecting every secret; review the committed code.

After a successful push, teammates can immediately fetch the branch:

```sh
git fetch origin
git switch --track origin/bots/drone/camera-receive
```

For an existing local branch, switch to it and use `git pull --ff-only`.
Do not switch/pull over uncommitted work. A bot-branch push does not update
`main`; integration remains a human-reviewed merge. No push has been performed
merely by installing/initializing this service.

## WhatsApp

**Not connected.** Personal WhatsApp has no supported bot API. No unofficial
QR/session bridge is installed. The official Business Platform route requires
a Meta app, business number/token, recipient opt-in, and verified webhook.
Its group API additionally requires an Official Business Account (OBA) and
supports up to eight participants. A normal personal WhatsApp group is not
eligible. No WhatsApp number, webhook, or join link has been provisioned.

## References

- <https://docs.x.ai/build/overview>
- Installed CLI help and `~/.grok/README.md`
- <https://support.discord.com/hc/en-us/articles/360028384531-Channel-Following-FAQ>
- <https://docs.discord.com/developers/events/gateway>
- <https://developers.facebook.com/documentation/business-messaging/whatsapp/get-started>
- <https://developers.facebook.com/documentation/business-messaging/whatsapp/groups>
