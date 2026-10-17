import { closeSync, existsSync, openSync, readFileSync, unlinkSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { ROLES, TeamError, configuration, discordConfigured, role, type Config } from "./config.ts";
import { connect, discord } from "./discord.ts";
import { containsSecret, coordinate, runGrok } from "./grok.ts";
import { botBranch, createWorkspace, ownedWorktrees, publish, git } from "./publish.ts";
import { Store } from "./store.ts";

process.umask(0o077);
const config = configuration();
const store = new Store(join(config.state, "team.sqlite"));
const mode = process.argv[2] || "doctor";

async function worker(config: Config, store: Store) {
  const lock = join(config.state, "worker.pid");
  if (existsSync(lock)) {
    const old = Number(readFileSync(lock, "utf8"));
    if (!Number.isInteger(old) || old <= 0) throw new TeamError("Invalid worker lock; inspect .state/worker.pid locally.");
    let alive = true;
    try { process.kill(old, 0); } catch (e) {
      if ((e as NodeJS.ErrnoException).code === "ESRCH") alive = false;
      else throw new TeamError("Cannot verify the existing worker process.");
    }
    if (alive) throw new TeamError("A worker is already running. Do not start a second copy.");
    unlinkSync(lock);
  }
  const descriptor = openSync(lock, "wx", 0o600);
  writeFileSync(descriptor, String(process.pid)); closeSync(descriptor);
  store.interrupt();
  let stopping = false;
  let current: Promise<unknown> | undefined;
  const cleanup = () => {
    if (existsSync(lock) && readFileSync(lock, "utf8") === String(process.pid)) unlinkSync(lock);
  };
  process.once("exit", cleanup);
  process.once("SIGINT", () => { stopping = true; });
  process.once("SIGTERM", () => { stopping = true; });
  let client: Awaited<ReturnType<typeof discord>> = null;
  try {
    client = await discord(config, store);
    console.log(`LOCAL READY: ${ROLES.length} on-demand Grok CLI roles; shared handoff queue active.`);
    if (!client) console.log("Discord is NOT connected: bot token/private server/channel configuration is incomplete.");
    if (!config.checks.length) console.log("Auto-publish is BLOCKED until real project checks are configured locally.");
    else console.log("Bot-branch auto-publish enabled with configured checks; never main or force pushes.");
    let nextScan = Date.now() + 30_000;
    while (!stopping) {
      const job = store.claim();
      if (job) {
        try {
          current = job.kind === "ask"
            ? coordinate(config, role(job.target), job.input, store)
            : publish(config, job.target, job.input);
          const result = await current as string;
          store.finish(job.id, result);
          if (job.kind === "ship") store.published(job.target, job.input);
          console.log(`Job ${job.id} completed (${job.target}).`);
        } catch (error) {
          const result = error instanceof TeamError ? error.message : "Worker failed; sensitive details suppressed.";
          store.finish(job.id, result, true);
          console.error(`Job ${job.id} failed (${job.target}): ${result}`);
        } finally { current = undefined; }
      } else if (config.checks.length && Date.now() >= nextScan) {
        nextScan = Date.now() + 30_000;
        try {
          for (const workspace of await ownedWorktrees(config)) {
            if (store.lastPublished(workspace.branch) === workspace.sha) continue;
            const count = Number(await git(["rev-list", "--count", `origin/main..${workspace.sha}`], workspace.path, config));
            if (count > 0)
              store.enqueue("ship", workspace.branch, workspace.sha, `auto:${workspace.branch}:${workspace.sha}`);
          }
        } catch { console.error("Auto-publish scan failed; no push attempted."); }
      } else await Bun.sleep(500);
    }
  } finally {
    await current;
    await client?.destroy();
    cleanup();
  }
}

try {
  if (mode === "doctor") {
    console.log(`Grok executable: ${config.bin}; model: ${config.model}`);
    console.log(`xAI API key present: ${Boolean(config.key)}`);
    console.log(`Discord configuration complete: ${discordConfigured(config)}`);
    console.log(`Configured publish checks: ${config.checks.length}`);
    console.log(`Stored recent announcements: ${store.announcements().length}; Discord mode: READ ONLY`);
    console.log("WhatsApp: not connected; personal WhatsApp has no supported bot API.");
  } else if (mode === "bootstrap") {
    const results = await Promise.allSettled(ROLES.map(async (owner) => {
      const result = await runGrok(config, owner,
        "Initialize your role. State your responsibility and one next readiness question in at most 70 words. No handoff and no claim that anything is connected or executed.", store);
      store.remember(owner, "Role initialized by local operator.", result.message);
      console.log(`${owner}: Grok CLI initialization passed.`);
    }));
    if (results.some((result) => result.status === "rejected"))
      throw new TeamError("Some Grok roles did not initialize. Successful roles were preserved; check authentication/usage/latency before retrying.");
  } else if (mode === "connect") {
    await connect(config);
  } else if (mode === "workspace") {
    const owner = role(process.argv[3] || "");
    const slug = process.argv[4] || "";
    const result = await createWorkspace(config, owner, slug);
    console.log(JSON.stringify(result));
    console.log("Use Factory Droid in this worktree. Commit only your own reviewed changes with the configured identity.");
  } else if (mode === "ask") {
    const owner = role(process.argv[3] || "");
    const input = process.argv.slice(4).join(" ");
    if (!input.trim() || input.length > 6000) throw new TeamError("Supply a task of 1–6000 characters.");
    if (containsSecret(input, config.secrets)) throw new TeamError("Task rejected: do not submit credentials.");
    console.log(`Queued job ${store.enqueue("ask", owner, input)}.`);
  } else if (mode === "ship") {
    const branch = botBranch(process.argv[3] || "");
    const workspace = (await ownedWorktrees(config)).find((w) => w.branch === branch);
    if (!workspace) throw new TeamError("Branch is not a bot-owned worktree.");
    console.log(`Queued publish job ${store.enqueue("ship", branch, workspace.sha)}.`);
  } else if (mode === "status") {
    const id = process.argv[3];
    const jobs = id ? [store.get(id)].filter((v) => v != null) : store.recent();
    for (const job of jobs) console.log(`${job.id} | ${job.target} | ${job.state}\n${job.output}`);
    if (!jobs.length) console.log("No jobs yet.");
  } else if (mode === "start") {
    await worker(config, store);
  } else throw new TeamError("Unknown command.");
} catch (error) {
  console.error(error instanceof TeamError ? error.message : "Setup failed; sensitive error details suppressed.");
  process.exitCode = 1;
} finally { store.close(); }
