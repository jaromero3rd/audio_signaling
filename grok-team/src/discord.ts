import {
  Client, Events, GatewayIntentBits, REST, Routes, type Message,
} from "discord.js";
import { TeamError, discordConfigured, installLink, type Config } from "./config.ts";
import { containsSecret } from "./grok.ts";
import type { Store } from "./store.ts";

export function selected(config: Config, guild: string | null, channel: string) {
  return guild === config.guild && config.channels.has(channel);
}

export type FeedMessage = {
  id: string; guildId: string | null; channelId: string;
  content: string; createdTimestamp: number;
  embeds: readonly { title: string | null; description: string | null }[];
};

export function collect(config: Config, store: Store, message: FeedMessage) {
  if (!selected(config, message.guildId, message.channelId)) return false;
  if (!/^\d{17,20}$/.test(message.id) || !Number.isFinite(message.createdTimestamp)) return false;
  let text = [message.content, ...message.embeds.flatMap((e) => [e.title, e.description])]
    .filter(Boolean).join("\n").slice(0, 6000);
  if (!text.trim()) return false;
  if (containsSecret(text, config.secrets)) text = "[Announcement text omitted by credential guard.]";
  store.announcement({
    id: message.id, guild: message.guildId!, channel: message.channelId,
    created: message.createdTimestamp, content: text,
    url: `https://discord.com/channels/${message.guildId}/${message.channelId}/${message.id}`,
  });
  return true;
}

export async function connect(config: Config) {
  // All Discord REST operations in this module are GETs. Never register commands,
  // post messages, follow channels, edit permissions, or create webhooks.
  if (!config.token) {
    if (config.application) {
      console.log(installLink(config.application, config.guild));
      console.log("Install link only. A bot token and selected channel access are still required.");
      return;
    }
    throw new TeamError("Add your app ID or bot token to grok-team/.env to generate the read-only invite.");
  }
  const rest = new REST({ version: "10" }).setToken(config.token);
  const app = await rest.get(Routes.oauth2CurrentApplication()) as { id: string; flags?: number };
  if (config.application && app.id !== config.application)
    throw new TeamError("Discord token does not belong to DISCORD_APPLICATION_ID.");
  console.log(installLink(app.id, config.guild));
  if (!discordConfigured(config)) {
    console.log("Invite generated. Configure your private DISCORD_GUILD_ID and DISCORD_CHANNEL_IDS next.");
    return;
  }
  await rest.get(Routes.guild(config.guild));
  for (const id of config.channels) {
    const channel = await rest.get(Routes.channel(id)) as { guild_id?: string };
    if (channel.guild_id !== config.guild) throw new TeamError("Selected channel does not belong to your configured private server.");
    await rest.get(Routes.channelMessages(id), { query: new URLSearchParams({ limit: "1" }) });
  }
  if (typeof app.flags === "number" && !(app.flags & ((1 << 18) | (1 << 19))))
    throw new TeamError("Enable Message Content Intent in your bot's Developer Portal settings.");
  console.log("Read-only Discord authentication and selected channel history access verified. No Discord messages sent.");
}

export async function discord(config: Config, store: Store) {
  if (!discordConfigured(config)) return null;
  const client = new Client({
    intents: [GatewayIntentBits.Guilds, GatewayIntentBits.GuildMessages, GatewayIntentBits.MessageContent],
  });
  let polling: ReturnType<typeof setInterval> | undefined;
  let backfilling = false;
  const backfill = async () => {
    if (backfilling) return;
    backfilling = true;
    try {
      for (const id of config.channels) {
        const channel = await client.channels.fetch(id);
        if (!channel || !("guildId" in channel) || channel.guildId !== config.guild ||
            !channel.isTextBased() || !("messages" in channel))
          throw new TeamError("Selected channel is not an accessible text channel in your private server.");
        const messages = await channel.messages.fetch({ limit: 50 });
        for (const message of messages.values()) collect(config, store, message);
      }
    } finally { backfilling = false; }
  };
  const startup = setTimeout(() => {
    console.error("Read-only Discord startup timed out; check token, server membership, and intents.");
    client.destroy();
  }, 30_000);
  client.once(Events.ClientReady, () => {
    clearTimeout(startup);
    console.log("DISCORD CONNECTED: read-only collector; channel access is checked by the initial history fetch.");
  });
  client.on(Events.Error, () => console.error("Discord read-only connection error; sensitive details suppressed."));
  client.on(Events.MessageCreate, (message) => {
    try { collect(config, store, message); }
    catch { console.error("Announcement could not be saved; message content suppressed."); }
  });
  client.on(Events.MessageUpdate, async (_old, updated) => {
    if (!selected(config, updated.guildId, updated.channelId)) return;
    try {
      const message = updated.partial ? await updated.fetch() : updated as Message;
      collect(config, store, message);
    } catch { console.error("Announcement edit could not be read; sensitive details suppressed."); }
  });
  try {
    await client.login(config.token);
    await backfill();
    console.log("DISCORD READY: selected announcement history read; no Discord message-send capability is implemented.");
    polling = setInterval(() => {
      void backfill().catch(() => console.error("Announcement refresh failed; check selected-channel access."));
    }, 60_000);
  } catch {
    clearTimeout(startup);
    client.destroy();
    throw new TeamError("Read-only Discord failed; verify bot installation, View Channel/Read Message History, and Message Content Intent.");
  }
  return {
    destroy: async () => {
      if (polling) clearInterval(polling);
      client.destroy();
      while (backfilling) await Bun.sleep(50);
    },
  };
}
