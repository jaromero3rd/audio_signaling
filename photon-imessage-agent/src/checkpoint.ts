import { createHash } from "node:crypto";
import { mkdirSync, readFileSync, renameSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import type { Config } from "./config";
import { SetupError } from "./config";

export function checkpoint(config: Config, directory = ".state") {
  mkdirSync(directory, { recursive: true, mode: 0o700 });
  const consumer = createHash("sha256").update(`${config.project}:${config.sender}:${config.agent}`).digest("hex");
  const path = join(directory, `${consumer}.json`);
  let cursor = 0;
  try {
    cursor = JSON.parse(readFileSync(path, "utf8")).cursor;
    if (!Number.isSafeInteger(cursor) || cursor < 0) throw new SetupError("Invalid saved event cursor.");
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code !== "ENOENT") {
      throw new SetupError("Cannot read the saved event cursor. Preserve .state and investigate before restarting.");
    }
  }
  return {
    get cursor() { return cursor; },
    commit(seq: number) {
      if (!Number.isSafeInteger(seq) || seq <= cursor) throw new SetupError("Invalid event checkpoint.");
      writeFileSync(`${path}.tmp`, JSON.stringify({ cursor: seq }) + "\n", { mode: 0o600 });
      renameSync(`${path}.tmp`, path);
      cursor = seq;
    },
  };
}

export type SpaceContext = { spaceId: string; agentId: string; senderId: string };

export function spaceContext(config: Config, directory = ".state") {
  mkdirSync(directory, { recursive: true, mode: 0o700 });
  const consumer = createHash("sha256").update(`${config.project}:${config.sender}:${config.agent}`).digest("hex");
  const path = join(directory, `${consumer}.space.json`);
  return {
    read(): SpaceContext | null {
      try {
        const ctx = JSON.parse(readFileSync(path, "utf8"));
        if (typeof ctx.spaceId === "string" && typeof ctx.agentId === "string" && typeof ctx.senderId === "string") {
          return ctx;
        }
      } catch { /* absent or unreadable */ }
      return null;
    },
    write(ctx: SpaceContext) {
      writeFileSync(`${path}.tmp`, JSON.stringify(ctx) + "\n", { mode: 0o600 });
      renameSync(`${path}.tmp`, path);
    },
  };
}
