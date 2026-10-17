import { describe, expect, test } from "bun:test";
import { mkdtempSync, readdirSync, readFileSync, rmSync, statSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { checkpoint } from "../src/checkpoint";
import { configuration, type Config } from "../src/config";
import { replyFor } from "../src/handler";

const config: Config = {
  key: "synthetic-test-key",
  project: `pho_prj_${"0".repeat(26)}`,
  sender: "+14155550123",
  agent: "+14155550188",
  assignment: `pho_asg_${"0".repeat(26)}`,
};
function inbound() {
  return {
    id: "synthetic-event",
    type: "message.received",
    source: "imessage.photon",
    apiVersion: "2026-07-01",
    data: {
      projectId: config.project,
      object: {
        id: `pho_msg_${"0".repeat(26)}`,
        projectId: config.project,
        platform: "imessage",
        status: "received",
        sentBy: { id: `pho_usr_${"1".repeat(26)}`, platform: "imessage", kind: "human", handle: config.sender },
        receivedBy: { id: `pho_usr_${"0".repeat(26)}`, platform: "imessage", kind: "agent", handle: config.agent },
        space: { id: `pho_spc_${"0".repeat(26)}`, platform: "imessage", kind: "direct" },
        content: { type: "text", format: "plain", text: "Hello Photon" },
      },
    },
  };
}

describe("iMessage echo handler", () => {
  test("uses the inbound conversation, receiving agent, and exact echo text", () => {
    const event = inbound();
    const reply = replyFor(event, config)!;
    expect(reply.path.id).toBe(config.project);
    expect(reply.body.spaceId).toBe(event.data.object.space.id);
    expect(reply.body.senderId).toBe(event.data.object.receivedBy.id);
    expect(reply.body.parentMessageId).toBeUndefined();
    expect(reply.headers["idempotency-key"]).toBe(`photon-echo-v2:${event.data.object.id}`);
    expect(reply.body.content).toEqual({
      type: "text", format: "plain", text: "Photon Spectrum is connected. You said: Hello Photon",
    });
  });
  test("keeps the idempotency key unchanged when an event replays", () => {
    expect(replyFor(inbound(), config)?.headers).toEqual(replyFor(inbound(), config)?.headers);
  });
  test("ignores malformed payloads without throwing", () => {
    for (const value of [null, undefined, [], 42, "text", {}, { type: "message.received" }, { type: "message.received", source: "imessage.photon", data: null }]) {
      expect(replyFor(value, config)).toBeUndefined();
    }
  });
  test("ignores other senders", () => {
    const event = inbound();
    event.data.object.sentBy.handle = "+14155550199";
    expect(replyFor(event, config)).toBeUndefined();
  });
  test("ignores other agent lines", () => {
    const event = inbound();
    event.data.object.receivedBy.handle = "+14155550199";
    expect(replyFor(event, config)).toBeUndefined();
  });
  test("ignores outbound and delivery events", () => {
    for (const type of ["message.sent", "message.delivered", "message.dispatched"]) {
      expect(replyFor({ ...inbound(), type }, config)).toBeUndefined();
    }
  });
  test("ignores other projects, channels, groups, and non-text content", () => {
    for (const mutate of [
      (event: ReturnType<typeof inbound>) => { event.data.projectId = "other"; },
      (event: ReturnType<typeof inbound>) => { event.data.object.projectId = "other"; },
      (event: ReturnType<typeof inbound>) => { event.source = "sms.photon"; },
      (event: ReturnType<typeof inbound>) => { event.data.object.platform = "sms"; },
      (event: ReturnType<typeof inbound>) => { event.data.object.space.kind = "group"; },
      (event: ReturnType<typeof inbound>) => { event.data.object.content.type = "attachment"; },
      (event: ReturnType<typeof inbound>) => { event.data.object.content.text = "  "; },
      (event: ReturnType<typeof inbound>) => { event.data.object.status = "sent"; },
    ]) {
      const event = inbound();
      mutate(event);
      expect(replyFor(event, config)).toBeUndefined();
    }
  });
  test("does not reply to itself", () => {
    const event = inbound();
    event.data.object.sentBy.id = event.data.object.receivedBy.id;
    expect(replyFor(event, config)).toBeUndefined();
  });
  test("ignores missing receiving-agent data", () => {
    const event = inbound();
    expect(replyFor({ ...event, data: { ...event.data, object: { ...event.data.object, receivedBy: null } } }, config)).toBeUndefined();
  });
});

describe("configuration", () => {
  const env = {
    PHOTON_API_KEY: config.key, PHOTON_PROJECT_ID: config.project,
    PHOTON_TEST_SENDER: config.sender, PHOTON_AGENT_HANDLE: config.agent,
    PHOTON_ASSIGNMENT_ID: config.assignment,
  };
  test("loads the private environment without requiring unrelated service keys", () => {
    expect(configuration(env)).toEqual(config);
  });
  test("reports a missing variable by name, not its value", () => {
    expect(() => configuration({ ...env, PHOTON_API_KEY: "" })).toThrow("Missing PHOTON_API_KEY");
  });
  test("rejects a legacy project and invalid test numbers", () => {
    expect(() => configuration({ ...env, PHOTON_PROJECT_ID: "legacy-project" })).toThrow("Photon 2.0 Beta");
    expect(() => configuration({ ...env, PHOTON_TEST_SENDER: "123" })).toThrow("E.164");
    expect(() => configuration({ ...env, PHOTON_TEST_SENDER: config.agent })).toThrow("different");
  });
});

describe("durable event checkpoints", () => {
  test("persists only a cursor with private file permissions and resumes it", () => {
    const directory = mkdtempSync(join(tmpdir(), "photon-checkpoint-"));
    try {
      const state = checkpoint(config, directory);
      expect(state.cursor).toBe(0);
      state.commit(42);
      expect(checkpoint(config, directory).cursor).toBe(42);
      const path = join(directory, readdirSync(directory)[0]);
      expect(readFileSync(path, "utf8")).toBe('{"cursor":42}\n');
      expect(statSync(path).mode & 0o777).toBe(0o600);
      expect(() => state.commit(41)).toThrow("Invalid event checkpoint");
      expect(() => state.commit(Number.NaN)).toThrow("Invalid event checkpoint");
    } finally { rmSync(directory, { recursive: true }); }
  });
  test("does not silently reset corrupt saved progress", () => {
    const directory = mkdtempSync(join(tmpdir(), "photon-checkpoint-"));
    try {
      checkpoint(config, directory).commit(1);
      writeFileSync(join(directory, readdirSync(directory)[0]), "invalid json");
      expect(() => checkpoint(config, directory)).toThrow("Cannot read the saved event cursor");
    } finally { rmSync(directory, { recursive: true }); }
  });
});
