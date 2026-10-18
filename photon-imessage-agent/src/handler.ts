import type { SendMessageInput } from "@photon-ai/api";
import type { Config } from "./config";

export function record(value: unknown): Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};
}

export type SpaceContextInput = { spaceId: string; agentId: string; senderId: string };

export function spaceContextFor(event: unknown, config: Config): SpaceContextInput | undefined {
  const envelope = record(event);
  if (envelope.type !== "message.received" || envelope.source !== "imessage.photon") return;
  const data = record(envelope.data);
  const message = record(data.object);
  if (data.projectId !== config.project || message.projectId !== config.project) return;
  if (message.platform !== "imessage" || message.status !== "received") return;
  const sender = record(message.sentBy);
  const agent = record(message.receivedBy);
  const space = record(message.space);
  if (sender.platform !== "imessage" || agent.platform !== "imessage" || space.platform !== "imessage") return;
  if (sender.kind !== "human" || sender.handle !== config.sender) return;
  if (agent.kind !== "agent" || agent.handle !== config.agent || sender.id === agent.id) return;
  if (space.kind !== "direct") return;
  if (typeof sender.id !== "string" || typeof agent.id !== "string" || typeof space.id !== "string") return;
  return { spaceId: space.id, agentId: agent.id, senderId: sender.id };
}

type InboundMessage = {
  message: Record<string, unknown>;
  content: Record<string, unknown>;
  space: Record<string, unknown>;
  agent: Record<string, unknown>;
};

function inboundMessage(event: unknown, config: Config): InboundMessage | undefined {
  const envelope = record(event);
  if (envelope.type !== "message.received" || envelope.source !== "imessage.photon") return;
  const data = record(envelope.data);
  const message = record(data.object);
  if (data.projectId !== config.project || message.projectId !== config.project) return;
  if (message.platform !== "imessage" || message.status !== "received") return;
  const sender = record(message.sentBy);
  const agent = record(message.receivedBy);
  const space = record(message.space);
  const content = record(message.content);
  if (sender.platform !== "imessage" || agent.platform !== "imessage" || space.platform !== "imessage") return;
  if (sender.kind !== "human" || sender.handle !== config.sender) return;
  if (agent.kind !== "agent" || agent.handle !== config.agent || sender.id === agent.id) return;
  if (space.kind !== "direct") return;
  if (typeof sender.id !== "string" || !sender.id) return;
  if (typeof message.id !== "string" || !message.id || typeof space.id !== "string" || !space.id) return;
  if (typeof agent.id !== "string" || !agent.id) return;
  return { message, content, space, agent };
}

const WORDS: Record<string, number> = {
  zero: 0, one: 1, two: 2, three: 3, four: 4, five: 5,
  six: 6, seven: 7, eight: 8, nine: 9, ten: 10,
};

function parseNumber(token: string): number | undefined {
  if (/^\d+$/.test(token)) return Number(token);
  return WORDS[token];
}

/** Test-only keyword answers. Does not queue Droid or fly. */
export function testAnswer(transcript: string): string | undefined {
  const t = transcript.toLowerCase().replace(/['’]/g, "").replace(/[?!.,]/g, " ").replace(/\s+/g, " ").trim();
  const sum = t.match(/\b(?:whats|what is)?\s*(\w+)\s+(?:plus|\+)\s+(\w+)\b/);
  if (sum) {
    const a = parseNumber(sum[1]);
    const b = parseNumber(sum[2]);
    if (a !== undefined && b !== undefined) return String(a + b);
  }
  if (/\bpi\b/.test(t)) return "3.14159";
  return undefined;
}

export function inboundTextFor(event: unknown, config: Config): string | undefined {
  const inbound = inboundMessage(event, config);
  if (!inbound) return;
  const { content } = inbound;
  if (content.type !== "text" || content.format !== "plain") return;
  if (typeof content.text !== "string" || !content.text.trim()) return;
  return content.text.trim();
}

export function operatorReply(droidMessage: string): string {
  const text = droidMessage.replace(/\s+/g, " ").trim().slice(0, 1500);
  return text || "Droid did not return a message.";
}

const AUDIO_NAME = /\.(m4a|caf|mp3|wav|aac|ogg|mp4)$/i;

function isAudioAttachment(content: Record<string, unknown>): boolean {
  const mime = String(content.mime ?? content.contentType ?? content.mimeType ?? "").toLowerCase();
  if (mime.startsWith("audio/")) return true;
  const filename = String(content.filename ?? content.name ?? content.fileName ?? "");
  return AUDIO_NAME.test(filename);
}

export function audioRefFor(event: unknown, config: Config): { messageId: string; attachmentId: string } | undefined {
  const inbound = inboundMessage(event, config);
  if (!inbound) return;
  const { message, content } = inbound;
  if (typeof content.attachmentId !== "string" || !content.attachmentId) return;
  if (content.type === "voice") return { messageId: message.id as string, attachmentId: content.attachmentId };
  if (content.type !== "attachment" || !isAudioAttachment(content)) return;
  return { messageId: message.id as string, attachmentId: content.attachmentId };
}

export function commandReplyFor(
  event: unknown,
  config: Config,
  bodyText: string,
  kind: "text" | "voice" = "text",
): SendMessageInput | undefined {
  const inbound = inboundMessage(event, config);
  if (!inbound) return;
  const { message, space, agent } = inbound;
  const key = kind === "voice" ? `photon-voice-v1:${message.id}` : `photon-droid-cmd-v1:${message.id}`;
  return {
    path: { id: config.project },
    headers: { "idempotency-key": key },
    body: {
      spaceId: space.id as string,
      senderId: agent.id as string,
      content: {
        type: "text",
        format: "plain",
        text: operatorReply(bodyText),
      },
    },
  };
}

export function replyFor(event: unknown, config: Config): SendMessageInput | undefined {
  const text = inboundTextFor(event, config);
  if (!text) return;
  return commandReplyFor(event, config, `Queued for Droid: ${text}`);
}

export function audioReplyFor(
  event: unknown,
  config: Config,
  transcript: string,
): SendMessageInput | undefined {
  if (!audioRefFor(event, config)) return;
  return commandReplyFor(event, config, transcript, "voice");
}
