import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { existsSync, mkdirSync, realpathSync } from "node:fs";
import { join, resolve, sep } from "node:path";
import { REPO, REPOSITORY, TeamError, role, type Config, type Role } from "./config.ts";
import { childEnvironment, containsSecret } from "./grok.ts";

const exec = promisify(execFile);
export function botBranch(branch: string) {
  if (!/^bots\/(coordinator|drone|logistics|vision|release)\/[a-z0-9][a-z0-9-]{0,47}$/.test(branch))
    throw new TeamError("Only bots/<role>/<lowercase-task> branches can publish. Never main.");
  return branch;
}

export async function command(argv: string[], cwd = REPO, config?: Config) {
  const env = config ? childEnvironment(config) : undefined;
  if (env) {
    delete env.XAI_API_KEY;
    if (process.env.SSH_AUTH_SOCK) env.SSH_AUTH_SOCK = process.env.SSH_AUTH_SOCK;
  }
  try {
    const result = await exec(argv[0], argv.slice(1), {
      cwd, env, encoding: "utf8", timeout: 120_000, maxBuffer: 20 * 1024 * 1024,
    });
    return result.stdout.trim();
  } catch { throw new TeamError("Local command/check failed; inspect it in the owned worktree. No success is assumed."); }
}

export async function git(args: string[], cwd = REPO, config?: Config) {
  return command(["git", ...args], cwd, config);
}

export async function ownedWorktrees(config: Config) {
  const text = await git(["worktree", "list", "--porcelain"]);
  const base = resolve(config.state, "worktrees") + sep;
  const entries: { path: string; branch: string; sha: string }[] = [];
  for (const block of text.split("\n\n")) {
    const lines = block.split("\n");
    const path = lines.find((line) => line.startsWith("worktree "))?.slice(9);
    const branch = lines.find((line) => line.startsWith("branch refs/heads/"))?.slice(18);
    const sha = lines.find((line) => line.startsWith("HEAD "))?.slice(5);
    if (!path || !branch || !sha || !existsSync(path)) continue;
    if (!realpathSync(path).startsWith(base)) continue;
    try { botBranch(branch); } catch { continue; }
    entries.push({ path, branch, sha });
  }
  return entries;
}

export async function createWorkspace(config: Config, owner: Role, slug: string) {
  role(owner);
  const branch = botBranch(`bots/${owner}/${slug}`);
  const path = join(config.state, "worktrees", owner, slug);
  if (existsSync(path)) throw new TeamError("Workspace already exists; use it rather than replacing it.");
  mkdirSync(join(config.state, "worktrees", owner), { recursive: true, mode: 0o700 });
  await verifyRemote(config);
  await git(["fetch", "origin", "main"], REPO, config);
  await git(["worktree", "add", "-b", branch, path, "origin/main"], REPO, config);
  return { path, branch };
}

export async function verifyRemote(config: Config, cwd = REPO) {
  const urls = (await git(["remote", "get-url", "--push", "--all", "origin"], cwd, config))
    .split("\n").filter(Boolean);
  if (!urls.length || urls.some((url) =>
    !/^(?:https:\/\/github\.com\/|git@github\.com:)jaromero3rd\/audio_signaling(?:\.git)?$/.test(url)))
    throw new TeamError(`Publisher only targets ${REPOSITORY}; origin does not match.`);
}

export function inspectChanges(paths: string[], patch: string, secrets: string[]) {
  for (const path of paths) {
    const parts = path.split("/");
    if (parts.some((part) =>
      (part.startsWith(".env") && part !== ".env.example") ||
      [".state", ".venv", "node_modules", ".git", ".grok", ".ssh"].includes(part)) ||
      /(?:^|\/)(?:auth|credentials)\.json$|\.(?:pem|p12|key)$/.test(path))
      throw new TeamError("Publishing blocked: private or generated files are in the committed changes.");
  }
  if (containsSecret(patch, secrets)) throw new TeamError("Publishing blocked: committed history looks like it contains credentials.");
}

export async function publish(config: Config, branch: string, expected: string, io = {
  git, command, ownedWorktrees, verifyRemote,
}) {
  botBranch(branch);
  if (!/^[a-f0-9]{40}$/.test(expected)) throw new TeamError("Invalid expected commit.");
  if (!config.checks.length) throw new TeamError("Publishing blocked: configure real project checks in PUBLISH_CHECKS_JSON locally first.");
  const workspace = (await io.ownedWorktrees(config)).find((entry) => entry.branch === branch);
  if (!workspace || workspace.sha !== expected) throw new TeamError("Branch is not an owned worktree at the requested commit.");
  const clean = async () => {
    if (await io.git(["status", "--porcelain"], workspace.path, config))
      throw new TeamError("Publishing blocked: worktree has uncommitted or untracked work.");
    if (await io.git(["symbolic-ref", "--short", "HEAD"], workspace.path, config) !== branch)
      throw new TeamError("Publishing blocked: worktree switched branches.");
    if (await io.git(["rev-parse", "HEAD"], workspace.path, config) !== expected)
      throw new TeamError("Publishing blocked: commit changed while checks ran.");
  };
  await io.verifyRemote(config, workspace.path);
  await clean();
  await io.git(["fetch", "origin", "main"], REPO, config);
  await io.git(["merge-base", "--is-ancestor", "origin/main", expected], workspace.path, config);
  const range = `origin/main..${expected}`;
  if (!await io.git(["rev-list", "--count", range], workspace.path, config).then((n) => Number(n) > 0))
    throw new TeamError("No new bot commits to publish.");
  // Inspect every new commit, including merge diffs against each parent and
  // files later deleted from the final tree. Git otherwise omits merge diffs.
  const pathText = await io.git(["log", "--diff-merges=separate", "--name-only", "-z", "--format=", range], workspace.path, config);
  const patch = await io.git(["log", "--diff-merges=separate", "-p", "--format=", range], workspace.path, config);
  inspectChanges(pathText.split("\0").map((s) => s.trim()).filter(Boolean), patch, config.secrets);
  const numstat = await io.git(["log", "--diff-merges=separate", "--numstat", "--format=", range], workspace.path, config);
  if (numstat.split("\n").some((line) => line.startsWith("-\t-\t")))
    throw new TeamError("Binary changes require manual review; auto-publishing is blocked.");
  await io.git(["diff", "--check", `origin/main...${expected}`], workspace.path, config);
  for (const argv of config.checks) await io.command(argv, workspace.path, config);
  await clean();
  await io.verifyRemote(config, workspace.path);
  // The destination is explicit; never use configured upstream/push defaults.
  // No force, merge, staging, commit, clean, reset, or default-branch push.
  await io.git(["push", "origin", `${expected}:refs/heads/${branch}`], workspace.path, config);
  return [
    `Published ${branch} at ${expected.slice(0, 12)} after configured checks.`,
    `https://github.com/${REPOSITORY}/tree/${branch}`,
    `Teammates: git fetch origin && git switch --track origin/${branch}`,
    "This branch is not merged into main.",
  ].join("\n");
}
