import { SetupError, type Config } from "./config";
import { audioRefFor, inboundTextFor, record } from "./handler";

type Command = "start" | "abort" | "land";
type Bridge = { token: string; port: number };
type Transport = (url: string, init: RequestInit) => Promise<Response>;

export function parseCommand(text: string): Command | undefined {
  // Whole utterances only. Never turn questions, negations or extra instructions into flight.
  if (text.length > 128 || text.includes("?")) return;
  const normalized = text.toLowerCase().trim().replace(/[.!]+$/, "").replace(/\s+/g, " ");
  if (/^(start|start survey|start the survey|survey the wheat)$/.test(normalized)) return "start";
  if (/^(abort|abort now|abort mission|stop|stop now)$/.test(normalized)) return "abort";
  if (/^(land|land now|land the drone)$/.test(normalized)) return "land";
}

export function bridgeConfiguration(env: Record<string, string | undefined> = process.env): Bridge | undefined {
  const enabled = env.COMMAND_BRIDGE_ENABLED?.trim() ?? "";
  if (["", "0", "false"].includes(enabled)) return;
  if (!["1", "true"].includes(enabled)) throw new SetupError("COMMAND_BRIDGE_ENABLED must be true or false.");
  const token = env.COMMAND_BRIDGE_TOKEN ?? "";
  if (!/^[\x21-\x7e]{32,}$/.test(token)) {
    throw new SetupError("COMMAND_BRIDGE_TOKEN needs at least 32 non-whitespace printable ASCII characters.");
  }
  const portText = env.COMMAND_BRIDGE_PORT?.trim() || "8787";
  const port = Number(portText);
  if (!/^\d+$/.test(portText) || !Number.isInteger(port) || port < 1 || port > 65535) {
    throw new SetupError("COMMAND_BRIDGE_PORT must be between 1 and 65535.");
  }
  return { token, port };
}

export function commandForwarder(
  env: Record<string, string | undefined> = process.env,
  transport: Transport = fetch,
  now: () => number = () => Date.now() / 1000,
) {
  const bridge = bridgeConfiguration(env);
  return {
    enabled: bridge !== undefined,
    async forward(event: unknown, config: Config, transcript?: string): Promise<string> {
      // Recheck all routing fields here, including the audio transcript path.
      const text = transcript === undefined ? inboundTextFor(event, config)
        : audioRefFor(event, config) ? transcript : undefined;
      if (text === undefined) return "Message not authorized for commands.";
      if (!bridge) return "Phone commands are disabled on this laptop.";
      const command = parseCommand(text);
      if (!command) return "No command forwarded. Send exactly: start survey, abort, or land.";
      const message = record(record(record(event).data).object);
      const id = message.id;
      const receivedAt = record(message.timestamps).receivedAt;
      const issuedAt = typeof receivedAt === "string" ? Date.parse(receivedAt) / 1000 : NaN;
      if (typeof id !== "string" || !/^[A-Za-z0-9_-]{1,128}$/.test(id) ||
          !Number.isFinite(issuedAt) || issuedAt < now() - 30 || issuedAt > now() + 5) {
        return "Command expired or missing its original receipt time. Send a new command.";
      }
      try {
        const response = await transport(`http://127.0.0.1:${bridge.port}/command`, {
          method: "POST",
          headers: { "content-type": "application/json", authorization: `Bearer ${bridge.token}` },
          body: JSON.stringify({ command, id, issued_at: issuedAt }),
          signal: AbortSignal.timeout(2500),
          redirect: "error",
        });
        if (!response.ok) return "Laptop bridge rejected the command. Check local bridge setup.";
        const payload = record(await response.json());
        if (payload.ok !== true) return "Laptop rejected the command. No new start was approved.";
        if (command === "start") {
          return payload.duplicate
            ? "Start request already received; takeoff is not confirmed."
            : "Start armed. Laptop will join the Tello, then preflight/takeoff. Takeoff is not confirmed yet.";
        }
        return "Stop latched on the mission laptop; landing is requested, not confirmed.";
      } catch {
        return "Laptop bridge unavailable; command outcome is unknown. Check the laptop and use local stop.";
      }
    },
  };
}
