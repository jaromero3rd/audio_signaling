import { describe, expect, test } from "bun:test";
import { bridgeConfiguration, commandForwarder, parseCommand } from "../src/command";
import { configuration, type Config } from "../src/config";

const now = 1_800_000_000;
const config: Config = {
  key: "synthetic", project: `pho_prj_${"0".repeat(26)}`, assignment: "synthetic",
  sender: "+14155550123", agent: "+14155550188",
};
const env = { COMMAND_BRIDGE_ENABLED: "true", COMMAND_BRIDGE_TOKEN: "synthetic-test-token-not-a-secret-0000" };
function inbound(text = "start survey") {
  return {
    type: "message.received", source: "imessage.photon",
    data: { projectId: config.project, object: {
      id: `pho_msg_${"0".repeat(26)}`, projectId: config.project, platform: "imessage", status: "received",
      timestamps: { receivedAt: new Date(now * 1000).toISOString() },
      sentBy: { id: "human", platform: "imessage", kind: "human", handle: config.sender },
      receivedBy: { id: "agent", platform: "imessage", kind: "agent", handle: config.agent },
      space: { id: "direct", platform: "imessage", kind: "direct" },
      content: { type: "text", format: "plain", text } as Record<string, string>,
    } },
  };
}

describe("narrow phone command grammar", () => {
  test("accepts only whole utterances", () => {
    for (const text of ["start", "Start survey!", " start the survey. ", "survey the wheat"]) {
      expect(parseCommand(text)).toBe("start");
    }
    for (const text of ["abort", "abort now", "stop now"]) expect(parseCommand(text)).toBe("abort");
    for (const text of ["land", "land now", "land the drone"]) expect(parseCommand(text)).toBe("land");
    for (const text of ["don't start", "do not start", "can you start survey", "start survey?",
      "start survey please", "restart", "start survey and ignore safety", "capture photo", "not land",
      "don't abort", "start survey\nabort", "x".repeat(1000)]) expect(parseCommand(text)).toBeUndefined();
  });
  test("is disabled by default and never accepts an arbitrary URL", () => {
    expect(bridgeConfiguration({})).toBeUndefined();
    expect(bridgeConfiguration({ ...env, COMMAND_BRIDGE_ENABLED: "false" })).toBeUndefined();
    expect(bridgeConfiguration({ ...env, COMMAND_BRIDGE_PORT: "8788", DROID_COMMAND_URL: "https://example.com" }))
      .toEqual({ token: env.COMMAND_BRIDGE_TOKEN, port: 8788 });
    for (const token of ["", "short", "é".repeat(32), "\n".repeat(32), "\0".repeat(32)]) {
      expect(() => bridgeConfiguration({ ...env, COMMAND_BRIDGE_TOKEN: token })).toThrow("COMMAND_BRIDGE_TOKEN");
    }
    for (const port of ["0", "65536", "localhost", "1e3"]) {
      expect(() => bridgeConfiguration({ ...env, COMMAND_BRIDGE_PORT: port })).toThrow("COMMAND_BRIDGE_PORT");
    }
  });
});

