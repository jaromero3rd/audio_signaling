/**
 * Proactive operator alert: sends an optional image plus text into the
 * configured direct iMessage space.
 *
 *   bun run src/alert.ts --text "health 0.35" [--image captures/photo.jpg]
 *                        [--space <spaceId> --agent <agentUserId>]
 *
 * Space context (spaceId + agent User ID) is saved by the agent's checkpoint
 * the first time the configured sender messages the agent; pass the flags to
 * override, or run `bun start` and have the sender text the agent once first.
 */
import { ApiError, Photon } from "@photon-ai/api";
import { readFileSync } from "node:fs";
import { basename, extname } from "node:path";
import { spaceContext } from "./checkpoint";
import { configuration, SetupError } from "./config";
import { record } from "./handler";

const CONTENT_TYPES: Record<string, string> = {
  ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp",
};

function arg(flag: string): string | undefined {
  const i = process.argv.indexOf(flag);
  return i >= 0 ? process.argv[i + 1] : undefined;
}

const sleep = (ms: number) => new Promise(r => setTimeout(r, ms));

async function upload(photon: Photon, projectId: string, imagePath: string): Promise<string> {
  const bytes = readFileSync(imagePath);
  const contentType = CONTENT_TYPES[extname(imagePath).toLowerCase()];
  if (!contentType) throw new SetupError(`Unsupported image type for ${imagePath}; use jpeg/png/webp.`);
  const meta = JSON.stringify({
    contentType, sizeBytes: bytes.length, filename: basename(imagePath),
  });
  const boundary = `----agridrone${Date.now().toString(36)}`;
  const body = new Blob([
    `--${boundary}\r\ncontent-type: application/json\r\n\r\n${meta}\r\n`,
    `--${boundary}\r\ncontent-type: ${contentType}\r\n\r\n`,
    bytes,
    `\r\n--${boundary}--\r\n`,
  ], { type: `multipart/related; boundary=${boundary}` });

  const res = await photon.projects.uploadAttachment({
    path: { id: projectId },
    headers: {
      "content-length": String(body.size),
      "content-type": `multipart/related; boundary=${boundary}`,
      "idempotency-key": `agridrone-att:${basename(imagePath)}:${bytes.length}:${Date.now()}`,
    },
    body,
  });
  const attachment = record(res);
  if (typeof attachment.id !== "string" || !attachment.id) {
    throw new SetupError("Attachment upload returned no id.");
  }
  return attachment.id;
}

async function send(photon: Photon, projectId: string, spaceId: string, agentId: string,
                    content: SendMessageBody, idem: string) {
  const sent = await photon.projects.sendMessage({
    path: { id: projectId },
    headers: { "idempotency-key": idem },
    body: { spaceId, senderId: agentId, content },
  });
  if (typeof sent.id !== "string") throw new SetupError("Photon accepted the alert but returned no message ID.");
  return sent.id;
}

type SendMessageBody =
  | { type: "text"; format: string; text: string }
  | { type: "attachment"; attachmentId: string };

async function main() {
  const text = arg("--text");
  const image = arg("--image");
  if (!text && !image) throw new SetupError("Pass --text and/or --image.");

  const config = configuration();
  const saved = spaceContext(config).read();
  const spaceId = arg("--space") ?? saved?.spaceId;
  const agentId = arg("--agent") ?? saved?.agentId;
  if (!spaceId || !agentId) {
    throw new SetupError(
      "No saved iMessage space. Run `bun start` and have the configured sender text the agent once, or pass --space/--agent.",
    );
  }

  const photon = new Photon({
    headers: { Authorization: `Bearer ${config.key}`, "User-Agent": "AgriDrone-Photon-Agent/0.1" },
    timeoutMs: 20_000,
  });

  const stamp = Date.now().toString(36);
  if (image) {
    const attachmentId = await upload(photon, config.project, image);
    // Attachments are processed asynchronously; sendMessage rejects an
    // unprocessed one with 409 ATTACHMENT_NOT_READY (Retry-After: 5).
    let sentImage: string | undefined;
    for (let attempt = 0; attempt < 8 && !sentImage; attempt++) {
      try {
        sentImage = await send(photon, config.project, spaceId, agentId,
          { type: "attachment", attachmentId }, `agridrone-img:${attachmentId}`);
      } catch (error) {
        if (error instanceof ApiError && error.status === 409 && record(error.body).code === "ATTACHMENT_NOT_READY") {
          await sleep(5_000);
          continue;
        }
        throw error;
      }
    }
    if (!sentImage) throw new SetupError(`Attachment ${attachmentId} never became ready.`);
    console.info(`Image alert accepted: ${sentImage}`);
  }
  if (text) {
    const sentText = await send(photon, config.project, spaceId, agentId,
      { type: "text", format: "plain", text }, `agridrone-txt:${stamp}`);
    console.info(`Text alert accepted: ${sentText}`);
  }
}

main().catch(error => {
  if (error instanceof ApiError) {
    console.error(`Photon API error: HTTP ${error.status}; request ${error.requestId ?? "unknown"}.`);
  } else {
    console.error(error instanceof Error ? error.message : "Alert failed.");
  }
  process.exitCode = 1;
});
