import { afterEach, describe, expect, test } from "bun:test";
import { execFileSync } from "node:child_process";
import { chmodSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import type { Config } from "../src/config.ts";
import { runGrok } from "../src/grok.ts";
import { publish, verifyRemote } from "../src/publish.ts";
import { Store } from "../src/store.ts";

const guild = "111111111111111111";
const channel = "222222222222222222";
const approved = "https://github.com/jaromero3rd/audio_signaling.git";
const config: Config = {
  state: "/unused", bin: "unused", model: "synthetic", key: "", token: "",
  application: "", guild, channels: new Set([channel]),
  checks: [["synthetic-check"]], secrets: [],
};
const directories: string[] = [];
const stores: Store[] = [];
function directory() {
  const path = mkdtempSync(join(tmpdir(), "agri-regression-"));
  directories.push(path);
  return path;
}
function memory() {
  const store = new Store(":memory:"); stores.push(store); return store;
}
afterEach(() => {
  for (const store of stores.splice(0)) store.close();
  for (const path of directories.splice(0)) rmSync(path, { recursive: true });
});

function seedAnnouncements(store: Store) {
  store.announcement({ id: "333333333333333333", guild, channel, created: 1,
    content: "Selected announcement", url: "https://discord.com/channels/synthetic/selected" });
  for (let i = 0; i < 12; i++)
    store.announcement({
      id: String(444444444444444440n + BigInt(i)), guild,
      channel: "555555555555555555", created: 100 + i,
      content: "Removed channel", url: "https://discord.com/channels/synthetic/removed",
    });
}

describe("selected announcement regressions", () => {
  test("filters the source before taking the latest ten", () => {
    const store = memory(); seedAnnouncements(store);
    expect(store.announcements({ guild, channels: new Set([channel]) }).map((a) => a.content))
      .toEqual(["Selected announcement"]);
  });
  test("keeps the latest ten across all selected channels and rejects empty selections", () => {
    const store = memory();
    for (let i = 0; i < 15; i++) store.announcement({
      id: String(666666666666666660n + BigInt(i)), guild,
      channel: i % 2 ? channel : "777777777777777777", created: i,
      content: `Selected ${i}`, url: "https://discord.com/channels/synthetic/selected",
    });
    const result = store.announcements({ guild, channels: new Set([channel, "777777777777777777"]) });
    expect(result.map((a) => a.created)).toEqual([14, 13, 12, 11, 10, 9, 8, 7, 6, 5]);
    expect(store.announcements({ guild: "other-guild", channels: new Set([channel]) })).toEqual([]);
    expect(store.announcements({ guild, channels: new Set() })).toEqual([]);
  });
  test("includes the selected older source in the actual CLI prompt", async () => {
    const path = directory();
    const capture = join(path, "prompt.json");
    const executable = join(path, "fake-grok");
    writeFileSync(executable, [
      "#!/usr/bin/env python3",
      "import json,sys",
      `open(${JSON.stringify(capture)}, 'w').write(sys.argv[-1])`,
      "print(json.dumps({'text':json.dumps({'message':'READY','handoff':None})}))",
      "",
    ].join("\n"));
    chmodSync(executable, 0o700);
    const store = memory(); seedAnnouncements(store);
    await runGrok({ ...config, state: path, bin: executable }, "logistics", "Summarize selected announcements.", store);
    const prompt = JSON.parse(readFileSync(capture, "utf8"));
    expect(prompt.untrustedDiscordAnnouncements.map((a: { content: string }) => a.content))
      .toEqual(["Selected announcement"]);
  });
});

describe("effective push destination regressions", () => {
  function repository(urls: string[]) {
    const path = directory();
    execFileSync("git", ["init", "--quiet", path]);
    execFileSync("git", ["-C", path, "remote", "add", "origin", approved]);
    for (const url of urls)
      execFileSync("git", ["-C", path, "config", "--add", "remote.origin.pushurl", url]);
    return path;
  }
  test("accepts the approved origin's default push URL", async () => {
    await expect(verifyRemote(config, repository([]))).resolves.toBeUndefined();
  });
  test("rejects an unapproved second push URL", async () => {
    const path = repository([approved, "https://example.invalid/unapproved.git"]);
    await expect(verifyRemote(config, path)).rejects.toThrow("Publisher only targets");
  });
  test("accepts multiple URLs only when every URL targets the approved repository", async () => {
    await expect(verifyRemote(config, repository([approved, "git@github.com:jaromero3rd/audio_signaling.git"])))
      .resolves.toBeUndefined();
  });
});

describe("merge history publishing regressions", () => {
  function fixture(material: "private" | "credential" | "binary" | "safe") {
    const sha = "a".repeat(40);
    const branch = "bots/release/regression";
    const calls: string[][] = [];
    const scopes: string[] = [];
    const path = "/synthetic-owned-worktree";
    const io = {
      ownedWorktrees: async () => [{ path, branch, sha }],
      verifyRemote: async (_config: Config, cwd?: string) => { scopes.push(cwd || ""); },
      command: async () => "",
      git: async (args: string[]) => {
        calls.push(args);
        if (args[0] === "symbolic-ref") return branch;
        if (args[0] === "rev-parse") return sha;
        if (args[0] === "rev-list") return "1";
        if (args[0] === "log" && args.includes("--diff-merges=separate")) {
          if (args.includes("--name-only")) return material === "private" ? ".env\0" : "safe.py\0";
          if (args.includes("-p") && material === "credential") return "xai-" + "z".repeat(60);
          if (args.includes("--numstat") && material === "binary") return "-\t-\tasset.bin";
        }
        return "";
      },
    };
    return { sha, branch, calls, scopes, path, io };
  }
  for (const [material, error] of [
    ["private", "private or generated files"],
    ["credential", "credentials"],
    ["binary", "Binary changes"],
  ] as const) {
    test(`rejects ${material} material introduced only by a merge`, async () => {
      const f = fixture(material);
      await expect(publish(config, f.branch, f.sha, f.io)).rejects.toThrow(error);
      expect(f.calls.some((args) => args[0] === "push")).toBe(false);
    });
  }
  test("allows a safe merge and scans every parent in all three history guards", async () => {
    const f = fixture("safe");
    await publish(config, f.branch, f.sha, f.io);
    const logs = f.calls.filter((args) => args[0] === "log");
    expect(logs.length).toBe(3);
    expect(logs.every((args) => args.includes("--diff-merges=separate"))).toBe(true);
    expect(f.scopes).toEqual([f.path, f.path]);
    expect(f.calls.at(-1)).toEqual(["push", "origin", `${f.sha}:refs/heads/${f.branch}`]);
  });
});
