import { ApiError, Photon } from "@photon-ai/api";
import { io } from "socket.io-client";
import { checkpoint, spaceContext } from "./checkpoint";
import { configuration, SetupError } from "./config";
import { record, replyFor, spaceContextFor } from "./handler";

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

async function main() {
  const config = configuration();
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
        console.info("READY: Photon Beta iMessage subscription accepted; waiting for the configured test sender.");
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
      const input = replyFor(event, config);
      if (input) {
        console.info("Inbound iMessage matched the configured sender; sending an echo reply.");
        const sent = await photon.projects.sendMessage(input);
        if (typeof sent.id !== "string" || !/^pho_msg_[0-7][0-9a-hjkmnp-tv-z]{25}$/.test(sent.id)) {
          throw new SetupError("Photon accepted the reply but returned no valid message ID. Preserve the checkpoint for idempotent recovery.");
        }
        outbound.add(sent.id);
        console.info(`Reply accepted for asynchronous dispatch: ${sent.id}. Delivery is not yet confirmed.`);
      } else {
        const envelope = record(event);
        const message = record(record(envelope.data).object);
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
      if (error instanceof SetupError) fatal("Event handling stopped without advancing its saved cursor.");
      if (error instanceof ApiError && [400, 401, 403, 409, 422].includes(error.status)) {
        fatal("Reply blocked by Photon. Fix the API permission or message error before restarting.");
      }
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
