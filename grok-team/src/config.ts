import { existsSync, readFileSync, mkdirSync, chmodSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { parseEnv } from "node:util";

export const APP = resolve(import.meta.dir, "..");
export const REPO = dirname(APP);
export const ROLES = ["coordinator", "drone", "logistics", "vision", "release"] as const;
export type Role = (typeof ROLES)[number];
export const REPOSITORY = "jaromero3rd/audio_signaling";
export class TeamError extends Error {}

export function role(value: string): Role {
  if (!ROLES.includes(value as Role)) throw new TeamError("Unknown agent role.");
  return value as Role;
}

function envFile(path: string): Record<string, string> {
  const values = existsSync(path) ? parseEnv(readFileSync(path, "utf8")) : {};
  return Object.fromEntries(Object.entries(values).filter((entry): entry is [string, string] => entry[1] !== undefined));
}

export function checks(value: string): string[][] {
  let data: unknown;
  try { data = JSON.parse(value); } catch { throw new TeamError("PUBLISH_CHECKS_JSON must be JSON argv arrays."); }
  if (!Array.isArray(data) || data.length > 10 || !data.every(
    (entry) => Array.isArray(entry) && entry.length > 0 && entry.length <= 30 &&
      entry.every((part: unknown) => typeof part === "string" && part.length > 0 && part.length < 1000),
  )) throw new TeamError("PUBLISH_CHECKS_JSON must be JSON argv arrays.");
  return data as string[][];
}

function ids(value: string): Set<string> {
  const entries = value.split(",").map((v) => v.trim()).filter(Boolean);
  if (entries.some((v) => !/^\d{17,20}$/.test(v))) throw new TeamError("Discord allowlists must contain comma-separated numeric IDs.");
  return new Set(entries);
}

export function configuration() {
  const local = { ...envFile(join(APP, ".env")), ...process.env };
  const root = envFile(join(REPO, ".env"));
  const state = join(APP, ".state");
  mkdirSync(state, { recursive: true, mode: 0o700 });
  chmodSync(state, 0o700);
  const secrets = Object.entries({ ...root, ...local })
    .filter(([name, value]) => /KEY|TOKEN|SECRET|PASSWORD/i.test(name) && value && value.length >= 12)
    .map(([, value]) => value!);
  return {
    state,
    bin: local.GROK_BIN || "grok",
    model: local.GROK_MODEL || "grok-4.7-build-fast",
    key: "", // Legacy coordination uses its CLI login, never injected provider keys.
    token: local.DISCORD_BOT_TOKEN || "",
    application: local.DISCORD_APPLICATION_ID || "",
    guild: local.DISCORD_GUILD_ID || "",
    channels: ids(local.DISCORD_CHANNEL_IDS || ""),
    checks: checks(local.PUBLISH_CHECKS_JSON || "[]"),
    secrets,
  };
}
export type Config = ReturnType<typeof configuration>;

export function discordConfigured(config: Config) {
  return !!config.token && /^\d{17,20}$/.test(config.guild) && config.channels.size > 0;
}

export function installLink(application: string, guild = ""): string {
  if (!/^\d{17,20}$/.test(application)) throw new TeamError("Set DISCORD_APPLICATION_ID before generating the install link.");
  const url = new URL("https://discord.com/oauth2/authorize");
  url.search = new URLSearchParams({
    client_id: application, scope: "bot", permissions: "66560", integration_type: "0",
    ...(guild ? { guild_id: guild, disable_guild_select: "true" } : {}),
  }).toString();
  return url.toString();
}
