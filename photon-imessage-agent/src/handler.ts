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

export function replyFor(event: unknown, config: Config): SendMessageInput | undefined {
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
  if (space.kind !== "direct" || content.type !== "text" || content.format !== "plain") return;
  if (typeof content.text !== "string" || !content.text.trim()) return;
  if (typeof sender.id !== "string" || !sender.id) return;
  if (typeof message.id !== "string" || !message.id || typeof space.id !== "string" || !space.id) return;
  if (typeof agent.id !== "string" || !agent.id) return;
  return {
    path: { id: config.project },
    headers: { "idempotency-key": `photon-echo-v2:${message.id}` },
    body: {
      spaceId: space.id,
      senderId: agent.id,
      // Shared iMessage Spaces support ordinary sends, not threaded replies.
      content: {
        type: "text",
        format: "plain",
        text: `Photon Spectrum is connected. You said: ${content.text}`,
      },
    },
  };
}
