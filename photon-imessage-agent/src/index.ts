import { ApiError, Photon } from "@photon-ai/api";
import { io } from "socket.io-client";
import { checkpoint, spaceContext } from "./checkpoint";
import { configuration, SetupError } from "./config";
import { commandForwarder } from "./command";
import {
  audioRefFor, audioReplyFor, commandReplyFor, inboundTextFor,
  record, spaceContextFor,
} from "./handler";
import { transcribeAudio } from "./transcribe";

function report(error: unknown) {
  if (error instanceof ApiError) {
    const code = record(error.body).code;
    const safeCode = typeof code === "string" && /^[A-Z_]+$/.test(code) ? code : "UNKNOWN";
    console.error(`Photon API error: HTTP ${error.status} (${safeCode}); request ${error.requestId ?? "unknown"}.`);
  } else if (error instanceof SetupError) {
    console.error(error.message);
  } else {
    console.error("Photon operation failed. Check connectivity and account permissions; no private payload logged.");
  }
}

type Commands = ReturnType<typeof commandForwarder>;

function voiceFail(event: unknown, config: ReturnType<typeof configuration>, message: string) {
  return audioReplyFor(event, config, message);
}

async function voiceReply(
  photon: Photon,
  config: ReturnType<typeof configuration>,
  event: unknown,
  attachmentId: string,
  commands: Commands,
) {
  if (!commands.enabled) return voiceFail(event, config, "Phone commands are disabled on this laptop.");
  try {
    const key = process.env.ELEVENLABS_API_KEY ?? "";
    if (!key.trim()) {
      return voiceFail(event, config, "Voice memo received, but transcription is not configured on the laptop.");
    }
    let blob: Blob | undefined;
    for (let attempt = 0; attempt < 8; attempt++) {
      try {
        blob = await photon.projects.downloadAttachment({
          path: { id: config.project, attachmentId },
        });
        break;
      } catch (error) {
        if (error instanceof ApiError && error.status === 409) {
          await new Promise(resolve => setTimeout(resolve, 1500));
          continue;
        }
        return voiceFail(event, config, "Could not download the voice memo. Try sending text instead.");
      }
    }
    if (!blob) return voiceFail(event, config, "Voice memo was not ready. Try sending text instead.");
    let filename = "voice.m4a";
    try {
      const meta = await photon.projects.getAttachment({
        path: { id: config.project, attachmentId },
      });
      if (typeof meta.filename === "string" && meta.filename.trim()) filename = meta.filename.trim();
    } catch { /* filename is best-effort */ }
    let transcript: string;
    try {
      transcript = await transcribeAudio(blob, filename, key);
    } catch {
      return voiceFail(event, config, "Could not transcribe the voice memo. Try sending text instead.");
    }
    const droid = await commands.forward(event, config, transcript);
    return audioReplyFor(event, config, droid);
  } catch {
    return voiceFail(event, config, "Could not process the voice memo. Try sending text instead.");
  }
}

