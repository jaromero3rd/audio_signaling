import { afterEach, describe, expect, test } from "bun:test";
import { mkdtempSync, rmSync, statSync, writeFileSync, readFileSync } from "node:fs";
import { execFileSync } from "node:child_process";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { checks, installLink, role, type Config } from "../src/config.ts";
import { collect, selected } from "../src/discord.ts";
import { answer, childEnvironment, containsSecret, coordinate, TOOL_FLAGS } from "../src/grok.ts";
import { botBranch, inspectChanges, publish } from "../src/publish.ts";
import { Store } from "../src/store.ts";

const user = "111111111111111111";
const channel = "222222222222222222";
const config: Config = {
  state: "/unused", bin: "grok", model: "grok-4.7", key: "",
  token: "synthetic-discord-token", application: "333333333333333333",
  guild: user, channels: new Set([channel]),
  checks: [["test-tool", "test"]], secrets: ["synthetic-secret-value"],
};
const stores: Store[] = [];
const dirs: string[] = [];
function memory() { const s = new Store(":memory:"); stores.push(s); return s; }
afterEach(() => {
  for (const s of stores.splice(0)) s.close();
  for (const path of dirs.splice(0)) rmSync(path, { recursive: true });
});

describe("read-only followed Discord announcements", () => {
  test("only reads allowlisted channels in the configured private server", () => {
    expect(selected(config, config.guild, channel)).toBe(true);
    expect(selected(config, "other-server", channel)).toBe(false);
    expect(selected(config, config.guild, "other-channel")).toBe(false);
    expect(selected(config, null, channel)).toBe(false);
  });
  test("stores text and embed announcements, deduplicates events, and tracks edits", () => {
    const store = memory();
    const message = {
      id: "444444444444444444", guildId: config.guild, channelId: channel,
      content: "Synthetic organizer announcement", createdTimestamp: 1000,
      embeds: [{ title: "Workshop", description: "Meet at the demo room." }],
    };
    expect(collect(config, store, message)).toBe(true);
    collect(config, store, message);
    expect(store.announcements().length).toBe(1);
    expect(store.announcements()[0].content).toContain("Workshop");
    collect(config, store, { ...message, content: "Updated announcement", embeds: [] });
    expect(store.announcements()[0].content).toBe("Updated announcement");
    expect(collect(config, store, { ...message, guildId: "other-server" })).toBe(false);
  });
  test("invite requests only View Channel and Read Message History in the private server", () => {
    const url = new URL(installLink(config.application, config.guild));
    expect(url.searchParams.get("scope")).toBe("bot");
    expect(url.searchParams.get("integration_type")).toBe("0");
    expect(url.searchParams.get("guild_id")).toBe(config.guild);
    expect(url.searchParams.get("permissions")).toBe("66560");
    expect(Number(url.searchParams.get("permissions")) & (2048 | 8)).toBe(0);
    expect(() => installLink("")).toThrow();
  });
  test("has no Discord message-send, reply, registration, or mutation calls", () => {
    const source = readFileSync(join(import.meta.dir, "../src/discord.ts"), "utf8");
    expect(source).not.toMatch(/\.(?:post|put|patch|delete|send|reply|followUp|editReply)\s*\(/);
    expect(source).toContain("rest.get(");
  });
  test("omits known credentials from collected announcements", () => {
    const store = memory();
    collect(config, store, {
      id: "444444444444444444", guildId: config.guild, channelId: channel,
      content: "synthetic-secret-value", createdTimestamp: 1000, embeds: [],
    });
    expect(store.announcements()[0].content).not.toContain("synthetic-secret-value");
    expect(store.announcements()[0].content).toContain("credential guard");
  });
});

describe("restricted Grok coordination", () => {
  test("uses an explicit known tool allowlist then removes that same tool", () => {
    expect(TOOL_FLAGS.slice(0, 4)).toEqual(["--tools", "todo_write", "--disallowed-tools", "todo_write"]);
    expect(TOOL_FLAGS).toContain("--no-subagents");
    expect(TOOL_FLAGS).not.toContain("--always-approve");
  });
  test("does not pass Discord or GitHub credentials to the CLI", () => {
    expect(childEnvironment(config)).not.toHaveProperty("DISCORD_BOT_TOKEN");
    expect(childEnvironment(config)).not.toHaveProperty("GH_TOKEN");
  });
  test("validates role, structured reply, and handoff", () => {
    expect(role("logistics")).toBe("logistics");
    expect(answer('{"message":"ready","handoff":null}').message).toBe("ready");
    expect(answer('```json\n{"message":"ready","handoff":{"role":"drone","task":"review safety"}}\n```').handoff?.role).toBe("drone");
    for (const text of ["text", "null", '{"message":""}', '{"message":"ok","handoff":{"role":"shell","task":"x"}}'])
      expect(() => answer(text)).toThrow();
    expect(() => role("../main")).toThrow();
  });
  test("bounds handoffs and stops cycles while preserving shared memory", async () => {
    const store = memory();
    const visited: string[] = [];
    const result = await coordinate(config, "coordinator", "prepare demo", store,
      async (_config, owner) => {
        visited.push(owner);
        return { message: "Readiness update.", handoff: { role: owner === "coordinator" ? "drone" : "coordinator", task: "next" } };
      });
    expect(visited).toEqual(["coordinator", "drone"]);
    expect(result).toContain("Handoff pending for coordinator");
    expect(store.context().length).toBe(2);
  });
  test("blocks known and recognizable credentials", () => {
    expect(containsSecret("synthetic-secret-value", config.secrets)).toBe(true);
    expect(containsSecret("xai-" + "z".repeat(60), [])).toBe(true);
    expect(containsSecret("ordinary task", config.secrets)).toBe(false);
  });
});

describe("durable shared task queue", () => {
  test("deduplicates interaction IDs and claims each job once", () => {
    const store = memory();
    const id = store.enqueue("ask", "drone", "task", "discord-interaction");
    store.enqueue("ask", "drone", "duplicate", id);
    expect(store.recent().length).toBe(1);
    expect(store.claim()?.input).toBe("task");
    expect(store.claim()).toBeNull();
    store.finish(id, "handoff complete");
    expect(store.get(id)?.state).toBe("done");
  });
  test("survives reopen with private file permissions and marks interrupted work honestly", () => {
    const dir = mkdtempSync(join(tmpdir(), "agri-team-test-")); dirs.push(dir);
    const path = join(dir, "team.sqlite");
    const first = new Store(path);
    const id = first.enqueue("ask", "vision", "check contract");
    first.claim(); first.close();
    const second = new Store(path); stores.push(second);
    expect(statSync(path).mode & 0o777).toBe(0o600);
    expect(second.get(id)?.state).toBe("running");
    second.interrupt();
    expect(second.get(id)?.state).toBe("failed");
  });
});

describe("branch publisher safety", () => {
  test("the chosen Python checker parses tracked files without executing them", () => {
    const dir = mkdtempSync(join(tmpdir(), "agri-syntax-test-")); dirs.push(dir);
    execFileSync("git", ["init", "--quiet", dir]);
    writeFileSync(join(dir, "safe.py"), "raise RuntimeError('must never execute')\n");
    execFileSync("git", ["-C", dir, "add", "--", "safe.py"]);
    const checker = join(import.meta.dir, "../checks/python_syntax.py");
    expect(Bun.spawnSync(["python3", checker], { cwd: dir }).exitCode).toBe(0);
    writeFileSync(join(dir, "safe.py"), "def broken(:\n");
    expect(Bun.spawnSync(["python3", checker], { cwd: dir }).exitCode).not.toBe(0);
  });
  test("only accepts role-owned bot branches and JSON argv check lists", () => {
    expect(botBranch("bots/drone/camera-receive")).toBe("bots/drone/camera-receive");
    for (const name of ["main", "master", "--all", "bots/drone/../main", "bots/unknown/task", "bots/drone/task:main"])
      expect(() => botBranch(name)).toThrow();
    expect(checks('[["bun","test"]]')).toEqual([["bun", "test"]]);
    for (const text of ['"bun test"', '[[]]', '[["bun",1]]', "not json"])
      expect(() => checks(text)).toThrow();
  });
  test("rejects secrets and private paths, including deleted files from history", () => {
    for (const path of [".env", "nested/.env.local", ".state/team.sqlite", ".grok/auth.json", "private.key"])
      expect(() => inspectChanges([path], "", [])).toThrow();
    expect(() => inspectChanges(["safe.py"], "synthetic-secret-value", config.secrets)).toThrow();
    expect(() => inspectChanges([".env.example", "safe.py"], "ordinary patch", [])).not.toThrow();
  });
  test("cannot publish without checks or from main", async () => {
    await expect(publish({ ...config, checks: [] }, "bots/drone/task", "a".repeat(40))).rejects.toThrow("configure real project checks");
    await expect(publish(config, "main", "a".repeat(40))).rejects.toThrow("Never main");
  });

  function fake(failure?: "check" | "dirty" | "changed" | "secret") {
    const sha = "a".repeat(40);
    const branch = "bots/drone/task";
    const calls: string[][] = [];
    let checked = false;
    const io = {
      verifyRemote: async () => { calls.push(["verify-remote"]); },
      ownedWorktrees: async () => [{ path: "/synthetic-owned-worktree", branch, sha }],
      command: async (argv: string[]) => {
        calls.push(argv); checked = true;
        if (failure === "check") throw new Error("synthetic check failure");
        return "";
      },
      git: async (args: string[]) => {
        calls.push(args);
        if (args[0] === "status") return failure === "dirty" ? " M user.py" : "";
        if (args[0] === "symbolic-ref") return branch;
        if (args[0] === "rev-parse") return checked && failure === "changed" ? "b".repeat(40) : sha;
        if (args[0] === "rev-list") return "1";
        if (args.includes("--name-only")) return failure === "secret" ? ".env\0" : "safe.py\0";
        return "";
      },
    };
    return { io, calls, sha, branch };
  }
  test("checks the exact commit and pushes an explicit bot ref, never a default ref", async () => {
    const f = fake();
    const result = await publish(config, f.branch, f.sha, f.io);
    expect(f.calls.at(-1)).toEqual(["push", "origin", `${f.sha}:refs/heads/${f.branch}`]);
    expect(f.calls.findIndex((c) => c[0] === "test-tool")).toBeLessThan(f.calls.findIndex((c) => c[0] === "push"));
    expect(f.calls.filter((c) => c[0] === "verify-remote").length).toBe(2);
    expect(result).toContain("not merged into main");
  });
  for (const reason of ["check", "dirty", "changed", "secret"] as const)
    test(`does not push when ${reason} guard fails`, async () => {
      const f = fake(reason);
      await expect(publish(config, f.branch, f.sha, f.io)).rejects.toThrow();
      expect(f.calls.some((c) => c[0] === "push")).toBe(false);
    });
});