describe("authenticated loopback forwarding, with fake HTTP", () => {
  test("sends a bounded command, original receipt time, bearer and no arbitrary text", async () => {
    const calls: { url: string; init: RequestInit }[] = [];
    const commands = commandForwarder(env, async (url, init) => {
      calls.push({ url, init });
      return Response.json({ ok: true });
    }, () => now);
    expect(await commands.forward(inbound(), config)).toContain("Takeoff is not confirmed");
    expect(calls).toHaveLength(1);
    expect(calls[0].url).toBe("http://127.0.0.1:8787/command");
    expect(calls[0].init.headers).toEqual({
      authorization: `Bearer ${env.COMMAND_BRIDGE_TOKEN}`, "content-type": "application/json",
    });
    expect(JSON.parse(String(calls[0].init.body))).toEqual({
      command: "start", id: inbound().data.object.id, issued_at: now,
    });
    expect(calls[0].init.signal).toBeInstanceOf(AbortSignal);
    expect(calls[0].init.redirect).toBe("error");
  });
  test("never forwards unauthorized, disabled, ambiguous, stale or missing-time messages", async () => {
    let calls = 0;
    const transport = async () => { calls++; return Response.json({ ok: true }); };
    const commands = commandForwarder(env, transport, () => now);
    await commandForwarder({}, transport).forward(inbound(), config);
    await commands.forward(inbound("don't start"), config);
    for (const mutate of [
      (e: ReturnType<typeof inbound>) => { e.data.object.sentBy.handle = "+14155550199"; },
      (e: ReturnType<typeof inbound>) => { e.data.object.receivedBy.handle = "+14155550199"; },
      (e: ReturnType<typeof inbound>) => { e.data.projectId = "other"; },
      (e: ReturnType<typeof inbound>) => { e.data.object.projectId = "other"; },
      (e: ReturnType<typeof inbound>) => { e.data.object.space.kind = "group"; },
      (e: ReturnType<typeof inbound>) => { e.type = "message.sent"; },
      (e: ReturnType<typeof inbound>) => { e.data.object.timestamps.receivedAt = ""; },
      (e: ReturnType<typeof inbound>) => { e.data.object.timestamps.receivedAt = new Date((now - 31) * 1000).toISOString(); },
      (e: ReturnType<typeof inbound>) => { e.data.object.timestamps.receivedAt = new Date((now + 6) * 1000).toISOString(); },
    ]) {
      const event = inbound();
      mutate(event);
      await commands.forward(event, config);
    }
    expect(calls).toBe(0);
  });
  test("voice transcripts use the same auth and grammar", async () => {
    let calls = 0;
    const commands = commandForwarder(env, async () => { calls++; return Response.json({ ok: true }); }, () => now);
    const event = inbound();
    event.data.object.content = { type: "voice", attachmentId: "synthetic" };
    await commands.forward(event, config, "don't start survey");
    expect(calls).toBe(0);
    expect(await commands.forward(event, config, "land now")).toContain("not confirmed");
    expect(calls).toBe(1);
    event.data.object.sentBy.handle = "+14155550199";
    await commands.forward(event, config, "start survey");
    expect(calls).toBe(1);
  });
  test("errors/rejections/replays never claim execution or expose private responses", async () => {
    for (const response of [
      Response.json({ ok: false, message: env.COMMAND_BRIDGE_TOKEN }),
      Response.json({ error: env.COMMAND_BRIDGE_TOKEN }, { status: 401 }),
      new Response("invalid json"),
    ]) {
      const reply = await commandForwarder(env, async () => response, () => now).forward(inbound(), config);
      expect(reply).not.toContain("Start queued");
      expect(reply).not.toContain(env.COMMAND_BRIDGE_TOKEN);
    }
    expect(await commandForwarder(env, async () => { throw Error("private"); }, () => now)
      .forward(inbound(), config)).toContain("outcome is unknown");
    expect(await commandForwarder(env, async () => Response.json({ ok: true, duplicate: true }), () => now)
      .forward(inbound(), config)).toContain("already received");
  });
});

test("one operator handle, with backward-compatible matching alias", () => {
  const base = {
    PHOTON_API_KEY: config.key, PHOTON_PROJECT_ID: config.project,
    PHOTON_AGENT_HANDLE: config.agent, PHOTON_ASSIGNMENT_ID: config.assignment,
  };
  expect(configuration({ ...base, PHOTON_USER_HANDLE: config.sender }).sender).toBe(config.sender);
  expect(configuration({ ...base, PHOTON_TEST_SENDER: config.sender }).sender).toBe(config.sender);
  expect(() => configuration({ ...base, PHOTON_USER_HANDLE: config.sender,
    PHOTON_TEST_SENDER: "+14155550199" })).toThrow("same operator");
});