async function main() {
  const config = configuration();
  const commands = commandForwarder();
  const photon = new Photon({
    headers: { Authorization: `Bearer ${config.key}`, "User-Agent": "AgriDrone-Photon-Agent/0.1" },
    timeoutMs: 20_000,
  });
  const project = await photon.projects.get({ path: { projectId: config.project } });
  const assignment = await photon.projects.platforms.imessage.assignments.get({
    path: { id: config.project, assignmentId: config.assignment },
  });
  if (project.deletedAt || assignment.projectId !== config.project ||
      assignment.platform !== "imessage" || assignment.releasedAt ||
      assignment.handle !== config.sender || assignment.assignedPhoneNumber !== config.agent) {
    throw new SetupError("The configured iMessage assignment is not active for this sender and project.");
  }
  console.info("Photon Beta credentials and active iMessage assignment verified.");
  if (process.argv.includes("--check")) {
    console.info("Configuration check passed. No event connection opened and no message sent.");
    return;
  }

  const state = checkpoint(config);
  const spaces = spaceContext(config);
  const socket = io("https://api.photon.codes", {
    path: `/v1/projects/${config.project}/events/ws`,
    addTrailingSlash: false,
    transports: ["websocket"],
    extraHeaders: { Authorization: `Bearer ${config.key}`, "User-Agent": "AgriDrone-Photon-Agent/0.1" },
    autoConnect: false,
    reconnectionAttempts: 10,
    reconnectionDelayMax: 15_000,
    timeout: 20_000,
  });
  let epoch = 0;
  let pending = 0;
  let queue = Promise.resolve();
  let stopping = false;
  const outbound = new Set<string>();
  const fatal = (message: string) => {
    console.error(message);
    socket.disconnect();
    process.exit(1);
  };
  const startupDeadline = setTimeout(() => fatal("Photon subscription did not become ready within 60 seconds."), 60_000);

  socket.on("connect", () => {
    socket.timeout(10_000).emit("subscribe", { startSeq: state.cursor, apiVersion: "2026-07-01" },
      (error: Error | null, result: unknown) => {
        if (error) { socket.io.engine.close(); return; }
        const ack = record(result);
        if (ack.ok !== true || ack.projectId !== config.project || ack.apiVersion !== "2026-07-01") {
          fatal("Photon rejected the event subscription or returned an unexpected project/API version.");
          return;
        }
        clearTimeout(startupDeadline);
        const connectionId = typeof ack.connectionId === "string" ? ack.connectionId : "unknown";
        const cursor = typeof ack.startSeq === "number" ? ack.startSeq : state.cursor;
        console.info(`READY: Photon Beta iMessage subscription accepted; connection ${connectionId}; cursor ${cursor}.`);
      });
  });
  socket.on("disconnect", () => {
    epoch++;
    if (!stopping) console.warn("Photon event stream disconnected; reconnecting from the saved cursor.");
  });
  socket.on("connect_error", () => {
    console.error("Photon event connection failed. Check network access and project:read permission.");
  });
  socket.io.on("reconnect_failed", () => fatal("Photon event connection exhausted its reconnect attempts."));
  socket.onAny((name: string) => {
    if (name !== "delivery") console.info(`Photon socket event: ${name}`);
  });
  socket.on("delivery_error", (error: unknown) => {
    const details = record(error);
    const code = typeof details.code === "string" && /^[a-z_]+$/.test(details.code) ? details.code : "unknown";
    console.error(`Photon delivery error: ${code}.`);
    if (details.retryable === false) fatal("Photon event delivery requires configuration or account changes.");
    else if (details.fatal) socket.io.engine.close();
  });
  socket.on("delivery", (event: unknown, metadata: unknown) => {
    const seq = record(metadata).seq;
    if (typeof seq !== "number" || !Number.isSafeInteger(seq) || seq <= 0) {
      fatal("Photon sent invalid delivery metadata; stopped without advancing the cursor.");
      return;
    }
    const generation = epoch;
    if (++pending > 128) {
      pending--;
      epoch++;
      socket.io.engine.close();
      return;
    }
    queue = queue.then(async () => {
      if (generation !== epoch || seq <= state.cursor) return;
      const ctx = spaceContextFor(event, config);
      if (ctx) spaces.write(ctx);
      const audio = audioRefFor(event, config);
      const spoken = inboundTextFor(event, config);
      const input = audio
        ? await voiceReply(photon, config, event, audio.attachmentId, commands)
        : spoken
          ? commandReplyFor(event, config, await commands.forward(event, config))
          : undefined;
      if (input) {
        console.info(audio
          ? "Inbound voice memo matched; sending host reply."
          : "Inbound iMessage matched; sending host command result.");
        const sent = await photon.projects.sendMessage(input);
        if (typeof sent.id !== "string" || !/^pho_msg_[0-7][0-9a-hjkmnp-tv-z]{25}$/.test(sent.id)) {
          throw new SetupError("Photon accepted the reply but returned no valid message ID. Preserve the checkpoint for idempotent recovery.");
        }
        outbound.add(sent.id);
        console.info(`Reply accepted for asynchronous dispatch: ${sent.id}. Delivery is not yet confirmed.`);
      } else {
        const envelope = record(event);
        const message = record(record(envelope.data).object);
        const content = record(message.content);
        if (envelope.type === "message.received") {
          const kind = typeof content.type === "string" ? content.type : "unknown";
          console.info(`Inbound event ignored (content type: ${kind}).`);
        }
        if (typeof message.id === "string" && outbound.has(message.id)) {
          if (envelope.type === "message.delivered") {
            console.info(`Reply delivery confirmed by Photon: ${message.id}.`);
            outbound.delete(message.id);
          } else if (envelope.type === "message.failed") {
            console.error(`Reply delivery failed: ${message.id}. Check Photon API Activities.`);
            outbound.delete(message.id);
          }
        }
      }
      state.commit(seq);
    }).catch(error => {
      report(error);
      const transcribeFail = error instanceof Error && /speech-to-text|transcript|ELEVENLABS_API_KEY|never became ready/i.test(error.message);
      if (error instanceof SetupError && !transcribeFail) fatal("Event handling stopped without advancing its saved cursor.");
      if (error instanceof ApiError && [400, 401, 403, 409, 422].includes(error.status)) {
        fatal("Reply blocked by Photon. Fix the API permission or message error before restarting.");
      }
      if (transcribeFail) return;
      if (generation === epoch) {
        epoch++;
        socket.io.engine.close();
      }
    }).finally(() => { pending--; });
  });
  for (const signal of ["SIGINT", "SIGTERM"] as const) {
    process.once(signal, () => {
      stopping = true;
      clearTimeout(startupDeadline);
      socket.disconnect();
      const deadline = setTimeout(() => process.exit(1), 25_000);
      void queue.finally(() => {
        clearTimeout(deadline);
        console.info("Photon agent stopped.");
        process.exit(0);
      });
    });
  }
  socket.connect();
}

main().catch(error => {
  report(error);
  process.exitCode = 1;
});
